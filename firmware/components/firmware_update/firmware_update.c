#include "firmware_update.h"

#include <stdatomic.h>
#include <stdio.h>
#include <string.h>

#include "board.h"
#include "esp_app_desc.h"
#include "esp_app_format.h"
#include "esp_attr.h"
#include "esp_log.h"
#include "esp_ota_ops.h"
#include "esp_private/startup_internal.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "protocol_contract.h"
#include "psa/crypto.h"
#include "sdkconfig.h"

#define IMAGE_DESCRIPTOR_OFFSET                                             \
    (sizeof(esp_image_header_t) + sizeof(esp_image_segment_header_t) +       \
     sizeof(esp_app_desc_t))
#define FINALIZE_ACK_GRACE_US (100LL * 1000LL)
#define RESTART_ACK_GRACE_US (1500LL * 1000LL)
#define BOOT_DIAGNOSTICS_MAGIC 0x424F4F54u

static const char *TAG = "firmware_update";

static atomic_int s_state;
static const esp_partition_t *s_target_partition;
static esp_ota_handle_t s_update_handle;
static bool s_update_handle_active;
static size_t s_expected_size;
static size_t s_received_size;
static uint8_t s_expected_sha256[PSA_HASH_LENGTH(PSA_ALG_SHA_256)];
static char s_expected_version[32];
static psa_hash_operation_t s_hash_operation = PSA_HASH_OPERATION_INIT;
static bool s_hash_active;
static atomic_bool s_finalize_pending;
static int64_t s_finalize_deadline_us;
static atomic_bool s_reboot_pending;
static int64_t s_reboot_deadline_us;

typedef struct {
    uint32_t magic;
    uint32_t current_sequence;
    uint32_t current_stage;
    int32_t current_confirm_error;
    uint32_t previous_sequence;
    uint32_t previous_stage;
    int32_t previous_reset_reason;
    int32_t previous_confirm_error;
} boot_diagnostics_t;

static RTC_NOINIT_ATTR boot_diagnostics_t s_boot_diagnostics;

void firmware_update_boot_diagnostics_start(void)
{
    const esp_reset_reason_t reset_reason = esp_reset_reason();
    const bool preserved =
        s_boot_diagnostics.magic == BOOT_DIAGNOSTICS_MAGIC &&
        reset_reason != ESP_RST_POWERON &&
        s_boot_diagnostics.current_stage <= FIRMWARE_BOOT_STAGE_MAIN_LOOP;
    if (!preserved) {
        memset(&s_boot_diagnostics, 0, sizeof(s_boot_diagnostics));
        s_boot_diagnostics.magic = BOOT_DIAGNOSTICS_MAGIC;
    } else {
        s_boot_diagnostics.previous_sequence =
            s_boot_diagnostics.current_sequence;
        s_boot_diagnostics.previous_stage = s_boot_diagnostics.current_stage;
        s_boot_diagnostics.previous_confirm_error =
            s_boot_diagnostics.current_confirm_error;
        s_boot_diagnostics.previous_reset_reason = (int32_t)reset_reason;
    }
    s_boot_diagnostics.current_sequence =
        s_boot_diagnostics.previous_sequence + 1u;
    s_boot_diagnostics.current_stage = FIRMWARE_BOOT_STAGE_SYSTEM_READY;
    s_boot_diagnostics.current_confirm_error = ESP_OK;
}

/* esp_reset_reason() is populated by an ESP-IDF constructor. CORE runs
 * before constructors; SECONDARY runs afterwards, still before app_main. */
ESP_SYSTEM_INIT_FN(wmp_boot_diagnostics, SECONDARY, BIT(0), 0)
{
    firmware_update_boot_diagnostics_start();
    firmware_update_boot_stage(FIRMWARE_BOOT_STAGE_SYSTEM_READY);
    return ESP_OK;
}

void firmware_update_boot_stage(firmware_boot_stage_t stage)
{
    if (s_boot_diagnostics.magic == BOOT_DIAGNOSTICS_MAGIC &&
        stage >= FIRMWARE_BOOT_STAGE_SYSTEM_READY &&
        stage <= FIRMWARE_BOOT_STAGE_MAIN_LOOP) {
        s_boot_diagnostics.current_stage = (uint32_t)stage;
    }
}

static void set_reason(char *reason, size_t reason_size, const char *message)
{
    if (reason != NULL && reason_size > 0) {
        snprintf(reason, reason_size, "%s", message);
    }
}

