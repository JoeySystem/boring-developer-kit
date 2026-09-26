#include "config_protocol.h"

/*
 * Streaming parser for the frozen WMP v1 binary header plus UTF-8 JSON payload.
 * Configuration commands share the same framed CDC transport as discovery.
 * Writes are transactional: validate, persist to the inactive slot, verify,
 * then activate only after all controls return to neutral.
 */

#include <ctype.h>
#include <inttypes.h>
#include <stdbool.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cJSON.h"
#include "board.h"
#include "board_mist_ui.h"
#include "codex_micro.h"
#include "claude_status.h"
#include "claude_status_codec.h"
#include "codex_micro_controls.h"
#include "codex_micro_protocol.h"
#include "config_store.h"
#include "ble_name.h"
#include "device_auth_codec.h"
#include "device_identity.h"
#include "esp_app_desc.h"
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "firmware_update.h"
#include "firmware_update_protocol.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "cw2015.h"
#include "joystick_calibration_model.h"
#include "prompt_store.h"
#include "psa/crypto.h"
#include "protocol_contract.h"
#include "screen_icon_protocol.h"
#include "screen_glyph_protocol.h"
#include "screen_glyph_store.h"
#include "screen_icon_store.h"
#include "sdkconfig.h"

#define RX_CAPACITY (WMP_PROTOCOL_HEADER_SIZE + WMP_PROTOCOL_MAX_PAYLOAD)
#define TX_PAYLOAD_CAPACITY WMP_PROTOCOL_MAX_PAYLOAD
#define REPLAY_CACHE_ENTRIES 4u
#define REPLAY_CACHE_BYTES (24u * 1024u)
#define DIAGNOSTIC_EVENT_QUEUE_CAPACITY 64u

#ifndef WMP_BUILD_ID
#define WMP_BUILD_ID "local-unidentified"
#endif

typedef struct {
    bool valid;
    uint32_t request_id;
    uint8_t message_type;
    uint32_t payload_length;
    uint8_t request_digest[PSA_HASH_LENGTH(PSA_ALG_SHA_256)];
    uint8_t *response;
    size_t response_length;
    uint32_t age;
} replay_entry_t;

typedef struct {
    uint32_t sequence;
    char control[24];
    bool pressed;
} diagnostic_protocol_event_t;

static uint8_t s_rx[RX_CAPACITY];
static size_t s_rx_length;
static uint8_t s_tx[WMP_PROTOCOL_HEADER_SIZE + TX_PAYLOAD_CAPACITY];
static char s_serial[18] = "CP01-000000000000";
static config_protocol_tx_fn s_tx_fn;
static void *s_tx_context;
static config_protocol_tx_fn s_usb_tx_fn;
static void *s_usb_tx_context;
static atomic_int s_active_transport =
    ATOMIC_VAR_INIT(CONFIG_PROTOCOL_TRANSPORT_USB);
static SemaphoreHandle_t s_transport_mutex;

/* Updated/read under s_transport_mutex. No task handle escapes its lifetime. */
typedef struct {
    uint32_t min_free_bytes;
    uint32_t samples;
    const char *task;
} protocol_stack_diagnostics_t;
static protocol_stack_diagnostics_t s_stack_diagnostics[2];

static void sample_protocol_stack(uint8_t message_type)
{
    switch (message_type) {
    case WMP_MSG_HELLO:
    case WMP_MSG_AUTH_GET_CERTIFICATE:
    case WMP_MSG_AUTH_CHALLENGE:
    case WMP_MSG_GET_CONFIG:
    case WMP_MSG_GET_STATUS:
        break;
    default:
        return;
    }
    protocol_stack_diagnostics_t *sample = &s_stack_diagnostics[
        atomic_load(&s_active_transport) == CONFIG_PROTOCOL_TRANSPORT_BLE];
    /* ESP-IDF's watermark is in bytes, not standard FreeRTOS words. */
    sample->min_free_bytes = uxTaskGetStackHighWaterMark(NULL);
    sample->samples++;
    sample->task = pcTaskGetName(NULL);
}

static void add_protocol_stack_diagnostics(cJSON *result)
{
    cJSON *root = cJSON_AddObjectToObject(result, "stack_diagnostics");
    for (unsigned i = 0; i < 2; ++i) {
        const char *name = i ? "ble" : "usb";
        const protocol_stack_diagnostics_t *sample = &s_stack_diagnostics[i];
        if (sample->samples == 0) {
            cJSON_AddNullToObject(root, name);
            continue;
        }
        cJSON *entry = cJSON_AddObjectToObject(root, name);
        cJSON_AddNumberToObject(entry, "min_free_bytes", sample->min_free_bytes);
        cJSON_AddNumberToObject(entry, "samples", sample->samples);
        cJSON_AddStringToObject(entry, "task", sample->task);
    }
}

static replay_entry_t s_replay_cache[REPLAY_CACHE_ENTRIES];
static size_t s_replay_cache_bytes;
static uint32_t s_replay_age;
static bool s_capture_response;
static uint32_t s_inflight_request_id;
static uint8_t s_inflight_message_type;
static uint32_t s_inflight_payload_length;
static uint8_t s_inflight_request_digest[PSA_HASH_LENGTH(PSA_ALG_SHA_256)];
/* Protected by the existing transport mutex. Storage runs only in poll(),
 * never in the USB/BLE receive callbacks. Keep the original replay identity
 * until its delayed ACK/NACK is sent. */
static struct {
    bool pending;
    uint32_t request_id;
    uint32_t payload_length;
    uint8_t digest[PSA_HASH_LENGTH(PSA_ALG_SHA_256)];
    char name[WMP_BLE_NAME_MAX_UTF8_BYTES + 1u];
} s_ble_name_write;
static atomic_bool s_session_active;
static atomic_bool s_ble_service_ready;
static const char *s_action_host_output_state = "unknown";
static const char *s_action_local_page = "unknown";
static const char *s_action_round_setting_state = "unknown";
static bool s_action_quick_config_active;
static bool s_action_local_confirm_selected;
static bool s_action_round_feedback_active;
static bool s_action_idle_circle_active;
static bool s_action_idle_standby_active;
static bool s_action_usb_standby_active;
static atomic_bool s_lighting_preview_busy = ATOMIC_VAR_INIT(true);
static portMUX_TYPE s_lighting_preview_mailbox_lock =
    portMUX_INITIALIZER_UNLOCKED;
static bool s_lighting_preview_mailbox_pending;
static config_protocol_lighting_preview_request_t
    s_lighting_preview_mailbox;
static portMUX_TYPE s_diagnostic_capture_lock =
    portMUX_INITIALIZER_UNLOCKED;
static bool s_diagnostic_capture_mailbox_pending;
static config_protocol_diagnostic_capture_request_t
    s_diagnostic_capture_mailbox;
static bool s_diagnostic_capture_active;
static bool s_diagnostic_capture_waiting_for_neutral;
static uint32_t s_diagnostic_capture_event_sequence;
static bool s_diagnostic_capture_has_last_event;
static char s_diagnostic_capture_last_control[24];
static bool s_diagnostic_capture_last_pressed;
static diagnostic_protocol_event_t
    s_diagnostic_capture_events[DIAGNOSTIC_EVENT_QUEUE_CAPACITY];
static size_t s_diagnostic_capture_event_read;
static size_t s_diagnostic_capture_event_count;
static joystick_calibration_model_t s_joystick_calibration;
static uint32_t s_next_calibration_session_id = 1;
static bool s_screen_icon_factory_reset;

static uint32_t protocol_uptime_ms(void)
{
    return (uint32_t)(esp_timer_get_time() / 1000);
}

static bool lighting_preview_supported(void);

static bool screen_icon_supported(void)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    return screen_icon_store_ready();
#else
    return false;
#endif
}

static bool codex_usage_display_supported(void)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    return true;
#else
    return false;
#endif
}

static bool screen_icon_maintenance_busy(void)
{
    firmware_update_status_t status;
    firmware_update_get_status(&status);
    return s_screen_icon_factory_reset || config_store_has_pending() ||
           s_action_quick_config_active ||
           s_joystick_calibration.state != JOYSTICK_CALIBRATION_INACTIVE ||
           status.state == FIRMWARE_UPDATE_RECEIVING ||
           status.state == FIRMWARE_UPDATE_FINALIZING || status.reboot_pending;
}

static bool ble_name_supported(void)
{
#if CONFIG_CODEX_MICRO_BLE_ENABLED
    return board_is_product_target();
#else
    return false;
#endif
}

static bool ble_name_write_busy(void)
{
    return screen_icon_maintenance_busy() || screen_icon_protocol_active();
}

static const char *calibration_state_name(joystick_calibration_state_t state)
{
    switch (state) {
    case JOYSTICK_CALIBRATION_CENTERING:
        return "CENTERING";
    case JOYSTICK_CALIBRATION_CAPTURING:
        return "CAPTURING";
    case JOYSTICK_CALIBRATION_INACTIVE:
    default:
        return "INACTIVE";
    }
}

static void calibration_end(void)
{
    board_set_joystick_calibration_active(false);
    joystick_calibration_model_cancel(&s_joystick_calibration);
}

void config_protocol_set_action_diagnostics(
    const char *host_output_state, const char *local_page,
    const char *round_setting_state, bool quick_config_active,
    bool local_confirm_selected, bool round_feedback_active,
    bool idle_circle_active, bool idle_standby_active,
    bool usb_standby_active, bool lighting_preview_busy)
{
    s_action_host_output_state = host_output_state != NULL
                                     ? host_output_state : "unknown";
    s_action_local_page = local_page != NULL ? local_page : "unknown";
    s_action_round_setting_state = round_setting_state != NULL
                                       ? round_setting_state : "unknown";
    s_action_quick_config_active = quick_config_active;
    s_action_local_confirm_selected = local_confirm_selected;
    s_action_round_feedback_active = round_feedback_active;
    s_action_idle_circle_active = idle_circle_active;
    s_action_idle_standby_active = idle_standby_active;
    s_action_usb_standby_active = usb_standby_active;
    config_protocol_set_lighting_preview_busy(lighting_preview_busy);
}

void config_protocol_set_lighting_preview_busy(bool busy)
{
    atomic_store(&s_lighting_preview_busy, busy);
}

static void publish_lighting_preview_request(
    const config_protocol_lighting_preview_request_t *request)
{
    if (request == NULL) {
        return;
    }
    portENTER_CRITICAL(&s_lighting_preview_mailbox_lock);
    s_lighting_preview_mailbox = *request;
    s_lighting_preview_mailbox_pending = true;
    portEXIT_CRITICAL(&s_lighting_preview_mailbox_lock);
}

bool config_protocol_take_lighting_preview_request(
    config_protocol_lighting_preview_request_t *request)
{
    if (request == NULL) {
        return false;
    }
    bool pending;
    portENTER_CRITICAL(&s_lighting_preview_mailbox_lock);
    pending = s_lighting_preview_mailbox_pending;
    if (pending) {
        *request = s_lighting_preview_mailbox;
        s_lighting_preview_mailbox_pending = false;
    }
    portEXIT_CRITICAL(&s_lighting_preview_mailbox_lock);
    return pending;
}

static void publish_diagnostic_capture_request(
    config_protocol_diagnostic_capture_operation_t operation)
{
    portENTER_CRITICAL(&s_diagnostic_capture_lock);
    const bool start_pending = s_diagnostic_capture_mailbox_pending &&
                               s_diagnostic_capture_mailbox.operation ==
                                   CONFIG_PROTOCOL_DIAGNOSTIC_CAPTURE_START;
    if (operation == CONFIG_PROTOCOL_DIAGNOSTIC_CAPTURE_START) {
        if (!s_diagnostic_capture_active && !start_pending) {
            s_diagnostic_capture_active = true;
            s_diagnostic_capture_waiting_for_neutral = false;
            s_diagnostic_capture_event_sequence = 0u;
            s_diagnostic_capture_has_last_event = false;
            s_diagnostic_capture_last_control[0] = '\0';
            s_diagnostic_capture_last_pressed = false;
            s_diagnostic_capture_event_read = 0u;
            s_diagnostic_capture_event_count = 0u;
        }
    } else {
        const bool owned = s_diagnostic_capture_active ||
                           s_diagnostic_capture_waiting_for_neutral ||
                           start_pending;
        s_diagnostic_capture_active = false;
        s_diagnostic_capture_waiting_for_neutral = owned;
    }
    s_diagnostic_capture_mailbox.operation = operation;
    s_diagnostic_capture_mailbox_pending = true;
    portEXIT_CRITICAL(&s_diagnostic_capture_lock);
}

bool config_protocol_take_diagnostic_capture_request(
    config_protocol_diagnostic_capture_request_t *request)
{
    if (request == NULL) {
        return false;
    }
    bool pending;
    portENTER_CRITICAL(&s_diagnostic_capture_lock);
    pending = s_diagnostic_capture_mailbox_pending;
    if (pending) {
        *request = s_diagnostic_capture_mailbox;
        s_diagnostic_capture_mailbox_pending = false;
    }
    portEXIT_CRITICAL(&s_diagnostic_capture_lock);
    return pending;
}

void config_protocol_set_diagnostic_capture_status(
    bool active, bool waiting_for_neutral, uint32_t event_sequence,
    const char *last_control, bool last_pressed)
{
    portENTER_CRITICAL(&s_diagnostic_capture_lock);
    const bool new_event = active && last_control != NULL &&
                           (!s_diagnostic_capture_has_last_event ||
                            event_sequence !=
                                s_diagnostic_capture_event_sequence);
    if (new_event) {
        if (s_diagnostic_capture_event_count ==
            DIAGNOSTIC_EVENT_QUEUE_CAPACITY) {
            s_diagnostic_capture_event_read =
                (s_diagnostic_capture_event_read + 1u) %
                DIAGNOSTIC_EVENT_QUEUE_CAPACITY;
            --s_diagnostic_capture_event_count;
        }
        const size_t write =
            (s_diagnostic_capture_event_read +
             s_diagnostic_capture_event_count) %
            DIAGNOSTIC_EVENT_QUEUE_CAPACITY;
        s_diagnostic_capture_events[write].sequence = event_sequence;
        snprintf(s_diagnostic_capture_events[write].control,
                 sizeof(s_diagnostic_capture_events[write].control), "%s",
                 last_control);
        s_diagnostic_capture_events[write].pressed = last_pressed;
        ++s_diagnostic_capture_event_count;
    }
    s_diagnostic_capture_active = active;
    s_diagnostic_capture_waiting_for_neutral = waiting_for_neutral;
    s_diagnostic_capture_event_sequence = event_sequence;
    s_diagnostic_capture_has_last_event = last_control != NULL;
    if (last_control != NULL) {
        snprintf(s_diagnostic_capture_last_control,
                 sizeof(s_diagnostic_capture_last_control), "%s",
                 last_control);
    } else {
        s_diagnostic_capture_last_control[0] = '\0';
    }
    s_diagnostic_capture_last_pressed = last_pressed;
    portEXIT_CRITICAL(&s_diagnostic_capture_lock);
}

