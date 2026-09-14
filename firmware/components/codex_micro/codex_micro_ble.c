#include "codex_micro.h"
#include "claude_status.h"

#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>

#include "cJSON.h"
#include "battery_service.h"
#include "config_store.h"
#include "codex_micro_attention_model.h"
#include "codex_micro_controls.h"
#include "codex_micro_protocol.h"
#include "codex_micro_radial_filter.h"
#include "cw2015.h"
#include "esp_bt.h"
#include "esp_bt_main.h"
#include "esp_attr.h"
#include "esp_app_desc.h"
#include "esp_gap_ble_api.h"
#include "esp_hid_common.h"
#include "esp_hidd.h"
#include "esp_hidd_gatts.h"
#include "esp_log.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "nvs.h"
#include "nvs_flash.h"

#define CODEX_MICRO_CONNECTED_LED_INDEX CODEX_MICRO_TASK_COUNT
#define CODEX_MICRO_LAYER_LED_INDEX (CODEX_MICRO_TASK_COUNT + 1u)
/* Must remain aligned with usb_service.h's stable standard report IDs. */
#define USB_SERVICE_REPORT_ID_KEYBOARD 1u
#define USB_SERVICE_REPORT_ID_CONSUMER 2u
#define USB_SERVICE_REPORT_ID_MOUSE 3u
#define CODEX_JOYSTICK_REPORT_INTERVAL_US 16000
#define CODEX_JOYSTICK_ANGLE_EPSILON 0.0025f
#define CODEX_JOYSTICK_REVERSE_JITTER_TURNS 0.006f
#define CODEX_JOYSTICK_RELEASE_GRACE_US 30000
#define CODEX_MICRO_SOFT_RESTART_MODE_MAGIC 0x4D4F4445u
#define CODEX_MICRO_BLE_NVS_NAMESPACE "codex_ble"

typedef struct {
    uint8_t valid;
    uint8_t address_type;
    uint8_t address[ESP_BD_ADDR_LEN];
} codex_micro_ble_peer_t;

typedef enum {
    BLE_PEER_FILTER_IDLE = 0,
    BLE_PEER_FILTER_STOPPING,
    BLE_PEER_FILTER_CLEARING,
    BLE_PEER_FILTER_ADDING,
} ble_peer_filter_state_t;

static const char *TAG = "codex_micro_ble";

#ifndef WMP_BUILD_ID
#define WMP_BUILD_ID "local-unidentified"
#endif

static const char *firmware_version(void)
{
    const esp_app_desc_t *description = esp_app_get_description();
    return description != NULL ? description->version : "unknown";
}

#define HID_INPUT(flags) 0x81, (flags)
#define HID_OUTPUT(flags) 0x91, (flags)

static const uint8_t REPORT_MAP[] = {
    /* Standard six-key rollover keyboard, report ID 1. */
    0x05, 0x01, 0x09, 0x06, 0xA1, 0x01,
    0x85, USB_SERVICE_REPORT_ID_KEYBOARD,
    0x05, 0x07, 0x19, 0xE0, 0x29, 0xE7,
    0x15, 0x00, 0x25, 0x01, 0x95, 0x08, 0x75, 0x01,
    HID_INPUT(0x02),
    0x95, 0x01, 0x75, 0x08, HID_INPUT(0x01),
    0x05, 0x08, 0x19, 0x01, 0x29, 0x05,
    0x95, 0x05, 0x75, 0x01, HID_OUTPUT(0x02),
    0x95, 0x01, 0x75, 0x03, HID_OUTPUT(0x01),
    0x05, 0x07, 0x19, 0x00, 0x2A, 0xFF, 0x00,
    0x15, 0x00, 0x26, 0xFF, 0x00, 0x95, 0x06, 0x75, 0x08,
    HID_INPUT(0x00), 0xC0,

    /* Consumer control, report ID 2. */
    0x05, 0x0C, 0x09, 0x01, 0xA1, 0x01,
    0x85, USB_SERVICE_REPORT_ID_CONSUMER,
    0x15, 0x00, 0x26, 0xFF, 0x03, 0x19, 0x00, 0x2A, 0xFF, 0x03,
    0x95, 0x01, 0x75, 0x10, HID_INPUT(0x00), 0xC0,

    /* Five-button relative mouse with vertical and horizontal wheels, ID 3. */
    0x05, 0x01, 0x09, 0x02, 0xA1, 0x01,
    0x85, USB_SERVICE_REPORT_ID_MOUSE,
    0x09, 0x01, 0xA1, 0x00,
    0x05, 0x09, 0x19, 0x01, 0x29, 0x05,
    0x15, 0x00, 0x25, 0x01, 0x95, 0x05, 0x75, 0x01,
    HID_INPUT(0x02),
    0x95, 0x01, 0x75, 0x03, HID_INPUT(0x01),
    0x05, 0x01, 0x09, 0x30, 0x09, 0x31,
    0x15, 0x81, 0x25, 0x7F, 0x95, 0x02, 0x75, 0x08,
    HID_INPUT(0x06),
    0x09, 0x38, 0x15, 0x81, 0x25, 0x7F, 0x95, 0x01, 0x75, 0x08,
    HID_INPUT(0x06),
    0x05, 0x0C, 0x0A, 0x38, 0x02,
    0x15, 0x81, 0x25, 0x7F, 0x95, 0x01, 0x75, 0x08,
    HID_INPUT(0x06), 0xC0, 0xC0,

    /* Codex vendor input/output channel, report ID 6. */
    0x06, 0x00, 0xFF, /* Usage Page (Vendor Defined 0xFF00) */
    0x09, 0x01,       /* Usage (1) */
    0xA1, 0x01,       /* Collection (Application) */
    0x85, CODEX_MICRO_REPORT_ID, /* Report ID */
    0x15, 0x00,       /* Logical Minimum (0) */
    0x26, 0xFF, 0x00, /* Logical Maximum (255) */
    0x75, 0x08,       /* Report Size (8) */
    0x95, CODEX_MICRO_REPORT_BYTES, /* Report Count */
    0x09, 0x01,       /* Usage (1) */
    HID_INPUT(0x02),  /* Input (Data, Variable, Absolute) */
    0x95, CODEX_MICRO_REPORT_BYTES, /* Report Count */
    0x09, 0x02,       /* Usage (2) */
    HID_OUTPUT(0x02), /* Output (Data, Variable, Absolute) */
    0xC0,             /* End Collection */
};

static esp_hid_raw_report_map_t s_report_maps[] = {
    {.data = REPORT_MAP, .len = sizeof(REPORT_MAP)},
};

static esp_hid_device_config_t s_hid_config = {
    .vendor_id = CODEX_MICRO_BLE_VID,
    .product_id = CODEX_MICRO_BLE_PID,
    .version = CODEX_MICRO_COMPAT_BCD_DEVICE,
    .device_name = WMP_BLE_NAME_DEFAULT,
    .manufacturer_name = CODEX_MICRO_COMPAT_MANUFACTURER,
    .serial_number = NULL,
    .report_maps = s_report_maps,
    .report_maps_len = 1,
};

static char s_ble_device_name[CODEX_MICRO_BLE_DEVICE_NAME_BYTES] =
    WMP_BLE_NAME_DEFAULT;
static esp_hidd_dev_t *s_hid;
static SemaphoreHandle_t s_tx_mutex;
static SemaphoreHandle_t s_rx_mutex;

typedef enum {
    CODEX_TRANSPORT_AUTO = 0,
    CODEX_TRANSPORT_USB,
    CODEX_TRANSPORT_BLE,
} codex_transport_t;

static codex_micro_usb_hid_t s_usb_hid;
static atomic_bool s_usb_connected;
static atomic_bool s_input_suppressed;
static atomic_bool s_ble_suspended;
static atomic_bool s_connected;
static codex_micro_ble_gatts_observer_fn s_gatts_observer;
static atomic_bool s_hid_services_ready;
static void *s_gatts_observer_context;
static atomic_int s_mode;
static atomic_bool s_transport_wait_neutral;
static atomic_bool s_advertising_configured;
static atomic_bool s_scan_response_configured;
static atomic_bool s_advertising_active;
static atomic_bool s_advertising_starting;
static atomic_bool s_advertising_retry_running;
static atomic_bool s_peer_filter_ready;
static atomic_int s_peer_filter_state;
static atomic_uchar s_active_peer_slot;
static atomic_uint_least32_t s_notify_subscribe_events;
static atomic_uint_least32_t s_notify_unsubscribe_events;
static atomic_uint_least16_t s_last_subscribe_attr;
static atomic_bool s_last_subscribe_notify;
static atomic_uint_least16_t s_negotiated_mtu;
static atomic_uint_least32_t s_notify_tx_events;
static atomic_uint_least32_t s_notify_tx_successes;
static atomic_uint_least32_t s_notify_tx_failures;
static atomic_uint_least16_t s_last_notify_tx_attr;
static atomic_int_least32_t s_last_notify_tx_status;
static atomic_uint_least32_t s_rx_output_reports;
static atomic_uint_least32_t s_rx_parse_errors;
static atomic_uint_least32_t s_rpc_responses;
static atomic_uint_least32_t s_tx_attempts;
static atomic_uint_least32_t s_tx_successes;
static atomic_uint_least32_t s_tx_failures;
static atomic_int_least32_t s_last_tx_error;
static RTC_NOINIT_ATTR uint32_t s_soft_restart_mode_magic;
static RTC_NOINIT_ATTR uint32_t s_soft_restart_mode_value;
static bool s_soft_restart_mode_pending;
static codex_micro_mode_t s_soft_restart_pending_mode;
static codex_micro_rx_t s_usb_rx;
static codex_micro_rx_t s_ble_rx;
static portMUX_TYPE s_state_lock = portMUX_INITIALIZER_UNLOCKED;
static codex_agent_press_model_t s_agent_press = {
    .latest = {.agent = -1},
};
static board_rgb_t s_task_colors[CODEX_MICRO_TASK_COUNT];
static uint32_t s_task_base_colors[CODEX_MICRO_TASK_COUNT];
static float s_task_brightness[CODEX_MICRO_TASK_COUNT];
typedef struct {
    uint32_t rgb;
    float brightness;
    codex_transport_t source;
} codex_task_light_t;
/* CODEX owns a normalized, transient view; other modes retain raw host colors. */
static codex_task_light_t s_codex_task_lights[CODEX_MICRO_TASK_COUNT];
static codex_micro_attention_model_t
    s_attention_models[CODEX_TRANSPORT_BLE + 1u];
static codex_micro_attention_t
    s_attention_notifications[CODEX_TRANSPORT_BLE + 1u];
static bool s_ble_press_active[BOARD_CONTROL_COUNT];
static codex_transport_t s_press_transport[BOARD_CONTROL_COUNT];
static bool s_radial_active;
static float s_radial_angle;
static int64_t s_radial_last_sent_at_us;
static codex_transport_t s_radial_transport;
static codex_micro_radial_filter_t s_radial_filter;
static codex_micro_radial_release_gate_t s_radial_release_gate;
static uint32_t s_radial_filter_generation;
static nvs_handle_t s_ble_peer_nvs;
static codex_micro_ble_peer_t
    s_ble_peers[CODEX_MICRO_BLE_SLOT_COUNT];