static bool decode_hex_nibble(char value, uint8_t *nibble)
{
    if (value >= '0' && value <= '9') {
        *nibble = (uint8_t)(value - '0');
        return true;
    }
    if (value >= 'a' && value <= 'f') {
        *nibble = (uint8_t)(value - 'a' + 10);
        return true;
    }
    return false;
}

static bool parse_sha256(const char *hex, uint8_t output[32])
{
    if (hex == NULL || strlen(hex) != FIRMWARE_UPDATE_SHA256_HEX_LENGTH) {
        return false;
    }
    for (size_t index = 0; index < 32; ++index) {
        uint8_t high = 0;
        uint8_t low = 0;
        if (!decode_hex_nibble(hex[index * 2], &high) ||
            !decode_hex_nibble(hex[index * 2 + 1], &low)) {
            return false;
        }
        output[index] = (uint8_t)((high << 4) | low);
    }
    return true;
}

static bool version_string_valid(const char *version)
{
    if (version == NULL || version[0] == '\0' ||
        strlen(version) >= sizeof(s_expected_version)) {
        return false;
    }
    for (const char *cursor = version; *cursor != '\0'; ++cursor) {
        const char value = *cursor;
        if (!((value >= 'a' && value <= 'z') ||
              (value >= 'A' && value <= 'Z') ||
              (value >= '0' && value <= '9') || value == '.' || value == '-' ||
              value == '_' || value == '+')) {
            return false;
        }
    }
    return true;
}

static void reset_hash(void)
{
    if (s_hash_active) {
        (void)psa_hash_abort(&s_hash_operation);
    }
    s_hash_operation = psa_hash_operation_init();
    s_hash_active = false;
}

static void clear_transaction(void)
{
    if (s_update_handle_active) {
        (void)esp_ota_abort(s_update_handle);
    }
    s_update_handle_active = false;
    reset_hash();
    s_target_partition = NULL;
    s_expected_size = 0;
    s_received_size = 0;
    memset(s_expected_sha256, 0, sizeof(s_expected_sha256));
    memset(s_expected_version, 0, sizeof(s_expected_version));
}

static bool descriptor_string_valid(const char *value, size_t capacity,
                                    const char *expected)
{
    return memchr(value, '\0', capacity) != NULL && strcmp(value, expected) == 0;
}

static esp_err_t validate_first_chunk(const uint8_t *data, size_t length,
                                      char *reason, size_t reason_size)
{
    const size_t descriptor_end =
        IMAGE_DESCRIPTOR_OFFSET + sizeof(firmware_image_descriptor_t);
    if (length < descriptor_end) {
        set_reason(reason, reason_size,
                   "first chunk is too small to identify the firmware image");
        return ESP_ERR_INVALID_SIZE;
    }

    firmware_image_descriptor_t descriptor;
    memcpy(&descriptor, data + IMAGE_DESCRIPTOR_OFFSET, sizeof(descriptor));
    if (descriptor.magic != FIRMWARE_IMAGE_DESCRIPTOR_MAGIC ||
        descriptor.descriptor_version != FIRMWARE_IMAGE_DESCRIPTOR_VERSION ||
        descriptor.descriptor_size != sizeof(firmware_image_descriptor_t)) {
        set_reason(reason, reason_size,
                   "firmware image does not contain a supported WMP descriptor");
        return ESP_ERR_INVALID_VERSION;
    }
    if (!descriptor_string_valid(descriptor.product_id,
                                 sizeof(descriptor.product_id), WMP_PRODUCT_ID) ||
        !descriptor_string_valid(descriptor.hardware_id,
                                 sizeof(descriptor.hardware_id),
                                 board_hardware_id())) {
        set_reason(reason, reason_size,
                   "firmware image is for a different product or hardware target");
        return FIRMWARE_UPDATE_ERR_HARDWARE_MISMATCH;
    }
    return ESP_OK;
}

esp_err_t firmware_update_init(void)
{
    clear_transaction();
    atomic_store(&s_state, FIRMWARE_UPDATE_IDLE);
    atomic_store(&s_finalize_pending, false);
    s_finalize_deadline_us = 0;
    atomic_store(&s_reboot_pending, false);
    s_reboot_deadline_us = 0;
    return psa_crypto_init() == PSA_SUCCESS ? ESP_OK : ESP_FAIL;
}