static bool diagnostic_capture_start_allowed(void)
{
    bool active;
    bool waiting;
    bool start_pending;
    portENTER_CRITICAL(&s_diagnostic_capture_lock);
    active = s_diagnostic_capture_active;
    waiting = s_diagnostic_capture_waiting_for_neutral;
    start_pending = s_diagnostic_capture_mailbox_pending &&
                    s_diagnostic_capture_mailbox.operation ==
                        CONFIG_PROTOCOL_DIAGNOSTIC_CAPTURE_START;
    portEXIT_CRITICAL(&s_diagnostic_capture_lock);
    if (active || start_pending) {
        return true;
    }
    if (waiting ||
        s_joystick_calibration.state != JOYSTICK_CALIBRATION_INACTIVE ||
        strcmp(s_action_host_output_state, "enabled") != 0 ||
        strcmp(s_action_local_page, "none") != 0 ||
        s_action_quick_config_active || s_action_idle_standby_active ||
        s_action_usb_standby_active || !board_inputs_neutral()) {
        return false;
    }
    config_store_status_t status;
    config_store_get_status(&status);
    return !status.pending;
}

static bool diagnostic_capture_busy(void)
{
    bool busy;
    portENTER_CRITICAL(&s_diagnostic_capture_lock);
    busy = s_diagnostic_capture_active ||
           s_diagnostic_capture_waiting_for_neutral ||
           (s_diagnostic_capture_mailbox_pending &&
            s_diagnostic_capture_mailbox.operation ==
                CONFIG_PROTOCOL_DIAGNOSTIC_CAPTURE_START);
    portEXIT_CRITICAL(&s_diagnostic_capture_lock);
    return busy;
}

static void add_battery_status(cJSON *result)
{
    fuel_gauge_sample_t sample;
    const bool valid = fuel_gauge_latest(&sample);
    if (valid) {
        cJSON_AddNumberToObject(result, "battery",
                                (uint8_t)(sample.soc_percent + 0.5f));
    } else {
        cJSON_AddNullToObject(result, "battery");
    }
    cJSON_AddBoolToObject(result, "battery_valid", valid);
    /* USB host connectivity is not a measurement of battery charging. */
    cJSON_AddNullToObject(result, "is_charging");
}

static void add_codex_agent_press_status(cJSON *result)
{
    const codex_agent_press_snapshot_t snapshot = codex_micro_get_agent_press();
    const codex_agent_press_transport_t requesting_transport =
        config_protocol_active_transport() == CONFIG_PROTOCOL_TRANSPORT_USB
            ? CODEX_AGENT_PRESS_TRANSPORT_USB : CODEX_AGENT_PRESS_TRANSPORT_BLE;
    cJSON *press = cJSON_AddObjectToObject(result, "codex_agent_press");
    cJSON_AddNumberToObject(press, "sequence", snapshot.sequence);
    if (snapshot.agent >= 0 && snapshot.transport == requesting_transport) {
        cJSON_AddNumberToObject(press, "agent", snapshot.agent);
        cJSON_AddStringToObject(press, "transport",
            snapshot.transport == CODEX_AGENT_PRESS_TRANSPORT_USB ? "usb" : "ble");
    } else {
        cJSON_AddNullToObject(press, "agent");
        cJSON_AddNullToObject(press, "transport");
    }
}

static void add_diagnostic_capture_status(cJSON *result)
{
    bool active;
    uint32_t event_sequence;
    bool has_last_event;
    bool last_pressed;
    char last_control[sizeof(s_diagnostic_capture_last_control)];
    portENTER_CRITICAL(&s_diagnostic_capture_lock);
    active = s_diagnostic_capture_active;
    event_sequence = s_diagnostic_capture_event_sequence;
    has_last_event = s_diagnostic_capture_has_last_event;
    last_pressed = s_diagnostic_capture_last_pressed;
    snprintf(last_control, sizeof(last_control), "%s",
             s_diagnostic_capture_last_control);
    if (active && s_diagnostic_capture_event_count > 0u) {
        const diagnostic_protocol_event_t *event =
            &s_diagnostic_capture_events[s_diagnostic_capture_event_read];
        event_sequence = event->sequence;
        has_last_event = true;
        last_pressed = event->pressed;
        snprintf(last_control, sizeof(last_control), "%s", event->control);
        s_diagnostic_capture_event_read =
            (s_diagnostic_capture_event_read + 1u) %
            DIAGNOSTIC_EVENT_QUEUE_CAPACITY;
        --s_diagnostic_capture_event_count;
    }
    portEXIT_CRITICAL(&s_diagnostic_capture_lock);

    cJSON *capture =
        cJSON_AddObjectToObject(result, "diagnostic_capture");
    cJSON_AddBoolToObject(capture, "active", active);
    cJSON_AddNumberToObject(capture, "timeout_ms",
                            CONFIG_PROTOCOL_DIAGNOSTIC_CAPTURE_TIMEOUT_MS);
    cJSON_AddNumberToObject(capture, "event_sequence", event_sequence);
    if (has_last_event) {
        cJSON_AddStringToObject(capture, "last_control", last_control);
        cJSON_AddBoolToObject(capture, "last_pressed", last_pressed);
    } else {
        cJSON_AddNullToObject(capture, "last_control");
        cJSON_AddNullToObject(capture, "last_pressed");
    }
}

static void add_active_controls(cJSON *result)
{
    cJSON *active = cJSON_AddArrayToObject(result, "active_controls");
    board_control_t controls[BOARD_CONTROL_COUNT];
    const size_t count = board_get_active_controls(controls, BOARD_CONTROL_COUNT);
    for (size_t index = 0; index < count; ++index) {
        const char *control_id = board_control_id(controls[index]);
        if (control_id != NULL) {
            cJSON_AddItemToArray(active, cJSON_CreateString(control_id));
        }
    }
}

static const char *platform_name(config_platform_t platform)
{
    switch (platform) {
    case CONFIG_PLATFORM_MACOS:
        return "macos";
    case CONFIG_PLATFORM_WINDOWS_LINUX:
        return "windows_linux";
    case CONFIG_PLATFORM_CUSTOM:
        return "custom";
    case CONFIG_PLATFORM_UNSELECTED:
    default:
        return "unselected";
    }
}

static config_platform_t parse_platform(const cJSON *value)
{
    if (!cJSON_IsString(value)) {
        return CONFIG_PLATFORM_UNSELECTED;
    }
    const config_platform_t choices[] = {
        CONFIG_PLATFORM_MACOS,
        CONFIG_PLATFORM_WINDOWS_LINUX,
    };
    for (size_t index = 0; index < sizeof(choices) / sizeof(choices[0]); ++index) {
        if (strcmp(value->valuestring, platform_name(choices[index])) == 0) {
            return choices[index];
        }
    }
    return CONFIG_PLATFORM_UNSELECTED;
}

static uint32_t read_u32_le(const uint8_t *data)
{
    return (uint32_t)data[0] | ((uint32_t)data[1] << 8) |
           ((uint32_t)data[2] << 16) | ((uint32_t)data[3] << 24);
}

static void write_u32_le(uint8_t *data, uint32_t value)
{
    data[0] = (uint8_t)value;
    data[1] = (uint8_t)(value >> 8);
    data[2] = (uint8_t)(value >> 16);
    data[3] = (uint8_t)(value >> 24);
}

static uint32_t crc32_ieee(const uint8_t *data, size_t length)
{
    uint32_t crc = UINT32_MAX;
    for (size_t i = 0; i < length; ++i) {
        crc ^= data[i];
        for (unsigned bit = 0; bit < 8; ++bit) {
            crc = (crc >> 1) ^ (0xEDB88320u & (uint32_t)-(int32_t)(crc & 1u));
        }
    }
    return crc ^ UINT32_MAX;
}

static bool request_digest(uint8_t message_type, const uint8_t *payload, size_t length,
                           uint8_t output[PSA_HASH_LENGTH(PSA_ALG_SHA_256)])
{
    psa_hash_operation_t operation = PSA_HASH_OPERATION_INIT;
    size_t output_length = 0;
    if (psa_hash_setup(&operation, PSA_ALG_SHA_256) != PSA_SUCCESS ||
        psa_hash_update(&operation, &message_type, sizeof(message_type)) != PSA_SUCCESS ||
        psa_hash_update(&operation, payload, length) != PSA_SUCCESS ||
        psa_hash_finish(&operation, output, PSA_HASH_LENGTH(PSA_ALG_SHA_256),
                        &output_length) != PSA_SUCCESS) {
        (void)psa_hash_abort(&operation);
        return false;
    }
    return output_length == PSA_HASH_LENGTH(PSA_ALG_SHA_256);
}

static void replay_entry_clear(replay_entry_t *entry)
{
    if (entry->valid) {
        s_replay_cache_bytes -= entry->response_length;
        free(entry->response);
    }
    memset(entry, 0, sizeof(*entry));
}

static replay_entry_t *oldest_replay_entry(void)
{
    replay_entry_t *oldest = &s_replay_cache[0];
    for (size_t index = 0; index < REPLAY_CACHE_ENTRIES; ++index) {
        if (!s_replay_cache[index].valid) {
            return &s_replay_cache[index];
        }
        if (s_replay_cache[index].age < oldest->age) {
            oldest = &s_replay_cache[index];
        }
    }
    return oldest;
}

static replay_entry_t *oldest_valid_replay_entry(void)
{
    replay_entry_t *oldest = NULL;
    for (size_t index = 0; index < REPLAY_CACHE_ENTRIES; ++index) {
        if (s_replay_cache[index].valid &&
            (oldest == NULL || s_replay_cache[index].age < oldest->age)) {
            oldest = &s_replay_cache[index];
        }
    }
    return oldest;
}