static esp_bd_addr_t s_connected_peer_address;
static atomic_bool s_connected_peer_valid;

static const char *const BLE_PEER_NVS_KEYS[CODEX_MICRO_BLE_SLOT_COUNT] = {
    "peer1", "peer2", "peer3",
};

static esp_err_t start_advertising(void);
static void ensure_advertising(void);
static void release_active_controls(void);
static void poll_radial_joystick(void);
static void restore_soft_restart_mode_if_ready(void);
static bool handle_send_result(esp_err_t result, bool pressed,
                               const char *control_name);
static esp_err_t refresh_peer_filter(void);

static bool valid_ble_slot(uint8_t slot)
{
    return slot >= 1u && slot <= CODEX_MICRO_BLE_SLOT_COUNT;
}

static codex_micro_ble_peer_t peer_for_slot(uint8_t slot)
{
    codex_micro_ble_peer_t peer = {0};
    if (!valid_ble_slot(slot)) {
        return peer;
    }
    portENTER_CRITICAL(&s_state_lock);
    peer = s_ble_peers[slot - 1u];
    portEXIT_CRITICAL(&s_state_lock);
    return peer;
}

static esp_err_t persist_active_peer_slot(uint8_t slot)
{
    esp_err_t result = nvs_set_u8(s_ble_peer_nvs, "active", slot);
    return result == ESP_OK ? nvs_commit(s_ble_peer_nvs) : result;
}