esp_err_t firmware_update_confirm_running(void)
{
#if CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE
    const esp_partition_t *running = esp_ota_get_running_partition();
    if (running == NULL) {
        return ESP_ERR_NOT_FOUND;
    }
    esp_ota_img_states_t state = ESP_OTA_IMG_UNDEFINED;
    const esp_err_t state_error = esp_ota_get_state_partition(running, &state);
    if (state_error != ESP_OK) {
        s_boot_diagnostics.current_confirm_error = state_error;
        return state_error;
    }
    if (state != ESP_OTA_IMG_PENDING_VERIFY) {
        firmware_update_boot_stage(FIRMWARE_BOOT_STAGE_CONFIRMED);
        return ESP_OK;
    }
    const esp_err_t error = esp_ota_mark_app_valid_cancel_rollback();
    s_boot_diagnostics.current_confirm_error = error;
    if (error == ESP_OK) {
        firmware_update_boot_stage(FIRMWARE_BOOT_STAGE_CONFIRMED);
        ESP_LOGI(TAG, "confirmed OTA image in %s after startup self-test",
                 running->label);
    }
    return error;
#else
    firmware_update_boot_stage(FIRMWARE_BOOT_STAGE_CONFIRMED);
    return ESP_OK;
#endif
}

esp_err_t firmware_update_begin(const char *product_id, const char *hardware_id,
                                const char *version, size_t image_size,
                                const char *sha256_hex, char *reason,
                                size_t reason_size)
{
    uint8_t expected_sha256[PSA_HASH_LENGTH(PSA_ALG_SHA_256)];
    if (!board_is_product_target()) {
        set_reason(reason, reason_size,
                   "firmware update is disabled on the generic devkit target");
        return ESP_ERR_NOT_SUPPORTED;
    }
    const firmware_update_state_t state =
        (firmware_update_state_t)atomic_load(&s_state);
    if (state == FIRMWARE_UPDATE_RECEIVING ||
        state == FIRMWARE_UPDATE_FINALIZING ||
        atomic_load(&s_finalize_pending) ||
        atomic_load(&s_reboot_pending)) {
        set_reason(reason, reason_size, "another firmware update is already active");
        return ESP_ERR_INVALID_STATE;
    }
    if (product_id == NULL || hardware_id == NULL || version == NULL ||
        strcmp(product_id, WMP_PRODUCT_ID) != 0 ||
        strcmp(hardware_id, board_hardware_id()) != 0) {
        set_reason(reason, reason_size,
                   "product_id or hardware_id does not match this device");
        return FIRMWARE_UPDATE_ERR_HARDWARE_MISMATCH;
    }
    if (!version_string_valid(version) ||
        !parse_sha256(sha256_hex, expected_sha256)) {
        set_reason(reason, reason_size, "version or SHA-256 is invalid");
        return ESP_ERR_INVALID_ARG;
    }

    const esp_partition_t *target = esp_ota_get_next_update_partition(NULL);
    if (target == NULL) {
        set_reason(reason, reason_size, "no inactive OTA application slot exists");
        return ESP_ERR_NOT_FOUND;
    }
    if (image_size == 0 || image_size > target->size) {
        set_reason(reason, reason_size, "firmware image does not fit the OTA slot");
        return ESP_ERR_INVALID_SIZE;
    }

    clear_transaction();
    const esp_err_t begin_error =
        esp_ota_begin(target, OTA_WITH_SEQUENTIAL_WRITES, &s_update_handle);
    if (begin_error != ESP_OK) {
        set_reason(reason, reason_size, "could not prepare the inactive OTA slot");
        atomic_store(&s_state, FIRMWARE_UPDATE_FAILED);
        return begin_error;
    }
    s_update_handle_active = true;
    if (psa_hash_setup(&s_hash_operation, PSA_ALG_SHA_256) != PSA_SUCCESS) {
        clear_transaction();
        atomic_store(&s_state, FIRMWARE_UPDATE_FAILED);
        set_reason(reason, reason_size, "could not initialize firmware hashing");
        return ESP_FAIL;
    }
    s_hash_active = true;
    s_target_partition = target;
    s_expected_size = image_size;
    memcpy(s_expected_sha256, expected_sha256, sizeof(s_expected_sha256));
    snprintf(s_expected_version, sizeof(s_expected_version), "%s", version);
    s_received_size = 0;
    atomic_store(&s_state, FIRMWARE_UPDATE_RECEIVING);
    set_reason(reason, reason_size, "ready");
    return ESP_OK;
}