static uint8_t *allocate_replay_response(size_t length)
{
#if CONFIG_SPIRAM
    return heap_caps_malloc(length, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
#else
    return malloc(length);
#endif
}

static void cache_response(const uint8_t *frame, size_t length)
{
    if (!s_capture_response || length > REPLAY_CACHE_BYTES) {
        return;
    }
    replay_entry_t *slot = oldest_replay_entry();
    if (slot->valid) {
        replay_entry_clear(slot);
    }
    while (s_replay_cache_bytes + length > REPLAY_CACHE_BYTES) {
        replay_entry_t *oldest = oldest_valid_replay_entry();
        if (oldest == NULL) {
            return;
        }
        replay_entry_clear(oldest);
    }
    uint8_t *copy = allocate_replay_response(length);
    if (copy == NULL) {
        return;
    }
    memcpy(copy, frame, length);
    slot->valid = true;
    slot->request_id = s_inflight_request_id;
    slot->message_type = s_inflight_message_type;
    slot->payload_length = s_inflight_payload_length;
    memcpy(slot->request_digest, s_inflight_request_digest,
           sizeof(slot->request_digest));
    slot->response = copy;
    slot->response_length = length;
    slot->age = ++s_replay_age;
    s_replay_cache_bytes += length;
    s_capture_response = false;
}

static replay_entry_t *find_replay_entry(uint32_t request_id)
{
    for (size_t index = 0; index < REPLAY_CACHE_ENTRIES; ++index) {
        if (s_replay_cache[index].valid &&
            s_replay_cache[index].request_id == request_id) {
            return &s_replay_cache[index];
        }
    }
    return NULL;
}

static void consume(size_t count)
{
    if (count >= s_rx_length) {
        s_rx_length = 0;
        return;
    }
    memmove(s_rx, s_rx + count, s_rx_length - count);
    s_rx_length -= count;
}

static esp_err_t send_frame(uint8_t type, uint8_t flags, uint32_t request_id, const char *json)
{
    const size_t payload_length = strlen(json);
    if (payload_length > TX_PAYLOAD_CAPACITY || s_tx_fn == NULL) {
        return ESP_ERR_INVALID_SIZE;
    }
    memcpy(s_tx, WMP_PROTOCOL_MAGIC, 4);
    s_tx[4] = WMP_PROTOCOL_MAJOR;
    s_tx[5] = WMP_PROTOCOL_MINOR;
    s_tx[6] = type;
    s_tx[7] = flags;
    write_u32_le(s_tx + 8, request_id);
    write_u32_le(s_tx + 12, payload_length);
    write_u32_le(s_tx + 16, crc32_ieee((const uint8_t *)json, payload_length));
    memcpy(s_tx + WMP_PROTOCOL_HEADER_SIZE, json, payload_length);
    const size_t frame_length = WMP_PROTOCOL_HEADER_SIZE + payload_length;
    /*
     * Capture before transport enqueue. If CDC backpressure rejects this send,
     * an identical request ID can still replay the original result without
     * executing a state-changing handler twice.
     */
    cache_response(s_tx, frame_length);
    const esp_err_t error = s_tx_fn(s_tx, frame_length, s_tx_context);
    return error;
}

static void send_nack(uint32_t request_id, const char *command, enum wmp_error_code code,
                      const char *name, const char *message)
{
    char payload[320];
    snprintf(payload, sizeof(payload),
             "{\"command\":\"%s\",\"error\":{\"code\":%u,\"name\":\"%s\",\"message\":\"%s\",\"details\":{}}}",
             command, (unsigned)code, name, message);
    (void)send_frame(WMP_MSG_NACK, WMP_FLAG_RESPONSE | WMP_FLAG_ERROR, request_id, payload);
}

static esp_err_t send_firmware_ack(uint32_t request_id, const char *json)
{
    return send_frame(WMP_MSG_ACK, WMP_FLAG_RESPONSE, request_id, json);
}

static void send_screen_icon_response(uint8_t type, uint32_t request_id,
                                      const char *json)
{
    (void)send_frame(type, WMP_FLAG_RESPONSE |
                           (type == WMP_MSG_NACK ? WMP_FLAG_ERROR : 0),
                     request_id, json);
}

static void send_firmware_nack(uint32_t request_id, const char *command,
                               uint16_t code, const char *name,
                               const char *message)
{
    send_nack(request_id, command, (enum wmp_error_code)code, name, message);
}

static const firmware_update_protocol_callbacks_t FIRMWARE_UPDATE_CALLBACKS = {
    .send_ack = send_firmware_ack,
    .send_nack = send_firmware_nack,
};

static void send_store_error(uint32_t request_id, const char *command, esp_err_t error,
                             const char *reason)
{
    if (error == ESP_ERR_INVALID_VERSION) {
        send_nack(request_id, command, WMP_ERROR_GENERATION_CONFLICT,
                  "GENERATION_CONFLICT", reason);
    } else if (error == ESP_ERR_INVALID_STATE) {
        send_nack(request_id, command, WMP_ERROR_BUSY, "BUSY", reason);
    } else if (error == ESP_ERR_INVALID_ARG || error == ESP_ERR_INVALID_CRC ||
               error == ESP_ERR_INVALID_SIZE) {
        send_nack(request_id, command, WMP_ERROR_VALIDATION_FAILED,
                  "VALIDATION_FAILED", reason);
    } else if (error == ESP_ERR_NOT_FOUND) {
        send_nack(request_id, command, WMP_ERROR_NOT_FOUND, "NOT_FOUND", reason);
    } else {
        send_nack(request_id, command, WMP_ERROR_STORAGE_FAILURE,
                  "STORAGE_FAILURE", reason);
    }
}

static void send_json_value(uint32_t request_id, cJSON *response)
{
    char *json = cJSON_PrintUnformatted(response);
    if (json == NULL || send_frame(WMP_MSG_ACK, WMP_FLAG_RESPONSE, request_id, json) != ESP_OK) {
        send_nack(request_id, "UNKNOWN", WMP_ERROR_INTERNAL, "INTERNAL",
                  "could not encode response");
    }
    cJSON_free(json);
}

static void send_ble_name(uint32_t request_id, const char *command)
{
    char saved[WMP_BLE_NAME_MAX_UTF8_BYTES + 1u];
    const esp_err_t error = config_store_get_ble_name(saved, sizeof(saved));
    if (error != ESP_OK) {
        send_store_error(request_id, command, error, "could not read Bluetooth name");
        return;
    }
    const char *active = codex_micro_ble_device_name();
    cJSON *root = cJSON_CreateObject();
    cJSON *result = NULL;
    const bool built = root != NULL &&
        cJSON_AddStringToObject(root, "command", command) &&
        (result = cJSON_AddObjectToObject(root, "result")) != NULL &&
        cJSON_AddStringToObject(result, "saved_name", saved) &&
        cJSON_AddStringToObject(result, "active_name", active) &&
        cJSON_AddStringToObject(result, "default_name", WMP_BLE_NAME_DEFAULT) &&
        cJSON_AddBoolToObject(result, "restart_required", strcmp(saved, active) != 0);
    if (built) {
        send_json_value(request_id, root);
    } else {
        send_nack(request_id, command, WMP_ERROR_INTERNAL, "INTERNAL",
                  "could not encode Bluetooth name");
    }
    cJSON_Delete(root);
}

static void handle_ble_name(uint8_t message_type, uint32_t request_id,
                            const cJSON *request)
{
    const bool set = message_type == WMP_MSG_BLE_NAME_SET;
    const char *command = set ? "BLE_NAME_SET" : "BLE_NAME_GET";
    if (!ble_name_supported()) {
        send_nack(request_id, command, WMP_ERROR_READ_ONLY, "READ_ONLY",
                  "target does not support Bluetooth names");
        return;
    }
    if (!atomic_load(&s_session_active)) {
        send_nack(request_id, command, WMP_ERROR_BUSY, "BUSY", "HELLO is required");
        return;
    }
    const cJSON *name = cJSON_GetObjectItemCaseSensitive(request, "name");
    if (!cJSON_IsObject(request) ||
        cJSON_GetArraySize(request) != (set ? 1 : 0) ||
        (set && (!cJSON_IsString(name) || name->valuestring == NULL ||
                 !ble_name_valid(name->valuestring, strlen(name->valuestring))))) {
        send_nack(request_id, command, WMP_ERROR_VALIDATION_FAILED,
                  "VALIDATION_FAILED", "invalid Bluetooth name request");
        return;
    }
    if (!set) {
        send_ble_name(request_id, command);
        return;
    }
    if (s_ble_name_write.pending || ble_name_write_busy()) {
        send_nack(request_id, command, WMP_ERROR_BUSY, "BUSY",
                  "finish the pending device operation first");
        return;
    }
    s_ble_name_write.request_id = request_id;
    s_ble_name_write.payload_length = s_inflight_payload_length;
    memcpy(s_ble_name_write.digest, s_inflight_request_digest,
           sizeof(s_ble_name_write.digest));
    memcpy(s_ble_name_write.name, name->valuestring, strlen(name->valuestring) + 1u);
    s_ble_name_write.pending = true;
}

static void poll_ble_name_write(void)
{
    if (!s_ble_name_write.pending) return;
    s_ble_name_write.pending = false;
    s_inflight_request_id = s_ble_name_write.request_id;
    s_inflight_message_type = WMP_MSG_BLE_NAME_SET;
    s_inflight_payload_length = s_ble_name_write.payload_length;
    memcpy(s_inflight_request_digest, s_ble_name_write.digest,
           sizeof(s_inflight_request_digest));
    s_capture_response = true;
    const esp_err_t error = ble_name_write_busy() ? ESP_ERR_INVALID_STATE
        : config_store_set_ble_name(s_ble_name_write.name);
    if (error == ESP_OK) {
        send_ble_name(s_ble_name_write.request_id, "BLE_NAME_SET");
    } else {
        send_store_error(s_ble_name_write.request_id, "BLE_NAME_SET", error,
                         "Bluetooth name could not be saved");
    }
    s_capture_response = false;
}

static bool encode_json_string(const char *value, char *output, size_t capacity)
{
    cJSON *string = cJSON_CreateString(value);
    char *encoded = string != NULL ? cJSON_PrintUnformatted(string) : NULL;
    const bool valid = encoded != NULL && strlen(encoded) < capacity;
    if (valid) {
        snprintf(output, capacity, "%s", encoded);
    }
    cJSON_free(encoded);
    cJSON_Delete(string);
    return valid;
}

static void add_control_name(cJSON *controls, board_control_t control)
{
    const char *control_id = board_control_id(control);
    if (control_id != NULL && board_control_supported(control)) {
        cJSON_AddItemToArray(controls, cJSON_CreateString(control_id));
    }
}

static void send_capabilities(uint32_t request_id)
{
    const bool product = board_is_product_target();
    cJSON *root = cJSON_CreateObject();
    cJSON_AddStringToObject(root, "command", "CAPABILITIES");
    cJSON *result = cJSON_AddObjectToObject(root, "result");
    cJSON *limits = cJSON_AddObjectToObject(result, "limits");
    cJSON_AddNumberToObject(limits, "profiles", product ? 8 : 1);
    cJSON_AddNumberToObject(limits, "controls_per_profile",
                            board_control_count());
    cJSON_AddNumberToObject(limits, "macro_bytes", product ? 256 : 0);
    cJSON_AddNumberToObject(limits, "all_macro_bytes", product ? 4096 : 0);
    cJSON_AddNumberToObject(limits, "config_bytes", product ? 16384 : 0);
    cJSON_AddNumberToObject(limits, "prompt_slots",
                            product ? PROMPT_STORE_SLOT_COUNT : 0);
    cJSON_AddNumberToObject(limits, "prompt_name_bytes",
                            product ? PROMPT_STORE_NAME_MAX_BYTES : 0);
    cJSON_AddNumberToObject(limits, "prompt_body_bytes",
                            product ? PROMPT_STORE_BODY_MAX_BYTES : 0);
    cJSON_AddNumberToObject(limits, "all_prompt_body_bytes",
                            product ? PROMPT_STORE_TOTAL_BODY_MAX_BYTES : 0);
    cJSON_AddNumberToObject(limits, "frame_payload_bytes",
                            WMP_PROTOCOL_MAX_PAYLOAD);
    cJSON_AddNumberToObject(limits, "ble_name_max_utf8_bytes",
                            ble_name_supported() ? WMP_BLE_NAME_MAX_UTF8_BYTES : 0);
    firmware_update_status_t update_status;
    firmware_update_get_status(&update_status);
    cJSON_AddNumberToObject(limits, "firmware_chunk_bytes",
                            FIRMWARE_UPDATE_MAX_CHUNK_BYTES);
    cJSON_AddNumberToObject(limits, "firmware_image_bytes",
                            update_status.target_capacity);

    cJSON *controls = cJSON_AddArrayToObject(result, "controls");
    for (size_t key_index = 0; key_index < board_key_count(); ++key_index) {
        board_control_t control = BOARD_CONTROL_KEY_1;
        if (board_control_from_key_index(key_index, &control)) {
            add_control_name(controls, control);
        }
    }
    for (board_control_t control = BOARD_CONTROL_ENCODER_CCW;
         control <= BOARD_CONTROL_JOYSTICK_PRESS;
         control = (board_control_t)(control + 1)) {
        add_control_name(controls, control);
    }

    cJSON *actions = cJSON_AddArrayToObject(result, "actions");
    static const char *const PRODUCT_ACTIONS[] = {
        "key", "consumer", "mouse", "macro", "prompt", "profile", "device", "none",
    };
    const size_t action_count =
        product ? sizeof(PRODUCT_ACTIONS) / sizeof(PRODUCT_ACTIONS[0]) : 2U;
    for (size_t index = 0; index < action_count; ++index) {
        const char *action = product ? PRODUCT_ACTIONS[index]
                                     : (index == 0 ? "key" : "none");
        cJSON_AddItemToArray(actions, cJSON_CreateString(action));
    }

    cJSON *features = cJSON_AddObjectToObject(result, "features");
    cJSON_AddBoolToObject(features, "ble_name", ble_name_supported());
    cJSON_AddBoolToObject(features, "display", product);
    cJSON_AddNumberToObject(features, "status_rgb_count",
                            board_status_rgb_count());
    cJSON_AddNumberToObject(features, "under_key_rgb_count",
                            board_under_key_rgb_count());
    const size_t agent_status_under_key_count =
        product && board_status_rgb_count() == 0 &&
                board_under_key_rgb_count() >= 6
            ? 6U : 0U;
    cJSON_AddNumberToObject(features, "agent_status_under_key_count",
                            agent_status_under_key_count);
    cJSON_AddBoolToObject(features, "haptic", product);
    cJSON_AddBoolToObject(features, "haptic_channels", product);
    cJSON_AddBoolToObject(features, "joystick_calibration", product);
    cJSON_AddBoolToObject(features, "quick_config", product);
    cJSON_AddBoolToObject(features, "platform_selection", product);
    cJSON_AddBoolToObject(
        features, "eda_mode",
        product && codex_micro_active_key_layout() ==
                       CODEX_MICRO_KEY_LAYOUT_REV_A);
    cJSON_AddBoolToObject(
        features, "claude_code_mode",
        product && codex_micro_active_key_layout() ==
                       CODEX_MICRO_KEY_LAYOUT_MATRIX12);
    cJSON_AddBoolToObject(features, "firmware_update", product);
    cJSON_AddBoolToObject(features, "claude_code_status", claude_status_supported());
    cJSON_AddBoolToObject(features, "codex_usage_display",
                          codex_usage_display_supported());
    cJSON_AddBoolToObject(features, "codex_agent_focus",
                          product && codex_micro_agent_focus_supported());
    cJSON_AddBoolToObject(features, "prompt_storage",
                          product && prompt_store_ready());
    cJSON_AddBoolToObject(features, "prompt_trigger_usb",
                          product && prompt_store_ready());
    cJSON_AddBoolToObject(features, "lighting_preview",
                          lighting_preview_supported());
    cJSON_AddBoolToObject(features, "diagnostic_capture", product);
    cJSON_AddBoolToObject(features, "ble_configuration",
                          product && atomic_load(&s_ble_service_ready));
    cJSON_AddBoolToObject(features, "device_authentication",
                          product && device_identity_ready());
    screen_icon_protocol_add_capability(result, screen_icon_supported());
    screen_glyph_protocol_add_capability(result, screen_icon_supported() && screen_glyph_store_ready());
    cJSON *authentication =
        cJSON_AddObjectToObject(result, "device_authentication");
    cJSON_AddNumberToObject(authentication, "version",
                            WMP_DEVICE_AUTH_VERSION);
    cJSON_AddStringToObject(authentication, "signature_algorithm",
                            WMP_DEVICE_AUTH_SIGNATURE_ALGORITHM);
#if CONFIG_CODEX_MICRO_BLE_ENABLED
    cJSON_AddNumberToObject(features, "ble_host_slots",
                            CODEX_MICRO_BLE_SLOT_COUNT);
#else
    cJSON_AddNumberToObject(features, "ble_host_slots", 0);
#endif
#ifdef CONFIG_SECURE_SIGNED_APPS
    cJSON_AddBoolToObject(features, "firmware_signature_required", true);
#else
    cJSON_AddBoolToObject(features, "firmware_signature_required", false);
#endif
    send_json_value(request_id, root);
    cJSON_Delete(root);
}

static bool read_uint32_field(const cJSON *object, const char *name, uint32_t *output)
{
    const cJSON *value = cJSON_GetObjectItemCaseSensitive(object, name);
    if (!cJSON_IsNumber(value) || value->valuedouble != value->valueint ||
        value->valuedouble < 0 || value->valuedouble > UINT32_MAX) {
        return false;
    }
    *output = (uint32_t)value->valuedouble;
    return true;
}

static bool read_bounded_uint8(const cJSON *value, uint8_t maximum,
                               uint8_t *output)
{
    if (!cJSON_IsNumber(value) ||
        value->valuedouble != value->valueint ||
        value->valueint < 0 || value->valueint > maximum) {
        return false;
    }
    *output = (uint8_t)value->valueint;
    return true;
}

static bool read_lighting_preview_rgb(const cJSON *value,
                                      board_rgb_t *output)
{
    if (!cJSON_IsObject(value) || cJSON_GetArraySize(value) != 3) {
        return false;
    }
    return read_bounded_uint8(
               cJSON_GetObjectItemCaseSensitive(value, "r"), UINT8_MAX,
               &output->red) &&
           read_bounded_uint8(
               cJSON_GetObjectItemCaseSensitive(value, "g"), UINT8_MAX,
               &output->green) &&
           read_bounded_uint8(
               cJSON_GetObjectItemCaseSensitive(value, "b"), UINT8_MAX,
               &output->blue);
}

static bool parse_lighting_preview_request(
    const cJSON *request,
    config_protocol_lighting_preview_request_t *output)
{
    if (!cJSON_IsObject(request) || cJSON_GetArraySize(request) != 3 ||
        output == NULL) {
        return false;
    }
    const cJSON *enabled =
        cJSON_GetObjectItemCaseSensitive(request, "enabled");
    const cJSON *brightness =
        cJSON_GetObjectItemCaseSensitive(request, "brightness");
    const cJSON *under_key =
        cJSON_GetObjectItemCaseSensitive(request, "under_key");
    const size_t expected_count = board_under_key_rgb_count();
    if (!cJSON_IsBool(enabled) || !cJSON_IsArray(under_key) ||
        expected_count == 0 ||
        expected_count > BOARD_MAX_UNDER_KEY_RGB_COUNT ||
        cJSON_GetArraySize(under_key) != (int)expected_count) {
        return false;
    }
    config_protocol_lighting_preview_request_t parsed = {
        .operation = CONFIG_PROTOCOL_LIGHTING_PREVIEW_SET,
        .enabled = cJSON_IsTrue(enabled),
        .under_key_count = expected_count,
    };
    if (!read_bounded_uint8(brightness, 100, &parsed.brightness)) {
        return false;
    }
    for (size_t index = 0; index < expected_count; ++index) {
        if (!read_lighting_preview_rgb(
                cJSON_GetArrayItem(under_key, (int)index),
                &parsed.under_key[index])) {
            return false;
        }
    }
    *output = parsed;
    return true;
}

static bool lighting_preview_supported(void)
{
    const size_t under_key_count = board_under_key_rgb_count();
    return board_is_product_target() && under_key_count > 0 &&
           under_key_count <= BOARD_MAX_UNDER_KEY_RGB_COUNT;
}

static bool read_prompt_id(const cJSON *request, uint8_t *prompt_id)
{
    uint32_t value = 0;
    if (!read_uint32_field(request, "prompt_id", &value) ||
        value < 1u || value > PROMPT_STORE_SLOT_COUNT) {
        return false;
    }
    *prompt_id = (uint8_t)value;
    return true;
}

static bool calibration_session_matches(const cJSON *request,
                                        uint32_t *session_id)
{
    return read_uint32_field(request, "session_id", session_id) &&
           s_joystick_calibration.state != JOYSTICK_CALIBRATION_INACTIVE &&
           *session_id == s_joystick_calibration.session_id;
}

static void add_calibration_snapshot(cJSON *result)
{
    cJSON_AddNumberToObject(result, "session_id",
                            s_joystick_calibration.session_id);
    cJSON_AddStringToObject(
        result, "state",
        calibration_state_name(s_joystick_calibration.state));
    cJSON *raw = cJSON_AddObjectToObject(result, "raw");
    cJSON_AddNumberToObject(raw, "x", s_joystick_calibration.raw_x);
    cJSON_AddNumberToObject(raw, "y", s_joystick_calibration.raw_y);
    if (s_joystick_calibration.state == JOYSTICK_CALIBRATION_CAPTURING) {
        cJSON *center = cJSON_AddObjectToObject(result, "center");
        cJSON_AddNumberToObject(center, "x", s_joystick_calibration.center_x);
        cJSON_AddNumberToObject(center, "y", s_joystick_calibration.center_y);
        cJSON *minimum = cJSON_AddObjectToObject(result, "minimum");
        cJSON_AddNumberToObject(minimum, "x", s_joystick_calibration.minimum_x);
        cJSON_AddNumberToObject(minimum, "y", s_joystick_calibration.minimum_y);
        cJSON *maximum = cJSON_AddObjectToObject(result, "maximum");
        cJSON_AddNumberToObject(maximum, "x", s_joystick_calibration.maximum_x);
        cJSON_AddNumberToObject(maximum, "y", s_joystick_calibration.maximum_y);
        joystick_calibration_candidate_t candidate;
        cJSON_AddBoolToObject(
            result, "travel_complete",
            joystick_calibration_model_candidate(&s_joystick_calibration,
                                                  &candidate));
    } else {
        cJSON_AddNullToObject(result, "center");
        cJSON_AddNullToObject(result, "minimum");
        cJSON_AddNullToObject(result, "maximum");
        cJSON_AddBoolToObject(result, "travel_complete", false);
    }
}

static bool read_ble_slot(const cJSON *object, uint8_t *slot)
{
    uint32_t value = 0;
    if (!read_uint32_field(object, "slot", &value) ||
        value < 1u || value > CODEX_MICRO_BLE_SLOT_COUNT) {
        return false;
    }
    *slot = (uint8_t)value;
    return true;
}

static void add_ble_slot_status(cJSON *ble)
{
    cJSON_AddNumberToObject(ble, "active_slot",
                            codex_micro_active_ble_slot());
    cJSON *slots = cJSON_AddArrayToObject(ble, "slots");
    for (uint8_t slot = 1u; slot <= CODEX_MICRO_BLE_SLOT_COUNT; ++slot) {
        cJSON *entry = cJSON_CreateObject();
        cJSON_AddNumberToObject(entry, "slot", slot);
        cJSON_AddBoolToObject(entry, "paired",
                              codex_micro_ble_slot_paired(slot));
        cJSON_AddBoolToObject(entry, "connected",
                              codex_micro_ble_slot_connected(slot));
        cJSON_AddItemToArray(slots, entry);
    }
}

static bool validate_config_identity(uint32_t request_id, const char *command,
                                     const cJSON *candidate)
{
    const cJSON *product = cJSON_GetObjectItemCaseSensitive(candidate, "product_id");
    const cJSON *hardware = cJSON_GetObjectItemCaseSensitive(candidate, "hardware_id");
    const cJSON *schema = cJSON_GetObjectItemCaseSensitive(candidate, "schema_version");
    if (!cJSON_IsString(product) || !cJSON_IsString(hardware) ||
        strcmp(product->valuestring, WMP_PRODUCT_ID) != 0 ||
        strcmp(hardware->valuestring, board_hardware_id()) != 0) {
        send_nack(request_id, command, WMP_ERROR_HARDWARE_MISMATCH,
                  "HARDWARE_MISMATCH", "product_id or hardware_id does not match");
        return false;
    }
    if (!cJSON_IsNumber(schema) || schema->valuedouble != 1) {
        send_nack(request_id, command, WMP_ERROR_UNSUPPORTED_SCHEMA,
                  "UNSUPPORTED_SCHEMA", "schema_version is unsupported");
        return false;
    }
    return true;
}

static cJSON *parse_json_object(const uint8_t *payload, size_t length)
{
    /* Reject trailing non-whitespace so one frame always contains one JSON object. */
    const char *parse_end = NULL;
    cJSON *value = cJSON_ParseWithLengthOpts((const char *)payload, length, &parse_end, false);
    const char *payload_end = (const char *)payload + length;
    while (parse_end != NULL && parse_end < payload_end && isspace((unsigned char)*parse_end)) {
        ++parse_end;
    }
    if (value == NULL || !cJSON_IsObject(value) || parse_end != payload_end) {
        cJSON_Delete(value);
        return NULL;
    }
    return value;
}

/* cJSON stores strings as C strings, so reject decoded NUL before it can
 * truncate a BLE name. Escaped backslashes ("\\\\u0000") remain literal. */
static bool ble_name_payload_has_nul(const uint8_t *payload, size_t length)
{
    for (size_t i = 0; i < length; ++i) {
        if (payload[i] == 0) return true;
        if (payload[i] != '\\' || i + 1u >= length) continue;
        if (i + 5u < length && memcmp(payload + i, "\\u0000", 6u) == 0) return true;
        ++i;
    }
    return false;
}

static void send_auth_certificate(uint32_t request_id)
{
    if (!device_identity_ready()) {
        send_nack(request_id, "AUTH_GET_CERTIFICATE",
                  WMP_ERROR_DEVICE_IDENTITY_UNAVAILABLE,
                  "DEVICE_IDENTITY_UNAVAILABLE",
                  "device identity is not provisioned or is unavailable");
        return;
    }
    /* Request-owned storage: USB and BLE never share a writable certificate. */
    device_identity_certificate_t *identity = malloc(sizeof(*identity));
    if (identity == NULL) {
        send_nack(request_id, "AUTH_GET_CERTIFICATE", WMP_ERROR_INTERNAL,
                  "INTERNAL", "could not allocate certificate workspace");
        return;
    }
    if (device_identity_get_certificate(identity) != ESP_OK) {
        free(identity);
        send_nack(request_id, "AUTH_GET_CERTIFICATE",
                  WMP_ERROR_DEVICE_IDENTITY_UNAVAILABLE,
                  "DEVICE_IDENTITY_UNAVAILABLE",
                  "device identity is not provisioned or is unavailable");
        return;
    }
    cJSON *root = cJSON_CreateObject();
    cJSON *result = NULL;
    cJSON *certificate = NULL;
    const bool built = root != NULL &&
        cJSON_AddStringToObject(root, "command", "AUTH_GET_CERTIFICATE") &&
        (result = cJSON_AddObjectToObject(root, "result")) != NULL &&
        (certificate = cJSON_AddObjectToObject(result, "certificate")) != NULL &&
        cJSON_AddNumberToObject(certificate, "version", identity->version) &&
        cJSON_AddStringToObject(certificate, "issuer_key_id", identity->issuer_key_id) &&
        cJSON_AddStringToObject(certificate, "product_id", identity->product_id) &&
        cJSON_AddStringToObject(certificate, "hardware_id", identity->hardware_id) &&
        cJSON_AddStringToObject(certificate, "serial", identity->serial) &&
        cJSON_AddStringToObject(certificate, "public_key_algorithm", identity->public_key_algorithm) &&
        cJSON_AddStringToObject(certificate, "public_key_spki", identity->public_key_spki) &&
        cJSON_AddStringToObject(result, "issuer_signature", identity->issuer_signature) &&
        cJSON_AddStringToObject(result, "signature_algorithm", identity->signature_algorithm);
    /* cJSON owns copies of the certificate strings at this point. */
    free(identity);
    if (!built) {
        cJSON_Delete(root);
        send_nack(request_id, "AUTH_GET_CERTIFICATE", WMP_ERROR_INTERNAL,
                  "INTERNAL", "could not build certificate response");
        return;
    }
    send_json_value(request_id, root);
    cJSON_Delete(root);
}

static void send_auth_challenge(uint32_t request_id, const char *nonce)
{
    if (!device_identity_ready()) {
        send_nack(request_id, "AUTH_CHALLENGE",
                  WMP_ERROR_DEVICE_IDENTITY_UNAVAILABLE,
                  "DEVICE_IDENTITY_UNAVAILABLE",
                  "device identity is not provisioned or is unavailable");
        return;
    }
    /* Keep large buffers off the callback task stack. Ownership is local to
     * this request even if USB/BLE scheduling changes; no global scratch area. */
    typedef struct {
        device_identity_certificate_t identity;
        char signing_object[256];
        uint8_t signature[DEVICE_IDENTITY_RSA_SIGNATURE_BYTES];
        char encoded_signature[513];
    } auth_workspace_t;
    auth_workspace_t *work = malloc(sizeof(*work));
    if (work == NULL) {
        send_nack(request_id, "AUTH_CHALLENGE",
                  WMP_ERROR_DEVICE_SIGNING_FAILED, "DEVICE_SIGNING_FAILED",
                  "device could not sign the authentication challenge");
        return;
    }
    if (device_identity_get_certificate(&work->identity) != ESP_OK) {
        free(work);
        send_nack(request_id, "AUTH_CHALLENGE",
                  WMP_ERROR_DEVICE_IDENTITY_UNAVAILABLE,
                  "DEVICE_IDENTITY_UNAVAILABLE",
                  "device identity is not provisioned or is unavailable");
        return;
    }
    size_t signing_length = 0;
    uint8_t digest[PSA_HASH_LENGTH(PSA_ALG_SHA_256)];
    size_t digest_length = 0;
    size_t signature_length = 0;
    if (!device_auth_build_signing_object(
            nonce, work->identity.serial, work->signing_object, sizeof(work->signing_object),
            &signing_length) ||
        psa_hash_compute(PSA_ALG_SHA_256,
                         (const uint8_t *)work->signing_object,
                         signing_length, digest, sizeof(digest),
                         &digest_length) != PSA_SUCCESS ||
        digest_length != sizeof(digest) ||
        device_identity_sign_digest(digest, work->signature, sizeof(work->signature),
                                    &signature_length) != ESP_OK ||
        signature_length != sizeof(work->signature) ||
        !device_auth_base64url_encode(work->signature, signature_length,
                                      work->encoded_signature,
                                      sizeof(work->encoded_signature))) {
        memset(work->signature, 0, sizeof(work->signature));
        free(work);
        send_nack(request_id, "AUTH_CHALLENGE",
                  WMP_ERROR_DEVICE_SIGNING_FAILED, "DEVICE_SIGNING_FAILED",
                  "device could not sign the authentication challenge");
        return;
    }
    memset(work->signature, 0, sizeof(work->signature));
    cJSON *root = cJSON_CreateObject();
    cJSON *result = NULL;
    const bool built = root != NULL &&
        cJSON_AddStringToObject(root, "command", "AUTH_CHALLENGE") &&
        (result = cJSON_AddObjectToObject(root, "result")) != NULL &&
        cJSON_AddStringToObject(result, "nonce", nonce) &&
        cJSON_AddStringToObject(result, "signature_algorithm",
                                WMP_DEVICE_AUTH_SIGNATURE_ALGORITHM) &&
        cJSON_AddStringToObject(result, "signature", work->encoded_signature);
    free(work);
    if (!built) {
        cJSON_Delete(root);
        send_nack(request_id, "AUTH_CHALLENGE",
                  WMP_ERROR_DEVICE_SIGNING_FAILED, "DEVICE_SIGNING_FAILED",
                  "device could not sign the authentication challenge");
        return;
    }
    send_json_value(request_id, root);
    cJSON_Delete(root);
}

static void handle_request(uint8_t message_type, uint32_t request_id,
                           const cJSON *request)
{
    char response[900];
    screen_icon_protocol_poll(protocol_uptime_ms());
    const char *conflicting =
        screen_icon_protocol_conflicting_command(message_type);
    if (conflicting != NULL) {
        send_nack(request_id, conflicting, WMP_ERROR_BUSY, "BUSY",
                  "finish or abort the screen icon upload first");
        return;
    }
    if (message_type >= WMP_MSG_SCREEN_ICON_GET &&
        message_type <= WMP_MSG_SCREEN_ICON_RESET) {
        screen_icon_protocol_handle(message_type, request_id, request,
            protocol_uptime_ms(), screen_icon_supported(),
            atomic_load(&s_session_active), screen_icon_maintenance_busy(),
            send_screen_icon_response);
        return;
    }
    if (message_type >= WMP_MSG_SCREEN_GLYPH_LIST &&
        message_type <= WMP_MSG_SCREEN_GLYPH_RESET) {
        screen_glyph_protocol_handle(message_type, request_id, request,
            screen_icon_supported() && screen_glyph_store_ready(),
            atomic_load(&s_session_active),
            screen_icon_maintenance_busy() || screen_icon_protocol_active(),
            send_screen_icon_response);
        return;
    }
    switch (message_type) {
    case WMP_MSG_BLE_NAME_GET:
    case WMP_MSG_BLE_NAME_SET:
        handle_ble_name(message_type, request_id, request);
        break;
    case WMP_MSG_HELLO: {
        const cJSON *client = cJSON_GetObjectItemCaseSensitive(request, "client");
        const cJSON *protocol = cJSON_GetObjectItemCaseSensitive(request, "protocol");
        if (!cJSON_IsObject(client) || !cJSON_IsObject(protocol)) {
            send_nack(request_id, "HELLO", WMP_ERROR_VALIDATION_FAILED, "VALIDATION_FAILED", "hello shape is invalid");
            break;
        }
        const cJSON *min_major = cJSON_GetObjectItemCaseSensitive(protocol, "min_major");
        const cJSON *max_major = cJSON_GetObjectItemCaseSensitive(protocol, "max_major");
        const cJSON *max_minor = cJSON_GetObjectItemCaseSensitive(protocol, "max_minor");
        if (!cJSON_IsNumber(min_major) || !cJSON_IsNumber(max_major) ||
            !cJSON_IsNumber(max_minor) ||
            min_major->valuedouble != min_major->valueint ||
            max_major->valuedouble != max_major->valueint ||
            max_minor->valuedouble != max_minor->valueint ||
            min_major->valueint < 0 || max_major->valueint < 0 ||
            max_minor->valueint < 0) {
            send_nack(request_id, "HELLO", WMP_ERROR_VALIDATION_FAILED, "VALIDATION_FAILED", "hello shape is invalid");
            break;
        }
        if (min_major->valueint > WMP_PROTOCOL_MAJOR || max_major->valueint < WMP_PROTOCOL_MAJOR ||
            (max_major->valueint == WMP_PROTOCOL_MAJOR &&
             (int)max_minor->valueint < (int)WMP_PROTOCOL_MINOR)) {
            send_nack(request_id, "HELLO", WMP_ERROR_UNSUPPORTED_PROTOCOL, "UNSUPPORTED_PROTOCOL", "client protocol range excludes device major");
            break;
        }
        /* A fresh HELLO starts a new logical connection. A byte-identical
         * cached HELLO retry is handled before reaching this branch. */
        s_ble_name_write.pending = false;
        screen_icon_protocol_reset_session();
        for (size_t index = 0; index < REPLAY_CACHE_ENTRIES; ++index) {
            replay_entry_clear(&s_replay_cache[index]);
        }
        config_store_status_t status;
        config_store_get_status(&status);
        const bool writable = board_is_product_target();
        const esp_app_desc_t *app_description = esp_app_get_description();
        char firmware_version_json[96];
        char build_id_json[96];
        if (!encode_json_string(
                app_description != NULL ? app_description->version : "unknown",
                firmware_version_json, sizeof(firmware_version_json)) ||
            !encode_json_string(WMP_BUILD_ID, build_id_json,
                                sizeof(build_id_json))) {
            send_nack(request_id, "HELLO", WMP_ERROR_INTERNAL, "INTERNAL",
                      "could not encode firmware build identity");
            break;
        }
        snprintf(response, sizeof(response),
                 "{\"command\":\"HELLO\",\"result\":{\"identity\":{\"product_id\":\"%s\",\"hardware_id\":\"%s\",\"serial\":\"%s\",\"usb_vid\":%u,\"usb_pid\":%u},"
                 "\"versions\":{\"firmware\":%s,\"build_id\":%s,\"protocol_major\":1,\"protocol_minor\":0,\"schema_version\":1},"
                 "\"config\":{\"generation\":%" PRIu32 ",\"digest\":\"%s\"},"
                 "\"compatibility\":{\"read\":true,\"write\":%s,\"reason\":\"%s\"}}}",
                 WMP_PRODUCT_ID, board_hardware_id(), s_serial, WMP_USB_VID,
                 WMP_USB_PID_ENGINEERING,
                 firmware_version_json, build_id_json,
                 status.active_generation, status.active_digest,
                 writable ? "true" : "false",
                 writable ? "compatible" : "devkit_read_only");
        if (send_frame(WMP_MSG_ACK, WMP_FLAG_RESPONSE, request_id, response) ==
            ESP_OK) {
            atomic_store(&s_session_active, true);
        }
        break;
    }
    case WMP_MSG_CAPABILITIES:
        send_capabilities(request_id);
        break;
    case WMP_MSG_PING: {
        const cJSON *nonce = cJSON_GetObjectItemCaseSensitive(request, "nonce");
        if (!cJSON_IsString(nonce) || nonce->valuestring == NULL || strlen(nonce->valuestring) > 128u) {
            send_nack(request_id, "PING", WMP_ERROR_VALIDATION_FAILED, "VALIDATION_FAILED", "nonce must be a string of at most 128 bytes");
            break;
        }
        char *encoded_nonce = cJSON_PrintUnformatted(nonce);
        if (encoded_nonce == NULL) {
            send_nack(request_id, "PING", WMP_ERROR_INTERNAL, "INTERNAL", "could not encode nonce");
            break;
        }
        const uint64_t uptime_ms = (uint64_t)(esp_timer_get_time() / 1000);
        const int response_length = snprintf(response, sizeof(response),
                                             "{\"command\":\"PING\",\"result\":{\"nonce\":%s,\"uptime_ms\":%" PRIu64 "}}",
                                             encoded_nonce, uptime_ms);
        cJSON_free(encoded_nonce);
        if (response_length < 0 || (size_t)response_length >= sizeof(response)) {
            send_nack(request_id, "PING", WMP_ERROR_PAYLOAD_TOO_LARGE, "PAYLOAD_TOO_LARGE", "ping payload is too large");
            break;
        }
        (void)send_frame(WMP_MSG_ACK, WMP_FLAG_RESPONSE, request_id, response);
        break;
    }
    case WMP_MSG_AUTH_GET_CERTIFICATE:
        if (cJSON_GetArraySize(request) != 0) {
            send_nack(request_id, "AUTH_GET_CERTIFICATE",
                      WMP_ERROR_VALIDATION_FAILED, "VALIDATION_FAILED",
                      "request must be an empty object");
            break;
        }
        send_auth_certificate(request_id);
        break;
    case WMP_MSG_AUTH_CHALLENGE: {
        const cJSON *nonce =
            cJSON_GetObjectItemCaseSensitive(request, "nonce");
        if (cJSON_GetArraySize(request) != 1 || !cJSON_IsString(nonce) ||
            !device_auth_nonce_valid(nonce->valuestring)) {
            send_nack(request_id, "AUTH_CHALLENGE",
                      WMP_ERROR_VALIDATION_FAILED, "VALIDATION_FAILED",
                      "nonce must be one canonical 32-byte base64url value");
            break;
        }
        send_auth_challenge(request_id, nonce->valuestring);
        break;
    }
    case WMP_MSG_GET_CONFIG: {
        char *active_json = NULL;
        uint32_t generation = 0;
        char digest[CONFIG_STORE_DIGEST_HEX_LENGTH + 1];
        if (config_store_get_json(&active_json, &generation, digest) != ESP_OK) {
            send_nack(request_id, "GET_CONFIG", WMP_ERROR_STORAGE_FAILURE,
                      "STORAGE_FAILURE", "active config is unavailable");
            break;
        }
        cJSON *config = cJSON_Parse(active_json);
        free(active_json);
        cJSON *root = cJSON_CreateObject();
        cJSON *result = cJSON_AddObjectToObject(root, "result");
        cJSON_AddStringToObject(root, "command", "GET_CONFIG");
        cJSON_AddNumberToObject(result, "generation", generation);
        cJSON_AddStringToObject(result, "digest", digest);
        if (config == NULL || !cJSON_AddItemToObject(result, "config", config)) {
            cJSON_Delete(config);
            cJSON_Delete(root);
            send_nack(request_id, "GET_CONFIG", WMP_ERROR_INTERNAL, "INTERNAL",
                      "could not encode active config");
            break;
        }
        send_json_value(request_id, root);
        cJSON_Delete(root);
        break;
    }
    case WMP_MSG_VALIDATE_CONFIG: {
        const cJSON *candidate = cJSON_GetObjectItemCaseSensitive(request, "config");
        const cJSON *digest = cJSON_GetObjectItemCaseSensitive(request, "digest");
        if (!cJSON_IsObject(candidate) || !cJSON_IsString(digest)) {
            send_nack(request_id, "VALIDATE_CONFIG", WMP_ERROR_VALIDATION_FAILED,
                      "VALIDATION_FAILED", "config and digest are required");
            break;
        }
        if (!validate_config_identity(request_id, "VALIDATE_CONFIG", candidate)) {
            break;
        }
        char reason[128] = "validation failed";
        const esp_err_t error = config_store_validate(candidate, digest->valuestring,
                                                       reason, sizeof(reason));
        if (error != ESP_OK) {
            send_store_error(request_id, "VALIDATE_CONFIG", error, reason);
            break;
        }
        snprintf(response, sizeof(response),
                 "{\"command\":\"VALIDATE_CONFIG\",\"result\":{\"valid\":true,\"digest\":\"%s\",\"field_errors\":[]}}",
                 digest->valuestring);
        (void)send_frame(WMP_MSG_ACK, WMP_FLAG_RESPONSE, request_id, response);
        break;
    }
    case WMP_MSG_SET_CONFIG: {
        if (!board_is_product_target()) {
            send_nack(request_id, "SET_CONFIG", WMP_ERROR_READ_ONLY, "READ_ONLY",
                      "devkit target cannot activate product configuration");
            break;
        }
        const cJSON *candidate = cJSON_GetObjectItemCaseSensitive(request, "config");
        const cJSON *digest = cJSON_GetObjectItemCaseSensitive(request, "digest");
        uint32_t base_generation = 0;
        if (!cJSON_IsObject(candidate) || !cJSON_IsString(digest) ||
            !read_uint32_field(request, "base_generation", &base_generation)) {
            send_nack(request_id, "SET_CONFIG", WMP_ERROR_VALIDATION_FAILED,
                      "VALIDATION_FAILED", "config, digest, and base_generation are required");
            break;
        }
        if (!validate_config_identity(request_id, "SET_CONFIG", candidate)) {
            break;
        }
        char reason[128] = "write failed";
        const esp_err_t error = config_store_stage(candidate, digest->valuestring,
                                                    base_generation, reason, sizeof(reason));
        if (error != ESP_OK) {
            send_store_error(request_id, "SET_CONFIG", error, reason);
            break;
        }
        config_store_status_t status;
        config_store_get_status(&status);
        cJSON *root = cJSON_CreateObject();
        cJSON_AddStringToObject(root, "command", "SET_CONFIG");
        cJSON *result = cJSON_AddObjectToObject(root, "result");
        cJSON_AddStringToObject(result, "state", "PENDING_ACTIVATION");
        cJSON_AddNumberToObject(result, "generation", status.pending_generation);
        cJSON_AddStringToObject(result, "digest", status.pending_digest);
        add_active_controls(result);
        send_json_value(request_id, root);
        cJSON_Delete(root);
        break;
    }
    case WMP_MSG_GET_STATUS: {
        config_store_status_t status;
        config_store_get_status(&status);
        cJSON *root = cJSON_CreateObject();
        cJSON_AddStringToObject(root, "command", "GET_STATUS");
        cJSON *result = cJSON_AddObjectToObject(root, "result");
        cJSON_AddStringToObject(result, "build_id", WMP_BUILD_ID);
        add_battery_status(result);
        cJSON_AddStringToObject(result, "state",
                                status.pending ? "PENDING_ACTIVATION" : "ACTIVE");
        cJSON_AddBoolToObject(result, "activation_failed", status.activation_failed);
        cJSON *active = cJSON_AddObjectToObject(result, "active");
        cJSON_AddNumberToObject(active, "generation", status.active_generation);
        cJSON_AddStringToObject(active, "digest", status.active_digest);
        if (status.pending) {
            cJSON *pending = cJSON_AddObjectToObject(result, "pending");
            cJSON_AddNumberToObject(pending, "generation", status.pending_generation);
            cJSON_AddStringToObject(pending, "digest", status.pending_digest);
        } else {
            cJSON_AddNullToObject(result, "pending");
        }
        cJSON_AddBoolToObject(result, "inputs_neutral", board_inputs_neutral());
        cJSON_AddStringToObject(result, "platform",
                                platform_name(config_store_get_platform()));
        cJSON_AddStringToObject(
            result, "operating_mode",
            codex_micro_mode_name(codex_micro_mode()));
        add_codex_agent_press_status(result);
        cJSON *action = cJSON_AddObjectToObject(result, "action_engine");
        cJSON_AddStringToObject(action, "host_output_state",
                                s_action_host_output_state);
        cJSON_AddStringToObject(action, "local_page", s_action_local_page);
        cJSON_AddStringToObject(action, "round_setting_state",
                                s_action_round_setting_state);
        cJSON_AddBoolToObject(action, "quick_config_active",
                              s_action_quick_config_active);
        cJSON_AddBoolToObject(action, "local_confirm_selected",
                              s_action_local_confirm_selected);
        cJSON_AddBoolToObject(action, "round_feedback_active",
                              s_action_round_feedback_active);
        cJSON_AddBoolToObject(action, "idle_circle_active",
                              s_action_idle_circle_active);
        cJSON_AddBoolToObject(action, "idle_standby_active",
                              s_action_idle_standby_active);
        cJSON_AddBoolToObject(action, "usb_standby_active",
                              s_action_usb_standby_active);
        add_diagnostic_capture_status(result);
        add_protocol_stack_diagnostics(result);
        claude_status_model_t cc_snapshot;
        claude_status_get(&cc_snapshot);
        cJSON *cc = cJSON_AddObjectToObject(result, "claude_code_status");
        cJSON_AddBoolToObject(cc, "active", cc_snapshot.active);
        cJSON_AddBoolToObject(cc, "selected",
            codex_micro_mode() == CODEX_MICRO_MODE_CLAUDE_CODE);
        cJSON_AddNumberToObject(cc, "lease_ms", CLAUDE_STATUS_LEASE_MS);
        cJSON *cc_states = cJSON_AddArrayToObject(cc, "states");
        for (unsigned i = 0; i < CLAUDE_STATUS_SLOT_COUNT; ++i) {
            cJSON_AddItemToArray(cc_states,
                cJSON_CreateString(claude_status_state_name(cc_snapshot.slots[i])));
        }
        firmware_update_protocol_add_status(result);
        add_active_controls(result);
        board_joystick_diagnostics_t joystick_diagnostics;
        if (board_get_joystick_diagnostics(&joystick_diagnostics)) {
            cJSON *joystick = cJSON_AddObjectToObject(result, "joystick_diagnostics");
            cJSON_AddNumberToObject(joystick, "raw_x", joystick_diagnostics.raw_x);
            cJSON_AddNumberToObject(joystick, "raw_y", joystick_diagnostics.raw_y);
            cJSON_AddNumberToObject(joystick, "filtered_x", joystick_diagnostics.filtered_x);
            cJSON_AddNumberToObject(joystick, "filtered_y", joystick_diagnostics.filtered_y);
            cJSON_AddNumberToObject(joystick, "center_x", joystick_diagnostics.center_x);
            cJSON_AddNumberToObject(joystick, "center_y", joystick_diagnostics.center_y);
            cJSON_AddNumberToObject(joystick, "minimum_x", joystick_diagnostics.minimum_x);
            cJSON_AddNumberToObject(joystick, "maximum_x", joystick_diagnostics.maximum_x);
            cJSON_AddNumberToObject(joystick, "minimum_y", joystick_diagnostics.minimum_y);
            cJSON_AddNumberToObject(joystick, "maximum_y", joystick_diagnostics.maximum_y);
            cJSON_AddNumberToObject(joystick, "deadzone_x", joystick_diagnostics.deadzone_x);
            cJSON_AddNumberToObject(joystick, "deadzone_y", joystick_diagnostics.deadzone_y);
            cJSON_AddNumberToObject(joystick, "directions", joystick_diagnostics.directions);
            cJSON_AddBoolToObject(joystick, "radial_active", joystick_diagnostics.radial_active);
            cJSON_AddBoolToObject(joystick, "radial_valid", joystick_diagnostics.radial_valid);
            cJSON_AddNumberToObject(joystick, "radial_angle_turns",
                                    joystick_diagnostics.radial_angle_turns);
        }
#if CONFIG_CODEX_MICRO_BLE_ENABLED
        codex_micro_diagnostics_t diagnostics;
        codex_micro_get_diagnostics(&diagnostics);
        cJSON *ble = cJSON_AddObjectToObject(result, "codex_micro");
        cJSON_AddBoolToObject(ble, "connected", diagnostics.connected);
        cJSON_AddBoolToObject(ble, "usb_connected",
                              diagnostics.usb_connected);
        cJSON_AddBoolToObject(ble, "ble_connected",
                              diagnostics.ble_connected);
        cJSON_AddNumberToObject(ble, "init_error", diagnostics.init_error);
        cJSON_AddBoolToObject(ble, "ble_services_ready",
                              diagnostics.ble_services_ready);
        cJSON_AddStringToObject(
            ble, "mode", codex_micro_mode_name(diagnostics.mode));
        add_ble_slot_status(ble);
        cJSON_AddNumberToObject(
            ble, "notify_subscribe_events",
            diagnostics.notify_subscribe_events);
        cJSON_AddNumberToObject(
            ble, "notify_unsubscribe_events",
            diagnostics.notify_unsubscribe_events);
        cJSON_AddNumberToObject(
            ble, "last_subscribe_attr", diagnostics.last_subscribe_attr);
        cJSON_AddBoolToObject(
            ble, "last_subscribe_notify",
            diagnostics.last_subscribe_notify);
        cJSON_AddNumberToObject(
            ble, "negotiated_mtu", diagnostics.negotiated_mtu);
        cJSON_AddNumberToObject(
            ble, "notify_tx_events", diagnostics.notify_tx_events);
        cJSON_AddNumberToObject(
            ble, "notify_tx_successes", diagnostics.notify_tx_successes);
        cJSON_AddNumberToObject(
            ble, "notify_tx_failures", diagnostics.notify_tx_failures);
        cJSON_AddNumberToObject(
            ble, "last_notify_tx_attr", diagnostics.last_notify_tx_attr);
        cJSON_AddNumberToObject(
            ble, "last_notify_tx_status",
            diagnostics.last_notify_tx_status);
        cJSON_AddNumberToObject(
            ble, "rx_output_reports", diagnostics.rx_output_reports);
        cJSON_AddNumberToObject(
            ble, "rx_parse_errors", diagnostics.rx_parse_errors);
        cJSON_AddNumberToObject(
            ble, "rpc_responses", diagnostics.rpc_responses);
        cJSON_AddNumberToObject(ble, "tx_attempts", diagnostics.tx_attempts);
        cJSON_AddNumberToObject(ble, "tx_successes", diagnostics.tx_successes);
        cJSON_AddNumberToObject(ble, "tx_failures", diagnostics.tx_failures);
        cJSON_AddNumberToObject(
            ble, "last_tx_error", diagnostics.last_tx_error);
#endif
        send_json_value(request_id, root);
        cJSON_Delete(root);
        break;
    }
    case WMP_MSG_SET_PLATFORM: {
        if (!board_is_product_target()) {
            send_nack(request_id, "SET_PLATFORM", WMP_ERROR_READ_ONLY, "READ_ONLY",
                      "devkit target cannot select a product platform");
            break;
        }
        const cJSON *platform = cJSON_GetObjectItemCaseSensitive(request, "platform");
        const config_platform_t selected = parse_platform(platform);
        if (selected == CONFIG_PLATFORM_UNSELECTED) {
            send_nack(request_id, "SET_PLATFORM", WMP_ERROR_VALIDATION_FAILED,
                      "VALIDATION_FAILED", "platform must be macos or windows_linux");
            break;
        }
        const esp_err_t error = config_store_select_platform(selected);
        if (error != ESP_OK) {
            send_store_error(request_id, "SET_PLATFORM", error,
                             error == ESP_ERR_NOT_FOUND ? "matching built-in Profile is unavailable"
                                                        : "platform selection could not be saved");
            break;
        }
        config_store_status_t status;
        config_store_get_status(&status);
        cJSON *root = cJSON_CreateObject();
        cJSON_AddStringToObject(root, "command", "SET_PLATFORM");
        cJSON *result = cJSON_AddObjectToObject(root, "result");
        cJSON_AddStringToObject(result, "state",
                                status.pending ? "PENDING_ACTIVATION" : "ACTIVE");
        cJSON_AddStringToObject(result, "platform", platform_name(selected));
        if (status.pending) {
            cJSON_AddNumberToObject(result, "generation", status.pending_generation);
            cJSON_AddStringToObject(result, "digest", status.pending_digest);
            add_active_controls(result);
        } else {
            cJSON_AddNumberToObject(result, "generation", status.active_generation);
            cJSON_AddStringToObject(result, "digest", status.active_digest);
        }
        send_json_value(request_id, root);
        cJSON_Delete(root);
        break;
    }
    case WMP_MSG_BLE_SLOT_SELECT:
    case WMP_MSG_BLE_SLOT_CLEAR: {
        const bool clear = message_type == WMP_MSG_BLE_SLOT_CLEAR;
        const char *command = clear ? "BLE_SLOT_CLEAR" : "BLE_SLOT_SELECT";
        uint8_t slot = 0;
        if (!board_is_product_target()) {
            send_nack(request_id, command, WMP_ERROR_READ_ONLY, "READ_ONLY",
                      "devkit target cannot change BLE host slots");
            break;
        }
        if (!read_ble_slot(request, &slot)) {
            send_nack(request_id, command, WMP_ERROR_VALIDATION_FAILED,
                      "VALIDATION_FAILED", "slot must be an integer from 1 to 3");
            break;
        }
        const esp_err_t error = clear
                                    ? codex_micro_clear_ble_slot(slot)
                                    : codex_micro_select_ble_slot(slot);
        if (error != ESP_OK) {
            send_store_error(request_id, command, error,
                             clear ? "BLE slot could not be cleared"
                                   : "BLE slot could not be selected");
            break;
        }
        cJSON *root = cJSON_CreateObject();
        cJSON_AddStringToObject(root, "command", command);
        cJSON *result = cJSON_AddObjectToObject(root, "result");
        cJSON_AddNumberToObject(result, "slot", slot);
        cJSON_AddBoolToObject(result, "paired",
                              codex_micro_ble_slot_paired(slot));
        cJSON_AddBoolToObject(result, "connected",
                              codex_micro_ble_slot_connected(slot));
        send_json_value(request_id, root);
        cJSON_Delete(root);
        break;
    }
    case WMP_MSG_GET_PROMPT_LIST: {
        if (!board_is_product_target() || !prompt_store_ready()) {
            send_nack(request_id, "GET_PROMPT_LIST", WMP_ERROR_NOT_FOUND,
                      "NOT_FOUND", "prompt storage is unavailable");
            break;
        }
        prompt_store_prompt_t *prompt = calloc(1, sizeof(*prompt));
        if (prompt == NULL) {
            send_nack(request_id, "GET_PROMPT_LIST", WMP_ERROR_INTERNAL,
                      "INTERNAL", "could not allocate prompt response");
            break;
        }
        cJSON *root = cJSON_CreateObject();
        cJSON_AddStringToObject(root, "command", "GET_PROMPT_LIST");
        cJSON *result = cJSON_AddObjectToObject(root, "result");
        cJSON *prompts = cJSON_AddArrayToObject(result, "prompts");
        esp_err_t error = ESP_OK;
        for (uint8_t prompt_id = 1; prompt_id <= PROMPT_STORE_SLOT_COUNT;
             ++prompt_id) {
            if (!prompt_store_exists(prompt_id)) {
                continue;
            }
            error = prompt_store_get(prompt_id, prompt);
            if (error != ESP_OK) {
                break;
            }
            cJSON *item = cJSON_CreateObject();
            cJSON_AddNumberToObject(item, "prompt_id", prompt_id);
            cJSON_AddStringToObject(item, "name", prompt->name);
            cJSON_AddNumberToObject(item, "body_bytes", prompt->body_length);
            cJSON_AddItemToArray(prompts, item);
        }
        free(prompt);
        if (error != ESP_OK) {
            cJSON_Delete(root);
            send_store_error(request_id, "GET_PROMPT_LIST", error,
                             "stored prompt could not be read");
            break;
        }
        send_json_value(request_id, root);
        cJSON_Delete(root);
        break;
    }
    case WMP_MSG_GET_PROMPT: {
        uint8_t prompt_id = 0;
        if (!read_prompt_id(request, &prompt_id)) {
            send_nack(request_id, "GET_PROMPT", WMP_ERROR_VALIDATION_FAILED,
                      "VALIDATION_FAILED", "prompt_id must be an integer from 1 to 12");
            break;
        }
        prompt_store_prompt_t *prompt = calloc(1, sizeof(*prompt));
        if (prompt == NULL) {
            send_nack(request_id, "GET_PROMPT", WMP_ERROR_INTERNAL,
                      "INTERNAL", "could not allocate prompt response");
            break;
        }
        const esp_err_t error = prompt_store_get(prompt_id, prompt);
        if (error != ESP_OK) {
            free(prompt);
            send_store_error(request_id, "GET_PROMPT", error,
                             "prompt is unavailable");
            break;
        }
        cJSON *root = cJSON_CreateObject();
        cJSON_AddStringToObject(root, "command", "GET_PROMPT");
        cJSON *result = cJSON_AddObjectToObject(root, "result");
        cJSON_AddNumberToObject(result, "prompt_id", prompt_id);
        cJSON_AddStringToObject(result, "name", prompt->name);
        cJSON_AddStringToObject(result, "body", prompt->body);
        cJSON_AddNumberToObject(result, "body_bytes", prompt->body_length);
        free(prompt);
        send_json_value(request_id, root);
        cJSON_Delete(root);
        break;
    }
    case WMP_MSG_SET_PROMPT: {
        uint8_t prompt_id = 0;
        const cJSON *name = cJSON_GetObjectItemCaseSensitive(request, "name");
        const cJSON *body = cJSON_GetObjectItemCaseSensitive(request, "body");
        if (!board_is_product_target()) {
            send_nack(request_id, "SET_PROMPT", WMP_ERROR_READ_ONLY,
                      "READ_ONLY", "devkit target cannot store prompts");
            break;
        }
        if (!read_prompt_id(request, &prompt_id) || !cJSON_IsString(name) ||
            !cJSON_IsString(body)) {
            send_nack(request_id, "SET_PROMPT", WMP_ERROR_VALIDATION_FAILED,
                      "VALIDATION_FAILED", "prompt_id, name, and body are required");
            break;
        }
        const esp_err_t error = prompt_store_set(prompt_id, name->valuestring,
                                                 body->valuestring);
        if (error != ESP_OK) {
            send_store_error(request_id, "SET_PROMPT", error,
                             "prompt must contain valid UTF-8 within device limits");
            break;
        }
        cJSON *root = cJSON_CreateObject();
        cJSON_AddStringToObject(root, "command", "SET_PROMPT");
        cJSON *result = cJSON_AddObjectToObject(root, "result");
        cJSON_AddNumberToObject(result, "prompt_id", prompt_id);
        cJSON_AddStringToObject(result, "name", name->valuestring);
        cJSON_AddNumberToObject(result, "body_bytes", strlen(body->valuestring));
        send_json_value(request_id, root);
        cJSON_Delete(root);
        break;
    }
    case WMP_MSG_DELETE_PROMPT: {
        uint8_t prompt_id = 0;
        if (!board_is_product_target()) {
            send_nack(request_id, "DELETE_PROMPT", WMP_ERROR_READ_ONLY,
                      "READ_ONLY", "devkit target cannot delete prompts");
            break;
        }
        if (!read_prompt_id(request, &prompt_id)) {
            send_nack(request_id, "DELETE_PROMPT", WMP_ERROR_VALIDATION_FAILED,
                      "VALIDATION_FAILED", "prompt_id must be an integer from 1 to 12");
            break;
        }
        const esp_err_t error = prompt_store_delete(prompt_id);
        if (error != ESP_OK) {
            send_store_error(request_id, "DELETE_PROMPT", error,
                             "prompt could not be deleted");
            break;
        }
        cJSON *root = cJSON_CreateObject();
        cJSON_AddStringToObject(root, "command", "DELETE_PROMPT");
        cJSON *result = cJSON_AddObjectToObject(root, "result");
        cJSON_AddNumberToObject(result, "prompt_id", prompt_id);
        cJSON_AddBoolToObject(result, "deleted", true);
        send_json_value(request_id, root);
        cJSON_Delete(root);
        break;
    }
    case WMP_MSG_GET_PROMPT_EVENT: {
        if (!board_is_product_target() || !prompt_store_ready()) {
            send_nack(request_id, "GET_PROMPT_EVENT", WMP_ERROR_NOT_FOUND,
                      "NOT_FOUND", "prompt storage is unavailable");
            break;
        }
        prompt_store_event_t event;
        const bool available = !diagnostic_capture_busy() &&
                               prompt_store_listener_poll(&event);
        cJSON *root = cJSON_CreateObject();
        cJSON_AddStringToObject(root, "command", "GET_PROMPT_EVENT");
        cJSON *result = cJSON_AddObjectToObject(root, "result");
        cJSON_AddNumberToObject(result, "poll_after_ms",
                                PROMPT_TRIGGER_POLL_AFTER_MS);
        if (available) {
            cJSON *event_json = cJSON_AddObjectToObject(result, "event");
            cJSON_AddNumberToObject(event_json, "event_id", event.event_id);
            cJSON_AddNumberToObject(event_json, "prompt_id", event.prompt_id);
        } else {
            cJSON_AddNullToObject(result, "event");
        }
        send_json_value(request_id, root);
        cJSON_Delete(root);
        break;
    }
    case WMP_MSG_DIAGNOSTIC_START: {
        if (!board_is_product_target()) {
            send_nack(request_id, "DIAGNOSTIC_START", WMP_ERROR_READ_ONLY,
                      "READ_ONLY", "devkit target has no diagnostic input capture");
            break;
        }
        if (!cJSON_IsObject(request) || cJSON_GetArraySize(request) != 0) {
            send_nack(request_id, "DIAGNOSTIC_START",
                      WMP_ERROR_VALIDATION_FAILED, "VALIDATION_FAILED",
                      "diagnostic start payload must be empty");
            break;
        }
        if (!diagnostic_capture_start_allowed()) {
            send_nack(request_id, "DIAGNOSTIC_START", WMP_ERROR_BUSY,
                      "BUSY", "release all controls and exit other input owners first");
            break;
        }
        const bool first_start = !diagnostic_capture_busy();
        publish_diagnostic_capture_request(
            CONFIG_PROTOCOL_DIAGNOSTIC_CAPTURE_START);
        if (first_start) {
            prompt_store_listener_reset();
        }
        snprintf(response, sizeof(response),
                 "{\"command\":\"DIAGNOSTIC_START\",\"result\":{\"active\":true,\"timeout_ms\":%u}}",
                 CONFIG_PROTOCOL_DIAGNOSTIC_CAPTURE_TIMEOUT_MS);
        (void)send_frame(WMP_MSG_ACK, WMP_FLAG_RESPONSE, request_id, response);
        break;
    }
    case WMP_MSG_DIAGNOSTIC_STOP: {
        if (!board_is_product_target()) {
            send_nack(request_id, "DIAGNOSTIC_STOP", WMP_ERROR_READ_ONLY,
                      "READ_ONLY", "devkit target has no diagnostic input capture");
            break;
        }
        if (!cJSON_IsObject(request) || cJSON_GetArraySize(request) != 0) {
            send_nack(request_id, "DIAGNOSTIC_STOP",
                      WMP_ERROR_VALIDATION_FAILED, "VALIDATION_FAILED",
                      "diagnostic stop payload must be empty");
            break;
        }
        publish_diagnostic_capture_request(
            CONFIG_PROTOCOL_DIAGNOSTIC_CAPTURE_STOP);
        snprintf(response, sizeof(response),
                 "{\"command\":\"DIAGNOSTIC_STOP\",\"result\":{\"active\":false}}"
        );
        (void)send_frame(WMP_MSG_ACK, WMP_FLAG_RESPONSE, request_id, response);
        break;
    }
    case WMP_MSG_CALIBRATION_START: {
        if (!board_is_product_target()) {
            send_nack(request_id, "CALIBRATION_START", WMP_ERROR_READ_ONLY,
                      "READ_ONLY", "devkit target has no calibratable joystick");
            break;
        }
        config_store_status_t status;
        config_store_get_status(&status);
        if (status.pending || diagnostic_capture_busy() ||
            s_joystick_calibration.state != JOYSTICK_CALIBRATION_INACTIVE) {
            send_nack(request_id, "CALIBRATION_START", WMP_ERROR_BUSY,
                      "BUSY", "another configuration operation is active");
            break;
        }
        board_joystick_diagnostics_t diagnostics;
        if (!board_get_joystick_diagnostics(&diagnostics) ||
            !diagnostics.supported) {
            send_nack(request_id, "CALIBRATION_START", WMP_ERROR_NOT_FOUND,
                      "NOT_FOUND", "joystick diagnostics are unavailable");
            break;
        }
        uint32_t session_id = s_next_calibration_session_id++;
        if (session_id == 0) {
            session_id = s_next_calibration_session_id++;
        }
        const uint32_t now_ms = protocol_uptime_ms();
        joystick_calibration_model_start(
            &s_joystick_calibration, session_id, now_ms,
            diagnostics.raw_x, diagnostics.raw_y);
        board_set_joystick_calibration_active(true);
        cJSON *root = cJSON_CreateObject();
        cJSON_AddStringToObject(root, "command", "CALIBRATION_START");
        cJSON *result = cJSON_AddObjectToObject(root, "result");
        add_calibration_snapshot(result);
        cJSON_AddNumberToObject(result, "center_window_ms",
                                JOYSTICK_CALIBRATION_CENTER_WINDOW_MS);
        cJSON_AddNumberToObject(result, "timeout_ms",
                                JOYSTICK_CALIBRATION_TIMEOUT_MS);
        send_json_value(request_id, root);
        cJSON_Delete(root);
        break;
    }
    case WMP_MSG_CALIBRATION_SAMPLE: {
        uint32_t session_id = 0;
        if (!calibration_session_matches(request, &session_id)) {
            send_nack(request_id, "CALIBRATION_SAMPLE", WMP_ERROR_NOT_FOUND,
                      "NOT_FOUND", "calibration session is not active");
            break;
        }
        joystick_calibration_model_touch(&s_joystick_calibration,
                                         protocol_uptime_ms());
        cJSON *root = cJSON_CreateObject();
        cJSON_AddStringToObject(root, "command", "CALIBRATION_SAMPLE");
        cJSON *result = cJSON_AddObjectToObject(root, "result");
        add_calibration_snapshot(result);
        send_json_value(request_id, root);
        cJSON_Delete(root);
        break;
    }
    case WMP_MSG_CALIBRATION_CONFIRM: {
        uint32_t session_id = 0;
        uint32_t base_generation = 0;
        if (!calibration_session_matches(request, &session_id)) {
            send_nack(request_id, "CALIBRATION_CONFIRM", WMP_ERROR_NOT_FOUND,
                      "NOT_FOUND", "calibration session is not active");
            break;
        }
        if (!read_uint32_field(request, "base_generation", &base_generation)) {
            send_nack(request_id, "CALIBRATION_CONFIRM",
                      WMP_ERROR_VALIDATION_FAILED, "VALIDATION_FAILED",
                      "base_generation is required");
            break;
        }
        joystick_calibration_candidate_t candidate;
        if (!joystick_calibration_model_candidate(&s_joystick_calibration,
                                                  &candidate)) {
            send_nack(request_id, "CALIBRATION_CONFIRM",
                      WMP_ERROR_VALIDATION_FAILED, "VALIDATION_FAILED",
                      "move the joystick through its full travel before confirming");
            break;
        }
        if (abs(s_joystick_calibration.raw_x - candidate.center_x) >
                candidate.deadzone_x ||
            abs(s_joystick_calibration.raw_y - candidate.center_y) >
                candidate.deadzone_y) {
            send_nack(request_id, "CALIBRATION_CONFIRM",
                      WMP_ERROR_VALIDATION_FAILED, "VALIDATION_FAILED",
                      "return the joystick to center before confirming");
            break;
        }
        const config_joystick_calibration_t calibration = {
            .minimum_x = candidate.minimum_x,
            .center_x = candidate.center_x,
            .maximum_x = candidate.maximum_x,
            .minimum_y = candidate.minimum_y,
            .center_y = candidate.center_y,
            .maximum_y = candidate.maximum_y,
            .deadzone_x = candidate.deadzone_x,
            .deadzone_y = candidate.deadzone_y,
        };
        char reason[128] = "calibration could not be saved";
        const esp_err_t error = config_store_set_joystick_calibration(
            &calibration, base_generation, reason, sizeof(reason));
        if (error != ESP_OK) {
            send_store_error(request_id, "CALIBRATION_CONFIRM", error, reason);
            break;
        }
        calibration_end();
        config_store_status_t status;
        config_store_get_status(&status);
        cJSON *root = cJSON_CreateObject();
        cJSON_AddStringToObject(root, "command", "CALIBRATION_CONFIRM");
        cJSON *result = cJSON_AddObjectToObject(root, "result");
        cJSON_AddStringToObject(result, "state", "PENDING_ACTIVATION");
        cJSON_AddNumberToObject(result, "generation", status.pending_generation);
        cJSON_AddStringToObject(result, "digest", status.pending_digest);
        add_active_controls(result);
        send_json_value(request_id, root);
        cJSON_Delete(root);
        break;
    }
    case WMP_MSG_CALIBRATION_CANCEL: {
        uint32_t session_id = 0;
        if (!calibration_session_matches(request, &session_id)) {
            send_nack(request_id, "CALIBRATION_CANCEL", WMP_ERROR_NOT_FOUND,
                      "NOT_FOUND", "calibration session is not active");
            break;
        }
        calibration_end();
        snprintf(response, sizeof(response),
                 "{\"command\":\"CALIBRATION_CANCEL\",\"result\":{\"state\":\"CANCELLED\",\"session_id\":%" PRIu32 "}}",
                 session_id);
        (void)send_frame(WMP_MSG_ACK, WMP_FLAG_RESPONSE, request_id, response);
        break;
    }
    case WMP_MSG_SET_LIGHTING_PREVIEW: {
        if (!lighting_preview_supported()) {
            send_nack(request_id, "SET_LIGHTING_PREVIEW",
                      WMP_ERROR_READ_ONLY, "READ_ONLY",
                      "this target does not provide under-key lighting preview");
            break;
        }
        config_protocol_lighting_preview_request_t preview;
        if (!parse_lighting_preview_request(request, &preview)) {
            send_nack(request_id, "SET_LIGHTING_PREVIEW",
                      WMP_ERROR_VALIDATION_FAILED, "VALIDATION_FAILED",
                      "preview requires enabled, brightness, and the exact under_key RGB array");
            break;
        }
        if (atomic_load(&s_lighting_preview_busy)) {
            send_nack(request_id, "SET_LIGHTING_PREVIEW", WMP_ERROR_BUSY,
                      "BUSY", "device lighting output is locally owned or unavailable");
            break;
        }
        publish_lighting_preview_request(&preview);
        snprintf(response, sizeof(response),
                 "{\"command\":\"SET_LIGHTING_PREVIEW\",\"result\":{\"state\":\"ACTIVE\"}}");
        (void)send_frame(WMP_MSG_ACK, WMP_FLAG_RESPONSE, request_id, response);
        break;
    }
    case WMP_MSG_CLEAR_LIGHTING_PREVIEW: {
        if (!lighting_preview_supported()) {
            send_nack(request_id, "CLEAR_LIGHTING_PREVIEW",
                      WMP_ERROR_READ_ONLY, "READ_ONLY",
                      "this target does not provide under-key lighting preview");
            break;
        }
        if (!cJSON_IsObject(request) || cJSON_GetArraySize(request) != 0) {
            send_nack(request_id, "CLEAR_LIGHTING_PREVIEW",
                      WMP_ERROR_VALIDATION_FAILED, "VALIDATION_FAILED",
                      "clear preview payload must be empty");
            break;
        }
        const config_protocol_lighting_preview_request_t clear = {
            .operation = CONFIG_PROTOCOL_LIGHTING_PREVIEW_CLEAR,
        };
        publish_lighting_preview_request(&clear);
        snprintf(response, sizeof(response),
                 "{\"command\":\"CLEAR_LIGHTING_PREVIEW\",\"result\":{\"state\":\"CLEARED\"}}");
        (void)send_frame(WMP_MSG_ACK, WMP_FLAG_RESPONSE, request_id, response);
        break;
    }
    case WMP_MSG_SET_AGENT_STATUS:
    case WMP_MSG_CLEAR_AGENT_STATUS: {
        const bool set = message_type == WMP_MSG_SET_AGENT_STATUS;
        const char *command = set ? "SET_AGENT_STATUS" : "CLEAR_AGENT_STATUS";
        if (!claude_status_supported()) {
            send_nack(request_id, command, WMP_ERROR_READ_ONLY,
                "READ_ONLY", "Claude Code status is not supported on this target");
            break;
        }
        claude_status_state_t states[CLAUDE_STATUS_SLOT_COUNT];
        if (!(set ? claude_status_parse_snapshot(request, states)
                  : claude_status_parse_clear(request))) {
            send_nack(request_id, command, WMP_ERROR_VALIDATION_FAILED,
                "VALIDATION_FAILED", set
                    ? "expected claude_code source and six valid states"
                    : "expected only the claude_code source");
            break;
        }
        if (set) {
            claude_status_set(states);
        } else {
            claude_status_clear();
        }
        snprintf(response, sizeof(response),
            "{\"command\":\"%s\",\"result\":{\"source\":\"claude_code\",\"active\":%s,\"lease_ms\":%u}}",
            command, set ? "true" : "false", CLAUDE_STATUS_LEASE_MS);
        send_frame(WMP_MSG_ACK, WMP_FLAG_RESPONSE, request_id, response);
        break;
    }
    case WMP_MSG_SET_CODEX_USAGE:
    case WMP_MSG_CLEAR_CODEX_USAGE: {
        const bool set = message_type == WMP_MSG_SET_CODEX_USAGE;
        const char *command = set ? "SET_CODEX_USAGE" : "CLEAR_CODEX_USAGE";
        if (!codex_usage_display_supported()) {
            send_nack(request_id, command, WMP_ERROR_READ_ONLY, "READ_ONLY",
                      "Codex usage display is not supported on this target");
            break;
        }
        const cJSON *source = cJSON_GetObjectItemCaseSensitive(request, "source");
        uint8_t weekly = 0u;
        if (!cJSON_IsObject(request) || !cJSON_IsString(source) ||
            strcmp(source->valuestring, "codex") != 0 ||
            cJSON_GetArraySize(request) != (set ? 2 : 1) ||
            (set && !read_bounded_uint8(
                cJSON_GetObjectItemCaseSensitive(request, "weekly_remaining"),
                100u, &weekly))) {
            send_nack(request_id, command, WMP_ERROR_VALIDATION_FAILED,
                      "VALIDATION_FAILED", "invalid Codex usage snapshot");
            break;
        }
        if (set) board_mist_ui_set_codex_usage(weekly, protocol_uptime_ms());
        else board_mist_ui_clear_codex_usage();
        snprintf(response, sizeof(response),
                 "{\"command\":\"%s\",\"result\":{\"active\":%s,\"lease_ms\":%u}}",
                 command, set ? "true" : "false", 600000u);
        send_frame(WMP_MSG_ACK, WMP_FLAG_RESPONSE, request_id, response);
        break;
    }
    case WMP_MSG_FACTORY_DEFAULT: {
        if (!board_is_product_target()) {
            send_nack(request_id, "FACTORY_DEFAULT", WMP_ERROR_READ_ONLY, "READ_ONLY",
                      "devkit target cannot activate product configuration");
            break;
        }
        uint32_t base_generation = 0;
        const cJSON *confirmation = cJSON_GetObjectItemCaseSensitive(request, "confirmation");
        if (!read_uint32_field(request, "base_generation", &base_generation) ||
            !cJSON_IsString(confirmation)) {
            send_nack(request_id, "FACTORY_DEFAULT", WMP_ERROR_VALIDATION_FAILED,
                      "VALIDATION_FAILED", "base_generation and confirmation are required");
            break;
        }
        char reason[128] = "factory reset failed";
        const esp_err_t error = config_store_factory_default(base_generation,
                                                              confirmation->valuestring,
                                                              reason, sizeof(reason));
        if (error != ESP_OK) {
            send_store_error(request_id, "FACTORY_DEFAULT", error, reason);
            break;
        }
        config_store_status_t status;
        config_store_get_status(&status);
        snprintf(response, sizeof(response),
                 "{\"command\":\"FACTORY_DEFAULT\",\"result\":{\"state\":\"PENDING_ACTIVATION\",\"generation\":%" PRIu32 ",\"digest\":\"%s\"}}",
                 status.pending_generation, status.pending_digest);
        (void)send_frame(WMP_MSG_ACK, WMP_FLAG_RESPONSE, request_id, response);
        break;
    }
    case WMP_MSG_FW_BEGIN:
    case WMP_MSG_FW_DATA:
    case WMP_MSG_FW_STATUS:
    case WMP_MSG_FW_END:
    case WMP_MSG_FW_ABORT:
        (void)firmware_update_protocol_handle(
            message_type, request_id, request, &FIRMWARE_UPDATE_CALLBACKS);
        break;
    default:
        send_nack(request_id, "UNKNOWN", WMP_ERROR_UNKNOWN_COMMAND, "UNKNOWN_COMMAND", "command is not implemented in Engineering Alpha");
        break;
    }
}

static void parse_available(void)
{
    while (s_rx_length >= WMP_PROTOCOL_HEADER_SIZE) {
        if (memcmp(s_rx, WMP_PROTOCOL_MAGIC, 4) != 0) {
            size_t skip = s_rx_length > 3 ? s_rx_length - 3 : 1;
            for (size_t index = 1; index + 4 <= s_rx_length; ++index) {
                if (memcmp(s_rx + index, WMP_PROTOCOL_MAGIC, 4) == 0) {
                    skip = index;
                    break;
                }
            }
            /* Preserve a possible partial magic suffix for the next CDC chunk. */
            consume(skip);
            continue;
        }

        const uint8_t major = s_rx[4];
        const uint8_t minor = s_rx[5];
        const uint8_t message_type = s_rx[6];
        const uint8_t flags = s_rx[7];
        const uint32_t request_id = read_u32_le(s_rx + 8);
        const uint32_t payload_length = read_u32_le(s_rx + 12);
        const uint32_t expected_crc = read_u32_le(s_rx + 16);

        if (payload_length > WMP_PROTOCOL_MAX_PAYLOAD) {
            if (request_id != 0) {
                send_nack(request_id, "UNKNOWN", WMP_ERROR_PAYLOAD_TOO_LARGE, "PAYLOAD_TOO_LARGE", "payload exceeds 20480 bytes");
            }
            consume(4);
            continue;
        }
        const size_t frame_length = WMP_PROTOCOL_HEADER_SIZE + payload_length;
        if (s_rx_length < frame_length) {
            return;
        }
        if (request_id == 0 || (flags & ~0x03u) != 0 || flags != 0) {
            if (request_id != 0) {
                send_nack(request_id, "UNKNOWN", WMP_ERROR_INVALID_FRAME, "INVALID_FRAME", "request header is invalid");
            }
            consume(frame_length);
            continue;
        }
        if (major != WMP_PROTOCOL_MAJOR || minor != WMP_PROTOCOL_MINOR) {
            send_nack(request_id, "UNKNOWN", WMP_ERROR_UNSUPPORTED_PROTOCOL, "UNSUPPORTED_PROTOCOL", "protocol header version is incompatible");
            consume(frame_length);
            continue;
        }
        const uint8_t *payload = s_rx + WMP_PROTOCOL_HEADER_SIZE;
        if (crc32_ieee(payload, payload_length) != expected_crc) {
            send_nack(request_id, "UNKNOWN", WMP_ERROR_CRC_MISMATCH, "CRC_MISMATCH", "payload CRC32 does not match");
            consume(frame_length);
            continue;
        }
        uint8_t digest[PSA_HASH_LENGTH(PSA_ALG_SHA_256)];
        if (!request_digest(message_type, payload, payload_length, digest)) {
            send_nack(request_id, "UNKNOWN", WMP_ERROR_INTERNAL, "INTERNAL",
                      "could not fingerprint request");
            consume(frame_length);
            continue;
        }
        replay_entry_t *cached = find_replay_entry(request_id);
        if (s_ble_name_write.pending && request_id == s_ble_name_write.request_id) {
            const bool identical = message_type == WMP_MSG_BLE_NAME_SET &&
                payload_length == s_ble_name_write.payload_length &&
                memcmp(digest, s_ble_name_write.digest, sizeof(digest)) == 0;
            if (!identical) {
                send_nack(request_id, "BLE_NAME_SET", WMP_ERROR_DUPLICATE_REQUEST_MISMATCH,
                          "DUPLICATE_REQUEST_MISMATCH", "request ID is awaiting persistence");
            }
            /* An identical retransmit waits for the original main-loop ACK. */
            consume(frame_length);
            continue;
        }
        if (cached != NULL) {
            const bool identical = cached->message_type == message_type &&
                                   cached->payload_length == payload_length &&
                                   memcmp(cached->request_digest, digest,
                                          sizeof(cached->request_digest)) == 0;
            if (identical) {
                const esp_err_t replay_error =
                    s_tx_fn(cached->response, cached->response_length, s_tx_context);
                if (replay_error == ESP_OK && message_type == WMP_MSG_HELLO) {
                    atomic_store(&s_session_active, true);
                }
                cached->age = ++s_replay_age;
            } else {
                send_nack(request_id, "UNKNOWN", WMP_ERROR_DUPLICATE_REQUEST_MISMATCH,
                          "DUPLICATE_REQUEST_MISMATCH",
                          "request ID was already used for different content");
            }
            consume(frame_length);
            continue;
        }
        if (message_type == WMP_MSG_BLE_NAME_SET &&
            ble_name_payload_has_nul(payload, payload_length)) {
            send_nack(request_id, "BLE_NAME_SET", WMP_ERROR_VALIDATION_FAILED,
                      "VALIDATION_FAILED", "Bluetooth name cannot contain NUL");
            consume(frame_length);
            continue;
        }
        cJSON *request = parse_json_object(payload, payload_length);
        if (request == NULL) {
            send_nack(request_id, "UNKNOWN", WMP_ERROR_INVALID_JSON, "INVALID_JSON", "payload must be one JSON object");
            consume(frame_length);
            continue;
        }
        s_inflight_request_id = request_id;
        s_inflight_message_type = message_type;
        s_inflight_payload_length = payload_length;
        memcpy(s_inflight_request_digest, digest, sizeof(digest));
        s_capture_response = true;
        handle_request(message_type, request_id, request);
        sample_protocol_stack(message_type);
        s_capture_response = false;
        cJSON_Delete(request);
        consume(frame_length);
    }
}

void config_protocol_set_ble_service_ready(bool ready)
{
    atomic_store(&s_ble_service_ready, ready);
}

void config_protocol_init(const char *serial, config_protocol_tx_fn tx, void *tx_context)
{
    if (serial != NULL) {
        snprintf(s_serial, sizeof(s_serial), "%s", serial);
    }
    s_usb_tx_fn = tx;
    s_usb_tx_context = tx_context;
    s_tx_fn = tx;
    s_tx_context = tx_context;
    atomic_store(&s_active_transport, CONFIG_PROTOCOL_TRANSPORT_USB);
    if (s_transport_mutex == NULL) {
        s_transport_mutex = xSemaphoreCreateMutex();
    }
    config_protocol_reset();
}

void config_protocol_feed(const uint8_t *data, size_t length)
{
    config_protocol_feed_transport(CONFIG_PROTOCOL_TRANSPORT_USB, data,
                                   length);
}

bool config_protocol_claim_transport(config_protocol_transport_t transport,
                                     config_protocol_tx_fn tx,
                                     void *tx_context)
{
    if (transport != CONFIG_PROTOCOL_TRANSPORT_BLE || tx == NULL) {
        return false;
    }
    if (s_transport_mutex == NULL ||
        xSemaphoreTake(s_transport_mutex, portMAX_DELAY) != pdTRUE) {
        return false;
    }
    config_protocol_reset();
    s_tx_fn = tx;
    s_tx_context = tx_context;
    atomic_store(&s_active_transport, transport);
    xSemaphoreGive(s_transport_mutex);
    return true;
}

void config_protocol_release_transport(config_protocol_transport_t transport)
{
    if (atomic_load(&s_active_transport) != transport) {
        return;
    }
    if (s_transport_mutex == NULL ||
        xSemaphoreTake(s_transport_mutex, portMAX_DELAY) != pdTRUE) {
        return;
    }
    if (atomic_load(&s_active_transport) != transport) {
        xSemaphoreGive(s_transport_mutex);
        return;
    }
    config_protocol_reset();
    s_tx_fn = s_usb_tx_fn;
    s_tx_context = s_usb_tx_context;
    atomic_store(&s_active_transport, CONFIG_PROTOCOL_TRANSPORT_USB);
    xSemaphoreGive(s_transport_mutex);
}

config_protocol_transport_t config_protocol_active_transport(void)
{
    return (config_protocol_transport_t)atomic_load(&s_active_transport);
}

void config_protocol_feed_transport(config_protocol_transport_t transport,
                                    const uint8_t *data, size_t length)
{
    if (transport != config_protocol_active_transport() || data == NULL) {
        return;
    }
    if (s_transport_mutex == NULL ||
        xSemaphoreTake(s_transport_mutex, portMAX_DELAY) != pdTRUE) {
        return;
    }
    if (transport != config_protocol_active_transport()) {
        xSemaphoreGive(s_transport_mutex);
        return;
    }
    /* USB and BLE callbacks may split or combine frames. Only the claimed
     * transport reaches this single bounded parser. */
    while (length > 0) {
        const size_t available = RX_CAPACITY - s_rx_length;
        if (available == 0) {
            /* A full buffer without a consumable frame cannot be recovered safely. */
            config_protocol_reset();
            xSemaphoreGive(s_transport_mutex);
            return;
        }
        const size_t copy_length = length < available ? length : available;
        memcpy(s_rx + s_rx_length, data, copy_length);
        s_rx_length += copy_length;
        data += copy_length;
        length -= copy_length;
        parse_available();
    }
    xSemaphoreGive(s_transport_mutex);
}

void config_protocol_poll(void)
{
    /* Closing CDC need not produce a USB detach. Expire uploads independently
     * of calibration, using the same mutex as connection callbacks/parser. */
    if (s_transport_mutex != NULL &&
        xSemaphoreTake(s_transport_mutex, 0) == pdTRUE) {
        poll_ble_name_write();
        screen_icon_protocol_poll(protocol_uptime_ms());
        xSemaphoreGive(s_transport_mutex);
    }
    if (s_joystick_calibration.state == JOYSTICK_CALIBRATION_INACTIVE) {
        return;
    }
    const uint32_t now_ms = protocol_uptime_ms();
    if (joystick_calibration_model_timed_out(&s_joystick_calibration,
                                             now_ms)) {
        calibration_end();
        return;
    }
    board_joystick_diagnostics_t diagnostics;
    if (board_get_joystick_diagnostics(&diagnostics) && diagnostics.supported) {
        joystick_calibration_model_sample(
            &s_joystick_calibration, now_ms,
            diagnostics.raw_x, diagnostics.raw_y);
    }
}

void config_protocol_reset(void)
{
    s_ble_name_write.pending = false;
    screen_icon_protocol_reset_session();
    claude_status_clear();
    board_mist_ui_clear_codex_usage();
    calibration_end();
    publish_diagnostic_capture_request(
        CONFIG_PROTOCOL_DIAGNOSTIC_CAPTURE_STOP);
    prompt_store_listener_reset();
    const config_protocol_lighting_preview_request_t clear_preview = {
        .operation = CONFIG_PROTOCOL_LIGHTING_PREVIEW_CLEAR,
    };
    publish_lighting_preview_request(&clear_preview);
    atomic_store(&s_session_active, false);
    s_rx_length = 0;
    s_capture_response = false;
    for (size_t index = 0; index < REPLAY_CACHE_ENTRIES; ++index) {
        replay_entry_clear(&s_replay_cache[index]);
    }
    s_replay_age = 0;
}

bool config_protocol_session_active(void)
{
    return atomic_load(&s_session_active);
}

bool config_protocol_screen_icon_upload_active(void)
{
    return screen_icon_protocol_active();
}

esp_err_t config_protocol_screen_icon_factory_default(void)
{
    if (s_transport_mutex == NULL ||
        xSemaphoreTake(s_transport_mutex, portMAX_DELAY) != pdTRUE)
        return ESP_ERR_INVALID_STATE;
    s_screen_icon_factory_reset = true;
    screen_icon_protocol_reset_session();
    esp_err_t error = screen_icon_store_erase_all();
    if (error == ESP_OK && screen_glyph_store_ready()) error = screen_glyph_store_erase_all();
    /* Successful Factory Default is followed by the existing restart. */
    if (error != ESP_OK) s_screen_icon_factory_reset = false;
    xSemaphoreGive(s_transport_mutex);
    return error;
}