static esp_err_t persist_peer(uint8_t slot,
                              const codex_micro_ble_peer_t *peer)
{
    if (!valid_ble_slot(slot) || peer == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    esp_err_t result = nvs_set_blob(s_ble_peer_nvs,
                                    BLE_PEER_NVS_KEYS[slot - 1u], peer,
                                    sizeof(*peer));
    return result == ESP_OK ? nvs_commit(s_ble_peer_nvs) : result;
}

static esp_err_t load_ble_peer_slots(void)
{
    esp_err_t result = nvs_open(CODEX_MICRO_BLE_NVS_NAMESPACE,
                                NVS_READWRITE, &s_ble_peer_nvs);
    if (result != ESP_OK) {
        return result;
    }

    memset(s_ble_peers, 0, sizeof(s_ble_peers));
    for (size_t i = 0; i < CODEX_MICRO_BLE_SLOT_COUNT; ++i) {
        size_t length = sizeof(s_ble_peers[i]);
        result = nvs_get_blob(s_ble_peer_nvs, BLE_PEER_NVS_KEYS[i],
                              &s_ble_peers[i], &length);
        if (result == ESP_ERR_NVS_NOT_FOUND) {
            memset(&s_ble_peers[i], 0, sizeof(s_ble_peers[i]));
            continue;
        }
        if (result != ESP_OK || length != sizeof(s_ble_peers[i]) ||
            s_ble_peers[i].valid != 1u) {
            memset(&s_ble_peers[i], 0, sizeof(s_ble_peers[i]));
        }
    }

    uint8_t active_slot = 1u;
    result = nvs_get_u8(s_ble_peer_nvs, "active", &active_slot);
    if (result != ESP_OK || !valid_ble_slot(active_slot)) {
        active_slot = 1u;
    }
    atomic_store(&s_active_peer_slot, active_slot);
    return ESP_OK;
}

static esp_ble_wl_addr_type_t whitelist_address_type(uint8_t address_type)
{
    return address_type == BLE_ADDR_TYPE_PUBLIC
               ? BLE_WL_ADDR_TYPE_PUBLIC
               : BLE_WL_ADDR_TYPE_RANDOM;
}

static bool peer_address_matches(const codex_micro_ble_peer_t *peer,
                                 const uint8_t address[ESP_BD_ADDR_LEN])
{
    return peer != NULL && peer->valid == 1u && address != NULL &&
           memcmp(peer->address, address, ESP_BD_ADDR_LEN) == 0;
}

static bool peer_address_used_by_other_slot(
    uint8_t selected_slot, const uint8_t address[ESP_BD_ADDR_LEN])
{
    for (uint8_t slot = 1u; slot <= CODEX_MICRO_BLE_SLOT_COUNT; ++slot) {
        const codex_micro_ble_peer_t peer = peer_for_slot(slot);
        if (slot != selected_slot && peer_address_matches(&peer, address)) {
            return true;
        }
    }
    return false;
}

static esp_err_t clear_peer_whitelist(void)
{
    atomic_store(&s_peer_filter_state, BLE_PEER_FILTER_CLEARING);
    const esp_err_t result = esp_ble_gap_clear_whitelist();
    if (result != ESP_OK) {
        atomic_store(&s_peer_filter_state, BLE_PEER_FILTER_IDLE);
        ESP_LOGE(TAG, "BLE peer whitelist clear failed: %s",
                 esp_err_to_name(result));
    }
    return result;
}

static esp_err_t refresh_peer_filter(void)
{
    atomic_store(&s_peer_filter_ready, false);
    if (atomic_load(&s_advertising_starting)) {
        atomic_store(&s_peer_filter_state, BLE_PEER_FILTER_STOPPING);
        return ESP_OK;
    }
    if (atomic_load(&s_advertising_active)) {
        atomic_store(&s_peer_filter_state, BLE_PEER_FILTER_STOPPING);
        const esp_err_t result = esp_ble_gap_stop_advertising();
        if (result == ESP_OK) {
            return ESP_OK;
        }
        atomic_store(&s_advertising_active, false);
        atomic_store(&s_advertising_starting, false);
    }
    return clear_peer_whitelist();
}

static esp_err_t publish_battery_level(uint8_t level, void *context)
{
    (void)context;
    return s_hid != NULL ? esp_hidd_dev_battery_set(s_hid, level)
                         : ESP_ERR_INVALID_STATE;
}

static void publish_latest_battery_level(void)
{
    uint8_t level = 0;
    if (battery_service_latest(&level)) {
        (void)publish_battery_level(level, NULL);
    }
}

static void clear_common_runtime_state(void)
{
    atomic_store(&s_transport_wait_neutral, true);
    release_active_controls();
    if (s_rx_mutex != NULL &&
        xSemaphoreTake(s_rx_mutex, portMAX_DELAY) == pdTRUE) {
        codex_micro_rx_reset(&s_usb_rx);
        codex_micro_rx_reset(&s_ble_rx);
        xSemaphoreGive(s_rx_mutex);
    } else if (s_rx_mutex == NULL) {
        codex_micro_rx_reset(&s_usb_rx);
        codex_micro_rx_reset(&s_ble_rx);
    }
}

static codex_micro_attention_model_t *attention_model_for_transport(
    codex_transport_t transport)
{
    if (transport == CODEX_TRANSPORT_USB ||
        transport == CODEX_TRANSPORT_BLE) {
        return &s_attention_models[transport];
    }
    return NULL;
}

static void clear_all_attention_state(void)
{
    portENTER_CRITICAL(&s_state_lock);
    codex_micro_attention_model_init(
        &s_attention_models[CODEX_TRANSPORT_USB]);
    codex_micro_attention_model_init(
        &s_attention_models[CODEX_TRANSPORT_BLE]);
    memset(s_attention_notifications, 0, sizeof(s_attention_notifications));
    portEXIT_CRITICAL(&s_state_lock);
}

static void clear_runtime_state(void)
{
    portENTER_CRITICAL(&s_state_lock);
    memset(s_codex_task_lights, 0, sizeof(s_codex_task_lights));
    portEXIT_CRITICAL(&s_state_lock);
    clear_common_runtime_state();
    clear_all_attention_state();
}

static void clear_transport_runtime_state(codex_transport_t transport)
{
    portENTER_CRITICAL(&s_state_lock);
    for (size_t i = 0; i < CODEX_MICRO_TASK_COUNT; ++i) {
        if (s_codex_task_lights[i].source == transport) {
            s_codex_task_lights[i].source = CODEX_TRANSPORT_AUTO;
        }
    }
    portEXIT_CRITICAL(&s_state_lock);
    clear_common_runtime_state();
    codex_micro_attention_model_t *attention_model =
        attention_model_for_transport(transport);
    if (attention_model != NULL) {
        portENTER_CRITICAL(&s_state_lock);
        codex_micro_attention_model_init(attention_model);
        s_attention_notifications[transport] = CODEX_ATTENTION_NONE;
        portEXIT_CRITICAL(&s_state_lock);
    }
}

static bool usb_codex_connected(void)
{
#if CONFIG_CODEX_MICRO_USB_ENABLED
    return s_usb_hid.connected != NULL &&
           s_usb_hid.connected(s_usb_hid.context);
#else
    return false;
#endif
}

static void load_soft_restart_mode(void)
{
    s_soft_restart_mode_pending = false;
    s_soft_restart_pending_mode = CODEX_MICRO_MODE_NORMAL;
    if (esp_reset_reason() == ESP_RST_SW &&
        s_soft_restart_mode_magic == CODEX_MICRO_SOFT_RESTART_MODE_MAGIC &&
        s_soft_restart_mode_value > CODEX_MICRO_MODE_NORMAL &&
        s_soft_restart_mode_value <= CODEX_MICRO_MODE_CLAUDE_CODE) {
        s_soft_restart_pending_mode =
            (codex_micro_mode_t)s_soft_restart_mode_value;
        s_soft_restart_mode_pending = true;
    }
    s_soft_restart_mode_magic = 0;
    s_soft_restart_mode_value = CODEX_MICRO_MODE_NORMAL;
}

static void restore_soft_restart_mode_if_ready(void)
{
    if (!s_soft_restart_mode_pending) {
        return;
    }
    const codex_micro_mode_t mode = s_soft_restart_pending_mode;
    if (mode == CODEX_MICRO_MODE_EDA &&
        codex_micro_active_key_layout() != CODEX_MICRO_KEY_LAYOUT_REV_A) {
        s_soft_restart_mode_pending = false;
        return;
    }
    if (mode == CODEX_MICRO_MODE_CLAUDE_CODE &&
        codex_micro_active_key_layout() != CODEX_MICRO_KEY_LAYOUT_MATRIX12) {
        s_soft_restart_mode_pending = false;
        return;
    }
    s_soft_restart_mode_pending = false;
    if (mode == codex_micro_mode()) {
        return;
    }
    release_active_controls();
    atomic_store(&s_mode, mode);
    ESP_LOGI(TAG, "restored mode=%s after soft restart",
             codex_micro_mode_name(mode));
}

static esp_err_t send_vendor_report(const uint8_t *data, size_t length,
                                    codex_transport_t transport)
{
#if CONFIG_CODEX_MICRO_USB_ENABLED
    if (transport != CODEX_TRANSPORT_BLE && usb_codex_connected() &&
        s_usb_hid.send != NULL) {
        return s_usb_hid.send(data, length, s_usb_hid.context);
    }
#endif
    if (transport == CODEX_TRANSPORT_USB) {
        return ESP_ERR_INVALID_STATE;
    }
    if (s_hid == NULL || !atomic_load(&s_connected)) {
        return ESP_ERR_INVALID_STATE;
    }
    return esp_hidd_dev_input_set(s_hid, 0, CODEX_MICRO_REPORT_ID,
                                  (uint8_t *)data, length);
}

esp_err_t codex_micro_send_standard_report(uint8_t report_id,
                                           const uint8_t *data,
                                           size_t length)
{
    if (s_hid == NULL || data == NULL || !atomic_load(&s_connected)) {
        return ESP_ERR_INVALID_STATE;
    }
    return esp_hidd_dev_input_set(s_hid, 0, report_id, (uint8_t *)data,
                                  length);
}

static bool transport_connected(codex_transport_t transport)
{
    if (transport == CODEX_TRANSPORT_USB) {
        return usb_codex_connected();
    }
    if (transport == CODEX_TRANSPORT_BLE) {
        return atomic_load(&s_connected);
    }
    return codex_micro_connected();
}

static esp_err_t send_json(const char *json, codex_transport_t transport)
{
    if (json == NULL || !transport_connected(transport)) {
        return ESP_ERR_INVALID_STATE;
    }
    const size_t json_length = strlen(json);
    char *framed = malloc(json_length + 2);
    if (framed == NULL) {
        return ESP_ERR_NO_MEM;
    }
    memcpy(framed, json, json_length);
    framed[json_length] = '\n';
    framed[json_length + 1] = '\0';

    if (xSemaphoreTake(s_tx_mutex, portMAX_DELAY) != pdTRUE) {
        free(framed);
        return ESP_ERR_TIMEOUT;
    }
    esp_err_t result = ESP_OK;
    if (!transport_connected(transport)) {
        result = ESP_ERR_INVALID_STATE;
    } else {
        size_t offset = 0;
        while (offset < json_length + 1) {
            uint8_t report[CODEX_MICRO_REPORT_BYTES];
            const size_t consumed = codex_micro_encode_fragment(
                framed, json_length + 1, offset, report);
            if (consumed == 0) {
                result = ESP_ERR_INVALID_SIZE;
                break;
            }
            atomic_fetch_add(&s_tx_attempts, 1);
            result = send_vendor_report(report, sizeof(report), transport);
            atomic_store(&s_last_tx_error, result);
            if (result == ESP_OK) {
                atomic_fetch_add(&s_tx_successes, 1);
            } else {
                atomic_fetch_add(&s_tx_failures, 1);
            }
            if (result != ESP_OK) {
                break;
            }
            offset += consumed;
            if (offset < json_length + 1) {
                vTaskDelay(1);
            }
        }
    }
    xSemaphoreGive(s_tx_mutex);
    free(framed);
    return result;
}

static esp_err_t send_object(cJSON *message, codex_transport_t transport)
{
    char *json = cJSON_PrintUnformatted(message);
    if (json == NULL) {
        return ESP_ERR_NO_MEM;
    }
    const esp_err_t result = send_json(json, transport);
    cJSON_free(json);
    return result;
}

static void attach_id(cJSON *response, const cJSON *id)
{
    const bool bounded_string = cJSON_IsString(id) &&
                                id->valuestring != NULL &&
                                strlen(id->valuestring) <= 64u;
    if (id == NULL ||
        (!cJSON_IsNumber(id) && !bounded_string && !cJSON_IsNull(id))) {
        cJSON_AddNullToObject(response, "id");
        return;
    }
    cJSON *copy = cJSON_Duplicate(id, true);
    if (copy != NULL) {
        cJSON_AddItemToObject(response, "id", copy);
    }
}

static void send_result(const cJSON *id, cJSON *result,
                        codex_transport_t transport)
{
    cJSON *response = cJSON_CreateObject();
    if (response == NULL) {
        cJSON_Delete(result);
        return;
    }
    attach_id(response, id);
    cJSON_AddItemToObject(response, "result", result);
    if (send_object(response, transport) != ESP_OK) {
        ESP_LOGW(TAG, "failed to send RPC result");
    } else {
        atomic_fetch_add(&s_rpc_responses, 1);
    }
    cJSON_Delete(response);
}

static void send_success(const cJSON *id, codex_transport_t transport)
{
    cJSON *result = cJSON_CreateObject();
    if (result == NULL) {
        return;
    }
    cJSON_AddBoolToObject(result, "ok", true);
    send_result(id, result, transport);
}

static uint32_t codex_task_status_color(uint32_t rgb)
{
    switch (rgb) {
    case 0x304ffe:
    case 0x00ff4c:
    case 0xff6d00:
    case 0xff0033:
        return rgb;
    default:
        return 0xffffff;
    }
}

static void update_task_colors(const cJSON *params,
                               codex_transport_t transport)
{
    if (!cJSON_IsArray(params)) {
        return;
    }
    const cJSON *item = NULL;
    cJSON_ArrayForEach(item, params) {
        const cJSON *id = cJSON_GetObjectItemCaseSensitive(item, "id");
        const cJSON *color = cJSON_GetObjectItemCaseSensitive(item, "c");
        const cJSON *brightness = cJSON_GetObjectItemCaseSensitive(item, "b");
        if (!cJSON_IsNumber(id) || id->valueint < 0 ||
            id->valueint >= (int)CODEX_MICRO_TASK_COUNT) {
            continue;
        }
        const bool has_color = color != NULL;
        const bool has_brightness = brightness != NULL;
        if ((has_color &&
             (!cJSON_IsNumber(color) || color->valuedouble < 0.0 ||
              color->valuedouble > 16777215.0)) ||
            (has_brightness && !cJSON_IsNumber(brightness))) {
            continue;
        }

        const size_t index = (size_t)id->valueint;
        portENTER_CRITICAL(&s_state_lock);
        uint32_t rgb = s_task_base_colors[index];
        double multiplier = s_task_brightness[index];
        portEXIT_CRITICAL(&s_state_lock);
        if (has_color) {
            rgb = (uint32_t)color->valuedouble;
        }
        if (has_brightness) {
            multiplier = brightness->valuedouble;
        }
        if (multiplier < 0.0) {
            multiplier = 0.0;
        } else if (multiplier > 1.0) {
            multiplier = 1.0;
        }
        const board_rgb_t scaled = {
            .red =
                (uint8_t)((((rgb >> 16) & 0xFFu) * multiplier) / 16.0),
            .green =
                (uint8_t)((((rgb >> 8) & 0xFFu) * multiplier) / 16.0),
            .blue = (uint8_t)(((rgb & 0xFFu) * multiplier) / 16.0),
        };
        codex_micro_attention_t attention_requested = CODEX_ATTENTION_NONE;
        portENTER_CRITICAL(&s_state_lock);
        s_task_base_colors[index] = rgb;
        s_task_brightness[index] = (float)multiplier;
        s_task_colors[index] = scaled;
        codex_task_light_t *light = &s_codex_task_lights[index];
        if (light->source != transport) {
            light->rgb = 0xffffff;
            light->brightness = 1.0f;
        }
        if (has_color) {
            light->rgb = codex_task_status_color(rgb);
        }
        if (has_brightness) {
            light->brightness = (float)multiplier;
        }
        light->source = transport;
        if (has_color) {
            codex_micro_attention_model_t *attention_model =
                attention_model_for_transport(transport);
            if (attention_model != NULL) {
                attention_requested = codex_micro_attention_model_observe(
                    attention_model, index, rgb,
                    (uint32_t)(esp_timer_get_time() / 1000));
            }
        }
        if (attention_requested) {
            s_attention_notifications[transport] |= attention_requested;
        }
        portEXIT_CRITICAL(&s_state_lock);
    }
}

static void handle_rpc(const char *json, codex_transport_t transport)
{
    cJSON *request = cJSON_Parse(json);
    if (request == NULL) {
        atomic_fetch_add(&s_rx_parse_errors, 1);
        ESP_LOGW(TAG, "discarding malformed host RPC");
        return;
    }
    const cJSON *method_item =
        cJSON_GetObjectItemCaseSensitive(request, "method");
    const cJSON *params = cJSON_GetObjectItemCaseSensitive(request, "params");
    const cJSON *id = cJSON_GetObjectItemCaseSensitive(request, "id");
    const char *method =
        cJSON_IsString(method_item) ? method_item->valuestring : "";
    ESP_LOGI(TAG, "RPC method=%s", method);

    if (strcmp(method, "sys.version") == 0) {
        /* Host handshake also starts a new session without USB unplugging. */
        portENTER_CRITICAL(&s_state_lock);
        codex_micro_attention_model_t *model = attention_model_for_transport(transport);
        if (model != NULL) {
            codex_micro_attention_model_init(model);
            s_attention_notifications[transport] = CODEX_ATTENTION_NONE;
        }
        portEXIT_CRITICAL(&s_state_lock);
        cJSON *result = cJSON_CreateObject();
        if (result != NULL) {
            cJSON_AddStringToObject(result, "version", firmware_version());
            cJSON_AddStringToObject(result, "build_id", WMP_BUILD_ID);
            send_result(id, result, transport);
        }
    } else if (strcmp(method, "device.status") == 0) {
        cJSON *result = cJSON_CreateObject();
        if (result != NULL) {
            cJSON_AddStringToObject(result, "version", firmware_version());
            cJSON_AddStringToObject(result, "build_id", WMP_BUILD_ID);
            cJSON_AddNumberToObject(result, "profile_index", 0);
            cJSON_AddNumberToObject(result, "layer_index", 0);
            fuel_gauge_sample_t battery;
            const bool battery_valid = fuel_gauge_latest(&battery);
            if (battery_valid) {
                cJSON_AddNumberToObject(
                    result, "battery",
                    (uint8_t)(battery.soc_percent + 0.5f));
                cJSON_AddNumberToObject(result, "battery_voltage",
                                        battery.voltage_v);
            } else {
                cJSON_AddNullToObject(result, "battery");
                cJSON_AddNullToObject(result, "battery_voltage");
            }
            cJSON_AddBoolToObject(result, "battery_valid", battery_valid);
            cJSON_AddBoolToObject(result, "fuel_gauge_present",
                                  battery.present);
            if (battery.last_error == ESP_OK) {
                cJSON_AddNullToObject(result, "fuel_gauge_error");
            } else {
                cJSON_AddStringToObject(
                    result, "fuel_gauge_error",
                    esp_err_to_name(battery.last_error));
            }
            if (battery.last_update_time_ms > 0) {
                cJSON_AddNumberToObject(result, "fuel_gauge_last_update_ms",
                                        battery.last_update_time_ms);
            } else {
                cJSON_AddNullToObject(result,
                                      "fuel_gauge_last_update_ms");
            }
            /* No MCU-visible VBUS or charger-status signal is available yet. */
            cJSON_AddNullToObject(result, "is_charging");
            cJSON_AddNullToObject(result, "usb_present");
            send_result(id, result, transport);
        }
    } else if (strcmp(method, "v.oai.thstatus") == 0) {
        update_task_colors(params, transport);
        send_success(id, transport);
    } else if (strcmp(method, "v.oai.rgbcfg") == 0 ||
               strcmp(method, "lights.preview") == 0 ||
               strcmp(method, "host.focused_app") == 0) {
        send_success(id, transport);
    } else {
        cJSON *response = cJSON_CreateObject();
        cJSON *error = cJSON_CreateObject();
        if (response != NULL && error != NULL) {
            attach_id(response, id);
            cJSON_AddNumberToObject(error, "code", -32601);
            cJSON_AddStringToObject(error, "message", "Method not found");
            cJSON_AddItemToObject(response, "error", error);
            if (send_object(response, transport) == ESP_OK) {
                atomic_fetch_add(&s_rpc_responses, 1);
            }
            cJSON_Delete(response);
        } else {
            cJSON_Delete(response);
            cJSON_Delete(error);
        }
    }
    cJSON_Delete(request);
}

static void handle_output(const uint8_t *data, size_t length,
                          codex_transport_t transport)
{
#if CONFIG_CODEX_MICRO_USB_ENABLED
    if (s_rx_mutex == NULL ||
        xSemaphoreTake(s_rx_mutex, portMAX_DELAY) != pdTRUE) {
        atomic_fetch_add(&s_rx_parse_errors, 1);
        return;
    }
#endif
    codex_micro_rx_t *rx =
        transport == CODEX_TRANSPORT_USB ? &s_usb_rx : &s_ble_rx;
    bool complete = false;
    if (!codex_micro_rx_append(rx, data, length, &complete)) {
        atomic_fetch_add(&s_rx_parse_errors, 1);
        ESP_LOGW(TAG, "discarding invalid or oversized host report");
        codex_micro_rx_reset(rx);
#if CONFIG_CODEX_MICRO_USB_ENABLED
        xSemaphoreGive(s_rx_mutex);
#endif
        return;
    }
    if (complete) {
        handle_rpc(rx->data, transport);
        codex_micro_rx_reset(rx);
    }
#if CONFIG_CODEX_MICRO_USB_ENABLED
    xSemaphoreGive(s_rx_mutex);
#endif
}

static void hid_event(void *handler_args, esp_event_base_t base, int32_t id,
                      void *event_data)
{
    (void)handler_args;
    (void)base;
    esp_hidd_event_data_t *event = event_data;
    switch ((esp_hidd_event_t)id) {
    case ESP_HIDD_START_EVENT:
        atomic_store(&s_hid_services_ready, true);
        ESP_LOGI(TAG, "BLE HID ready");
        ensure_advertising();
        break;
    case ESP_HIDD_CONNECT_EVENT:
        atomic_store(&s_advertising_active, false);
        atomic_store(&s_advertising_starting, false);
        atomic_store(&s_connected, false);
        clear_transport_runtime_state(CODEX_TRANSPORT_BLE);
        ESP_LOGI(TAG, "BLE host connected; awaiting encrypted bond");
        break;
    case ESP_HIDD_OUTPUT_EVENT:
        if (event != NULL &&
            event->output.report_id == CODEX_MICRO_REPORT_ID) {
            atomic_fetch_add(&s_rx_output_reports, 1);
            if (atomic_load(&s_connected)) {
                handle_output(event->output.data, event->output.length,
                              CODEX_TRANSPORT_BLE);
            }
        }
        break;
    case ESP_HIDD_DISCONNECT_EVENT:
        atomic_store(&s_connected, false);
        atomic_store(&s_connected_peer_valid, false);
        atomic_store(&s_advertising_active, false);
        atomic_store(&s_advertising_starting, false);
        clear_transport_runtime_state(CODEX_TRANSPORT_BLE);
        ESP_LOGI(TAG, "BLE host disconnected");
        ensure_advertising();
        break;
    default:
        break;
    }
}

static void combined_gatts_event_handler(
    esp_gatts_cb_event_t event, esp_gatt_if_t gatts_if,
    esp_ble_gatts_cb_param_t *parameter)
{
    if (s_gatts_observer != NULL &&
        s_gatts_observer((int)event, (uint16_t)gatts_if, parameter,
                         s_gatts_observer_context)) {
        return;
    }
    esp_hidd_gatts_event_handler(event, gatts_if, parameter);
}

static const uint8_t ADVERTISED_SERVICE_UUID128[] = {
    /* HID 0x1812, compressed to a 16-bit UUID by Bluedroid. */
    0xfb, 0x34, 0x9b, 0x5f, 0x80, 0x00, 0x00, 0x80,
    0x00, 0x10, 0x00, 0x00, 0x12, 0x18, 0x00, 0x00,
    /* Existing WMP configuration service, little-endian BLE UUID order. */
    0x31, 0x47, 0x52, 0x4b, 0x8f, 0x6e, 0x2d, 0x9c,
    0x5b, 0x4a, 0x91, 0x7a, 0x01, 0x00, 0x2f, 0x7e,
};

static esp_ble_adv_data_t s_adv_data = {
    .set_scan_rsp = false,
    .include_name = false,
    .include_txpower = false,
    .min_interval = 0,
    .max_interval = 0,
    .appearance = 0,
    .manufacturer_len = 0,
    .p_manufacturer_data = NULL,
    .service_data_len = 0,
    .p_service_data = NULL,
    .service_uuid_len = sizeof(ADVERTISED_SERVICE_UUID128),
    .p_service_uuid = (uint8_t *)ADVERTISED_SERVICE_UUID128,
    .flag = ESP_BLE_ADV_FLAG_GEN_DISC | ESP_BLE_ADV_FLAG_BREDR_NOT_SPT,
};

/*
 * ESP-IDF 6.0.2 btc_to_bta_adv_data compresses HID to 16 bits; its
 * btm_ble_build_adv_data emits flags (3), HID (4), and WMP UUID (18): 25 bytes.
 * macOS needs those discovery flags to expose pairing. Scan response carries
 * the complete UTF-8 name (at most 24 + 2) and TX power (3): at most 29 bytes.
 */
static esp_ble_adv_data_t s_scan_response_data = {
    .set_scan_rsp = true,
    .include_name = true,
    .include_txpower = true,
    .min_interval = 0,
    .max_interval = 0,
    .appearance = 0,
    .manufacturer_len = 0,
    .p_manufacturer_data = NULL,
    .service_data_len = 0,
    .p_service_data = NULL,
    .service_uuid_len = 0,
    .p_service_uuid = NULL,
    .flag = 0,
};

static esp_ble_adv_params_t s_adv_params = {
    .adv_int_min = 0x20,
    .adv_int_max = 0x30,
    .adv_type = ADV_TYPE_IND,
    .own_addr_type = BLE_ADDR_TYPE_PUBLIC,
    .channel_map = ADV_CHNL_ALL,
    .adv_filter_policy = ADV_FILTER_ALLOW_SCAN_ANY_CON_ANY,
};

static void gap_event(esp_gap_ble_cb_event_t event,
                      esp_ble_gap_cb_param_t *parameter)
{
    switch (event) {
    case ESP_GAP_BLE_ADV_DATA_SET_COMPLETE_EVT:
        atomic_store(&s_advertising_configured,
                     parameter->adv_data_cmpl.status == ESP_BT_STATUS_SUCCESS);
        ensure_advertising();
        break;
    case ESP_GAP_BLE_SCAN_RSP_DATA_SET_COMPLETE_EVT:
        atomic_store(&s_scan_response_configured,
                     parameter->scan_rsp_data_cmpl.status ==
                         ESP_BT_STATUS_SUCCESS);
        ensure_advertising();
        break;
    case ESP_GAP_BLE_ADV_START_COMPLETE_EVT:
        atomic_store(&s_advertising_starting, false);
        atomic_store(&s_advertising_active,
                     parameter->adv_start_cmpl.status ==
                         ESP_BT_STATUS_SUCCESS);
        if (atomic_load(&s_ble_suspended) &&
            atomic_load(&s_advertising_active)) {
            (void)esp_ble_gap_stop_advertising();
            break;
        }
        if (!atomic_load(&s_peer_filter_ready) &&
            atomic_load(&s_peer_filter_state) ==
                BLE_PEER_FILTER_STOPPING &&
            atomic_load(&s_advertising_active)) {
            (void)esp_ble_gap_stop_advertising();
            break;
        }
        if (!atomic_load(&s_advertising_active)) {
            ESP_LOGW(TAG, "BLE advertising start failed: %d",
                     parameter->adv_start_cmpl.status);
            ensure_advertising();
        }
        break;
    case ESP_GAP_BLE_ADV_STOP_COMPLETE_EVT:
        atomic_store(&s_advertising_active, false);
        atomic_store(&s_advertising_starting, false);
        if (atomic_load(&s_peer_filter_state) ==
            BLE_PEER_FILTER_STOPPING) {
            (void)clear_peer_whitelist();
        }
        break;
    case ESP_GAP_BLE_UPDATE_WHITELIST_COMPLETE_EVT: {
        const ble_peer_filter_state_t state =
            (ble_peer_filter_state_t)atomic_load(&s_peer_filter_state);
        if (parameter->update_whitelist_cmpl.status !=
            ESP_BT_STATUS_SUCCESS) {
            atomic_store(&s_peer_filter_state, BLE_PEER_FILTER_IDLE);
            ESP_LOGE(TAG, "BLE peer whitelist update failed: %d",
                     parameter->update_whitelist_cmpl.status);
            break;
        }
        if (state == BLE_PEER_FILTER_CLEARING) {
            const uint8_t slot = atomic_load(&s_active_peer_slot);
            codex_micro_ble_peer_t peer = peer_for_slot(slot);
            if (peer.valid == 1u) {
                atomic_store(&s_peer_filter_state,
                             BLE_PEER_FILTER_ADDING);
                const esp_err_t result = esp_ble_gap_update_whitelist(
                    true, peer.address,
                    whitelist_address_type(peer.address_type));
                if (result != ESP_OK) {
                    atomic_store(&s_peer_filter_state,
                                 BLE_PEER_FILTER_IDLE);
                    ESP_LOGE(TAG, "BLE peer whitelist add failed: %s",
                             esp_err_to_name(result));
                }
                break;
            }
            s_adv_params.adv_filter_policy =
                ADV_FILTER_ALLOW_SCAN_ANY_CON_ANY;
        } else if (state == BLE_PEER_FILTER_ADDING) {
            s_adv_params.adv_filter_policy =
                ADV_FILTER_ALLOW_SCAN_ANY_CON_WLST;
        } else {
            break;
        }
        atomic_store(&s_peer_filter_state, BLE_PEER_FILTER_IDLE);
        atomic_store(&s_peer_filter_ready, true);
        ensure_advertising();
        break;
    }
    case ESP_GAP_BLE_SEC_REQ_EVT:
        (void)esp_ble_gap_security_rsp(parameter->ble_security.ble_req.bd_addr,
                                       true);
        break;
    case ESP_GAP_BLE_AUTH_CMPL_EVT:
        if (parameter->ble_security.auth_cmpl.success) {
            const uint8_t slot = atomic_load(&s_active_peer_slot);
            codex_micro_ble_peer_t peer = peer_for_slot(slot);
            if (peer.valid == 1u &&
                !peer_address_matches(
                    &peer, parameter->ble_security.auth_cmpl.bd_addr)) {
                ESP_LOGW(TAG, "rejecting BLE peer outside active slot %u",
                         slot);
                (void)esp_ble_gap_disconnect(
                    parameter->ble_security.auth_cmpl.bd_addr);
                break;
            }
            if (peer.valid != 1u && peer_address_used_by_other_slot(
                                             slot,
                                             parameter->ble_security.auth_cmpl
                                                 .bd_addr)) {
                ESP_LOGW(TAG,
                         "rejecting BLE peer already assigned to another slot");
                (void)esp_ble_gap_disconnect(
                    parameter->ble_security.auth_cmpl.bd_addr);
                break;
            }
            if (peer.valid != 1u) {
                peer.valid = 1u;
                peer.address_type =
                    (uint8_t)parameter->ble_security.auth_cmpl.addr_type;
                memcpy(peer.address,
                       parameter->ble_security.auth_cmpl.bd_addr,
                       ESP_BD_ADDR_LEN);
                portENTER_CRITICAL(&s_state_lock);
                s_ble_peers[slot - 1u] = peer;
                portEXIT_CRITICAL(&s_state_lock);
                const esp_err_t result = persist_peer(slot, &peer);
                if (result != ESP_OK) {
                    ESP_LOGE(TAG, "failed to persist BLE peer slot %u: %s",
                             slot, esp_err_to_name(result));
                }
                (void)refresh_peer_filter();
                ESP_LOGI(TAG, "BLE peer assigned to slot %u", slot);
            }
            memcpy(s_connected_peer_address,
                   parameter->ble_security.auth_cmpl.bd_addr,
                   ESP_BD_ADDR_LEN);
            atomic_store(&s_connected_peer_valid, true);
            clear_transport_runtime_state(CODEX_TRANSPORT_BLE);
            atomic_store(&s_connected, true);
            publish_latest_battery_level();
            ESP_LOGI(TAG, "encrypted BLE bond ready on slot %u", slot);
        } else {
            atomic_store(&s_connected, false);
            ESP_LOGW(TAG, "BLE pairing failed: 0x%x",
                     parameter->ble_security.auth_cmpl.fail_reason);
        }
        break;
    default:
        break;
    }
}

static esp_err_t start_advertising(void)
{
    if (atomic_load(&s_ble_suspended)) {
        return ESP_ERR_INVALID_STATE;
    }
    if (atomic_load(&s_connected) || atomic_load(&s_advertising_active) ||
        atomic_load(&s_advertising_starting)) {
        return ESP_OK;
    }
    if (!atomic_load(&s_advertising_configured) ||
        !atomic_load(&s_scan_response_configured) ||
        !atomic_load(&s_peer_filter_ready)) {
        return ESP_ERR_INVALID_STATE;
    }
    bool expected = false;
    if (!atomic_compare_exchange_strong(&s_advertising_starting, &expected,
                                        true)) {
        return ESP_OK;
    }
    const esp_err_t result = esp_ble_gap_start_advertising(&s_adv_params);
    if (result != ESP_OK) {
        atomic_store(&s_advertising_starting, false);
    }
    return result;
}

static void advertising_retry_task(void *parameter)
{
    (void)parameter;
    for (unsigned attempt = 0; attempt < 4u; ++attempt) {
        if (atomic_load(&s_ble_suspended)) {
            atomic_store(&s_advertising_retry_running, false);
            vTaskDelete(NULL);
            return;
        }
        if (codex_micro_connected() || start_advertising() == ESP_OK) {
            atomic_store(&s_advertising_retry_running, false);
            vTaskDelete(NULL);
            return;
        }
        vTaskDelay(pdMS_TO_TICKS(100u << attempt));
    }
    ESP_LOGE(TAG, "BLE advertising unavailable after retries");
    atomic_store(&s_advertising_retry_running, false);
    vTaskDelete(NULL);
}

static void ensure_advertising(void)
{
    if (atomic_load(&s_ble_suspended)) {
        return;
    }
    if (start_advertising() == ESP_OK) {
        return;
    }
    bool expected = false;
    if (!atomic_compare_exchange_strong(&s_advertising_retry_running,
                                        &expected, true)) {
        return;
    }
    if (xTaskCreate(advertising_retry_task, "codex_ble_adv", 3072, NULL, 4,
                    NULL) != pdPASS) {
        atomic_store(&s_advertising_retry_running, false);
        ESP_LOGE(TAG, "failed to start BLE advertising retry task");
    }
}

static esp_err_t send_key(const char *key, uint8_t action, int agent,
                          codex_transport_t transport)
{
    cJSON *message = cJSON_CreateObject();
    cJSON *params = cJSON_CreateObject();
    if (message == NULL || params == NULL) {
        cJSON_Delete(message);
        cJSON_Delete(params);
        return ESP_ERR_NO_MEM;
    }
    cJSON_AddStringToObject(message, "method", "v.oai.hid");
    cJSON_AddStringToObject(params, "k", key);
    cJSON_AddNumberToObject(params, "act", action);
    if (agent != CODEX_MICRO_AGENT_NONE) {
        cJSON_AddNumberToObject(params, "ag", agent);
    }
    cJSON_AddItemToObject(message, "params", params);
    const esp_err_t result = send_object(message, transport);
    cJSON_Delete(message);
    return result;
}

static esp_err_t send_direction(float angle, bool pressed,
                                codex_transport_t transport)
{
    char message[CODEX_MICRO_RADIAL_JSON_BUFFER_BYTES];
    if (codex_micro_encode_radial_json(message, sizeof(message), angle,
                                       pressed) == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    return send_json(message, transport);
}

static codex_transport_t preferred_transport(void)
{
    return usb_codex_connected() ? CODEX_TRANSPORT_USB
                                 : CODEX_TRANSPORT_BLE;
}

static float radial_angle_delta(float first, float second)
{
    float delta = first > second ? first - second : second - first;
    return delta > 0.5f ? 1.0f - delta : delta;
}

static void poll_radial_joystick(void)
{
    if (atomic_load(&s_input_suppressed)) {
        return;
    }
    if (!codex_micro_input_ready() ||
        codex_micro_mode() != CODEX_MICRO_MODE_CODEX) {
        return;
    }

    float angle = 0.0f;
    const bool active = board_get_joystick_radial(&angle);
    codex_micro_radial_filter_t filter = {0};
    uint32_t filter_generation = 0;
    if (active) {
        portENTER_CRITICAL(&s_state_lock);
        filter = s_radial_filter;
        filter_generation = s_radial_filter_generation;
        portEXIT_CRITICAL(&s_state_lock);
        angle = codex_micro_radial_filter_update(
            &filter, angle,
            CODEX_JOYSTICK_REVERSE_JITTER_TURNS);
    }
    const int64_t now_us = esp_timer_get_time();
    codex_transport_t transport = preferred_transport();
    bool send = false;

    if (active &&
        (!codex_micro_input_ready() ||
         codex_micro_mode() != CODEX_MICRO_MODE_CODEX)) {
        return;
    }
    portENTER_CRITICAL(&s_state_lock);
    if (active && filter_generation != s_radial_filter_generation) {
        portEXIT_CRITICAL(&s_state_lock);
        return;
    }
    if (!active) {
        const bool release_due =
            s_radial_active &&
            codex_micro_radial_release_gate_update(
                &s_radial_release_gate, false, now_us,
                CODEX_JOYSTICK_RELEASE_GRACE_US);
        if (release_due) {
            send = true;
            angle = s_radial_angle;
            transport = s_radial_transport;
            if (s_radial_filter.has_output) {
                codex_micro_radial_filter_reset(&s_radial_filter);
                s_radial_filter_generation++;
            }
            codex_micro_radial_release_gate_reset(&s_radial_release_gate);
            s_radial_active = false;
            s_radial_transport = CODEX_TRANSPORT_AUTO;
        }
    } else {
        (void)codex_micro_radial_release_gate_update(
            &s_radial_release_gate, true, now_us,
            CODEX_JOYSTICK_RELEASE_GRACE_US);
        s_radial_filter = filter;
        if (!s_radial_active ||
            ((now_us - s_radial_last_sent_at_us) >=
                 CODEX_JOYSTICK_REPORT_INTERVAL_US &&
             radial_angle_delta(angle, s_radial_angle) >=
                 CODEX_JOYSTICK_ANGLE_EPSILON)) {
            send = true;
            s_radial_active = true;
            s_radial_angle = angle;
            s_radial_last_sent_at_us = now_us;
            s_radial_transport = transport;
        }
    }
    portEXIT_CRITICAL(&s_state_lock);

    if (send && transport_connected(transport)) {
        (void)handle_send_result(
            send_direction(angle, active, transport), active, "joystick");
    }
}

static void release_active_controls(void)
{
    portENTER_CRITICAL(&s_state_lock);
    codex_agent_press_model_clear(&s_agent_press);
    const bool radial_active = s_radial_active;
    const float radial_angle = s_radial_angle;
    const codex_transport_t radial_transport = s_radial_transport;
    s_radial_active = false;
    s_radial_transport = CODEX_TRANSPORT_AUTO;
    codex_micro_radial_filter_reset(&s_radial_filter);
    codex_micro_radial_release_gate_reset(&s_radial_release_gate);
    s_radial_filter_generation++;
    portEXIT_CRITICAL(&s_state_lock);
    if (radial_active && radial_transport != CODEX_TRANSPORT_AUTO &&
        transport_connected(radial_transport)) {
        (void)send_direction(radial_angle, false, radial_transport);
    }

    for (size_t control = 0; control < BOARD_CONTROL_COUNT; ++control) {
        portENTER_CRITICAL(&s_state_lock);
        const bool active = s_ble_press_active[control];
        const codex_transport_t transport = s_press_transport[control];
        s_ble_press_active[control] = false;
        s_press_transport[control] = CODEX_TRANSPORT_AUTO;
        portEXIT_CRITICAL(&s_state_lock);
        if (!active || transport == CODEX_TRANSPORT_AUTO ||
            !transport_connected(transport)) {
            continue;
        }

        size_t key_index = 0;
        if (board_control_key_index((board_control_t)control, &key_index)) {
            const codex_micro_key_mapping_t mapping =
                codex_micro_map_key_index(codex_micro_active_key_layout(),
                                          key_index);
            if (mapping.kind == CODEX_MICRO_KEY_VENDOR &&
                mapping.vendor_key != NULL) {
                (void)send_key(mapping.vendor_key, 0, mapping.agent,
                               transport);
            }
        } else if (control == BOARD_CONTROL_ENCODER_PRESS) {
            (void)send_key("ENC", 0, CODEX_MICRO_AGENT_NONE, transport);
        } else {
            float angle = 0.0f;
            bool direction = true;
            switch (control) {
            case BOARD_CONTROL_JOYSTICK_RIGHT:
                angle = 0.0f;
                break;
            case BOARD_CONTROL_JOYSTICK_DOWN:
                angle = 0.25f;
                break;
            case BOARD_CONTROL_JOYSTICK_LEFT:
                angle = 0.50f;
                break;
            case BOARD_CONTROL_JOYSTICK_UP:
                angle = 0.75f;
                break;
            default:
                direction = false;
                break;
            }
            if (direction) {
                (void)send_direction(angle, false, transport);
            }
        }
    }
}

static bool handle_send_result(esp_err_t result, bool pressed,
                               const char *control_name)
{
    (void)pressed;
    if (result == ESP_OK) {
        return true;
    }
    ESP_LOGW(TAG, "Codex %s send failed: %s", control_name,
             esp_err_to_name(result));
    return true;
}

bool codex_micro_ble_services_ready(void)
{
    return atomic_load(&s_hid_services_ready);
}

/* CDC readback retains the exact synchronous initialization result. */
static esp_err_t s_init_error = ESP_ERR_INVALID_STATE;

const char *codex_micro_ble_device_name(void)
{
    return s_ble_device_name;
}

esp_err_t codex_micro_init(const char *serial)
{
    if (serial == NULL || serial[0] == '\0') {
        return s_init_error = ESP_ERR_INVALID_ARG;
    }
    atomic_store(&s_hid_services_ready, false);
    s_hid_config.serial_number = serial;
    atomic_store(&s_connected, false);
    atomic_store(&s_usb_connected, false);
    atomic_store(&s_ble_suspended, false);
    atomic_store(&s_mode, CODEX_MICRO_MODE_NORMAL);
    atomic_store(&s_advertising_configured, false);
    atomic_store(&s_scan_response_configured, false);
    atomic_store(&s_advertising_active, false);
    atomic_store(&s_advertising_starting, false);
    atomic_store(&s_advertising_retry_running, false);
    atomic_store(&s_peer_filter_ready, false);
    atomic_store(&s_peer_filter_state, BLE_PEER_FILTER_IDLE);
    atomic_store(&s_active_peer_slot, 1u);
    atomic_store(&s_connected_peer_valid, false);
    atomic_store(&s_notify_subscribe_events, 0);
    atomic_store(&s_notify_unsubscribe_events, 0);
    atomic_store(&s_last_subscribe_attr, 0);
    atomic_store(&s_last_subscribe_notify, false);
    atomic_store(&s_negotiated_mtu, 23);
    atomic_store(&s_notify_tx_events, 0);
    atomic_store(&s_notify_tx_successes, 0);
    atomic_store(&s_notify_tx_failures, 0);
    atomic_store(&s_last_notify_tx_attr, 0);
    atomic_store(&s_last_notify_tx_status, 0);
    atomic_store(&s_rx_output_reports, 0);
    atomic_store(&s_rx_parse_errors, 0);
    atomic_store(&s_rpc_responses, 0);
    atomic_store(&s_tx_attempts, 0);
    atomic_store(&s_tx_successes, 0);
    atomic_store(&s_tx_failures, 0);
    atomic_store(&s_last_tx_error, ESP_OK);
    portENTER_CRITICAL(&s_state_lock);
    codex_agent_press_model_init(&s_agent_press);
    portEXIT_CRITICAL(&s_state_lock);
    clear_runtime_state();
    load_soft_restart_mode();
    s_tx_mutex = xSemaphoreCreateMutex();
#if CONFIG_CODEX_MICRO_USB_ENABLED
    s_rx_mutex = xSemaphoreCreateMutex();
#endif
    if (s_tx_mutex == NULL
#if CONFIG_CODEX_MICRO_USB_ENABLED
        || s_rx_mutex == NULL
#endif
    ) {
#if CONFIG_CODEX_MICRO_USB_ENABLED
        if (s_rx_mutex != NULL) {
            vSemaphoreDelete(s_rx_mutex);
            s_rx_mutex = NULL;
        }
#endif
        if (s_tx_mutex != NULL) {
            vSemaphoreDelete(s_tx_mutex);
            s_tx_mutex = NULL;
        }
        return s_init_error = ESP_ERR_NO_MEM;
    }

    /* Keep common USB locks ready even when the BLE name cannot be loaded. */
    esp_err_t result = config_store_get_ble_name(
        s_ble_device_name, sizeof(s_ble_device_name));
    if (result != ESP_OK) {
        ESP_LOGE(TAG, "BLE name storage unavailable: %s", esp_err_to_name(result));
        return s_init_error = result;
    }
    s_hid_config.device_name = s_ble_device_name;

    /*
     * Bluedroid stores SMP bonding keys in the default "nvs" partition.
     * config_store initializes a separate "config_nvs" partition, so the
     * default partition must be initialized explicitly before Bluetooth.
     */
    result = nvs_flash_init();
    if (result != ESP_OK) {
        ESP_LOGE(TAG, "BLE bond storage unavailable: %s",
                 esp_err_to_name(result));
        return s_init_error = result;
    }
    result = load_ble_peer_slots();
    if (result != ESP_OK) {
        ESP_LOGE(TAG, "BLE peer slot storage unavailable: %s",
                 esp_err_to_name(result));
        return s_init_error = result;
    }

    result = esp_bt_controller_mem_release(ESP_BT_MODE_CLASSIC_BT);
    if (result != ESP_OK && result != ESP_ERR_INVALID_STATE) {
        return s_init_error = result;
    }
    esp_bt_controller_config_t controller =
        BT_CONTROLLER_INIT_CONFIG_DEFAULT();
    result = esp_bt_controller_init(&controller);
    if (result != ESP_OK) {
        return s_init_error = result;
    }
    result = esp_bt_controller_enable(ESP_BT_MODE_BLE);
    if (result != ESP_OK) {
        return s_init_error = result;
    }
    esp_bluedroid_config_t bluedroid_config =
        BT_BLUEDROID_INIT_CONFIG_DEFAULT();
    result = esp_bluedroid_init_with_cfg(&bluedroid_config);
    if (result != ESP_OK) {
        return s_init_error = result;
    }
    result = esp_bluedroid_enable();
    if (result != ESP_OK) {
        return s_init_error = result;
    }
    result = esp_ble_gap_register_callback(gap_event);
    if (result != ESP_OK) {
        return s_init_error = result;
    }
    /*
     * esp_hidd_dev_init() registers the GATT applications, but Bluedroid
     * requires the application to install the HID GATTS dispatcher first.
     * Without it macOS can open the radio link but never discovers the
     * encrypted HID database, so pairing remains stuck with encryption off.
     */
    result = esp_ble_gatts_register_callback(combined_gatts_event_handler);
    if (result != ESP_OK) {
        return s_init_error = result;
    }
    /*
     * Match the proven Codex Micro pairing flow. Requiring Secure Connections
     * while declaring no input/output can leave macOS composite keyboards
     * stuck between discovery and bonding.
     */
    esp_ble_auth_req_t auth = ESP_LE_AUTH_BOND;
    esp_ble_io_cap_t io_capability = ESP_IO_CAP_NONE;
    uint8_t key_size = 16;
    uint8_t init_key = ESP_BLE_ENC_KEY_MASK | ESP_BLE_ID_KEY_MASK;
    uint8_t response_key = ESP_BLE_ENC_KEY_MASK | ESP_BLE_ID_KEY_MASK;
    if ((result = esp_ble_gap_set_security_param(
             ESP_BLE_SM_AUTHEN_REQ_MODE, &auth, sizeof(auth))) != ESP_OK ||
        (result = esp_ble_gap_set_security_param(
             ESP_BLE_SM_IOCAP_MODE, &io_capability,
             sizeof(io_capability))) != ESP_OK ||
        (result = esp_ble_gap_set_security_param(
             ESP_BLE_SM_MAX_KEY_SIZE, &key_size, sizeof(key_size))) != ESP_OK ||
        (result = esp_ble_gap_set_security_param(
             ESP_BLE_SM_SET_INIT_KEY, &init_key, sizeof(init_key))) != ESP_OK ||
        (result = esp_ble_gap_set_security_param(
             ESP_BLE_SM_SET_RSP_KEY, &response_key,
             sizeof(response_key))) != ESP_OK) {
        return s_init_error = result;
    }
    result = esp_ble_gap_set_device_name(s_ble_device_name);
    if (result != ESP_OK) {
        return s_init_error = result;
    }
    result = esp_ble_gap_config_adv_data(&s_adv_data);
    if (result != ESP_OK) {
        return s_init_error = result;
    }
    result = esp_ble_gap_config_adv_data(&s_scan_response_data);
    if (result != ESP_OK) {
        return s_init_error = result;
    }

    result = refresh_peer_filter();
    if (result != ESP_OK) {
        return s_init_error = result;
    }

    result = esp_hidd_dev_init(&s_hid_config, ESP_HID_TRANSPORT_BLE,
                               hid_event, &s_hid);
    if (result != ESP_OK) {
        return s_init_error = result;
    }
    battery_service_set_publisher(publish_battery_level, NULL);
    uint8_t battery_level = 0;
    if (!battery_service_latest(&battery_level)) {
        /*
         * The standard BLE Battery Level characteristic cannot represent
         * "unknown". Override ESP-IDF's built-in 100 percent default with a
         * conservative empty value until the first valid gauge sample arrives.
         * The display and device.status still report the state as unknown.
         */
        (void)esp_hidd_dev_battery_set(s_hid, 0);
    }
    return s_init_error = ESP_OK;
}

void codex_micro_register_ble_gatts_observer(
    codex_micro_ble_gatts_observer_fn observer, void *context)
{
    s_gatts_observer = observer;
    s_gatts_observer_context = context;
}

void codex_micro_register_usb_hid(const codex_micro_usb_hid_t *transport)
{
#if CONFIG_CODEX_MICRO_USB_ENABLED
    if (transport != NULL && (s_tx_mutex == NULL || s_rx_mutex == NULL)) {
        ESP_LOGW(TAG, "USB Codex transport unavailable: common locks missing");
        return;
    }
    s_usb_hid =
        transport != NULL ? *transport : (codex_micro_usb_hid_t){0};
    const bool connected = usb_codex_connected();
    atomic_store(&s_usb_connected, connected);
    if (connected) {
        clear_transport_runtime_state(CODEX_TRANSPORT_USB);
    }
#else
    (void)transport;
#endif
}

void codex_micro_receive_usb_output(uint8_t report_id, const uint8_t *data,
                                    size_t length)
{
#if CONFIG_CODEX_MICRO_USB_ENABLED
    if (report_id != CODEX_MICRO_REPORT_ID || data == NULL ||
        !usb_codex_connected()) {
        return;
    }
    atomic_fetch_add(&s_rx_output_reports, 1);
    handle_output(data, length, CODEX_TRANSPORT_USB);
#else
    (void)report_id;
    (void)data;
    (void)length;
#endif
}

void codex_micro_poll(void)
{
#if CONFIG_CODEX_MICRO_USB_ENABLED
    const bool connected = usb_codex_connected();
    const bool previous = atomic_load(&s_usb_connected);
    if (connected != previous) {
        atomic_store(&s_usb_connected, connected);
        clear_transport_runtime_state(CODEX_TRANSPORT_USB);
        ESP_LOGI(TAG, "USB Codex transport %s",
                 connected ? "connected" : "disconnected");
    }
#endif
    restore_soft_restart_mode_if_ready();
    float neutral_angle = 0.0f;
    if (atomic_load(&s_transport_wait_neutral) &&
        codex_micro_connected() && board_inputs_neutral() &&
        !board_get_joystick_radial(&neutral_angle)) {
        atomic_store(&s_transport_wait_neutral, false);
    }
    poll_radial_joystick();
}

void codex_micro_prepare_soft_restart(void)
{
    const codex_micro_mode_t mode = codex_micro_mode();
    s_soft_restart_mode_value =
        mode >= CODEX_MICRO_MODE_NORMAL &&
                mode <= CODEX_MICRO_MODE_CLAUDE_CODE
            ? (uint32_t)mode : (uint32_t)CODEX_MICRO_MODE_NORMAL;
    s_soft_restart_mode_magic = CODEX_MICRO_SOFT_RESTART_MODE_MAGIC;
}

void codex_micro_set_input_suppressed(bool suppressed)
{
    const bool was_suppressed =
        atomic_exchange(&s_input_suppressed, suppressed);
    if (suppressed && !was_suppressed) {
        release_active_controls();
    }
}

void codex_micro_set_ble_suspended(bool suspended)
{
    const bool was_suspended =
        atomic_exchange(&s_ble_suspended, suspended);
    if (was_suspended == suspended) {
        return;
    }
    if (suspended) {
        release_active_controls();
        atomic_store(&s_connected, false);
        if (atomic_load(&s_connected_peer_valid)) {
            (void)esp_ble_gap_disconnect(s_connected_peer_address);
            atomic_store(&s_connected_peer_valid, false);
        }
        if (atomic_load(&s_advertising_active) ||
            atomic_load(&s_advertising_starting)) {
            (void)esp_ble_gap_stop_advertising();
        }
        return;
    }
    ensure_advertising();
}

bool codex_micro_connected(void)
{
    return atomic_load(&s_connected) || usb_codex_connected();
}

bool codex_micro_input_ready(void)
{
    return codex_micro_connected() &&
           !atomic_load(&s_transport_wait_neutral) &&
           !atomic_load(&s_input_suppressed);
}

bool codex_micro_ble_connected(void)
{
    return atomic_load(&s_connected);
}

esp_err_t codex_micro_select_ble_slot(uint8_t slot)
{
    if (!valid_ble_slot(slot)) {
        return ESP_ERR_INVALID_ARG;
    }
    if (s_ble_peer_nvs == 0) {
        return ESP_ERR_INVALID_STATE;
    }

    const uint8_t current = atomic_load(&s_active_peer_slot);
    const codex_micro_ble_peer_t selected = peer_for_slot(slot);
    if (slot == current && atomic_load(&s_connected_peer_valid) &&
        peer_address_matches(&selected, s_connected_peer_address)) {
        return ESP_OK;
    }

    const esp_err_t persisted = persist_active_peer_slot(slot);
    if (persisted != ESP_OK) {
        return persisted;
    }

    release_active_controls();
    atomic_store(&s_active_peer_slot, slot);
    atomic_store(&s_connected, false);
    if (atomic_load(&s_connected_peer_valid)) {
        (void)esp_ble_gap_disconnect(s_connected_peer_address);
        atomic_store(&s_connected_peer_valid, false);
    }
    const esp_err_t result = refresh_peer_filter();
    if (result == ESP_OK) {
        ESP_LOGI(TAG, "selected BLE peer slot %u (%s)", slot,
                 selected.valid == 1u ? "paired" : "pairing");
    }
    return result;
}

esp_err_t codex_micro_clear_ble_slot(uint8_t slot)
{
    if (!valid_ble_slot(slot)) {
        return ESP_ERR_INVALID_ARG;
    }
    if (s_ble_peer_nvs == 0) {
        return ESP_ERR_INVALID_STATE;
    }

#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    /* Forget the old computer's OS before making this slot pairable again. */
    const esp_err_t platform_error = config_store_clear_context_platform(slot);
    if (platform_error != ESP_OK) {
        return platform_error;
    }
#endif
    codex_micro_ble_peer_t peer = peer_for_slot(slot);
    esp_err_t result = nvs_erase_key(
        s_ble_peer_nvs, BLE_PEER_NVS_KEYS[slot - 1u]);
    if (result != ESP_OK && result != ESP_ERR_NVS_NOT_FOUND) {
        return result;
    }
    result = nvs_commit(s_ble_peer_nvs);
    if (result != ESP_OK) {
        return result;
    }
    if (peer.valid == 1u) {
        result = esp_ble_remove_bond_device(peer.address);
        if (result != ESP_OK) {
            return result;
        }
    }

    release_active_controls();
    if (atomic_load(&s_connected_peer_valid) &&
        peer_address_matches(&peer, s_connected_peer_address)) {
        (void)esp_ble_gap_disconnect(s_connected_peer_address);
        atomic_store(&s_connected_peer_valid, false);
        atomic_store(&s_connected, false);
    }
    portENTER_CRITICAL(&s_state_lock);
    memset(&s_ble_peers[slot - 1u], 0, sizeof(s_ble_peers[slot - 1u]));
    portEXIT_CRITICAL(&s_state_lock);
    result = persist_active_peer_slot(slot);
    if (result != ESP_OK) {
        return result;
    }
    atomic_store(&s_active_peer_slot, slot);
    result = refresh_peer_filter();
    if (result == ESP_OK) {
        ESP_LOGI(TAG, "cleared BLE peer slot %u; pairing enabled", slot);
    }
    return result;
}

esp_err_t codex_micro_factory_reset(void)
{
    if (s_ble_peer_nvs == 0) {
        return ESP_ERR_INVALID_STATE;
    }

    release_active_controls();
    atomic_store(&s_connected, false);
    if (atomic_load(&s_connected_peer_valid)) {
        (void)esp_ble_gap_disconnect(s_connected_peer_address);
        atomic_store(&s_connected_peer_valid, false);
    }

    for (size_t index = 0; index < CODEX_MICRO_BLE_SLOT_COUNT; ++index) {
        const esp_err_t erased =
            nvs_erase_key(s_ble_peer_nvs, BLE_PEER_NVS_KEYS[index]);
        if (erased != ESP_OK && erased != ESP_ERR_NVS_NOT_FOUND) {
            return erased;
        }
    }
    esp_err_t result = nvs_set_u8(s_ble_peer_nvs, "active", 1u);
    if (result != ESP_OK) {
        return result;
    }
    result = nvs_commit(s_ble_peer_nvs);
    if (result != ESP_OK) {
        return result;
    }

    portENTER_CRITICAL(&s_state_lock);
    memset(s_ble_peers, 0, sizeof(s_ble_peers));
    memset(s_connected_peer_address, 0, sizeof(s_connected_peer_address));
    portEXIT_CRITICAL(&s_state_lock);
    atomic_store(&s_active_peer_slot, 1u);
    s_soft_restart_mode_pending = false;
    s_soft_restart_mode_magic = 0;
    s_soft_restart_mode_value = CODEX_MICRO_MODE_NORMAL;
    atomic_store(&s_mode, CODEX_MICRO_MODE_NORMAL);

    int bond_count = esp_ble_get_bond_device_num();
    if (bond_count > 0) {
        esp_ble_bond_dev_t bonds[CODEX_MICRO_BLE_SLOT_COUNT];
        if (bond_count > (int)CODEX_MICRO_BLE_SLOT_COUNT) {
            bond_count = (int)CODEX_MICRO_BLE_SLOT_COUNT;
        }
        result = esp_ble_get_bond_device_list(&bond_count, bonds);
        if (result != ESP_OK) {
            return result;
        }
        for (int index = 0; index < bond_count; ++index) {
            result = esp_ble_remove_bond_device(bonds[index].bd_addr);
            if (result != ESP_OK) {
                return result;
            }
        }
    }

    result = refresh_peer_filter();
    if (result == ESP_OK) {
        ESP_LOGI(TAG, "factory reset BLE peers; slot 1 ready for pairing");
    }
    return result;
}

uint8_t codex_micro_active_ble_slot(void)
{
    const uint8_t slot = atomic_load(&s_active_peer_slot);
    return valid_ble_slot(slot) ? slot : 1u;
}

bool codex_micro_ble_slot_paired(uint8_t slot)
{
    return valid_ble_slot(slot) && peer_for_slot(slot).valid == 1u;
}

bool codex_micro_ble_slot_connected(uint8_t slot)
{
    if (!valid_ble_slot(slot) || slot != codex_micro_active_ble_slot() ||
        !atomic_load(&s_connected) ||
        !atomic_load(&s_connected_peer_valid)) {
        return false;
    }
    const codex_micro_ble_peer_t peer = peer_for_slot(slot);
    return peer_address_matches(&peer, s_connected_peer_address);
}

codex_micro_mode_t codex_micro_mode(void)
{
    return (codex_micro_mode_t)atomic_load(&s_mode);
}

codex_micro_mode_t codex_micro_toggle_mode(void)
{
    const bool eda_supported =
        codex_micro_active_key_layout() == CODEX_MICRO_KEY_LAYOUT_REV_A;
    const bool claude_code_supported =
        codex_micro_active_key_layout() == CODEX_MICRO_KEY_LAYOUT_MATRIX12;
    const codex_micro_mode_t current = codex_micro_mode();
    const codex_micro_mode_t next = codex_micro_next_mode(
        current, true, eda_supported,
        claude_code_supported);
    if (next == current) {
        return current;
    }
    s_soft_restart_mode_pending = false;
    release_active_controls();
    atomic_store(&s_mode, next);
    ESP_LOGI(TAG, "mode=%s", codex_micro_mode_name(next));
    return next;
}

bool codex_micro_has_active_control(void)
{
    bool active = false;
    portENTER_CRITICAL(&s_state_lock);
    active = s_radial_active;
    for (size_t index = 0; index < BOARD_CONTROL_COUNT; ++index) {
        if (s_ble_press_active[index]) {
            active = true;
            break;
        }
    }
    portEXIT_CRITICAL(&s_state_lock);
    return active;
}

bool codex_micro_agent_focus_supported(void)
{
    return codex_micro_active_key_layout() == CODEX_MICRO_KEY_LAYOUT_MATRIX12;
}

codex_agent_press_snapshot_t codex_micro_get_agent_press(void)
{
    const uint64_t now_ms = (uint64_t)esp_timer_get_time() / 1000u;
    portENTER_CRITICAL(&s_state_lock);
    codex_agent_press_snapshot_t snapshot =
        codex_agent_press_model_read(&s_agent_press, now_ms);
    portEXIT_CRITICAL(&s_state_lock);
    const codex_transport_t transport =
        snapshot.transport == CODEX_AGENT_PRESS_TRANSPORT_USB
            ? CODEX_TRANSPORT_USB : CODEX_TRANSPORT_BLE;
    if (codex_micro_mode() != CODEX_MICRO_MODE_CODEX ||
        atomic_load(&s_input_suppressed) || !codex_micro_input_ready() ||
        !transport_connected(transport)) {
        snapshot.agent = -1;
        snapshot.transport = CODEX_AGENT_PRESS_TRANSPORT_NONE;
    }
    return snapshot;
}

bool codex_micro_handle_event(const board_event_t *event)
{
    if (event == NULL || event->control >= BOARD_CONTROL_COUNT) {
        return false;
    }
    if (atomic_load(&s_input_suppressed)) {
        return true;
    }
    if (codex_micro_mode() != CODEX_MICRO_MODE_CODEX) {
        return false;
    }
    if (!codex_micro_input_ready()) {
        return true;
    }

    size_t key_index = 0;
    if (board_control_key_index(event->control, &key_index)) {
        const codex_micro_key_mapping_t mapping =
            codex_micro_map_key_index(codex_micro_active_key_layout(),
                                      key_index);
        if (mapping.kind == CODEX_MICRO_KEY_STANDARD_HID) {
            return false;
        }
        if (mapping.kind != CODEX_MICRO_KEY_VENDOR ||
            mapping.vendor_key == NULL) {
            return true;
        }
        codex_transport_t transport = preferred_transport();
        portENTER_CRITICAL(&s_state_lock);
        const bool new_press =
            event->pressed && !s_ble_press_active[event->control];
        const bool send_event =
            event->pressed || s_ble_press_active[event->control];
        if (!event->pressed) {
            transport = s_press_transport[event->control];
        }
        s_ble_press_active[event->control] = event->pressed;
        s_press_transport[event->control] =
            event->pressed ? transport : CODEX_TRANSPORT_AUTO;
        portEXIT_CRITICAL(&s_state_lock);
        if (!send_event) {
            return false;
        }
        const esp_err_t result = send_key(
            mapping.vendor_key, event->pressed ? 1 : 0,
            mapping.agent, transport);
        if (result == ESP_OK && new_press &&
            mapping.agent != CODEX_MICRO_AGENT_NONE &&
            codex_micro_mode() == CODEX_MICRO_MODE_CODEX &&
            !atomic_load(&s_input_suppressed) && codex_micro_input_ready() &&
            transport_connected(transport)) {
            const uint64_t now_ms = (uint64_t)esp_timer_get_time() / 1000u;
            portENTER_CRITICAL(&s_state_lock);
            codex_agent_press_model_record(
                &s_agent_press, mapping.agent,
                transport == CODEX_TRANSPORT_USB
                    ? CODEX_AGENT_PRESS_TRANSPORT_USB
                    : CODEX_AGENT_PRESS_TRANSPORT_BLE,
                now_ms);
            portEXIT_CRITICAL(&s_state_lock);
        }
        return handle_send_result(result, event->pressed, "key");
    }
    if ((event->control == BOARD_CONTROL_ENCODER_CCW ||
         event->control == BOARD_CONTROL_ENCODER_CW) &&
        event->pressed) {
        return handle_send_result(
            send_key(event->control == BOARD_CONTROL_ENCODER_CCW ? "ENC_CC"
                                                                 : "ENC_CW",
                     2, CODEX_MICRO_AGENT_NONE, preferred_transport()),
            true, "dial");
    }
    if (event->control == BOARD_CONTROL_ENCODER_PRESS) {
        codex_transport_t transport = preferred_transport();
        portENTER_CRITICAL(&s_state_lock);
        const bool send_event =
            event->pressed || s_ble_press_active[event->control];
        if (!event->pressed) {
            transport = s_press_transport[event->control];
        }
        s_ble_press_active[event->control] = event->pressed;
        s_press_transport[event->control] =
            event->pressed ? transport : CODEX_TRANSPORT_AUTO;
        portEXIT_CRITICAL(&s_state_lock);
        if (!send_event) {
            return false;
        }
        return handle_send_result(
            send_key("ENC", event->pressed ? 1 : 0,
                     CODEX_MICRO_AGENT_NONE, transport),
            event->pressed, "encoder");
    }

    switch (event->control) {
    case BOARD_CONTROL_JOYSTICK_RIGHT:
    case BOARD_CONTROL_JOYSTICK_DOWN:
    case BOARD_CONTROL_JOYSTICK_LEFT:
    case BOARD_CONTROL_JOYSTICK_UP:
        /*
         * CODEX mode consumes the legacy four-direction transitions, while
         * codex_micro_poll() sends the full analog trajectory. NORMAL mode
         * still routes these exact events through the existing Profile.
         */
        return true;
    default:
        return false;
    }
}

bool codex_micro_status_rgb(board_rgb_t status[CODEX_MICRO_STATUS_LED_COUNT])
{
    const codex_micro_mode_t mode = codex_micro_mode();
    if (status != NULL && mode == CODEX_MICRO_MODE_CODEX) {
        const bool usb = usb_codex_connected();
        const bool ble = codex_micro_ble_connected();
        portENTER_CRITICAL(&s_state_lock);
        for (size_t i = 0; i < CODEX_MICRO_TASK_COUNT; ++i) {
            const codex_task_light_t *light = &s_codex_task_lights[i];
            const bool live = (light->source == CODEX_TRANSPORT_USB && usb) ||
                              (light->source == CODEX_TRANSPORT_BLE && ble);
            const uint32_t rgb = live ? light->rgb : 0xffffff;
            const float brightness = live ? light->brightness : 1.0f;
            status[i] = (board_rgb_t){
                .red = (uint8_t)(((rgb >> 16) & 0xffu) * brightness / 16.0f),
                .green = (uint8_t)(((rgb >> 8) & 0xffu) * brightness / 16.0f),
                .blue = (uint8_t)((rgb & 0xffu) * brightness / 16.0f),
            };
        }
        portEXIT_CRITICAL(&s_state_lock);
        status[CODEX_MICRO_CONNECTED_LED_INDEX] = (usb || ble)
            ? (board_rgb_t){.green = 10, .blue = 2} : (board_rgb_t){0};
        status[CODEX_MICRO_LAYER_LED_INDEX] =
            (board_rgb_t){.red = 10, .blue = 10};
        return true;
    }
    if (status != NULL && mode == CODEX_MICRO_MODE_CLAUDE_CODE) {
        claude_status_model_t snapshot;
        claude_status_get(&snapshot);
        memset(status, 0, sizeof(*status) * CODEX_MICRO_STATUS_LED_COUNT);
        for (size_t i = 0; i < CLAUDE_STATUS_SLOT_COUNT; ++i) {
            const uint32_t rgb = claude_status_state_rgb(snapshot.slots[i]);
            status[i] = (board_rgb_t){
                .red = ((rgb >> 16) & 0xffu) / 16u,
                .green = ((rgb >> 8) & 0xffu) / 16u,
                .blue = (rgb & 0xffu) / 16u,
            };
        }
        status[CODEX_MICRO_CONNECTED_LED_INDEX] = snapshot.active
            ? (board_rgb_t){.green = 10, .blue = 2} : (board_rgb_t){0};
        status[CODEX_MICRO_LAYER_LED_INDEX] =
            (board_rgb_t){.red = 10, .blue = 10};
        /* A stale/missing CC source explicitly clears the six Agent lights;
         * never fall back to Codex or user-configured Agent colors. */
        return true;
    }
    if (status == NULL || !codex_micro_connected() ||
        mode == CODEX_MICRO_MODE_EDA) {
        return false;
    }
    portENTER_CRITICAL(&s_state_lock);
    memcpy(status, s_task_colors, sizeof(s_task_colors));
    status[CODEX_MICRO_CONNECTED_LED_INDEX] =
        (board_rgb_t){.red = 0, .green = 10, .blue = 2};
    if (mode == CODEX_MICRO_MODE_NORMAL) {
        status[CODEX_MICRO_LAYER_LED_INDEX] =
            (board_rgb_t){.red = 0, .green = 2, .blue = 10};
    } else {
        status[CODEX_MICRO_LAYER_LED_INDEX] =
            (board_rgb_t){.red = 10, .green = 0, .blue = 10};
    }
    portEXIT_CRITICAL(&s_state_lock);
    return true;
}

codex_micro_attention_t codex_micro_take_attention_notification(void)
{
    const codex_micro_mode_t mode = codex_micro_mode();
    static codex_micro_mode_t previous_mode = CODEX_MICRO_MODE_NORMAL;
    const bool source_changed = (mode == CODEX_MICRO_MODE_CLAUDE_CODE) !=
        (previous_mode == CODEX_MICRO_MODE_CLAUDE_CODE);
    previous_mode = mode;
    portENTER_CRITICAL(&s_state_lock);
    const codex_micro_attention_t notifications =
        s_attention_notifications[CODEX_TRANSPORT_USB] |
        s_attention_notifications[CODEX_TRANSPORT_BLE];
    memset(s_attention_notifications, 0, sizeof(s_attention_notifications));
    portEXIT_CRITICAL(&s_state_lock);
    const codex_micro_attention_t claude = claude_status_take_attention(
        mode == CODEX_MICRO_MODE_CLAUDE_CODE);
    if (source_changed) {
        return CODEX_ATTENTION_NONE;
    }
    return mode == CODEX_MICRO_MODE_CLAUDE_CODE ? claude :
        mode == CODEX_MICRO_MODE_NORMAL || mode == CODEX_MICRO_MODE_CODEX
            ? notifications : CODEX_ATTENTION_NONE;
}

void codex_micro_get_diagnostics(codex_micro_diagnostics_t *diagnostics)
{
    if (diagnostics == NULL) {
        return;
    }
    *diagnostics = (codex_micro_diagnostics_t){
        .connected = codex_micro_connected(),
        .usb_connected = usb_codex_connected(),
        .ble_connected = atomic_load(&s_connected),
        .init_error = s_init_error,
        .ble_services_ready = codex_micro_ble_services_ready(),
        .mode = codex_micro_mode(),
        .notify_subscribe_events =
            atomic_load(&s_notify_subscribe_events),
        .notify_unsubscribe_events =
            atomic_load(&s_notify_unsubscribe_events),
        .last_subscribe_attr = atomic_load(&s_last_subscribe_attr),
        .last_subscribe_notify = atomic_load(&s_last_subscribe_notify),
        .negotiated_mtu = atomic_load(&s_negotiated_mtu),
        .notify_tx_events = atomic_load(&s_notify_tx_events),
        .notify_tx_successes = atomic_load(&s_notify_tx_successes),
        .notify_tx_failures = atomic_load(&s_notify_tx_failures),
        .last_notify_tx_attr = atomic_load(&s_last_notify_tx_attr),
        .last_notify_tx_status = atomic_load(&s_last_notify_tx_status),
        .rx_output_reports = atomic_load(&s_rx_output_reports),
        .rx_parse_errors = atomic_load(&s_rx_parse_errors),
        .rpc_responses = atomic_load(&s_rpc_responses),
        .tx_attempts = atomic_load(&s_tx_attempts),
        .tx_successes = atomic_load(&s_tx_successes),
        .tx_failures = atomic_load(&s_tx_failures),
        .last_tx_error = atomic_load(&s_last_tx_error),
    };
}