esp_err_t firmware_update_write(size_t offset, const uint8_t *data, size_t length,
                                char *reason, size_t reason_size)
{
    if (atomic_load(&s_state) != FIRMWARE_UPDATE_RECEIVING ||
        !s_update_handle_active ||
        !s_hash_active) {
        set_reason(reason, reason_size, "no firmware update is receiving data");
        return ESP_ERR_INVALID_STATE;
    }
    if (data == NULL || length == 0 ||
        length > FIRMWARE_UPDATE_MAX_CHUNK_BYTES || offset != s_received_size ||
        length > s_expected_size - s_received_size) {
        set_reason(reason, reason_size,
                   "chunk is empty, out of order, too large, or exceeds image size");
        return ESP_ERR_INVALID_SIZE;
    }
    if (offset == 0) {
        const esp_err_t identity_error =
            validate_first_chunk(data, length, reason, reason_size);
        if (identity_error != ESP_OK) {
            return identity_error;
        }
    }
    if (psa_hash_update(&s_hash_operation, data, length) != PSA_SUCCESS) {
        set_reason(reason, reason_size, "could not hash firmware chunk");
        atomic_store(&s_state, FIRMWARE_UPDATE_FAILED);
        clear_transaction();
        return ESP_FAIL;
    }
    const esp_err_t write_error = esp_ota_write(s_update_handle, data, length);
    if (write_error != ESP_OK) {
        set_reason(reason, reason_size, "could not write firmware chunk");
        atomic_store(&s_state, FIRMWARE_UPDATE_FAILED);
        clear_transaction();
        return write_error;
    }
    s_received_size += length;
    set_reason(reason, reason_size, "written");
    return ESP_OK;
}

esp_err_t firmware_update_finish(char *reason, size_t reason_size)
{
    if (atomic_load(&s_state) != FIRMWARE_UPDATE_RECEIVING ||
        !s_update_handle_active ||
        !s_hash_active) {
        set_reason(reason, reason_size, "no firmware update can be finalized");
        return ESP_ERR_INVALID_STATE;
    }
    if (s_received_size != s_expected_size) {
        set_reason(reason, reason_size, "firmware image is incomplete");
        return ESP_ERR_INVALID_SIZE;
    }

    uint8_t actual_sha256[PSA_HASH_LENGTH(PSA_ALG_SHA_256)];
    size_t actual_length = 0;
    const psa_status_t hash_status = psa_hash_finish(
        &s_hash_operation, actual_sha256, sizeof(actual_sha256), &actual_length);
    s_hash_active = false;
    if (hash_status != PSA_SUCCESS || actual_length != sizeof(actual_sha256) ||
        memcmp(actual_sha256, s_expected_sha256, sizeof(actual_sha256)) != 0) {
        set_reason(reason, reason_size, "firmware SHA-256 does not match manifest");
        atomic_store(&s_state, FIRMWARE_UPDATE_FAILED);
        clear_transaction();
        return ESP_ERR_INVALID_CRC;
    }

    const esp_ota_handle_t completed_handle = s_update_handle;
    s_update_handle_active = false;
    const esp_err_t end_error = esp_ota_end(completed_handle);
    if (end_error != ESP_OK) {
        set_reason(reason, reason_size,
                   "firmware image validation or signature check failed");
        atomic_store(&s_state, FIRMWARE_UPDATE_FAILED);
        clear_transaction();
        return end_error;
    }

    esp_app_desc_t app_description;
    if (esp_ota_get_partition_description(s_target_partition,
                                          &app_description) != ESP_OK ||
        strcmp(app_description.project_name, "wired_macro_pad") != 0 ||
        strcmp(app_description.version, s_expected_version) != 0) {
        set_reason(reason, reason_size,
                   "firmware metadata does not match the update manifest");
        atomic_store(&s_state, FIRMWARE_UPDATE_FAILED);
        clear_transaction();
        return ESP_ERR_INVALID_VERSION;
    }

    /*
     * esp_ota_set_boot_partition() validates the full image again before it
     * writes otadata. Run that blocking work from the main loop, not from the
     * TinyUSB receive callback that delivered FW_END.
    */
    atomic_store(&s_state, FIRMWARE_UPDATE_FINALIZING);
    s_finalize_deadline_us = esp_timer_get_time() + FINALIZE_ACK_GRACE_US;
    atomic_store(&s_finalize_pending, true);
    set_reason(reason, reason_size, "verified; activation scheduled");
    return ESP_OK;
}

esp_err_t firmware_update_abort(char *reason, size_t reason_size)
{
    if (atomic_load(&s_state) != FIRMWARE_UPDATE_RECEIVING) {
        set_reason(reason, reason_size, "no receiving firmware update exists");
        return ESP_ERR_INVALID_STATE;
    }
    clear_transaction();
    atomic_store(&s_state, FIRMWARE_UPDATE_IDLE);
    set_reason(reason, reason_size, "aborted");
    return ESP_OK;
}

bool firmware_update_is_active(void)
{
    const firmware_update_state_t state = (firmware_update_state_t)atomic_load(&s_state);
    return state == FIRMWARE_UPDATE_RECEIVING || state == FIRMWARE_UPDATE_FINALIZING ||
           state == FIRMWARE_UPDATE_READY_TO_REBOOT;
}

void firmware_update_get_status(firmware_update_status_t *status)
{
    if (status == NULL) {
        return;
    }
    memset(status, 0, sizeof(*status));
    status->state = (firmware_update_state_t)atomic_load(&s_state);
    status->expected_size = s_expected_size;
    status->received_size = s_received_size;
    if (s_target_partition != NULL) {
        status->target_capacity = s_target_partition->size;
        snprintf(status->target_partition, sizeof(status->target_partition), "%s",
                 s_target_partition->label);
    } else {
        const esp_partition_t *target = esp_ota_get_next_update_partition(NULL);
        if (target != NULL) {
            status->target_capacity = target->size;
            snprintf(status->target_partition, sizeof(status->target_partition),
                     "%s", target->label);
        }
    }
    if (s_expected_size > 0) {
        static const char HEX_DIGITS[] = "0123456789abcdef";
        for (size_t index = 0; index < sizeof(s_expected_sha256); ++index) {
            status->expected_sha256[index * 2] =
                HEX_DIGITS[s_expected_sha256[index] >> 4];
            status->expected_sha256[index * 2 + 1] =
                HEX_DIGITS[s_expected_sha256[index] & 0x0f];
        }
    }
    snprintf(status->expected_version, sizeof(status->expected_version), "%s",
             s_expected_version);
    const esp_app_desc_t *running_description = esp_app_get_description();
    if (running_description != NULL) {
        snprintf(status->running_version, sizeof(status->running_version), "%s",
                 running_description->version);
    }
    const esp_partition_t *running = esp_ota_get_running_partition();
    if (running != NULL) {
        snprintf(status->running_partition, sizeof(status->running_partition),
                 "%s", running->label);
#if CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE
        esp_ota_img_states_t image_state = ESP_OTA_IMG_UNDEFINED;
        const esp_err_t state_error =
            esp_ota_get_state_partition(running, &image_state);
        status->rollback_pending = state_error != ESP_OK ||
                                   image_state == ESP_OTA_IMG_PENDING_VERIFY;
#endif
    }
    status->reboot_pending = atomic_load(&s_reboot_pending);
    if (s_boot_diagnostics.magic == BOOT_DIAGNOSTICS_MAGIC) {
        status->current_boot_sequence = s_boot_diagnostics.current_sequence;
        status->current_boot_stage =
            (firmware_boot_stage_t)s_boot_diagnostics.current_stage;
        status->current_confirm_error =
            s_boot_diagnostics.current_confirm_error;
        status->previous_boot_sequence = s_boot_diagnostics.previous_sequence;
        status->previous_boot_stage =
            (firmware_boot_stage_t)s_boot_diagnostics.previous_stage;
        status->previous_reset_reason =
            s_boot_diagnostics.previous_reset_reason;
        status->previous_confirm_error =
            s_boot_diagnostics.previous_confirm_error;
    }
}

void firmware_update_get_progress(size_t *received_size, size_t *expected_size)
{
    if (received_size != NULL) {
        *received_size = s_received_size;
    }
    if (expected_size != NULL) {
        *expected_size = s_expected_size;
    }
}

void firmware_update_poll(void)
{
    if (atomic_load(&s_finalize_pending) &&
        esp_timer_get_time() >= s_finalize_deadline_us &&
        atomic_exchange(&s_finalize_pending, false)) {
        const esp_err_t boot_error = s_target_partition == NULL
                                         ? ESP_ERR_INVALID_STATE
                                         : esp_ota_set_boot_partition(
                                               s_target_partition);
        if (boot_error != ESP_OK) {
            ESP_LOGE(TAG, "could not select the new boot partition: %s",
                     esp_err_to_name(boot_error));
            atomic_store(&s_state, FIRMWARE_UPDATE_FAILED);
            return;
        }
        atomic_store(&s_state, FIRMWARE_UPDATE_READY_TO_REBOOT);
        s_reboot_deadline_us = esp_timer_get_time() + RESTART_ACK_GRACE_US;
        atomic_store(&s_reboot_pending, true);
    }
    if (atomic_load(&s_reboot_pending) &&
        esp_timer_get_time() >= s_reboot_deadline_us) {
        ESP_LOGI(TAG, "restarting into verified OTA image");
        esp_restart();
    }
}
