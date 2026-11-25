#include "codex_micro.h"
#include "claude_status.h"
#include "desktop_agent_status.h"

#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>

#include "cJSON.h"
#include "battery_service.h"
#include "ble_advertising_backoff_model.h"
#include "ble_host_policy_model.h"
#include "ble_recovery_model.h"
#include "boot_guard.h"
#include "liveness.h"
#include "ble_slot_history_model.h"
#include "heap_checkpoint.h"
#include "config_store.h"
#include "codex_micro_attention_model.h"
#include "codex_micro_controls.h"
#include "codex_micro_protocol.h"
#include "codex_micro_radial_filter.h"
#include "battery_display.h"
#include "cw2015.h"
#include "esp_bt.h"
#include "esp_bt_main.h"
#include "esp_attr.h"
#include "esp_app_desc.h"
#include "esp_gap_ble_api.h"
#include "esp_hid_common.h"
#include "esp_hidd.h"
#include "esp_hidd_ble_subscriptions.h"
#include "esp_hidd_gatts.h"
#include "esp_log.h"
#include "esp_random.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "nvs.h"
#include "nvs_flash.h"
#include "storage_recovery.h"

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
#define BLE_FAST_ADVERTISING_DURATION_MS 30000U
/* An empty slot waits for a new host. Windows scans sparsely and can miss the
 * slow interval for minutes, so pairing keeps the fast interval longer. */
#define BLE_PAIRING_FAST_ADVERTISING_DURATION_MS 180000U
#define BLE_FAST_ADVERTISING_MIN 0x0020
#define BLE_FAST_ADVERTISING_MAX 0x0030
#define BLE_SLOW_ADVERTISING_MIN 0x0140
#define BLE_SLOW_ADVERTISING_MAX 0x0190
#define BLE_SLOT_CLEAR_TIMEOUT_MS 4000U
/* Re-check that the device is reachable (advertising or connected). */
#define BLE_ADVERTISING_WATCHDOG_MS 3000U
/* Advertise anyway if the added services never report ready. */
#define BLE_ADVERTISING_HOLD_TIMEOUT_MS 5000U

typedef struct {
    uint8_t valid;
    uint8_t address_type;
    uint8_t address[ESP_BD_ADDR_LEN];
} codex_micro_ble_peer_t;

typedef struct {
    uint8_t valid;
    uint8_t address_type;
    uint8_t address[ESP_BD_ADDR_LEN];
} codex_micro_ble_identity_t;

typedef enum {
    BLE_PEER_FILTER_IDLE = 0,
    BLE_PEER_FILTER_STOPPING,
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
static atomic_bool s_slow_advertising;
static atomic_bool s_advertising_interval_restart_pending;
static atomic_uint_least32_t s_fast_advertising_until_ms;
static atomic_bool s_peer_filter_ready;
static atomic_int s_peer_filter_state;
static atomic_uchar s_active_peer_slot;
/* Pairing window: the active slot is an empty slot selected in RAM only and
 * NVS "active" still names a host. Slot to go back to when the window ends
 * (0 = none), and the one-shot notice that BLE went back to a slot. */
static atomic_uchar s_pairing_return_slot;
static atomic_uchar s_ble_pairing_return_pending;
/* The link now up (or just ended) on an empty active slot came from a host
 * remembered in another slot. Such a host retries every few seconds and is
 * refused; its links must not give the pairing window a new 180 s. */
static atomic_bool s_link_remembered_elsewhere;
/* The host on this link sent an SMP Pairing Request (ESP_GAP_BLE_SEC_REQ_EVT,
 * raised by Bluedroid's smp_proc_pair_cmd when the central starts pairing):
 * it is pairing afresh and gets new keys. A host that reconnects with the
 * keys it already holds only encrypts the link and never sends that request. */
static atomic_bool s_link_pairing_requested;
static atomic_uint_least32_t s_pairing_window_returns;
/* NVS "active" could not be written after a host paired. */
static atomic_uint_least32_t s_active_slot_persist_failures;
static atomic_uchar s_ble_slot_redirect_pending;
static atomic_uchar s_ble_peer_rejection_pending;
static atomic_bool s_ble_slot_clear_pending;
static atomic_bool s_ble_slot_clear_result_pending;
static atomic_bool s_ble_slot_clear_protocol_result_pending;
static atomic_uint_least32_t s_ble_slot_clear_started_ms;
static atomic_uint_least32_t s_ble_slot_clear_timeout_count;
static atomic_bool s_auth_bond_removal_pending;
static atomic_uchar s_ble_slot_clear_result_slot;
static atomic_int s_ble_slot_clear_result_error;
static atomic_uint_least32_t s_auth_fail_events;
static atomic_uchar s_last_auth_fail_reason;
static atomic_uint_least32_t s_ble_reject_count;
static atomic_uchar s_ble_last_reject_reason;
static atomic_uchar s_ble_last_reject_slot;
/* s_ble_reject_count split by codex_micro_ble_reject_reason_t. */
static atomic_uint_least32_t
    s_ble_reject_reason_counts[CODEX_MICRO_BLE_REJECT_SUSPENDED + 1u];
/* Time from the start of advertising to an accepted encrypted link. */
static atomic_bool s_advertising_episode_valid;
static atomic_uint_least32_t s_advertising_episode_start_ms;
static atomic_uint_least32_t s_ble_connect_last_ms;
static atomic_uint_least32_t s_ble_connect_max_ms;
static atomic_uchar s_ble_pending_disconnect_source;
static atomic_uchar s_ble_last_disconnect_reason;
static atomic_uchar s_ble_last_disconnect_source;
static atomic_uchar s_ble_last_slot_op;
static atomic_uchar s_ble_last_slot_op_slot;
static atomic_int_least32_t s_ble_last_slot_op_error;
static atomic_bool s_ble_last_slot_op_timeout;
#define BLE_SLOT_HISTORY_NVS_KEY "slot_hist"
#define BLE_SLOT_BOOT_NVS_KEY "slot_boot"
static ble_slot_history_t s_slot_history;
static uint32_t s_slot_history_boot;
static portMUX_TYPE s_slot_history_lock = portMUX_INITIALIZER_UNLOCKED;
static atomic_uchar s_ble_slot_clear_source;
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
/* HID notification subscriptions (CCCD) saved per bonded slot. */
static uint32_t s_peer_subscriptions[CODEX_MICRO_BLE_SLOT_COUNT];
static bool s_peer_subscriptions_valid[CODEX_MICRO_BLE_SLOT_COUNT];
static atomic_uint_least32_t s_subscription_restores;
static atomic_uint_least32_t s_subscription_default_restores;
static atomic_uint_least32_t s_subscription_save_failures;
static atomic_uint_least32_t s_hid_reports_dropped_unsubscribed;
static atomic_uint_least32_t s_hid_report_failures;
/* Advertising waits until every GATT service is registered (see
 * codex_micro_hold_advertising_for_services). Not reset by init. */
static atomic_bool s_advertising_held;
static atomic_uint_least32_t s_hid_ready_ms;
/* A radio link exists, encrypted or not (HID connect .. disconnect). */
static atomic_bool s_link_connected;
/* The stack's number for the link s_link_connected is about. Written and
 * read only in the Bluetooth host task (GATTS callback). */
static uint16_t s_link_conn_id;
static uint32_t s_last_advertising_watchdog_ms;
static atomic_uint_least32_t s_advertising_recoveries;
static atomic_uint_least32_t s_advertising_starts;
static atomic_uint_least32_t s_advertising_start_failures;
static atomic_int s_advertising_last_start_status;
static atomic_uint_least32_t s_advertising_stops;
static atomic_uint_least32_t s_advertising_retries_exhausted;
/* A local key or knob input arrived (action engine task -> poll). */
static atomic_bool s_local_input_pending;
/* Set by local input, taken by the advertising watchdog (stability item 3). */
static atomic_bool s_advertising_input_pending;
static ble_adv_backoff_t s_advertising_backoff;
/* When s_advertising_starting / s_advertising_retry_running were set; a flag
 * older than BLE_ADVERTISING_FLAG_TIMEOUT_MS is stale. */
static atomic_uint_least32_t s_advertising_starting_ms;
static atomic_uint_least32_t s_advertising_retry_started_ms;
static atomic_uint_least32_t s_advertising_stale_flags;
#define BLE_ADVERTISING_FLAG_TIMEOUT_MS 10000U
static atomic_uint_least32_t s_advertising_pairing_boosts;
/* GATT database identity last announced to each bonded host. */
static uint32_t s_gatt_layout_id;
static uint32_t s_peer_gatt_layout[CODEX_MICRO_BLE_SLOT_COUNT];
static bool s_peer_gatt_layout_valid[CODEX_MICRO_BLE_SLOT_COUNT];
static codex_micro_service_change_fn s_service_change_fn;
static void *s_service_change_context;
static atomic_uchar s_service_change_pending_slot;
static atomic_uint_least32_t s_service_change_sent;
static atomic_uint_least32_t s_service_change_skipped;
/* Authentication failures. */
static atomic_uint_least32_t s_auth_fail_disconnects;
static atomic_uchar s_ble_reauth_pending;
static atomic_uint_least32_t s_reauth_prompts;
/* Bonds that belong to no slot. */
static bool s_orphan_cleanup_done;
static atomic_uint_least32_t s_orphan_bonds_removed;
static atomic_uint_least32_t s_orphan_cleanup_skipped;
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
/* NORMAL and CODEX share the normalized, source-aware task-status view. */
static codex_task_light_t s_codex_task_lights[CODEX_MICRO_TASK_COUNT];
static codex_micro_attention_model_t
    s_attention_models[CODEX_TRANSPORT_BLE + 1u];
static codex_micro_attention_t
    s_attention_notifications[CODEX_TRANSPORT_BLE + 1u];
/* Same notifications per task slot, so a desktop lease can keep only the
 * reminders of slots it yielded to Codex. */
static codex_micro_attention_t s_attention_slots[CODEX_MICRO_TASK_COUNT];
/* Last Codex task-status update (any transport), for status_age_ms. */
static atomic_bool s_codex_status_seen;
static atomic_uint_least32_t s_codex_status_at_ms;
static bool s_ble_press_active[BOARD_CONTROL_COUNT];
static codex_transport_t s_press_transport[BOARD_CONTROL_COUNT];
static bool s_radial_active;
static bool s_radial_wait_center;
static float s_radial_angle;
static int64_t s_radial_last_sent_at_us;
static codex_transport_t s_radial_transport;
static codex_micro_radial_filter_t s_radial_filter;
static codex_micro_radial_release_gate_t s_radial_release_gate;
static uint32_t s_radial_filter_generation;
static nvs_handle_t s_ble_peer_nvs;
static codex_micro_ble_peer_t
    s_ble_peers[CODEX_MICRO_BLE_SLOT_COUNT];
static codex_micro_ble_identity_t
    s_ble_peer_identities[CODEX_MICRO_BLE_SLOT_COUNT];
static codex_micro_ble_identity_t s_pairing_identity;
static esp_bd_addr_t s_pairing_identity_source;
static uint8_t s_ble_slot_clear_slot;
static codex_micro_ble_peer_t s_ble_slot_clear_peer;
static esp_bd_addr_t s_auth_bond_removal_address;
static uint8_t s_auth_bond_removal_attempts;
static esp_bd_addr_t s_connected_peer_address;
static atomic_bool s_connected_peer_valid;

static const char *const BLE_PEER_NVS_KEYS[CODEX_MICRO_BLE_SLOT_COUNT] = {
    "peer1", "peer2", "peer3",
};
static const char *const BLE_PEER_SUBSCRIPTION_NVS_KEYS[
    CODEX_MICRO_BLE_SLOT_COUNT] = {
    "peer1_sub", "peer2_sub", "peer3_sub",
};
static const char *const BLE_PEER_GATT_LAYOUT_NVS_KEYS[
    CODEX_MICRO_BLE_SLOT_COUNT] = {
    "peer1_gatt", "peer2_gatt", "peer3_gatt",
};
static const char *const BLE_PEER_IDENTITY_NVS_KEYS[
    CODEX_MICRO_BLE_SLOT_COUNT] = {
    "peer1_id", "peer2_id", "peer3_id",
};
/*
 * The address this device shows to the host of a slot. A host added by this
 * firmware gets an address of its own (static random) and from then on looks
 * for that address only, so it cannot reach the device while another host is
 * selected or while a new one is being added. With one shared address every
 * remembered host connects whenever the device advertises and has to be
 * dropped again; a Mac dropped that way during service discovery stopped
 * treating the device as a HID device (docs/verification/
 * 2026-11-11-ble-hosts-step1). A slot without an entry was paired by older
 * firmware on the public address and stays on it.
 */
static const char *const BLE_PEER_LOCAL_ADDRESS_NVS_KEYS[
    CODEX_MICRO_BLE_SLOT_COUNT] = {
    "peer1_la", "peer2_la", "peer3_la",
};
static esp_bd_addr_t s_ble_local_addresses[CODEX_MICRO_BLE_SLOT_COUNT];
static bool s_ble_local_address_valid[CODEX_MICRO_BLE_SLOT_COUNT];
/* Shown while the selected slot waits for a new host; becomes that slot's
 * address when the host pairs. */
static esp_bd_addr_t s_pairing_local_address;
static bool s_pairing_local_address_valid;
/* What the last advertising start used, hence what the current link is on. */
static esp_bd_addr_t s_advertised_local_address;
static bool s_advertised_local_address_random;
/* The stack takes a new random address only while it is not advertising and
 * otherwise keeps the one it had, reporting the refusal in a later event.
 * These count how often the address on air was not the one asked for. */
static atomic_uint_least32_t s_local_address_set_rejections;
static atomic_uint_least32_t s_local_address_apply_failures;
static atomic_uint_least32_t s_local_address_adopt_mismatches;
static atomic_uint_least32_t s_local_address_retries;
#define BLE_LOCAL_ADDRESS_RETRY_LIMIT 3u

static esp_err_t start_advertising(void);
static void ensure_advertising(void);
static void load_peer_local_address(uint8_t slot);
static esp_err_t forget_peer_local_address(uint8_t slot);
static void adopt_link_local_address(uint8_t slot);
static void verify_advertising_address(void);
static void reset_advertising_speed(void);
static void reset_advertising_speed_after_link(void);
static void poll_advertising_speed(void);
static void release_active_controls(void);
static void poll_radial_joystick(void);
static void restore_soft_restart_mode_if_ready(void);
static bool handle_send_result(esp_err_t result, bool pressed,
                               const char *control_name);
static esp_err_t refresh_peer_filter(void);
static uint32_t advertising_now_ms(void);
static void restart_pairing_window_timer(void);
static void poll_pairing_window(void);

static uint32_t monotonic_ms(void)
{
    return (uint32_t)(esp_timer_get_time() / 1000);
}

static void note_ble_reject(codex_micro_ble_reject_reason_t reason,
                            uint8_t slot)
{
    atomic_fetch_add(&s_ble_reject_count, 1u);
    if ((unsigned)reason <= CODEX_MICRO_BLE_REJECT_SUSPENDED) {
        atomic_fetch_add(&s_ble_reject_reason_counts[reason], 1u);
    }
    atomic_store(&s_ble_last_reject_reason, (uint8_t)reason);
    atomic_store(&s_ble_last_reject_slot, slot);
}

static void note_ble_slot_op(codex_micro_ble_slot_op_t op, uint8_t slot,
                             esp_err_t error, bool timeout,
                             codex_micro_ble_slot_source_t source)
{
    atomic_store(&s_ble_last_slot_op, (uint8_t)op);
    atomic_store(&s_ble_last_slot_op_slot, slot);
    atomic_store(&s_ble_last_slot_op_error, error);
    atomic_store(&s_ble_last_slot_op_timeout, timeout);
    const ble_slot_history_entry_t entry = {
        .op = (uint8_t)op,
        .slot = slot,
        .source = (uint8_t)source,
        .timeout = timeout ? 1u : 0u,
        .error = error,
        .uptime_ms = (uint32_t)(esp_timer_get_time() / 1000),
        .boot = s_slot_history_boot,
    };
    ble_slot_history_t snapshot;
    portENTER_CRITICAL(&s_slot_history_lock);
    ble_slot_history_record(&s_slot_history, &entry);
    snapshot = s_slot_history;
    portEXIT_CRITICAL(&s_slot_history_lock);
    /* Slot operations are rare; keep every one across power loss. */
    if (s_ble_peer_nvs != 0 &&
        nvs_set_blob(s_ble_peer_nvs, BLE_SLOT_HISTORY_NVS_KEY, &snapshot,
                     sizeof(snapshot)) == ESP_OK) {
        (void)nvs_commit(s_ble_peer_nvs);
    }
}

static void load_ble_slot_history(void)
{
    ble_slot_history_t stored;
    size_t length = sizeof(stored);
    const esp_err_t result = nvs_get_blob(
        s_ble_peer_nvs, BLE_SLOT_HISTORY_NVS_KEY, &stored, &length);
    uint32_t boot = 0;
    (void)nvs_get_u32(s_ble_peer_nvs, BLE_SLOT_BOOT_NVS_KEY, &boot);
    ++boot;
    if (nvs_set_u32(s_ble_peer_nvs, BLE_SLOT_BOOT_NVS_KEY, boot) == ESP_OK) {
        (void)nvs_commit(s_ble_peer_nvs);
    }
    portENTER_CRITICAL(&s_slot_history_lock);
    ble_slot_history_load(&s_slot_history, result == ESP_OK ? &stored : NULL,
                          length);
    s_slot_history_boot = boot;
    portEXIT_CRITICAL(&s_slot_history_lock);
}

size_t codex_micro_ble_slot_history(codex_micro_ble_slot_history_entry_t *entries,
                                    size_t capacity)
{
    if (entries == NULL) {
        return 0;
    }
    portENTER_CRITICAL(&s_slot_history_lock);
    const ble_slot_history_t history = s_slot_history;
    const uint32_t boot = s_slot_history_boot;
    portEXIT_CRITICAL(&s_slot_history_lock);
    const size_t count = history.count < capacity ? history.count : capacity;
    for (size_t index = 0; index < count; ++index) {
        const ble_slot_history_entry_t *entry = &history.entries[index];
        entries[index] = (codex_micro_ble_slot_history_entry_t){
            .op = entry->op,
            .slot = entry->slot,
            .source = entry->source,
            .timeout = entry->timeout != 0u,
            .error = entry->error,
            .uptime_ms = entry->uptime_ms,
            .boots_ago = boot >= entry->boot ? boot - entry->boot : 0u,
        };
    }
    return count;
}

static esp_err_t request_ble_disconnect(
    uint8_t address[ESP_BD_ADDR_LEN],
    codex_micro_ble_disconnect_source_t source)
{
    atomic_store(&s_ble_pending_disconnect_source, (uint8_t)source);
    const esp_err_t result = esp_ble_gap_disconnect(address);
    if (result != ESP_OK) {
        uint8_t expected = (uint8_t)source;
        (void)atomic_compare_exchange_strong(
            &s_ble_pending_disconnect_source, &expected,
            (uint8_t)CODEX_MICRO_BLE_DISCONNECT_NONE);
    }
    return result;
}

static void note_ble_disconnect_event(uint8_t reason)
{
    uint8_t source = atomic_exchange(
        &s_ble_pending_disconnect_source,
        (uint8_t)CODEX_MICRO_BLE_DISCONNECT_NONE);
    if (source == CODEX_MICRO_BLE_DISCONNECT_NONE) {
        source = CODEX_MICRO_BLE_DISCONNECT_STACK;
    }
    atomic_store(&s_ble_last_disconnect_reason, reason);
    atomic_store(&s_ble_last_disconnect_source, source);
}

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

_Static_assert(BLE_HOST_POLICY_SLOT_COUNT == CODEX_MICRO_BLE_SLOT_COUNT,
               "host policy and BLE slots must agree");

static ble_host_records_t host_records(void)
{
    ble_host_records_t records = {0};
    portENTER_CRITICAL(&s_state_lock);
    for (size_t index = 0; index < CODEX_MICRO_BLE_SLOT_COUNT; ++index) {
        records.paired[index] = s_ble_peers[index].valid == 1u;
    }
    portEXIT_CRITICAL(&s_state_lock);
    return records;
}

static esp_err_t persist_active_peer_slot(uint8_t slot)
{
    esp_err_t result = nvs_set_u8(s_ble_peer_nvs, "active", slot);
    return result == ESP_OK ? nvs_commit(s_ble_peer_nvs) : result;
}

static esp_err_t persist_peer_identity(
    uint8_t slot, const codex_micro_ble_peer_t *peer,
    const codex_micro_ble_identity_t *identity)
{
    if (!valid_ble_slot(slot) || peer == NULL || identity == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    /* Store the optional record first. If the required peer write fails,
     * an orphan identity is ignored because the slot still has no peer. */
    esp_err_t result = ESP_OK;
    if (identity->valid == 1u) {
        result = nvs_set_blob(s_ble_peer_nvs,
                              BLE_PEER_IDENTITY_NVS_KEYS[slot - 1u],
                              identity, sizeof(*identity));
    } else {
        result = nvs_erase_key(
            s_ble_peer_nvs, BLE_PEER_IDENTITY_NVS_KEYS[slot - 1u]);
        if (result == ESP_ERR_NVS_NOT_FOUND) {
            result = ESP_OK;
        }
    }
    if (result != ESP_OK) {
        return result;
    }
    result = nvs_set_blob(s_ble_peer_nvs,
                          BLE_PEER_NVS_KEYS[slot - 1u], peer,
                          sizeof(*peer));
    return result == ESP_OK ? nvs_commit(s_ble_peer_nvs) : result;
}

/*
 * A host was just saved in slot, which is therefore the slot to start on from
 * now on. During a pairing window NVS "active" still names the previous host.
 *
 * A failed write is only logged and counted: the pairing itself is complete
 * in both layers (record and bond), "active" still names a remembered host,
 * and undoing the pairing here would need a new path that erases a record.
 * After a restart the device starts on the previous host and the new one is
 * still in its slot.
 */
static void close_pairing_window_after_pair_save(uint8_t slot)
{
    atomic_store(&s_pairing_return_slot, 0u);
    const esp_err_t result = persist_active_peer_slot(slot);
    if (result != ESP_OK) {
        atomic_fetch_add(&s_active_slot_persist_failures, 1u);
        ESP_LOGE(TAG, "failed to persist active BLE slot %u: %s", slot,
                 esp_err_to_name(result));
    }
}

/*
 * ESP-IDF keeps HID CCCD values only in RAM. A bonded host need not write
 * them again after reconnecting (the server must remember them), and Windows
 * relies on that: without a restore, keys stop reaching Windows after every
 * device restart even though the link shows as connected.
 */
static void load_peer_subscriptions(uint8_t slot)
{
    uint32_t mask = 0;
    const bool valid = s_ble_peer_nvs != 0 &&
        nvs_get_u32(s_ble_peer_nvs, BLE_PEER_SUBSCRIPTION_NVS_KEYS[slot - 1u],
                    &mask) == ESP_OK;
    portENTER_CRITICAL(&s_state_lock);
    s_peer_subscriptions[slot - 1u] = valid ? mask : 0u;
    s_peer_subscriptions_valid[slot - 1u] = valid;
    portEXIT_CRITICAL(&s_state_lock);
}

static esp_err_t forget_peer_subscriptions(uint8_t slot)
{
    portENTER_CRITICAL(&s_state_lock);
    s_peer_subscriptions[slot - 1u] = 0u;
    s_peer_subscriptions_valid[slot - 1u] = false;
    portEXIT_CRITICAL(&s_state_lock);
    const esp_err_t result = nvs_erase_key(
        s_ble_peer_nvs, BLE_PEER_SUBSCRIPTION_NVS_KEYS[slot - 1u]);
    return result == ESP_ERR_NVS_NOT_FOUND ? ESP_OK : result;
}

static esp_err_t save_peer_subscriptions(uint8_t slot, uint32_t mask)
{
    portENTER_CRITICAL(&s_state_lock);
    const bool unchanged = s_peer_subscriptions_valid[slot - 1u] &&
                           s_peer_subscriptions[slot - 1u] == mask;
    portEXIT_CRITICAL(&s_state_lock);
    if (unchanged) {
        return ESP_OK;
    }
    esp_err_t result = nvs_set_u32(
        s_ble_peer_nvs, BLE_PEER_SUBSCRIPTION_NVS_KEYS[slot - 1u], mask);
    if (result == ESP_OK) {
        result = nvs_commit(s_ble_peer_nvs);
    }
    if (result != ESP_OK) {
        atomic_fetch_add(&s_subscription_save_failures, 1u);
        return result;
    }
    portENTER_CRITICAL(&s_state_lock);
    s_peer_subscriptions[slot - 1u] = mask;
    s_peer_subscriptions_valid[slot - 1u] = true;
    portEXIT_CRITICAL(&s_state_lock);
    return ESP_OK;
}

/* Called once the bonded host on @slot has re-established encryption. */
static void restore_peer_subscriptions(uint8_t slot, bool new_pairing)
{
    if (s_hid == NULL || !valid_ble_slot(slot)) {
        return;
    }
    uint32_t mask = 0;
    bool saved = false;
    if (new_pairing) {
        /* A different host now owns the slot; its subscriptions start over. */
        if (forget_peer_subscriptions(slot) != ESP_OK ||
            nvs_commit(s_ble_peer_nvs) != ESP_OK) {
            atomic_fetch_add(&s_subscription_save_failures, 1u);
        }
    } else {
        portENTER_CRITICAL(&s_state_lock);
        saved = s_peer_subscriptions_valid[slot - 1u];
        mask = s_peer_subscriptions[slot - 1u];
        portEXIT_CRITICAL(&s_state_lock);
    }
    if (!saved) {
        /* Bonded before this firmware saved subscriptions, or newly paired:
         * enable every input. Hosts that subscribe themselves (macOS, a new
         * pairing) overwrite this within the same connection. */
        if (esp_hidd_ble_get_subscription_mask_all(s_hid, &mask) != ESP_OK) {
            return;
        }
        atomic_fetch_add(&s_subscription_default_restores, 1u);
    }
    if (esp_hidd_ble_set_subscriptions(s_hid, mask) == ESP_OK) {
        atomic_fetch_add(&s_subscription_restores, 1u);
    }
}

/*
 * Service Changed is sent only when a bonded host may hold a stale copy of
 * the GATT database, i.e. once per host after the firmware (and possibly the
 * database) changed. Sending it on every connection made every host
 * rediscover all services, which slows reconnection and can make Windows
 * rebuild the HID device.
 */
static void load_peer_gatt_layout(uint8_t slot)
{
    uint32_t layout = 0;
    const bool valid = s_ble_peer_nvs != 0 &&
        nvs_get_u32(s_ble_peer_nvs, BLE_PEER_GATT_LAYOUT_NVS_KEYS[slot - 1u],
                    &layout) == ESP_OK;
    portENTER_CRITICAL(&s_state_lock);
    s_peer_gatt_layout[slot - 1u] = valid ? layout : 0u;
    s_peer_gatt_layout_valid[slot - 1u] = valid;
    portEXIT_CRITICAL(&s_state_lock);
}

static esp_err_t forget_peer_gatt_layout(uint8_t slot)
{
    portENTER_CRITICAL(&s_state_lock);
    s_peer_gatt_layout[slot - 1u] = 0u;
    s_peer_gatt_layout_valid[slot - 1u] = false;
    portEXIT_CRITICAL(&s_state_lock);
    const esp_err_t result = nvs_erase_key(
        s_ble_peer_nvs, BLE_PEER_GATT_LAYOUT_NVS_KEYS[slot - 1u]);
    return result == ESP_ERR_NVS_NOT_FOUND ? ESP_OK : result;
}

static void save_peer_gatt_layout(uint8_t slot, uint32_t layout)
{
    portENTER_CRITICAL(&s_state_lock);
    const bool unchanged = s_peer_gatt_layout_valid[slot - 1u] &&
                           s_peer_gatt_layout[slot - 1u] == layout;
    portEXIT_CRITICAL(&s_state_lock);
    if (unchanged) {
        return;
    }
    esp_err_t result = nvs_set_u32(
        s_ble_peer_nvs, BLE_PEER_GATT_LAYOUT_NVS_KEYS[slot - 1u], layout);
    if (result == ESP_OK) {
        result = nvs_commit(s_ble_peer_nvs);
    }
    if (result != ESP_OK) {
        /* Harmless: the host only gets one more Service Changed. */
        ESP_LOGW(TAG, "cannot save GATT layout for slot %u: %s", slot,
                 esp_err_to_name(result));
        return;
    }
    portENTER_CRITICAL(&s_state_lock);
    s_peer_gatt_layout[slot - 1u] = layout;
    s_peer_gatt_layout_valid[slot - 1u] = true;
    portEXIT_CRITICAL(&s_state_lock);
}

/* Called once the bonded host on @slot has re-established encryption. */
static void sync_peer_gatt_layout(uint8_t slot, bool new_pairing,
                                  const uint8_t address[ESP_BD_ADDR_LEN])
{
    if (!valid_ble_slot(slot)) {
        return;
    }
    if (new_pairing) {
        /* Pairing makes the host discover the complete database. */
        save_peer_gatt_layout(slot, s_gatt_layout_id);
        return;
    }
    portENTER_CRITICAL(&s_state_lock);
    const bool current = s_peer_gatt_layout_valid[slot - 1u] &&
                         s_peer_gatt_layout[slot - 1u] == s_gatt_layout_id;
    portEXIT_CRITICAL(&s_state_lock);
    if (current) {
        atomic_fetch_add(&s_service_change_skipped, 1u);
        return;
    }
    if (s_service_change_fn == NULL) {
        return;
    }
    atomic_store(&s_service_change_pending_slot, slot);
    if (s_service_change_fn(address, s_service_change_context) == ESP_OK) {
        atomic_fetch_add(&s_service_change_sent, 1u);
    } else {
        atomic_store(&s_service_change_pending_slot, 0u);
    }
}

static bool hid_subscriptions(uint32_t *mask)
{
    return s_hid != NULL && esp_hidd_ble_get_subscriptions(s_hid, mask) == ESP_OK;
}

static esp_err_t load_ble_peer_slots(void)
{
    esp_err_t result = nvs_open(CODEX_MICRO_BLE_NVS_NAMESPACE,
                                NVS_READWRITE, &s_ble_peer_nvs);
    if (result != ESP_OK) {
        return result;
    }
    load_ble_slot_history();

    memset(s_ble_peers, 0, sizeof(s_ble_peers));
    memset(s_ble_peer_identities, 0, sizeof(s_ble_peer_identities));
    for (size_t i = 0; i < CODEX_MICRO_BLE_SLOT_COUNT; ++i) {
        load_peer_local_address((uint8_t)(i + 1u));
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
            continue;
        }
        length = sizeof(s_ble_peer_identities[i]);
        result = nvs_get_blob(s_ble_peer_nvs,
                              BLE_PEER_IDENTITY_NVS_KEYS[i],
                              &s_ble_peer_identities[i], &length);
        if (result != ESP_OK ||
            length != sizeof(s_ble_peer_identities[i]) ||
            s_ble_peer_identities[i].valid != 1u) {
            memset(&s_ble_peer_identities[i], 0,
                   sizeof(s_ble_peer_identities[i]));
        }
    }

    for (uint8_t slot = 1u; slot <= CODEX_MICRO_BLE_SLOT_COUNT; ++slot) {
        load_peer_subscriptions(slot);
        load_peer_gatt_layout(slot);
    }

    uint8_t active_slot = 1u;
    result = nvs_get_u8(s_ble_peer_nvs, "active", &active_slot);
    if (result != ESP_OK || !valid_ble_slot(active_slot)) {
        active_slot = 1u;
    }
    /* A stored slot that is empty while another slot holds a host (older
     * firmware, or power lost during a pairing window) starts on a host
     * instead. RAM only: NVS is not written at boot. */
    const ble_host_records_t records = host_records();
    const uint8_t boot_slot =
        ble_host_policy_boot_slot(&records, active_slot);
    if (boot_slot != active_slot) {
        ESP_LOGW(TAG, "stored BLE slot %u is empty; starting on slot %u",
                 active_slot, boot_slot);
    }
    atomic_store(&s_active_peer_slot, boot_slot);
    atomic_store(&s_pairing_return_slot, 0u);
    return ESP_OK;
}

static void reload_ble_peer_slot(uint8_t slot)
{
    if (!valid_ble_slot(slot) || s_ble_peer_nvs == 0) {
        return;
    }
    codex_micro_ble_peer_t peer = {0};
    codex_micro_ble_identity_t identity = {0};
    size_t length = sizeof(peer);
    esp_err_t result = nvs_get_blob(
        s_ble_peer_nvs, BLE_PEER_NVS_KEYS[slot - 1u], &peer, &length);
    if (result != ESP_OK || length != sizeof(peer) || peer.valid != 1u) {
        memset(&peer, 0, sizeof(peer));
    } else {
        length = sizeof(identity);
        result = nvs_get_blob(
            s_ble_peer_nvs, BLE_PEER_IDENTITY_NVS_KEYS[slot - 1u],
            &identity, &length);
        if (result != ESP_OK || length != sizeof(identity) ||
            identity.valid != 1u) {
            memset(&identity, 0, sizeof(identity));
        }
    }
    portENTER_CRITICAL(&s_state_lock);
    s_ble_peers[slot - 1u] = peer;
    s_ble_peer_identities[slot - 1u] = identity;
    portEXIT_CRITICAL(&s_state_lock);
    load_peer_subscriptions(slot);
    load_peer_gatt_layout(slot);
}

static codex_micro_ble_identity_t peer_identity_for_slot(uint8_t slot)
{
    codex_micro_ble_identity_t identity = {0};
    if (!valid_ble_slot(slot)) {
        return identity;
    }
    portENTER_CRITICAL(&s_state_lock);
    identity = s_ble_peer_identities[slot - 1u];
    portEXIT_CRITICAL(&s_state_lock);
    return identity;
}

static bool peer_address_matches(const codex_micro_ble_peer_t *peer,
                                 const uint8_t address[ESP_BD_ADDR_LEN])
{
    return peer != NULL && peer->valid == 1u && address != NULL &&
           memcmp(peer->address, address, ESP_BD_ADDR_LEN) == 0;
}

static codex_micro_ble_address_kind_t ble_address_kind(
    const codex_micro_ble_peer_t *peer)
{
    if (peer == NULL || peer->valid != 1u) {
        return CODEX_MICRO_BLE_ADDRESS_NONE;
    }
    if (peer->address_type == BLE_ADDR_TYPE_PUBLIC) {
        return CODEX_MICRO_BLE_ADDRESS_PUBLIC;
    }
    switch (peer->address[0] & 0xC0u) {
    case 0xC0u:
        return CODEX_MICRO_BLE_ADDRESS_RANDOM_STATIC;
    case 0x40u:
        return CODEX_MICRO_BLE_ADDRESS_RPA;
    default:
        return CODEX_MICRO_BLE_ADDRESS_RANDOM_OTHER;
    }
}

static bool identity_address_matches(
    const codex_micro_ble_identity_t *identity,
    const uint8_t address[ESP_BD_ADDR_LEN])
{
    return identity != NULL && identity->valid == 1u && address != NULL &&
           memcmp(identity->address, address, ESP_BD_ADDR_LEN) == 0;
}

static bool peer_identity_matches(
    uint8_t slot, const uint8_t address[ESP_BD_ADDR_LEN],
    const codex_micro_ble_identity_t *presented_identity)
{
    codex_micro_ble_peer_t peer = peer_for_slot(slot);
    const codex_micro_ble_identity_t identity = peer_identity_for_slot(slot);
    return peer_address_matches(&peer, address) ||
           identity_address_matches(&identity, address) ||
           (presented_identity != NULL && presented_identity->valid == 1u &&
            identity_address_matches(&identity,
                                     presented_identity->address));
}

/* Persist a CCCD change made by the encrypted host of the active slot. */
static void note_subscription_change(uint32_t before, uint32_t after,
                                     uint16_t handle)
{
    if (before == after) {
        return;
    }
    const uint32_t enabled = after & ~before;
    const uint32_t disabled = before & ~after;
    if (enabled != 0u) {
        atomic_fetch_add(&s_notify_subscribe_events, 1u);
    }
    if (disabled != 0u) {
        atomic_fetch_add(&s_notify_unsubscribe_events, 1u);
    }
    atomic_store(&s_last_subscribe_attr, handle);
    atomic_store(&s_last_subscribe_notify, enabled != 0u);
    const uint8_t slot = atomic_load(&s_active_peer_slot);
    if (atomic_load(&s_connected) && atomic_load(&s_connected_peer_valid) &&
        valid_ble_slot(slot) &&
        peer_identity_matches(slot, s_connected_peer_address, NULL)) {
        (void)save_peer_subscriptions(slot, after);
    }
}

static uint8_t peer_slot_for_identity(
    uint8_t selected_slot, const uint8_t address[ESP_BD_ADDR_LEN],
    const codex_micro_ble_identity_t *presented_identity)
{
    for (uint8_t slot = 1u; slot <= CODEX_MICRO_BLE_SLOT_COUNT; ++slot) {
        if (slot != selected_slot &&
            peer_identity_matches(slot, address, presented_identity)) {
            return slot;
        }
    }
    return 0u;
}

static codex_micro_ble_identity_t take_pairing_identity(
    const uint8_t address[ESP_BD_ADDR_LEN])
{
    codex_micro_ble_identity_t identity = {0};
    portENTER_CRITICAL(&s_state_lock);
    if (s_pairing_identity.valid == 1u &&
        (memcmp(s_pairing_identity_source, address, ESP_BD_ADDR_LEN) == 0 ||
         memcmp(s_pairing_identity.address, address, ESP_BD_ADDR_LEN) == 0)) {
        identity = s_pairing_identity;
    }
    memset(&s_pairing_identity, 0, sizeof(s_pairing_identity));
    memset(s_pairing_identity_source, 0, sizeof(s_pairing_identity_source));
    portEXIT_CRITICAL(&s_state_lock);
    return identity;
}

static esp_err_t ble_bond_exists(
    const uint8_t address[ESP_BD_ADDR_LEN], bool *exists)
{
    if (address == NULL || exists == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    *exists = false;
    const int count = esp_ble_get_bond_device_num();
    if (count < 0) {
        return ESP_FAIL;
    }
    if (count == 0) {
        return ESP_OK;
    }
    esp_ble_bond_dev_t *bonds = calloc((size_t)count, sizeof(*bonds));
    if (bonds == NULL) {
        return ESP_ERR_NO_MEM;
    }
    int queried_count = count;
    const esp_err_t result =
        esp_ble_get_bond_device_list(&queried_count, bonds);
    if (result == ESP_OK) {
        for (int index = 0; index < queried_count; ++index) {
            if (memcmp(address, bonds[index].bd_addr,
                       ESP_BD_ADDR_LEN) == 0) {
                *exists = true;
                break;
            }
        }
    }
    free(bonds);
    return result;
}

static void remove_failed_auth_bond(
    const uint8_t address[ESP_BD_ADDR_LEN])
{
    if (address == NULL || atomic_load(&s_auth_bond_removal_pending)) {
        return;
    }
    portENTER_CRITICAL(&s_state_lock);
    memcpy(s_auth_bond_removal_address, address, ESP_BD_ADDR_LEN);
    s_auth_bond_removal_attempts = 1u;
    portEXIT_CRITICAL(&s_state_lock);
    atomic_store(&s_auth_bond_removal_pending, true);
    const esp_err_t result = esp_ble_remove_bond_device(
        s_auth_bond_removal_address);
    if (result != ESP_OK) {
        atomic_store(&s_auth_bond_removal_pending, false);
        ESP_LOGE(TAG, "failed to start rejected bond removal: %s",
                 esp_err_to_name(result));
    }
}

static void publish_ble_slot_clear_result(uint8_t slot, esp_err_t error)
{
    atomic_store(&s_ble_slot_clear_result_slot, slot);
    atomic_store(&s_ble_slot_clear_result_error, error);
    atomic_store(&s_ble_slot_clear_result_pending, true);
    atomic_store(&s_ble_slot_clear_protocol_result_pending, true);
}

static esp_err_t finish_ble_slot_clear(
    uint8_t slot, const codex_micro_ble_peer_t *peer)
{
    if (!valid_ble_slot(slot) || peer == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    const bool disconnect_connected =
        atomic_load(&s_connected_peer_valid) &&
        peer_identity_matches(slot, s_connected_peer_address, NULL);
    esp_err_t result = nvs_erase_key(
        s_ble_peer_nvs, BLE_PEER_NVS_KEYS[slot - 1u]);
    if (result != ESP_OK && result != ESP_ERR_NVS_NOT_FOUND) {
        reload_ble_peer_slot(slot);
        return result;
    }
    portENTER_CRITICAL(&s_state_lock);
    memset(&s_ble_peers[slot - 1u], 0, sizeof(s_ble_peers[slot - 1u]));
    memset(&s_ble_peer_identities[slot - 1u], 0,
           sizeof(s_ble_peer_identities[slot - 1u]));
    portEXIT_CRITICAL(&s_state_lock);

    esp_err_t cleanup_error = ESP_OK;
    result = nvs_erase_key(
        s_ble_peer_nvs, BLE_PEER_IDENTITY_NVS_KEYS[slot - 1u]);
    if (result != ESP_OK && result != ESP_ERR_NVS_NOT_FOUND) {
        cleanup_error = result;
    }
    result = forget_peer_subscriptions(slot);
    if (cleanup_error == ESP_OK && result != ESP_OK) {
        cleanup_error = result;
    }
    result = forget_peer_local_address(slot);
    if (cleanup_error == ESP_OK && result != ESP_OK) {
        cleanup_error = result;
    }
    result = forget_peer_gatt_layout(slot);
    if (cleanup_error == ESP_OK && result != ESP_OK) {
        cleanup_error = result;
    }
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    result = config_store_clear_context_platform(slot);
    if (cleanup_error == ESP_OK && result != ESP_OK) {
        cleanup_error = result;
    }
#endif
    /* RAM stays on the cleared slot, ready to pair (a pairing window), while
     * NVS "active" names a remembered host if there is one: the host in use
     * before the clear (or the open window's return slot) when it still
     * exists, otherwise the lowest remembered one. Forgetting computer 2
     * while working on computer 3 must not move a restart to computer 1. */
    const ble_host_records_t records = host_records();
    const uint8_t open_return = atomic_load(&s_pairing_return_slot);
    const uint8_t origin = open_return != 0u
        ? open_return : atomic_load(&s_active_peer_slot);
    const uint8_t kept_slot =
        ble_host_policy_return_slot(&records, origin, slot);
    const uint8_t persisted_slot = kept_slot != 0u
        ? kept_slot : ble_host_policy_active_after_clear(&records, slot);
    result = persist_active_peer_slot(persisted_slot);
    if (cleanup_error == ESP_OK && result != ESP_OK) {
        cleanup_error = result;
    }
    const bool active_persisted = result == ESP_OK;

    release_active_controls();
    atomic_store(&s_ble_slot_redirect_pending, 0u);
    atomic_store(&s_ble_peer_rejection_pending, 0u);
    if (disconnect_connected) {
        (void)request_ble_disconnect(
            s_connected_peer_address,
            CODEX_MICRO_BLE_DISCONNECT_SLOT_SWITCH);
        atomic_store(&s_connected_peer_valid, false);
        atomic_store(&s_connected, false);
    }
    if (active_persisted) {
        /* As in a slot selection: cleared before the slot changes, set after
         * the advertising window restarts. */
        atomic_store(&s_pairing_return_slot, 0u);
        atomic_store(&s_ble_pairing_return_pending, 0u);
        atomic_store(&s_active_peer_slot, slot);
    }
    result = refresh_peer_filter();
    if (active_persisted && persisted_slot != slot) {
        atomic_store(&s_pairing_return_slot, persisted_slot);
    }
    if (cleanup_error == ESP_OK && result != ESP_OK) {
        cleanup_error = result;
    }
    if (cleanup_error == ESP_OK) {
        ESP_LOGI(TAG, "cleared BLE peer slot %u; pairing enabled", slot);
    }
    return cleanup_error;
}

static bool claim_pending_ble_slot_clear(
    const uint8_t expected_address[ESP_BD_ADDR_LEN], uint8_t *slot,
    codex_micro_ble_peer_t *peer)
{
    if (!atomic_load(&s_ble_slot_clear_pending) || slot == NULL ||
        peer == NULL) {
        return false;
    }
    portENTER_CRITICAL(&s_state_lock);
    *slot = s_ble_slot_clear_slot;
    *peer = s_ble_slot_clear_peer;
    portEXIT_CRITICAL(&s_state_lock);
    if (expected_address != NULL &&
        !peer_address_matches(peer, expected_address)) {
        return false;
    }
    bool expected = true;
    return atomic_compare_exchange_strong(
        &s_ble_slot_clear_pending, &expected, false);
}

static void complete_pending_ble_slot_clear(
    uint8_t slot, const codex_micro_ble_peer_t *peer,
    bool completion_succeeded, bool timeout)
{
    bool still_bonded = true;
    const esp_err_t query_error =
        ble_bond_exists(peer->address, &still_bonded);
    esp_err_t result = query_error;
    if (completion_succeeded ||
        (query_error == ESP_OK && !still_bonded)) {
        result = finish_ble_slot_clear(slot, peer);
    } else if (query_error == ESP_OK) {
        result = timeout ? ESP_ERR_TIMEOUT : ESP_FAIL;
    }
    if (timeout) {
        atomic_fetch_add(&s_ble_slot_clear_timeout_count, 1u);
    }
    note_ble_slot_op(CODEX_MICRO_BLE_SLOT_OP_CLEAR, slot, result, timeout,
                     (codex_micro_ble_slot_source_t)atomic_load(
                         &s_ble_slot_clear_source));
    publish_ble_slot_clear_result(slot, result);
}

static void poll_ble_slot_clear_timeout(void)
{
    if (!atomic_load(&s_ble_slot_clear_pending)) {
        return;
    }
    const uint32_t started = atomic_load(&s_ble_slot_clear_started_ms);
    if ((uint32_t)(monotonic_ms() - started) < BLE_SLOT_CLEAR_TIMEOUT_MS) {
        return;
    }

    uint8_t slot = 0u;
    codex_micro_ble_peer_t peer = {0};
    if (!claim_pending_ble_slot_clear(NULL, &slot, &peer)) {
        return;
    }
    complete_pending_ble_slot_clear(slot, &peer, false, true);
}

static esp_err_t refresh_peer_filter(void)
{
    reset_advertising_speed();
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
    atomic_store(&s_peer_filter_state, BLE_PEER_FILTER_IDLE);
    atomic_store(&s_peer_filter_ready, true);
    ensure_advertising();
    return ESP_OK;
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
        /* Claude Code is not restored: it comes back as Normal. */
        s_soft_restart_mode_value < CODEX_MICRO_MODE_CLAUDE_CODE) {
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
    if (!esp_hidd_ble_input_subscribed(s_hid, CODEX_MICRO_REPORT_ID)) {
        atomic_fetch_add(&s_hid_reports_dropped_unsubscribed, 1u);
        return ESP_ERR_INVALID_STATE;
    }
    const esp_err_t result = esp_hidd_dev_input_set(
        s_hid, 0, CODEX_MICRO_REPORT_ID, (uint8_t *)data, length);
    if (result != ESP_OK) {
        atomic_fetch_add(&s_hid_report_failures, 1u);
    }
    return result;
}

esp_err_t codex_micro_send_standard_report(uint8_t report_id,
                                           const uint8_t *data,
                                           size_t length)
{
    if (s_hid == NULL || data == NULL || !atomic_load(&s_connected)) {
        return ESP_ERR_INVALID_STATE;
    }
    if (!esp_hidd_ble_input_subscribed(s_hid, report_id)) {
        /* The host is not listening to this report. Report it as delivered
         * so usb_service does not queue presses and replay them all at once
         * when the host subscribes later. */
        atomic_fetch_add(&s_hid_reports_dropped_unsubscribed, 1u);
        return ESP_OK;
    }
    const esp_err_t result = esp_hidd_dev_input_set(
        s_hid, 0, report_id, (uint8_t *)data, length);
    if (result != ESP_OK) {
        atomic_fetch_add(&s_hid_report_failures, 1u);
    }
    return result;
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
    atomic_store(&s_codex_status_at_ms,
                 (uint32_t)(esp_timer_get_time() / 1000));
    atomic_store(&s_codex_status_seen, true);
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
            s_attention_slots[index] |= attention_requested;
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
                    battery_display_percent(battery.soc_percent));
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

#if CONFIG_CODEX_MICRO_BLE_FAULT_INJECTION
/*
 * TEST BUILD ONLY (tools/build.sh --ble-fault-injection). One scripted fault
 * per boot exercises the start-up recovery on a healthy board; a power-on
 * reset restarts the script. Steps: 1 one failed init, 2 three failed inits,
 * 3 panic right after the controller starts, 4 panic when the HID services
 * report ready, 5 and later no fault (step 5 is the guard's skipped boot).
 */
#define BLE_FAULT_SCRIPT_MAGIC 0x424C4646u /* "BLFF" */
typedef struct {
    uint32_t magic;
    uint32_t step;
} ble_fault_script_t;
static RTC_NOINIT_ATTR ble_fault_script_t s_ble_fault_script;
static uint32_t s_ble_fault_init_failures;

static void ble_fault_next_boot(void)
{
    if (s_ble_fault_script.magic != BLE_FAULT_SCRIPT_MAGIC ||
        s_ble_fault_script.step > 100u || esp_reset_reason() == ESP_RST_POWERON) {
        s_ble_fault_script.magic = BLE_FAULT_SCRIPT_MAGIC;
        s_ble_fault_script.step = 0;
    }
    ++s_ble_fault_script.step;
    s_ble_fault_init_failures = s_ble_fault_script.step == 1u ? 1u
                              : s_ble_fault_script.step == 2u ? 3u : 0u;
    ESP_LOGW(TAG, "BLE FAULT INJECTION test build: step %u",
             (unsigned)s_ble_fault_script.step);
}

static bool ble_fault_fail_init(void)
{
    if (s_ble_fault_init_failures == 0) {
        return false;
    }
    --s_ble_fault_init_failures;
    return true;
}

static void ble_fault_panic_at(uint32_t step)
{
    if (s_ble_fault_script.step == step) {
        ESP_LOGE(TAG, "BLE FAULT INJECTION: panic at step %u", (unsigned)step);
        abort();
    }
}
#else
static inline void ble_fault_next_boot(void) {}
static inline bool ble_fault_fail_init(void) { return false; }
static inline void ble_fault_panic_at(uint32_t step) { (void)step; }
#endif

static void ble_start_ended(void);

/* Liveness supervisor probe (stability item 2): the answer arrives as
 * ESP_GAP_BLE_GET_DEV_NAME_COMPLETE_EVT. Blocks while the BTC queue is full,
 * which only a stuck BLE host leaves full. */
static bool ble_host_liveness_probe(void)
{
    if (!atomic_load(&s_hid_services_ready) ||
        esp_bluedroid_get_status() != ESP_BLUEDROID_STATUS_ENABLED) {
        return false;
    }
    return esp_ble_gap_get_device_name() == ESP_OK;
}

static void hid_event(void *handler_args, esp_event_base_t base, int32_t id,
                      void *event_data)
{
    (void)handler_args;
    (void)base;
    esp_hidd_event_data_t *event = event_data;
    switch ((esp_hidd_event_t)id) {
    case ESP_HIDD_START_EVENT:
        atomic_store(&s_hid_ready_ms, advertising_now_ms());
        atomic_store(&s_hid_services_ready, true);
        liveness_set_probe(LIVENESS_BLE_HOST, ble_host_liveness_probe,
                           LIVENESS_BLE_HOST_TIMEOUT_MS);
        ble_fault_panic_at(4);
        ble_start_ended();
        ESP_LOGI(TAG, "BLE HID ready");
        ensure_advertising();
        break;
    case ESP_HIDD_CONNECT_EVENT:
    case ESP_HIDD_DISCONNECT_EVENT:
        /* The link state follows the GATTS events (note_link_up and
         * note_link_down). These notices come from the HID library's own
         * event task and can arrive after the pairing events of the same
         * link, or after the next link is already up. */
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
    default:
        break;
    }
}

/*
 * A host connected. Runs in the Bluetooth host task, from the GATTS
 * callback, so it always comes before the pairing and encryption events of
 * the same link. The HID library reports the connection too, but through its
 * own event task: resetting s_connected there could run after a fast host
 * had already encrypted, and the link then stayed up with no input (Windows
 * showed connected, keys and knob did nothing; CP01-441BF6FFE080,
 * 2026-11-12).
 *
 * Every service of the link gets its own connect event with the same
 * conn_id; only the first one resets the state.
 */
static void note_link_up(uint16_t conn_id)
{
    const bool same_link = atomic_load(&s_link_connected);
    s_link_conn_id = conn_id;
    if (same_link) {
        return;
    }
    atomic_store(&s_link_connected, true);
    atomic_store(&s_advertising_active, false);
    atomic_store(&s_advertising_starting, false);
    atomic_store(&s_advertising_interval_restart_pending, false);
    atomic_store(&s_connected, false);
    clear_transport_runtime_state(CODEX_TRANSPORT_BLE);
    ESP_LOGI(TAG, "BLE host connected; awaiting encrypted bond");
}

/*
 * The link ended. Same task and ordering as note_link_up, so a disconnect
 * can never be applied to the link that follows it. Later events of the same
 * disconnect (one per service) and an event of any other link change nothing.
 */
static void note_link_down(uint16_t conn_id)
{
    if (!atomic_load(&s_link_connected) || conn_id != s_link_conn_id) {
        return;
    }
    atomic_store(&s_link_connected, false);
    atomic_store(&s_connected, false);
    atomic_store(&s_connected_peer_valid, false);
    portENTER_CRITICAL(&s_state_lock);
    memset(&s_pairing_identity, 0, sizeof(s_pairing_identity));
    memset(s_pairing_identity_source, 0, sizeof(s_pairing_identity_source));
    portEXIT_CRITICAL(&s_state_lock);
    atomic_store(&s_advertising_active, false);
    atomic_store(&s_advertising_starting, false);
    clear_transport_runtime_state(CODEX_TRANSPORT_BLE);
    ESP_LOGI(TAG, "BLE host disconnected");
    atomic_store(&s_link_pairing_requested, false);
    reset_advertising_speed_after_link();
    ensure_advertising();
}

static void combined_gatts_event_handler(
    esp_gatts_cb_event_t event, esp_gatt_if_t gatts_if,
    esp_ble_gatts_cb_param_t *parameter)
{
    if (event == ESP_GATTS_CONNECT_EVT && parameter != NULL) {
        heap_checkpoint("ble_first_connect");
        note_link_up(parameter->connect.conn_id);
        portENTER_CRITICAL(&s_state_lock);
        memset(&s_pairing_identity, 0, sizeof(s_pairing_identity));
        memset(s_pairing_identity_source, 0,
               sizeof(s_pairing_identity_source));
        portEXIT_CRITICAL(&s_state_lock);
        const uint8_t slot = atomic_load(&s_active_peer_slot);
        const codex_micro_ble_peer_t peer = peer_for_slot(slot);
        const bool suspended = atomic_load(&s_ble_suspended);
        const bool rejected = peer.valid == 1u &&
            !peer_identity_matches(slot, parameter->connect.remote_bda, NULL);
        const bool remembered_elsewhere = peer.valid != 1u &&
            peer_slot_for_identity(
                slot, parameter->connect.remote_bda, NULL) != 0u;
        atomic_store(&s_link_remembered_elsewhere, remembered_elsewhere);
        atomic_store(&s_link_pairing_requested, false);
        if (!suspended && peer.valid != 1u && !remembered_elsewhere) {
            /* Side effect only: a new host reached the empty slot. A host
             * remembered in another slot keeps retrying every few seconds
             * and is refused once it has encrypted; it must not hold the
             * pairing window open, or the window would never return. */
            restart_pairing_window_timer();
        }
        if (suspended) {
            note_ble_reject(CODEX_MICRO_BLE_REJECT_SUSPENDED, slot);
        } else if (rejected) {
            const uint8_t paired_slot = peer_slot_for_identity(
                slot, parameter->connect.remote_bda, NULL);
            if (paired_slot != 0u) {
                atomic_store(&s_ble_slot_redirect_pending, paired_slot);
            } else {
                atomic_store(&s_ble_peer_rejection_pending, slot);
            }
            note_ble_reject(
                paired_slot != 0u ? CODEX_MICRO_BLE_REJECT_OTHER_SLOT
                                  : CODEX_MICRO_BLE_REJECT_UNKNOWN_HOST,
                slot);
        }
        if (suspended || rejected) {
            const esp_err_t error = request_ble_disconnect(
                parameter->connect.remote_bda,
                CODEX_MICRO_BLE_DISCONNECT_REJECT);
            if (error != ESP_OK) {
                ESP_LOGW(TAG, "BLE rejected-connect disconnect failed: %s",
                         esp_err_to_name(error));
            }
        }
    } else if (event == ESP_GATTS_DISCONNECT_EVT && parameter != NULL) {
        note_ble_disconnect_event(parameter->disconnect.reason);
        note_link_down(parameter->disconnect.conn_id);
    }
    if (s_gatts_observer != NULL &&
        s_gatts_observer((int)event, (uint16_t)gatts_if, parameter,
                         s_gatts_observer_context)) {
        return;
    }
    uint32_t subscriptions_before = 0;
    const bool track_subscriptions = event == ESP_GATTS_WRITE_EVT &&
        parameter != NULL && hid_subscriptions(&subscriptions_before);
    esp_hidd_gatts_event_handler(event, gatts_if, parameter);
    if (parameter == NULL) {
        return;
    }
    uint32_t subscriptions_after = 0;
    if (track_subscriptions && hid_subscriptions(&subscriptions_after)) {
        note_subscription_change(subscriptions_before, subscriptions_after,
                                 parameter->write.handle);
    } else if (event == ESP_GATTS_MTU_EVT) {
        atomic_store(&s_negotiated_mtu, parameter->mtu.mtu);
    } else if (event == ESP_GATTS_CONF_EVT) {
        atomic_fetch_add(&s_notify_tx_events, 1u);
        if (parameter->conf.status == ESP_GATT_OK) {
            atomic_fetch_add(&s_notify_tx_successes, 1u);
        } else {
            atomic_fetch_add(&s_notify_tx_failures, 1u);
        }
        atomic_store(&s_last_notify_tx_attr, parameter->conf.handle);
        atomic_store(&s_last_notify_tx_status, parameter->conf.status);
    } else if (event == ESP_GATTS_DISCONNECT_EVT) {
        atomic_store(&s_negotiated_mtu, 23u);
    }
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
    .adv_int_min = BLE_FAST_ADVERTISING_MIN,
    .adv_int_max = BLE_FAST_ADVERTISING_MAX,
    .adv_type = ADV_TYPE_IND,
    .own_addr_type = BLE_ADDR_TYPE_PUBLIC,
    .channel_map = ADV_CHNL_ALL,
    .adv_filter_policy = ADV_FILTER_ALLOW_SCAN_ANY_CON_ANY,
};

/* Diagnostics only: how long the accepted host took to get in. */
static void note_ble_connect_time(void)
{
    if (!atomic_exchange(&s_advertising_episode_valid, false)) {
        return;
    }
    const uint32_t elapsed_ms =
        advertising_now_ms() - atomic_load(&s_advertising_episode_start_ms);
    atomic_store(&s_ble_connect_last_ms, elapsed_ms);
    if (elapsed_ms > atomic_load(&s_ble_connect_max_ms)) {
        atomic_store(&s_ble_connect_max_ms, elapsed_ms);
    }
}

/* Encryption with the stored keys failed: one side lost or replaced them. */
static bool auth_failure_means_key_mismatch(uint8_t reason)
{
    return reason == ESP_AUTH_SMP_ENC_FAIL ||
           reason == ESP_AUTH_SMP_DIV_NOT_AVAIL;
}

/*
 * An unencrypted link is useless (every HID characteristic needs
 * encryption) and, while it stays up, the device does not advertise. Drop it
 * so the host can retry. Bonds are never removed here (docs/ble-bond-fix.md);
 * when the slot's own host can no longer encrypt, ask the user to re-pair.
 */
static void handle_auth_failure(const uint8_t address[ESP_BD_ADDR_LEN],
                                uint8_t reason)
{
    const uint8_t slot = atomic_load(&s_active_peer_slot);
    const codex_micro_ble_peer_t peer = peer_for_slot(slot);
    if (valid_ble_slot(slot) && peer.valid == 1u &&
        auth_failure_means_key_mismatch(reason) &&
        peer_identity_matches(slot, address, NULL)) {
        atomic_store(&s_ble_reauth_pending, slot);
        atomic_fetch_add(&s_reauth_prompts, 1u);
    }
    if (atomic_load(&s_ble_pending_disconnect_source) !=
        CODEX_MICRO_BLE_DISCONNECT_NONE) {
        return; /* Already being dropped (rejected host, suspend, slot op). */
    }
    uint8_t target[ESP_BD_ADDR_LEN];
    memcpy(target, address, ESP_BD_ADDR_LEN);
    if (request_ble_disconnect(target, CODEX_MICRO_BLE_DISCONNECT_AUTH_FAIL) ==
        ESP_OK) {
        atomic_fetch_add(&s_auth_fail_disconnects, 1u);
    }
}

static void gap_event(esp_gap_ble_cb_event_t event,
                      esp_ble_gap_cb_param_t *parameter)
{
    switch (event) {
    case ESP_GAP_BLE_GET_DEV_NAME_COMPLETE_EVT:
        /* Liveness probe answer: the BTC/BTU tasks still process work. */
        liveness_beat(LIVENESS_BLE_HOST);
        break;
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
        if (parameter->adv_start_cmpl.status == ESP_BT_STATUS_SUCCESS) {
            atomic_fetch_add(&s_advertising_starts, 1u);
            if (!atomic_load(&s_advertising_episode_valid)) {
                atomic_store(&s_advertising_episode_start_ms,
                             advertising_now_ms());
                atomic_store(&s_advertising_episode_valid, true);
            }
        } else {
            atomic_fetch_add(&s_advertising_start_failures, 1u);
            atomic_store(&s_advertising_last_start_status,
                         (int)parameter->adv_start_cmpl.status);
        }
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
        if (atomic_load(&s_advertising_active)) {
            verify_advertising_address();
        }
        if (!atomic_load(&s_advertising_active)) {
            /* Review B8: no immediate retry; the main-loop watchdog retries
             * with backoff instead of a tight start/fail loop. */
            ESP_LOGW(TAG, "BLE advertising start failed: %d",
                     parameter->adv_start_cmpl.status);
        }
        break;
    case ESP_GAP_BLE_SET_STATIC_RAND_ADDR_EVT:
        if (parameter->set_rand_addr_cmpl.status != ESP_BT_STATUS_SUCCESS) {
            atomic_fetch_add(&s_local_address_set_rejections, 1u);
            ESP_LOGW(TAG, "BLE stack refused the random address: %d",
                     parameter->set_rand_addr_cmpl.status);
        }
        break;
    case ESP_GAP_BLE_ADV_STOP_COMPLETE_EVT:
        atomic_fetch_add(&s_advertising_stops, 1u);
        if (parameter->adv_stop_cmpl.status != ESP_BT_STATUS_SUCCESS) {
            ESP_LOGW(TAG, "BLE advertising stop completion failed: %d",
                     parameter->adv_stop_cmpl.status);
        }
        atomic_store(&s_advertising_active, false);
        atomic_store(&s_advertising_starting, false);
        if (atomic_load(&s_peer_filter_state) ==
            BLE_PEER_FILTER_STOPPING) {
            atomic_store(&s_advertising_interval_restart_pending, false);
            atomic_store(&s_peer_filter_state, BLE_PEER_FILTER_IDLE);
            atomic_store(&s_peer_filter_ready, true);
            ensure_advertising();
        } else {
            atomic_store(&s_advertising_interval_restart_pending, false);
            /* USB may disconnect before the earlier BLE stop completes. */
            if (!atomic_load(&s_ble_suspended) &&
                !atomic_load(&s_connected)) {
                ensure_advertising();
            }
        }
        break;
    case ESP_GAP_BLE_KEY_EVT:
        if (parameter->ble_security.ble_key.key_type == ESP_LE_KEY_PID) {
            const esp_ble_pid_keys_t *pid =
                &parameter->ble_security.ble_key.p_key_value.pid_key;
            portENTER_CRITICAL(&s_state_lock);
            s_pairing_identity.valid = 1u;
            s_pairing_identity.address_type = (uint8_t)pid->addr_type;
            memcpy(s_pairing_identity.address, pid->static_addr,
                   ESP_BD_ADDR_LEN);
            memcpy(s_pairing_identity_source,
                   parameter->ble_security.ble_key.bd_addr,
                   ESP_BD_ADDR_LEN);
            portEXIT_CRITICAL(&s_state_lock);
        }
        break;
    case ESP_GAP_BLE_SEC_REQ_EVT:
    {
        const uint8_t slot = atomic_load(&s_active_peer_slot);
        const codex_micro_ble_peer_t peer = peer_for_slot(slot);
        const bool suspended = atomic_load(&s_ble_suspended);
        const bool accept = !suspended &&
            (peer.valid != 1u || peer_identity_matches(
                slot, parameter->ble_security.ble_req.bd_addr, NULL));
        if (!accept) {
            const uint8_t paired_slot = peer_slot_for_identity(
                slot, parameter->ble_security.ble_req.bd_addr, NULL);
            if (suspended) {
                note_ble_reject(CODEX_MICRO_BLE_REJECT_SUSPENDED, slot);
            } else if (paired_slot != 0u) {
                atomic_store(&s_ble_slot_redirect_pending, paired_slot);
                note_ble_reject(CODEX_MICRO_BLE_REJECT_OTHER_SLOT, slot);
            } else {
                atomic_store(&s_ble_peer_rejection_pending, slot);
                note_ble_reject(CODEX_MICRO_BLE_REJECT_UNKNOWN_HOST, slot);
            }
        }
        if (accept && peer.valid != 1u &&
            peer_slot_for_identity(
                slot, parameter->ble_security.ble_req.bd_addr, NULL) == 0u) {
            /* Side effect only: a new host is pairing with the empty slot
             * (not one remembered in another slot, see the connect event). */
            restart_pairing_window_timer();
        }
        if (accept) {
            atomic_store(&s_link_pairing_requested, true);
        }
        (void)esp_ble_gap_security_rsp(
            parameter->ble_security.ble_req.bd_addr, accept);
        if (!accept) {
            (void)request_ble_disconnect(
                parameter->ble_security.ble_req.bd_addr,
                CODEX_MICRO_BLE_DISCONNECT_REJECT);
        }
        break;
    }
    case ESP_GAP_BLE_AUTH_CMPL_EVT:
        if (parameter->ble_security.auth_cmpl.success) {
            heap_checkpoint("ble_first_auth");
            if (atomic_load(&s_ble_suspended)) {
                const uint8_t slot = atomic_load(&s_active_peer_slot);
                note_ble_reject(CODEX_MICRO_BLE_REJECT_SUSPENDED, slot);
                const esp_err_t error = request_ble_disconnect(
                    parameter->ble_security.auth_cmpl.bd_addr,
                    CODEX_MICRO_BLE_DISCONNECT_REJECT);
                if (error != ESP_OK) {
                    ESP_LOGW(TAG, "BLE suspend disconnect failed: %s",
                             esp_err_to_name(error));
                }
                break;
            }
            uint8_t slot = atomic_load(&s_active_peer_slot);
            codex_micro_ble_peer_t peer = peer_for_slot(slot);
            codex_micro_ble_identity_t identity =
                peer_identity_for_slot(slot);
            bool returned_to_own_slot = false;
            const codex_micro_ble_identity_t presented_identity =
                take_pairing_identity(
                    parameter->ble_security.auth_cmpl.bd_addr);
            const uint8_t paired_slot =
                peer_slot_for_identity(
                    slot, parameter->ble_security.auth_cmpl.bd_addr,
                    &presented_identity);
            if (peer.valid == 1u &&
                !peer_identity_matches(
                    slot, parameter->ble_security.auth_cmpl.bd_addr,
                    &presented_identity)) {
                if (paired_slot != 0u) {
                    atomic_store(&s_ble_slot_redirect_pending, paired_slot);
                    note_ble_reject(
                        CODEX_MICRO_BLE_REJECT_OTHER_SLOT, slot);
                    ESP_LOGW(TAG,
                             "rejecting BLE peer assigned to slot %u",
                             paired_slot);
                } else {
                    atomic_store(&s_ble_peer_rejection_pending, slot);
                    note_ble_reject(
                        CODEX_MICRO_BLE_REJECT_UNKNOWN_HOST, slot);
                    ESP_LOGW(TAG, "rejecting BLE peer outside active slot %u",
                             slot);
                    remove_failed_auth_bond(
                        parameter->ble_security.auth_cmpl.bd_addr);
                }
                (void)request_ble_disconnect(
                    parameter->ble_security.auth_cmpl.bd_addr,
                    CODEX_MICRO_BLE_DISCONNECT_REJECT);
                break;
            }
            /*
             * The pairing window is open (empty active slot) and the host
             * that just paired is one remembered in another slot. If it
             * paired afresh on this link, the user removed this device on
             * that computer and is adding it again: it is the same computer,
             * so go to its own slot and keep it, with the keys the stack has
             * just stored for it. Refusing it here left the computer with new
             * keys and a link dropped on every attempt (Windows 11 showed
             * connected/not connected in turn, 2026-11-12).
             *
             * A remembered host that only reconnects with its old keys (no
             * pairing request on this link) is still refused below: it is the
             * neighbour retrying, and it must not take the window from the
             * new host the user is adding.
             */
            if (paired_slot != 0u && peer.valid != 1u &&
                (atomic_load(&s_link_pairing_requested) ||
                 presented_identity.valid == 1u)) {
                const esp_err_t persisted =
                    persist_active_peer_slot(paired_slot);
                if (persisted != ESP_OK) {
                    atomic_fetch_add(&s_active_slot_persist_failures, 1u);
                    ESP_LOGE(TAG, "failed to persist active BLE slot %u: %s",
                             paired_slot, esp_err_to_name(persisted));
                }
                atomic_store(&s_pairing_return_slot, 0u);
                atomic_store(&s_ble_pairing_return_pending, 0u);
                atomic_store(&s_link_remembered_elsewhere, false);
                atomic_store(&s_active_peer_slot, paired_slot);
                ESP_LOGI(TAG,
                         "BLE peer of slot %u paired again in the pairing "
                         "window of slot %u; back on its own slot",
                         paired_slot, slot);
                slot = paired_slot;
                peer = peer_for_slot(slot);
                identity = peer_identity_for_slot(slot);
                returned_to_own_slot = true;
            } else if (paired_slot != 0u) {
                ESP_LOGW(TAG,
                         "rejecting BLE peer already assigned to slot %u",
                         paired_slot);
                /* No "USE HOST n" while the active slot is empty (pairing
                 * window): the user is adding a host, and the window returns
                 * to the previous one by itself. The other redirect sites are
                 * reached only with a paired active slot. */
                if (peer.valid == 1u) {
                    atomic_store(&s_ble_slot_redirect_pending, paired_slot);
                } else {
                    /* Recognised only now, by its identity address. */
                    atomic_store(&s_link_remembered_elsewhere, true);
                }
                note_ble_reject(CODEX_MICRO_BLE_REJECT_OTHER_SLOT, slot);
                (void)request_ble_disconnect(
                    parameter->ble_security.auth_cmpl.bd_addr,
                    CODEX_MICRO_BLE_DISCONNECT_REJECT);
                break;
            }
            note_ble_connect_time();
            const bool pairing_record_changed = peer.valid != 1u ||
                (presented_identity.valid == 1u &&
                 (!peer_address_matches(
                      &peer, parameter->ble_security.auth_cmpl.bd_addr) ||
                  !identity_address_matches(
                      &identity, presented_identity.address)));
            const bool slot_was_empty = peer.valid != 1u;
            if (pairing_record_changed) {
                peer.valid = 1u;
                peer.address_type =
                    (uint8_t)parameter->ble_security.auth_cmpl.addr_type;
                memcpy(peer.address,
                       parameter->ble_security.auth_cmpl.bd_addr,
                       ESP_BD_ADDR_LEN);
                if (presented_identity.valid == 1u) {
                    identity = presented_identity;
                }
                const esp_err_t result =
                    persist_peer_identity(slot, &peer, &identity);
                note_ble_slot_op(
                    CODEX_MICRO_BLE_SLOT_OP_PAIR_SAVE, slot, result, false,
                    CODEX_MICRO_BLE_SLOT_SOURCE_PAIRING);
                if (result != ESP_OK) {
                    ESP_LOGE(TAG, "failed to persist BLE peer slot %u: %s",
                             slot, esp_err_to_name(result));
                    if (slot_was_empty) {
                        remove_failed_auth_bond(
                            parameter->ble_security.auth_cmpl.bd_addr);
                    }
                    (void)request_ble_disconnect(
                        parameter->ble_security.auth_cmpl.bd_addr,
                        CODEX_MICRO_BLE_DISCONNECT_REJECT);
                    break;
                }
                portENTER_CRITICAL(&s_state_lock);
                s_ble_peers[slot - 1u] = peer;
                s_ble_peer_identities[slot - 1u] = identity;
                portEXIT_CRITICAL(&s_state_lock);
                close_pairing_window_after_pair_save(slot);
                (void)refresh_peer_filter();
                ESP_LOGI(TAG, "BLE peer assigned to slot %u", slot);
            }
            if (returned_to_own_slot && !pairing_record_changed) {
                /* Same address and identity as before: only the keys are
                 * new, and the stack holds them. Still a pairing in the
                 * history of this slot. */
                note_ble_slot_op(
                    CODEX_MICRO_BLE_SLOT_OP_PAIR_SAVE, slot, ESP_OK, false,
                    CODEX_MICRO_BLE_SLOT_SOURCE_PAIRING);
            }
            /* Fresh keys: the host subscribes again and reads the services
             * again, as after any new pairing. */
            const bool new_pairing =
                pairing_record_changed || returned_to_own_slot;
            memcpy(s_connected_peer_address,
                   parameter->ble_security.auth_cmpl.bd_addr,
                   ESP_BD_ADDR_LEN);
            atomic_store(&s_connected_peer_valid, true);
            adopt_link_local_address(slot);
            restore_peer_subscriptions(slot, new_pairing);
            sync_peer_gatt_layout(slot, new_pairing,
                                  parameter->ble_security.auth_cmpl.bd_addr);
            clear_transport_runtime_state(CODEX_TRANSPORT_BLE);
            atomic_store(&s_connected, true);
            publish_latest_battery_level();
            ESP_LOGI(TAG, "encrypted BLE bond ready on slot %u", slot);
        } else {
            (void)take_pairing_identity(
                parameter->ble_security.auth_cmpl.bd_addr);
            atomic_store(&s_connected, false);
            atomic_fetch_add(&s_auth_fail_events, 1u);
            atomic_store(&s_last_auth_fail_reason,
                         parameter->ble_security.auth_cmpl.fail_reason);
            ESP_LOGW(TAG, "BLE pairing failed: 0x%x",
                     parameter->ble_security.auth_cmpl.fail_reason);
            handle_auth_failure(parameter->ble_security.auth_cmpl.bd_addr,
                                parameter->ble_security.auth_cmpl.fail_reason);
        }
        break;
    case ESP_GAP_BLE_REMOVE_BOND_DEV_COMPLETE_EVT:
        if (atomic_load(&s_ble_slot_clear_pending)) {
            uint8_t slot = 0u;
            codex_micro_ble_peer_t peer = {0};
            if (claim_pending_ble_slot_clear(
                    parameter->remove_bond_dev_cmpl.bd_addr,
                    &slot, &peer)) {
                complete_pending_ble_slot_clear(
                    slot, &peer,
                    parameter->remove_bond_dev_cmpl.status ==
                        ESP_BT_STATUS_SUCCESS,
                    false);
            }
        } else if (atomic_load(&s_auth_bond_removal_pending) &&
                   memcmp(s_auth_bond_removal_address,
                          parameter->remove_bond_dev_cmpl.bd_addr,
                          ESP_BD_ADDR_LEN) == 0) {
            if (parameter->remove_bond_dev_cmpl.status ==
                ESP_BT_STATUS_SUCCESS) {
                atomic_store(&s_auth_bond_removal_pending, false);
            } else if (s_auth_bond_removal_attempts < 2u) {
                ++s_auth_bond_removal_attempts;
                const esp_err_t retry = esp_ble_remove_bond_device(
                    s_auth_bond_removal_address);
                if (retry != ESP_OK) {
                    atomic_store(&s_auth_bond_removal_pending, false);
                    ESP_LOGE(TAG, "rejected bond removal retry failed: %s",
                             esp_err_to_name(retry));
                }
            } else {
                atomic_store(&s_auth_bond_removal_pending, false);
                ESP_LOGE(TAG, "rejected bond removal did not complete");
            }
        }
        break;
    default:
        break;
    }
}

static void load_peer_local_address(uint8_t slot)
{
    esp_bd_addr_t address = {0};
    size_t length = sizeof(address);
    const bool valid = s_ble_peer_nvs != 0 &&
        nvs_get_blob(s_ble_peer_nvs,
                     BLE_PEER_LOCAL_ADDRESS_NVS_KEYS[slot - 1u], address,
                     &length) == ESP_OK &&
        length == sizeof(address);
    portENTER_CRITICAL(&s_state_lock);
    s_ble_local_address_valid[slot - 1u] = valid;
    memcpy(s_ble_local_addresses[slot - 1u], address, sizeof(address));
    portEXIT_CRITICAL(&s_state_lock);
}

static esp_err_t forget_peer_local_address(uint8_t slot)
{
    portENTER_CRITICAL(&s_state_lock);
    s_ble_local_address_valid[slot - 1u] = false;
    memset(s_ble_local_addresses[slot - 1u], 0,
           sizeof(s_ble_local_addresses[slot - 1u]));
    portEXIT_CRITICAL(&s_state_lock);
    const esp_err_t result = nvs_erase_key(
        s_ble_peer_nvs, BLE_PEER_LOCAL_ADDRESS_NVS_KEYS[slot - 1u]);
    return result == ESP_ERR_NVS_NOT_FOUND ? ESP_OK : result;
}

/*
 * Chooses the address for the next advertising start: the selected slot's
 * own one, the public one for a slot paired by older firmware, or a fresh
 * one while the slot waits for a new host.
 */
static esp_err_t apply_advertising_address(void)
{
    const uint8_t slot = atomic_load(&s_active_peer_slot);
    esp_bd_addr_t address = {0};
    bool random = false;
    bool waits_to_pair = true;
    portENTER_CRITICAL(&s_state_lock);
    if (valid_ble_slot(slot) && s_ble_peers[slot - 1u].valid == 1u) {
        waits_to_pair = false;
        random = s_ble_local_address_valid[slot - 1u];
        memcpy(address, s_ble_local_addresses[slot - 1u], sizeof(address));
    } else if (s_pairing_local_address_valid) {
        random = true;
        memcpy(address, s_pairing_local_address, sizeof(address));
    }
    portEXIT_CRITICAL(&s_state_lock);
    if (waits_to_pair && !random) {
        esp_fill_random(address, sizeof(address));
        address[0] |= 0xC0u; /* static random address */
        random = true;
        portENTER_CRITICAL(&s_state_lock);
        memcpy(s_pairing_local_address, address, sizeof(address));
        s_pairing_local_address_valid = true;
        portEXIT_CRITICAL(&s_state_lock);
    }
    if (random) {
        const esp_err_t result = esp_ble_gap_set_rand_addr(address);
        if (result != ESP_OK) {
            return result;
        }
    }
    portENTER_CRITICAL(&s_state_lock);
    s_advertised_local_address_random = random;
    memcpy(s_advertised_local_address, address, sizeof(address));
    portEXIT_CRITICAL(&s_state_lock);
    s_adv_params.own_addr_type =
        random ? BLE_ADDR_TYPE_RANDOM : BLE_ADDR_TYPE_PUBLIC;
    return ESP_OK;
}

/*
 * The address the stack advertises and connects on right now. This is the
 * stack's own record, not what apply_advertising_address() asked for: the two
 * differ whenever the stack refused the request.
 */
static bool local_address_in_use(esp_bd_addr_t address, bool *random)
{
    uint8_t type = BLE_ADDR_TYPE_PUBLIC;
    if (esp_ble_gap_get_local_used_addr(address, &type) != ESP_OK) {
        return false;
    }
    *random = type == BLE_ADDR_TYPE_RANDOM;
    return true;
}

/*
 * Called once advertising has started. If the stack is not on the address
 * that was asked for, the host of the selected slot cannot find the device
 * (it knows the other address), and a host pairing now would learn an address
 * this firmware does not expect. Advertising is turned off and on again: with
 * advertising off the stack takes the address. After a few attempts in a row
 * it is left as it is; adopt_link_local_address() records what was used.
 */
static void verify_advertising_address(void)
{
    esp_bd_addr_t in_use;
    bool in_use_random;
    if (!local_address_in_use(in_use, &in_use_random)) {
        return;
    }
    portENTER_CRITICAL(&s_state_lock);
    const bool as_asked = in_use_random == s_advertised_local_address_random &&
        (!in_use_random || memcmp(in_use, s_advertised_local_address,
                                  sizeof(in_use)) == 0);
    portEXIT_CRITICAL(&s_state_lock);
    if (as_asked) {
        atomic_store(&s_local_address_retries, 0u);
        return;
    }
    atomic_fetch_add(&s_local_address_apply_failures, 1u);
    if (atomic_fetch_add(&s_local_address_retries, 1u) >=
        BLE_LOCAL_ADDRESS_RETRY_LIMIT) {
        ESP_LOGE(TAG, "BLE advertising is not on the address asked for");
        return;
    }
    ESP_LOGW(TAG, "BLE address not applied; restarting advertising");
    atomic_store(&s_peer_filter_ready, false);
    atomic_store(&s_peer_filter_state, BLE_PEER_FILTER_STOPPING);
    if (esp_ble_gap_stop_advertising() != ESP_OK) {
        atomic_store(&s_peer_filter_state, BLE_PEER_FILTER_IDLE);
        atomic_store(&s_peer_filter_ready, true);
    }
}

/*
 * The host of this link knows the device by the address it connected to.
 * Keep that address with the host's slot, also when the host paired through
 * the pairing window and went back to the slot it already had. The address
 * is read back from the stack: recording the one that was asked for left a
 * host unable to find the device whenever the stack had refused the request
 * (CP01-441BF6FFE080, 2026-11-12).
 */
static void adopt_link_local_address(uint8_t slot)
{
    if (!valid_ble_slot(slot)) {
        return;
    }
    esp_bd_addr_t address;
    bool random;
    bool as_asked = true;
    const bool known = local_address_in_use(address, &random);
    portENTER_CRITICAL(&s_state_lock);
    if (known) {
        as_asked = random == s_advertised_local_address_random &&
            (!random || memcmp(address, s_advertised_local_address,
                               sizeof(address)) == 0);
    } else {
        random = s_advertised_local_address_random;
        memcpy(address, s_advertised_local_address, sizeof(address));
    }
    if (!as_asked) {
        atomic_fetch_add(&s_local_address_adopt_mismatches, 1u);
    }
    const bool changed =
        random != s_ble_local_address_valid[slot - 1u] ||
        (random && memcmp(address, s_ble_local_addresses[slot - 1u],
                          sizeof(address)) != 0);
    if (changed) {
        s_ble_local_address_valid[slot - 1u] = random;
        memcpy(s_ble_local_addresses[slot - 1u], address, sizeof(address));
    }
    if (random && s_pairing_local_address_valid &&
        memcmp(address, s_pairing_local_address, sizeof(address)) == 0) {
        s_pairing_local_address_valid = false; /* taken by this slot */
    }
    portEXIT_CRITICAL(&s_state_lock);
    if (!changed) {
        return;
    }
    esp_err_t result = random
        ? nvs_set_blob(s_ble_peer_nvs,
                       BLE_PEER_LOCAL_ADDRESS_NVS_KEYS[slot - 1u], address,
                       sizeof(address))
        : nvs_erase_key(s_ble_peer_nvs,
                        BLE_PEER_LOCAL_ADDRESS_NVS_KEYS[slot - 1u]);
    if (result == ESP_ERR_NVS_NOT_FOUND) {
        result = ESP_OK;
    }
    if (result == ESP_OK) {
        result = nvs_commit(s_ble_peer_nvs);
    }
    if (result != ESP_OK) {
        ESP_LOGE(TAG, "failed to store the address of BLE slot %u: %s", slot,
                 esp_err_to_name(result));
    }
}

static esp_err_t start_advertising(void)
{
    if (atomic_load(&s_ble_suspended) || atomic_load(&s_advertising_held)) {
        return ESP_ERR_INVALID_STATE;
    }
    /* A link that is up, encrypted or not, is all this device serves: it is
     * a single-connection HID peripheral. Advertising beside it lets a host
     * remembered in another slot connect as a second link; refusing that
     * host then drops the state of the first one (seen right after a new
     * host paired, when the pairing was saved before s_connected was set). */
    if (atomic_load(&s_connected) || atomic_load(&s_link_connected) ||
        atomic_load(&s_advertising_active) ||
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
    atomic_store(&s_advertising_starting_ms, advertising_now_ms());
    const bool slow = atomic_load(&s_slow_advertising);
    s_adv_params.adv_int_min = slow ? BLE_SLOW_ADVERTISING_MIN
                                    : BLE_FAST_ADVERTISING_MIN;
    s_adv_params.adv_int_max = slow ? BLE_SLOW_ADVERTISING_MAX
                                    : BLE_FAST_ADVERTISING_MAX;
    esp_err_t result = apply_advertising_address();
    if (result == ESP_OK) {
        result = esp_ble_gap_start_advertising(&s_adv_params);
    }
    if (result != ESP_OK) {
        atomic_store(&s_advertising_starting, false);
    }
    return result;
}

static uint32_t advertising_now_ms(void)
{
    return (uint32_t)((uint64_t)esp_timer_get_time() / 1000U);
}

static bool active_slot_waits_to_pair(void)
{
    return peer_for_slot(atomic_load(&s_active_peer_slot)).valid != 1u;
}

static void reset_advertising_speed(void)
{
    const uint32_t duration_ms = active_slot_waits_to_pair()
        ? BLE_PAIRING_FAST_ADVERTISING_DURATION_MS
        : BLE_FAST_ADVERTISING_DURATION_MS;
    atomic_store(&s_slow_advertising, false);
    atomic_store(&s_advertising_interval_restart_pending, false);
    atomic_store(&s_fast_advertising_until_ms,
                 advertising_now_ms() + duration_ms);
    /* Diagnostics only: the next advertising start begins a new episode. */
    atomic_store(&s_advertising_episode_valid, false);
}

/*
 * Advertising restarts fast after every link. A refused host of another
 * slot does not extend the pairing window, though: the time the window had
 * left stays as it was, so that host retrying nearby cannot keep the window
 * open for ever.
 */
static void reset_advertising_speed_after_link(void)
{
    const uint32_t window_until = atomic_load(&s_fast_advertising_until_ms);
    const bool keep_window =
        atomic_exchange(&s_link_remembered_elsewhere, false) &&
        active_slot_waits_to_pair();
    reset_advertising_speed();
    if (keep_window) {
        atomic_store(&s_fast_advertising_until_ms, window_until);
    }
}

static void poll_advertising_speed(void)
{
    if (atomic_load(&s_ble_suspended) || atomic_load(&s_connected) ||
        !atomic_load(&s_advertising_active) ||
        atomic_load(&s_slow_advertising)) {
        return;
    }
    const uint32_t now_ms = advertising_now_ms();
    if ((int32_t)(now_ms - atomic_load(&s_fast_advertising_until_ms)) < 0) {
        return;
    }
    bool expected = false;
    if (!atomic_compare_exchange_strong(&s_slow_advertising, &expected,
                                        true)) {
        return;
    }
    atomic_store(&s_advertising_interval_restart_pending, true);
    const esp_err_t result = esp_ble_gap_stop_advertising();
    if (result != ESP_OK) {
        atomic_store(&s_advertising_interval_restart_pending, false);
        atomic_store(&s_slow_advertising, false);
        atomic_store(&s_fast_advertising_until_ms, now_ms + 1000U);
        ESP_LOGW(TAG, "BLE advertising interval transition failed: %s",
                 esp_err_to_name(result));
    }
}

/*
 * A local input while an empty slot waits to pair restarts the pairing fast
 * window; slow advertising is restarted with the fast interval. Paired slots,
 * links and suspended BLE are left alone. Without an input, the pairing
 * window is checked for its end (poll_pairing_window).
 */
static void poll_pairing_input_boost(void)
{
    if (!atomic_exchange(&s_local_input_pending, false)) {
        /* No key since the last poll: the pairing window may have ended. */
        poll_pairing_window();
        return;
    }
    if (atomic_load(&s_ble_suspended) || atomic_load(&s_link_connected) ||
        !active_slot_waits_to_pair()) {
        return;
    }
    if (atomic_load(&s_advertising_starting)) {
        atomic_store(&s_local_input_pending, true); /* Retry once started. */
        return;
    }
    atomic_store(&s_fast_advertising_until_ms,
                 advertising_now_ms() + BLE_PAIRING_FAST_ADVERTISING_DURATION_MS);
    if (!atomic_exchange(&s_slow_advertising, false)) {
        return; /* Already fast: the window was extended. */
    }
    if (atomic_load(&s_advertising_active) &&
        !atomic_load(&s_advertising_interval_restart_pending)) {
        atomic_store(&s_advertising_interval_restart_pending, true);
        const esp_err_t result = esp_ble_gap_stop_advertising();
        if (result != ESP_OK) {
            atomic_store(&s_advertising_interval_restart_pending, false);
            atomic_store(&s_slow_advertising, true);
            ESP_LOGW(TAG, "BLE pairing advertising boost failed: %s",
                     esp_err_to_name(result));
            return;
        }
    }
    atomic_fetch_add(&s_advertising_pairing_boosts, 1u);
}

/*
 * A host that reaches an empty active slot is someone pairing: give it the
 * full pairing window again, as a local key press does, so the window does
 * not expire under it. Advertising is not restarted here; the link that
 * caused this stops it, and the disconnect restarts it fast.
 */
static void restart_pairing_window_timer(void)
{
    atomic_store(&s_fast_advertising_until_ms,
                 advertising_now_ms() + BLE_PAIRING_FAST_ADVERTISING_DURATION_MS);
    atomic_store(&s_slow_advertising, false);
}

/*
 * End of the pairing window: nobody paired with the empty slot while it
 * advertised fast, so go back to the host that was active before. Nothing is
 * deleted or written; NVS "active" already names that host. Runs once per
 * window: the return slot is claimed before the switch.
 */
static void poll_pairing_window(void)
{
    uint8_t return_slot = atomic_load(&s_pairing_return_slot);
    if (return_slot == 0u) {
        return;
    }
    /* A key press not yet applied to the window, or an advertising restart
     * still in flight (the slow transition itself): decide on a later poll. */
    if (atomic_load(&s_local_input_pending) ||
        atomic_load(&s_advertising_interval_restart_pending) ||
        atomic_load(&s_advertising_starting) ||
        !atomic_load(&s_peer_filter_ready)) {
        return;
    }
    const ble_host_records_t records = host_records();
    const ble_host_window_t window = {
        .active_slot = atomic_load(&s_active_peer_slot),
        .return_slot = return_slot,
        /* Slow advertising is entered only after the pairing fast window ran
         * out while advertising, never while suspended; every restart of the
         * window (key, link, resume) clears it. */
        .fast_window_elapsed = atomic_load(&s_slow_advertising),
        .link_connected = atomic_load(&s_link_connected) ||
                          atomic_load(&s_connected) ||
                          atomic_load(&s_connected_peer_valid),
        .suspended = atomic_load(&s_ble_suspended),
        .busy = atomic_load(&s_ble_slot_clear_pending) ||
                atomic_load(&s_auth_bond_removal_pending),
    };
    if (!ble_host_policy_window_expired(&records, &window)) {
        return;
    }
    /* A slot selection or clear on another task resets the return slot
     * first; then it wins and this return is dropped. */
    if (!atomic_compare_exchange_strong(&s_pairing_return_slot, &return_slot,
                                        0u)) {
        return;
    }
    atomic_store(&s_ble_slot_redirect_pending, 0u);
    atomic_store(&s_ble_peer_rejection_pending, 0u);
    atomic_store(&s_active_peer_slot, return_slot);
    const esp_err_t result = refresh_peer_filter();
    atomic_store(&s_ble_pairing_return_pending, return_slot);
    atomic_fetch_add(&s_pairing_window_returns, 1u);
    note_ble_slot_op(CODEX_MICRO_BLE_SLOT_OP_AUTO_RETURN, return_slot, result,
                     false, CODEX_MICRO_BLE_SLOT_SOURCE_AUTO);
    ESP_LOGI(TAG, "pairing window ended; back on BLE slot %u", return_slot);
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
    atomic_fetch_add(&s_advertising_retries_exhausted, 1u);
    atomic_store(&s_advertising_retry_running, false);
    vTaskDelete(NULL);
}

static void ensure_advertising(void)
{
    /* Releasing the hold calls back here. */
    if (atomic_load(&s_ble_suspended) || atomic_load(&s_advertising_held)) {
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
    atomic_store(&s_advertising_retry_started_ms, advertising_now_ms());
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
    portENTER_CRITICAL(&s_state_lock);
    if (s_radial_wait_center) {
        if (!active) {
            s_radial_wait_center = false;
            codex_micro_radial_filter_reset(&s_radial_filter);
            codex_micro_radial_release_gate_reset(&s_radial_release_gate);
            s_radial_filter_generation++;
        }
        portEXIT_CRITICAL(&s_state_lock);
        return;
    }
    portEXIT_CRITICAL(&s_state_lock);
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
    desktop_agent_status_release_keys();
    portENTER_CRITICAL(&s_state_lock);
    codex_agent_press_model_clear(&s_agent_press);
    const bool radial_active = s_radial_active;
    const float radial_angle = s_radial_angle;
    const codex_transport_t radial_transport = s_radial_transport;
    s_radial_active = false;
    s_radial_wait_center = true;
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

/* BLE start-up recovery (ble_recovery_model.h). The guard survives warm
 * resets; its check word rejects power-on or brownout garbage. */
static RTC_NOINIT_ATTR ble_crash_guard_t s_ble_crash_guard;
static ble_recovery_result_t s_ble_recovery_result;
static uint32_t s_ble_recovery_attempts;
static esp_err_t s_ble_recovery_last_error;
static uint32_t s_ble_crash_loop_count;

static bool reset_was_crash(esp_reset_reason_t reason)
{
    return reason == ESP_RST_PANIC || reason == ESP_RST_INT_WDT ||
           reason == ESP_RST_TASK_WDT || reason == ESP_RST_WDT;
}

/*
 * Called once per boot before BLE starts; true means skip BLE this boot.
 * The controller task asserts asynchronously, so start-up stays "in
 * progress" until the services are ready or start-up fails cleanly.
 */
static bool ble_crash_loop_guard_trips(void)
{
    uint32_t count = 0;
    bool was_starting = false;
    (void)ble_crash_guard_load(&s_ble_crash_guard, &count, &was_starting);
    const esp_reset_reason_t reason = esp_reset_reason();
    s_ble_crash_loop_count = ble_crash_guard_next(
        count, reason == ESP_RST_POWERON, reset_was_crash(reason), was_starting);
    const bool skip = ble_crash_guard_skip_ble(s_ble_crash_loop_count);
    ble_crash_guard_store(&s_ble_crash_guard, s_ble_crash_loop_count, !skip);
    return skip;
}

/* BLE start-up ended without a crash: services ready or a clean error. */
static void ble_start_ended(void)
{
    ble_crash_guard_store(&s_ble_crash_guard, 0, false);
}

static esp_err_t controller_init_once(esp_bt_controller_config_t *controller)
{
    return ble_fault_fail_init() ? ESP_ERR_NO_MEM : esp_bt_controller_init(controller);
}

/* A failed init already powers the BT domain down, so each retry is one BT
 * power cycle without restarting the chip. */
static esp_err_t init_controller_with_retry(void)
{
    esp_bt_controller_config_t controller = BT_CONTROLLER_INIT_CONFIG_DEFAULT();
    esp_err_t result = controller_init_once(&controller);
    while (result != ESP_OK && s_ble_recovery_attempts < BLE_RECOVERY_INIT_RETRIES) {
        ESP_LOGW(TAG, "BLE controller init failed (%s); retrying",
                 esp_err_to_name(result));
        s_ble_recovery_last_error = result;
        vTaskDelay(pdMS_TO_TICKS(BLE_RECOVERY_RETRY_DELAY_MS));
        ++s_ble_recovery_attempts;
        controller = (esp_bt_controller_config_t)BT_CONTROLLER_INIT_CONFIG_DEFAULT();
        result = controller_init_once(&controller);
    }
    if (result != ESP_OK) {
        s_ble_recovery_last_error = result;
        s_ble_recovery_result = BLE_RECOVERY_RESULT_FAILED;
    } else if (s_ble_recovery_attempts > 0) {
        s_ble_recovery_result = BLE_RECOVERY_RESULT_RECOVERED_IN_PLACE;
    }
    return result;
}

const char *codex_micro_ble_device_name(void)
{
    return s_ble_device_name;
}

/* Everything after the crash-loop guard; errors return to codex_micro_init. */
static esp_err_t start_ble_stack(void)
{
    esp_err_t result;
    result = esp_bt_controller_mem_release(ESP_BT_MODE_CLASSIC_BT);
    if (result != ESP_OK && result != ESP_ERR_INVALID_STATE) {
        return result;
    }
    result = init_controller_with_retry();
    if (result != ESP_OK) {
        return result;
    }
    result = esp_bt_controller_enable(ESP_BT_MODE_BLE);
    if (result != ESP_OK) {
        return result;
    }
    ble_fault_panic_at(3);
    esp_bluedroid_config_t bluedroid_config =
        BT_BLUEDROID_INIT_CONFIG_DEFAULT();
    result = esp_bluedroid_init_with_cfg(&bluedroid_config);
    if (result != ESP_OK) {
        return result;
    }
    result = esp_bluedroid_enable();
    if (result != ESP_OK) {
        return result;
    }
    result = esp_ble_gap_register_callback(gap_event);
    if (result != ESP_OK) {
        return result;
    }
    /*
     * esp_hidd_dev_init() registers the GATT applications, but Bluedroid
     * requires the application to install the HID GATTS dispatcher first.
     * Without it macOS can open the radio link but never discovers the
     * encrypted HID database, so pairing remains stuck with encryption off.
     */
    result = esp_ble_gatts_register_callback(combined_gatts_event_handler);
    if (result != ESP_OK) {
        return result;
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
        return result;
    }
    result = esp_ble_gap_set_device_name(s_ble_device_name);
    if (result != ESP_OK) {
        return result;
    }
    result = esp_ble_gap_config_adv_data(&s_adv_data);
    if (result != ESP_OK) {
        return result;
    }
    result = esp_ble_gap_config_adv_data(&s_scan_response_data);
    if (result != ESP_OK) {
        return result;
    }

    result = refresh_peer_filter();
    if (result != ESP_OK) {
        return result;
    }

    result = esp_hidd_dev_init(&s_hid_config, ESP_HID_TRANSPORT_BLE,
                               hid_event, &s_hid);
    if (result != ESP_OK) {
        return result;
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
    return ESP_OK;
}

esp_err_t codex_micro_init(const char *serial, bool initially_suspended)
{
    if (serial == NULL || serial[0] == '\0') {
        return s_init_error = ESP_ERR_INVALID_ARG;
    }
    atomic_store(&s_hid_services_ready, false);
    s_hid_config.serial_number = serial;
    atomic_store(&s_connected, false);
    atomic_store(&s_usb_connected, false);
    atomic_store(&s_ble_suspended, initially_suspended);
    atomic_store(&s_mode, CODEX_MICRO_MODE_NORMAL);
    atomic_store(&s_advertising_configured, false);
    atomic_store(&s_scan_response_configured, false);
    atomic_store(&s_advertising_active, false);
    atomic_store(&s_advertising_starting, false);
    atomic_store(&s_advertising_retry_running, false);
    atomic_store(&s_peer_filter_ready, false);
    atomic_store(&s_peer_filter_state, BLE_PEER_FILTER_IDLE);
    atomic_store(&s_active_peer_slot, 1u);
    atomic_store(&s_link_remembered_elsewhere, false);
    atomic_store(&s_link_pairing_requested, false);
    atomic_store(&s_pairing_return_slot, 0u);
    atomic_store(&s_ble_pairing_return_pending, 0u);
    atomic_store(&s_active_slot_persist_failures, 0u);
    atomic_store(&s_pairing_window_returns, 0u);
    for (size_t index = 0; index <= CODEX_MICRO_BLE_REJECT_SUSPENDED;
         ++index) {
        atomic_store(&s_ble_reject_reason_counts[index], 0u);
    }
    atomic_store(&s_advertising_episode_valid, false);
    atomic_store(&s_ble_connect_last_ms, 0u);
    atomic_store(&s_ble_connect_max_ms, 0u);
    atomic_store(&s_ble_slot_redirect_pending, 0u);
    atomic_store(&s_ble_peer_rejection_pending, 0u);
    atomic_store(&s_ble_slot_clear_pending, false);
    atomic_store(&s_ble_slot_clear_result_pending, false);
    atomic_store(&s_ble_slot_clear_protocol_result_pending, false);
    atomic_store(&s_auth_bond_removal_pending, false);
    atomic_store(&s_ble_slot_clear_result_slot, 0u);
    atomic_store(&s_ble_slot_clear_result_error, ESP_OK);
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
    atomic_store(&s_link_connected, false);
    atomic_store(&s_advertising_recoveries, 0);
    atomic_store(&s_local_input_pending, false);
    atomic_store(&s_advertising_input_pending, false);
    memset(&s_advertising_backoff, 0, sizeof(s_advertising_backoff));
    atomic_store(&s_advertising_pairing_boosts, 0);
    atomic_store(&s_service_change_pending_slot, 0u);
    atomic_store(&s_service_change_sent, 0);
    atomic_store(&s_service_change_skipped, 0);
    atomic_store(&s_auth_fail_disconnects, 0);
    atomic_store(&s_ble_reauth_pending, 0u);
    atomic_store(&s_reauth_prompts, 0);
    atomic_store(&s_orphan_bonds_removed, 0);
    atomic_store(&s_orphan_cleanup_skipped, 0);
    s_orphan_cleanup_done = false;
    {
        /* Any firmware change may change the GATT database. */
        const esp_app_desc_t *app = esp_app_get_description();
        s_gatt_layout_id = app == NULL ? 0u :
            ((uint32_t)app->app_elf_sha256[0] << 24) |
            ((uint32_t)app->app_elf_sha256[1] << 16) |
            ((uint32_t)app->app_elf_sha256[2] << 8) |
            (uint32_t)app->app_elf_sha256[3];
    }
    atomic_store(&s_subscription_restores, 0);
    atomic_store(&s_subscription_default_restores, 0);
    atomic_store(&s_subscription_save_failures, 0);
    atomic_store(&s_hid_reports_dropped_unsubscribed, 0);
    atomic_store(&s_hid_report_failures, 0);
    portENTER_CRITICAL(&s_state_lock);
    codex_agent_press_model_init(&s_agent_press);
    memset(&s_pairing_identity, 0, sizeof(s_pairing_identity));
    memset(s_pairing_identity_source, 0, sizeof(s_pairing_identity_source));
    s_ble_slot_clear_slot = 0u;
    memset(&s_ble_slot_clear_peer, 0, sizeof(s_ble_slot_clear_peer));
    memset(s_auth_bond_removal_address, 0,
           sizeof(s_auth_bond_removal_address));
    s_auth_bond_removal_attempts = 0u;
    portEXIT_CRITICAL(&s_state_lock);
    clear_runtime_state();
    load_soft_restart_mode();
    reset_advertising_speed();
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
    /* Stability item 6: safe mode keeps BLE off (before any BLE storage is
     * read: the config store is closed in safe mode); USB stays usable. */
    if (boot_guard_safe_mode()) {
        ESP_LOGW(TAG, "safe mode; BLE not started");
        s_ble_recovery_result = BLE_RECOVERY_RESULT_SKIPPED_SAFE_MODE;
        return s_init_error = ESP_ERR_INVALID_STATE;
    }
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
    result = storage_recovery_init_partition(
        NVS_DEFAULT_PART_NAME, STORAGE_RECOVERY_PARTITION_BLE);
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

    ble_fault_next_boot();
    if (ble_crash_loop_guard_trips()) {
        ESP_LOGE(TAG, "BLE start-up crashed %u boots in a row; skipping BLE",
                 (unsigned)s_ble_crash_loop_count);
        s_ble_recovery_result = BLE_RECOVERY_RESULT_SKIPPED_AFTER_CRASH_LOOP;
        return s_init_error = ESP_ERR_INVALID_STATE;
    }
    result = start_ble_stack();
    if (result != ESP_OK) {
        ble_start_ended();
    }
    return s_init_error = result;
}

void codex_micro_hold_advertising_for_services(void)
{
    atomic_store(&s_advertising_held, true);
}

void codex_micro_release_advertising_hold(void)
{
    if (atomic_exchange(&s_advertising_held, false)) {
        ensure_advertising();
    }
}

void codex_micro_register_service_change_sender(
    codex_micro_service_change_fn sender, void *context)
{
    s_service_change_context = context;
    s_service_change_fn = sender;
}

void codex_micro_note_service_change_result(bool delivered)
{
    const uint8_t slot = atomic_exchange(&s_service_change_pending_slot, 0u);
    if (delivered && valid_ble_slot(slot)) {
        save_peer_gatt_layout(slot, s_gatt_layout_id);
    }
}

bool codex_micro_take_ble_reauth_needed(uint8_t *slot)
{
    const uint8_t pending = atomic_exchange(&s_ble_reauth_pending, 0u);
    if (slot != NULL) {
        *slot = pending;
    }
    return valid_ble_slot(pending);
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

/*
 * The retry task gives up after a few attempts. Re-check periodically that
 * the device is reachable: when nothing holds it back, it must be either
 * connected or advertising.
 */
static void poll_advertising_watchdog(void)
{
    const uint32_t now_ms = advertising_now_ms();
    const bool input = atomic_exchange(&s_advertising_input_pending, false);
    if (!input && (uint32_t)(now_ms - s_last_advertising_watchdog_ms) <
                      BLE_ADVERTISING_WATCHDOG_MS) {
        return;
    }
    s_last_advertising_watchdog_ms = now_ms;
    if (atomic_load(&s_link_connected) || atomic_load(&s_connected) ||
        atomic_load(&s_advertising_active)) {
        ble_adv_backoff_reset(&s_advertising_backoff);
        return;
    }
    /* A start that never completed, or a retry task that never finished,
     * must not hold advertising back forever. */
    if (atomic_load(&s_advertising_starting) &&
        now_ms - atomic_load(&s_advertising_starting_ms) >= BLE_ADVERTISING_FLAG_TIMEOUT_MS) {
        atomic_store(&s_advertising_starting, false);
        atomic_fetch_add(&s_advertising_stale_flags, 1u);
    }
    if (atomic_load(&s_advertising_retry_running) &&
        now_ms - atomic_load(&s_advertising_retry_started_ms) >=
            BLE_ADVERTISING_FLAG_TIMEOUT_MS) {
        atomic_store(&s_advertising_retry_running, false);
        atomic_fetch_add(&s_advertising_stale_flags, 1u);
    }
    if (s_hid == NULL || !atomic_load(&s_hid_services_ready) ||
        atomic_load(&s_ble_suspended) || atomic_load(&s_advertising_held) ||
        atomic_load(&s_advertising_starting) ||
        atomic_load(&s_advertising_retry_running)) {
        return;
    }
    /* Back off while restarts keep failing; a key press retries at once. */
    if (!ble_adv_backoff_take(&s_advertising_backoff, now_ms, input)) {
        return;
    }
    atomic_fetch_add(&s_advertising_recoveries, 1u);
    ESP_LOGW(TAG, "BLE not advertising while idle; restarting");
    if (!atomic_load(&s_advertising_configured)) {
        (void)esp_ble_gap_config_adv_data(&s_adv_data);
    }
    if (!atomic_load(&s_scan_response_configured)) {
        (void)esp_ble_gap_config_adv_data(&s_scan_response_data);
    }
    if (!atomic_load(&s_peer_filter_ready) &&
        atomic_load(&s_peer_filter_state) == BLE_PEER_FILTER_STOPPING) {
        /* The stop completion never arrived; nothing is advertising. */
        atomic_store(&s_peer_filter_state, BLE_PEER_FILTER_IDLE);
        atomic_store(&s_peer_filter_ready, true);
    }
    reset_advertising_speed();
    ensure_advertising();
}

static void poll_advertising_hold(void)
{
    if (!atomic_load(&s_advertising_held) ||
        !atomic_load(&s_hid_services_ready)) {
        return;
    }
    if ((uint32_t)(advertising_now_ms() - atomic_load(&s_hid_ready_ms)) >=
        BLE_ADVERTISING_HOLD_TIMEOUT_MS) {
        ESP_LOGW(TAG, "BLE services not reported ready; advertising anyway");
        codex_micro_release_advertising_hold();
    }
}

static bool same_address(const uint8_t a[ESP_BD_ADDR_LEN],
                         const uint8_t b[ESP_BD_ADDR_LEN])
{
    return memcmp(a, b, ESP_BD_ADDR_LEN) == 0;
}

static bool bond_matches_slot(const esp_ble_bond_dev_t *bond,
                              const codex_micro_ble_peer_t *peer,
                              const codex_micro_ble_identity_t *identity)
{
    const bool has_identity_key =
        (bond->bond_key.key_mask & ESP_LE_KEY_PID) != 0;
    if (peer->valid == 1u &&
        (same_address(bond->bd_addr, peer->address) ||
         (has_identity_key &&
          same_address(bond->bond_key.pid_key.static_addr, peer->address)))) {
        return true;
    }
    return identity->valid == 1u &&
           (same_address(bond->bd_addr, identity->address) ||
            (has_identity_key &&
             same_address(bond->bond_key.pid_key.static_addr,
                          identity->address)));
}

/*
 * Bluedroid keeps at most CONFIG_BT_SMP_MAX_BONDS and silently evicts one
 * when a new pairing exceeds it, possibly a slot's bond. Bonds that belong to
 * no slot (a pairing rejected while BLE was suspended, an interrupted slot
 * clear) only take up that space, so remove them once per boot. If any
 * paired slot cannot be matched to a bond, the mapping is uncertain and
 * nothing is removed.
 */
static void cleanup_orphan_bonds(void)
{
    s_orphan_cleanup_done = true;
    const int count = esp_ble_get_bond_device_num();
    if (count <= 0) {
        return;
    }
    esp_ble_bond_dev_t *bonds = calloc((size_t)count, sizeof(*bonds));
    bool *owned = calloc((size_t)count, sizeof(*owned));
    if (bonds == NULL || owned == NULL) {
        free(bonds);
        free(owned);
        return;
    }
    int queried = count;
    if (esp_ble_get_bond_device_list(&queried, bonds) != ESP_OK) {
        queried = 0;
    }
    bool slot_matched[CODEX_MICRO_BLE_SLOT_COUNT] = {false};
    for (int index = 0; index < queried; ++index) {
        for (uint8_t slot = 1u; slot <= CODEX_MICRO_BLE_SLOT_COUNT; ++slot) {
            const codex_micro_ble_peer_t peer = peer_for_slot(slot);
            const codex_micro_ble_identity_t identity =
                peer_identity_for_slot(slot);
            if (bond_matches_slot(&bonds[index], &peer, &identity)) {
                owned[index] = true;
                slot_matched[slot - 1u] = true;
            }
        }
    }
    bool mapping_certain = queried > 0;
    for (uint8_t slot = 1u; slot <= CODEX_MICRO_BLE_SLOT_COUNT; ++slot) {
        if (peer_for_slot(slot).valid == 1u && !slot_matched[slot - 1u]) {
            mapping_certain = false;
        }
    }
    if (!mapping_certain) {
        atomic_fetch_add(&s_orphan_cleanup_skipped, 1u);
        ESP_LOGW(TAG, "BLE bond cleanup skipped: slot/bond mapping uncertain");
    } else {
        for (int index = 0; index < queried; ++index) {
            if (!owned[index] &&
                esp_ble_remove_bond_device(bonds[index].bd_addr) == ESP_OK) {
                atomic_fetch_add(&s_orphan_bonds_removed, 1u);
            }
        }
    }
    free(owned);
    free(bonds);
}

static void poll_orphan_bond_cleanup(void)
{
    if (s_orphan_cleanup_done || s_hid == NULL ||
        !atomic_load(&s_hid_services_ready) ||
        atomic_load(&s_link_connected) ||
        atomic_load(&s_ble_slot_clear_pending) ||
        atomic_load(&s_auth_bond_removal_pending)) {
        return;
    }
    cleanup_orphan_bonds();
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
    poll_ble_slot_clear_timeout();
    poll_pairing_input_boost();
    poll_advertising_speed();
    poll_advertising_hold();
    poll_orphan_bond_cleanup();
    poll_advertising_watchdog();
    restore_soft_restart_mode_if_ready();
    if (atomic_load(&s_transport_wait_neutral) &&
        codex_micro_connected() && board_keys_neutral()) {
        atomic_store(&s_transport_wait_neutral, false);
    }
    poll_radial_joystick();
}

void codex_micro_prepare_soft_restart(void)
{
    const codex_micro_mode_t mode = codex_micro_mode();
    s_soft_restart_mode_value =
        mode >= CODEX_MICRO_MODE_NORMAL &&
                mode < CODEX_MICRO_MODE_CLAUDE_CODE
            ? (uint32_t)mode : (uint32_t)CODEX_MICRO_MODE_NORMAL;
    s_soft_restart_mode_magic = CODEX_MICRO_SOFT_RESTART_MODE_MAGIC;
}

void codex_micro_note_local_input(void)
{
    atomic_store(&s_local_input_pending, true);
    atomic_store(&s_advertising_input_pending, true);
}

void codex_micro_set_input_suppressed(bool suppressed)
{
    const bool was_suppressed =
        atomic_exchange(&s_input_suppressed, suppressed);
    if (suppressed && !was_suppressed) {
        release_active_controls();
    }
}

void codex_micro_set_ble_suspended_for_source(
    bool suspended, codex_micro_ble_disconnect_source_t source)
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
            const esp_err_t error = request_ble_disconnect(
                s_connected_peer_address, source);
            if (error != ESP_OK) {
                ESP_LOGW(TAG, "BLE suspend disconnect failed: %s",
                         esp_err_to_name(error));
            }
            atomic_store(&s_connected_peer_valid, false);
        }
        if (atomic_load(&s_advertising_active) ||
            atomic_load(&s_advertising_starting)) {
            const esp_err_t error = esp_ble_gap_stop_advertising();
            if (error != ESP_OK) {
                ESP_LOGW(TAG, "BLE suspend advertising stop failed: %s",
                         esp_err_to_name(error));
            }
        }
        return;
    }
    reset_advertising_speed();
    ensure_advertising();
}

void codex_micro_set_ble_suspended(bool suspended)
{
    codex_micro_set_ble_suspended_for_source(
        suspended, CODEX_MICRO_BLE_DISCONNECT_STANDBY);
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
    return codex_micro_select_ble_slot_from(slot,
                                            CODEX_MICRO_BLE_SLOT_SOURCE_PROTOCOL);
}

esp_err_t codex_micro_select_ble_slot_from(uint8_t slot,
                                           codex_micro_ble_slot_source_t source)
{
    if (!valid_ble_slot(slot)) {
        return ESP_ERR_INVALID_ARG;
    }
    if (s_ble_peer_nvs == 0) {
        return ESP_ERR_INVALID_STATE;
    }
    if (atomic_load(&s_ble_slot_clear_pending)) {
        return ESP_ERR_INVALID_STATE;
    }

    const uint8_t current = atomic_load(&s_active_peer_slot);
    const codex_micro_ble_peer_t selected = peer_for_slot(slot);
    if (slot == current && atomic_load(&s_connected_peer_valid) &&
        peer_identity_matches(slot, s_connected_peer_address, NULL)) {
        return ESP_OK;
    }

    /*
     * An empty slot chosen while another slot holds a host is a pairing
     * window: RAM only. NVS "active" keeps naming a host, so a restart or an
     * expired window lands on a host instead of an empty slot. The slot is
     * written once a host pairs (close_pairing_window_after_pair_save).
     */
    const ble_host_records_t records = host_records();
    const bool persists = ble_host_policy_select_persists(&records, slot);
    /* A window that is already open keeps its return slot: pressing an empty
     * slot's combo again must not move the return to a different host. */
    const uint8_t open_return = atomic_load(&s_pairing_return_slot);
    const uint8_t origin = open_return != 0u ? open_return : current;
    const uint8_t return_slot = persists
        ? 0u : ble_host_policy_return_slot(&records, origin, slot);
    if (persists) {
        const esp_err_t persisted = persist_active_peer_slot(slot);
        if (persisted != ESP_OK) {
            return persisted;
        }
    }

    release_active_controls();
    /* Cleared before the slot changes and set after the advertising window
     * restarts: the main-loop poll never pairs the new slot with the previous
     * slot's elapsed window. */
    atomic_store(&s_pairing_return_slot, 0u);
    atomic_store(&s_ble_pairing_return_pending, 0u);
    atomic_store(&s_ble_slot_redirect_pending, 0u);
    atomic_store(&s_ble_peer_rejection_pending, 0u);
    atomic_store(&s_active_peer_slot, slot);
    atomic_store(&s_connected, false);
    if (atomic_load(&s_connected_peer_valid)) {
        (void)request_ble_disconnect(
            s_connected_peer_address,
            CODEX_MICRO_BLE_DISCONNECT_SLOT_SWITCH);
        atomic_store(&s_connected_peer_valid, false);
    }
    const esp_err_t result = refresh_peer_filter();
    atomic_store(&s_pairing_return_slot, return_slot);
    /* Pressing the combo of the slot that is already active (key repeat, or
     * the user pressing again) changes nothing worth a history entry: eight
     * entries are all there is, and they exist to show who changed a
     * pairing. A failure is still recorded. */
    if (slot != current || result != ESP_OK) {
        note_ble_slot_op(
            CODEX_MICRO_BLE_SLOT_OP_SELECT, slot, result, false, source);
    }
    if (result == ESP_OK) {
        ESP_LOGI(TAG, "selected BLE peer slot %u (%s%s)", slot,
                 selected.valid == 1u ? "paired" : "pairing",
                 persists ? "" : ", not saved until a host pairs");
    }
    return result;
}

esp_err_t codex_micro_clear_ble_slot(uint8_t slot)
{
    return codex_micro_clear_ble_slot_from(slot,
                                           CODEX_MICRO_BLE_SLOT_SOURCE_PROTOCOL);
}

esp_err_t codex_micro_clear_ble_slot_from(uint8_t slot,
                                          codex_micro_ble_slot_source_t source)
{
    if (!valid_ble_slot(slot)) {
        return ESP_ERR_INVALID_ARG;
    }
    if (s_ble_peer_nvs == 0) {
        return ESP_ERR_INVALID_STATE;
    }
    bool expected = false;
    if (!atomic_compare_exchange_strong(
            &s_ble_slot_clear_pending, &expected, true)) {
        return ESP_ERR_INVALID_STATE;
    }
    atomic_store(&s_ble_slot_clear_started_ms, monotonic_ms());
    atomic_store(&s_ble_slot_clear_source, (uint8_t)source);
    codex_micro_ble_peer_t peer = peer_for_slot(slot);
    portENTER_CRITICAL(&s_state_lock);
    s_ble_slot_clear_slot = slot;
    s_ble_slot_clear_peer = peer;
    portEXIT_CRITICAL(&s_state_lock);

    bool bonded = false;
    esp_err_t result = peer.valid == 1u
        ? ble_bond_exists(peer.address, &bonded) : ESP_OK;
    if (result != ESP_OK) {
        atomic_store(&s_ble_slot_clear_pending, false);
        note_ble_slot_op(
            CODEX_MICRO_BLE_SLOT_OP_CLEAR, slot, result, false, source);
        return result;
    }
    if (!bonded) {
        result = finish_ble_slot_clear(slot, &peer);
        atomic_store(&s_ble_slot_clear_pending, false);
        note_ble_slot_op(
            CODEX_MICRO_BLE_SLOT_OP_CLEAR, slot, result, false, source);
        publish_ble_slot_clear_result(slot, result);
        return result;
    }
    result = esp_ble_remove_bond_device(peer.address);
    if (result != ESP_OK) {
        atomic_store(&s_ble_slot_clear_pending, false);
        note_ble_slot_op(
            CODEX_MICRO_BLE_SLOT_OP_CLEAR, slot, result, false, source);
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
        (void)request_ble_disconnect(
            s_connected_peer_address,
            CODEX_MICRO_BLE_DISCONNECT_SLOT_SWITCH);
        atomic_store(&s_connected_peer_valid, false);
    }

    for (size_t index = 0; index < CODEX_MICRO_BLE_SLOT_COUNT; ++index) {
        const esp_err_t identity_erased =
            nvs_erase_key(s_ble_peer_nvs,
                          BLE_PEER_IDENTITY_NVS_KEYS[index]);
        if (identity_erased != ESP_OK &&
            identity_erased != ESP_ERR_NVS_NOT_FOUND) {
            return identity_erased;
        }
        const esp_err_t erased =
            nvs_erase_key(s_ble_peer_nvs, BLE_PEER_NVS_KEYS[index]);
        if (erased != ESP_OK && erased != ESP_ERR_NVS_NOT_FOUND) {
            return erased;
        }
        const esp_err_t local_address_erased =
            forget_peer_local_address((uint8_t)(index + 1u));
        if (local_address_erased != ESP_OK) {
            return local_address_erased;
        }
        const esp_err_t subscriptions_erased =
            forget_peer_subscriptions((uint8_t)(index + 1u));
        if (subscriptions_erased != ESP_OK) {
            return subscriptions_erased;
        }
        const esp_err_t layout_erased =
            forget_peer_gatt_layout((uint8_t)(index + 1u));
        if (layout_erased != ESP_OK) {
            return layout_erased;
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
    memset(s_ble_peer_identities, 0, sizeof(s_ble_peer_identities));
    memset(s_connected_peer_address, 0, sizeof(s_connected_peer_address));
    portEXIT_CRITICAL(&s_state_lock);
    atomic_store(&s_pairing_return_slot, 0u);
    atomic_store(&s_ble_pairing_return_pending, 0u);
    atomic_store(&s_active_peer_slot, 1u);
    atomic_store(&s_ble_slot_redirect_pending, 0u);
    atomic_store(&s_ble_peer_rejection_pending, 0u);
    atomic_store(&s_ble_slot_clear_pending, false);
    atomic_store(&s_ble_slot_clear_result_pending, false);
    atomic_store(&s_ble_slot_clear_protocol_result_pending, false);
    atomic_store(&s_auth_bond_removal_pending, false);
    s_soft_restart_mode_pending = false;
    s_soft_restart_mode_magic = 0;
    s_soft_restart_mode_value = CODEX_MICRO_MODE_NORMAL;
    atomic_store(&s_mode, CODEX_MICRO_MODE_NORMAL);

    int bond_count = esp_ble_get_bond_device_num();
    if (bond_count > 0) {
        esp_ble_bond_dev_t *bonds =
            calloc((size_t)bond_count, sizeof(*bonds));
        if (bonds == NULL) {
            return ESP_ERR_NO_MEM;
        }
        result = esp_ble_get_bond_device_list(&bond_count, bonds);
        if (result != ESP_OK) {
            free(bonds);
            return result;
        }
        for (int index = 0; index < bond_count; ++index) {
            result = esp_ble_remove_bond_device(bonds[index].bd_addr);
            if (result != ESP_OK) {
                free(bonds);
                return result;
            }
        }
        free(bonds);
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
    return peer_identity_matches(slot, s_connected_peer_address, NULL);
}

bool codex_micro_ble_pairing_window(uint8_t *slot, uint8_t *return_slot)
{
    const ble_host_records_t records = host_records();
    const uint8_t active = codex_micro_active_ble_slot();
    const uint8_t stored = atomic_load(&s_pairing_return_slot);
    const bool any_host =
        records.paired[0] || records.paired[1] || records.paired[2];
    const bool open = !records.paired[active - 1u] && any_host;
    if (slot != NULL) {
        *slot = open ? active : 0u;
    }
    if (return_slot != NULL) {
        *return_slot = open && valid_ble_slot(stored) && stored != active &&
                       records.paired[stored - 1u] ? stored : 0u;
    }
    return open;
}

bool codex_micro_take_ble_pairing_return(uint8_t *slot)
{
    const uint8_t pending =
        atomic_exchange(&s_ble_pairing_return_pending, 0u);
    if (!valid_ble_slot(pending)) {
        return false;
    }
    if (slot != NULL) {
        *slot = pending;
    }
    return true;
}

uint8_t codex_micro_ble_free_slot(void)
{
    const ble_host_records_t records = host_records();
    return ble_host_policy_free_slot(&records);
}

uint8_t codex_micro_ble_next_host_slot(void)
{
    const ble_host_records_t records = host_records();
    return ble_host_policy_next_host(&records, codex_micro_active_ble_slot());
}

bool codex_micro_take_ble_slot_redirect(uint8_t *slot)
{
    if (atomic_load(&s_ble_slot_redirect_pending) == 0u) {
        return false;
    }
    const uint8_t pending =
        atomic_exchange(&s_ble_slot_redirect_pending, 0u);
    if (!valid_ble_slot(pending)) {
        return false;
    }
    if (slot != NULL) {
        *slot = pending;
    }
    return true;
}

bool codex_micro_take_ble_peer_rejection(uint8_t *slot)
{
    const uint8_t pending =
        atomic_exchange(&s_ble_peer_rejection_pending, 0u);
    if (!valid_ble_slot(pending)) {
        return false;
    }
    if (slot != NULL) {
        *slot = pending;
    }
    return true;
}

bool codex_micro_take_ble_slot_clear_result(
    uint8_t *slot, esp_err_t *error)
{
    if (!atomic_exchange(&s_ble_slot_clear_result_pending, false)) {
        return false;
    }
    if (slot != NULL) {
        *slot = atomic_load(&s_ble_slot_clear_result_slot);
    }
    if (error != NULL) {
        *error = (esp_err_t)atomic_load(&s_ble_slot_clear_result_error);
    }
    return true;
}

bool codex_micro_take_ble_slot_clear_protocol_result(
    uint8_t *slot, esp_err_t *error)
{
    if (!atomic_exchange(&s_ble_slot_clear_protocol_result_pending, false)) {
        return false;
    }
    if (slot != NULL) {
        *slot = atomic_load(&s_ble_slot_clear_result_slot);
    }
    if (error != NULL) {
        *error = (esp_err_t)atomic_load(&s_ble_slot_clear_result_error);
    }
    return true;
}

codex_micro_mode_t codex_micro_mode(void)
{
    return (codex_micro_mode_t)atomic_load(&s_mode);
}

codex_micro_mode_t codex_micro_toggle_mode(void)
{
    const bool eda_supported =
        codex_micro_active_key_layout() == CODEX_MICRO_KEY_LAYOUT_REV_A;
    const codex_micro_mode_t current = codex_micro_mode();
    /* Claude Code is no longer in the double-click cycle: Claude
     * is shown through the shared desktop status source in Normal mode. */
    const codex_micro_mode_t next = codex_micro_next_mode(
        current, true, eda_supported, false);
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

static bool codex_agent_keys_enabled(codex_micro_mode_t mode)
{
    return mode == CODEX_MICRO_MODE_NORMAL ||
           mode == CODEX_MICRO_MODE_CODEX;
}

void codex_micro_clear_agent_press(void)
{
    portENTER_CRITICAL(&s_state_lock);
    codex_agent_press_model_clear(&s_agent_press);
    portEXIT_CRITICAL(&s_state_lock);
}

static bool normal_agent_focus_selected(void)
{
    config_normal_agent_behavior_t behavior;
    return config_store_get_normal_agent_behavior(&behavior) == ESP_OK &&
           behavior == CONFIG_NORMAL_AGENT_BEHAVIOR_OPEN_CONVERSATION;
}

codex_agent_press_snapshot_t codex_micro_get_agent_press(void)
{
    const uint64_t now_ms = (uint64_t)esp_timer_get_time() / 1000u;
    portENTER_CRITICAL(&s_state_lock);
    codex_agent_press_snapshot_t snapshot =
        codex_agent_press_model_read(&s_agent_press, now_ms);
    portEXIT_CRITICAL(&s_state_lock);
    if (snapshot.agent < 0) {
        return snapshot;
    }
    const codex_transport_t transport =
        snapshot.transport == CODEX_AGENT_PRESS_TRANSPORT_USB
            ? CODEX_TRANSPORT_USB : CODEX_TRANSPORT_BLE;
    const codex_micro_mode_t mode = codex_micro_mode();
    if ((mode != CODEX_MICRO_MODE_CODEX &&
         !(mode == CODEX_MICRO_MODE_NORMAL && normal_agent_focus_selected())) ||
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
    const codex_micro_mode_t mode = codex_micro_mode();
    if (!codex_agent_keys_enabled(mode)) {
        return false;
    }

    size_t key_index = 0;
    const bool is_key = board_control_key_index(event->control, &key_index);
    const codex_micro_key_mapping_t mapping = is_key
        ? codex_micro_map_key_index(codex_micro_active_key_layout(), key_index)
        : (codex_micro_key_mapping_t){0};
    if (mode == CODEX_MICRO_MODE_NORMAL &&
        (event->control == BOARD_CONTROL_ENCODER_CW ||
         event->control == BOARD_CONTROL_ENCODER_CCW) &&
        desktop_agent_status_handle_encoder(
            event->control == BOARD_CONTROL_ENCODER_CW ? 1 : -1,
            event->pressed)) {
        return true;
    }
    if (mode == CODEX_MICRO_MODE_NORMAL &&
        (!is_key || mapping.kind != CODEX_MICRO_KEY_VENDOR ||
         mapping.agent == CODEX_MICRO_AGENT_NONE)) {
        return false;
    }
    /* An active desktop lease owns its (non-`codex`) Agent keys regardless of
     * the Codex connection, the profile preference and the Vendor HID result. */
    if (mode == CODEX_MICRO_MODE_NORMAL &&
        desktop_agent_status_handle_key(mapping.agent, event->pressed)) {
        /* A lease can arrive while an ordinary Agent key is held. Its up
         * edge must still release the command sent to the previous source. */
        portENTER_CRITICAL(&s_state_lock);
        const bool held = s_ble_press_active[event->control];
        const codex_transport_t transport = s_press_transport[event->control];
        s_ble_press_active[event->control] = false;
        s_press_transport[event->control] = CODEX_TRANSPORT_AUTO;
        portEXIT_CRITICAL(&s_state_lock);
        if (held && transport != CODEX_TRANSPORT_AUTO &&
            transport_connected(transport)) {
            (void)send_key(mapping.vendor_key, 0, mapping.agent, transport);
        }
        return true;
    }
    config_normal_agent_behavior_t normal_behavior =
        CONFIG_NORMAL_AGENT_BEHAVIOR_UNSELECTED;
    if (mode == CODEX_MICRO_MODE_NORMAL) {
        portENTER_CRITICAL(&s_state_lock);
        const bool held = s_ble_press_active[event->control];
        portEXIT_CRITICAL(&s_state_lock);
        if (!event->pressed && !held) {
            return true;
        }
        if (event->pressed &&
            (config_store_get_normal_agent_behavior(&normal_behavior) != ESP_OK ||
             normal_behavior == CONFIG_NORMAL_AGENT_BEHAVIOR_STATUS_ONLY)) {
            /* Unknown persistence state cannot enable the legacy output. */
            return true;
        }
    }
    if (!codex_micro_input_ready()) {
        return true;
    }

    if (is_key) {
        if (mapping.kind == CODEX_MICRO_KEY_STANDARD_HID) {
            return false;
        }
        if (mapping.kind != CODEX_MICRO_KEY_VENDOR ||
            mapping.vendor_key == NULL) {
            return true;
        }
        codex_transport_t transport = preferred_transport();
        if (mode == CODEX_MICRO_MODE_NORMAL && event->pressed &&
            normal_behavior == CONFIG_NORMAL_AGENT_BEHAVIOR_OPEN_CONVERSATION) {
            portENTER_CRITICAL(&s_state_lock);
            const bool already_pressed = s_ble_press_active[event->control];
            transport = s_codex_task_lights[mapping.agent].source;
            portEXIT_CRITICAL(&s_state_lock);
            if (already_pressed) {
                return true;
            }
            if (transport == CODEX_TRANSPORT_AUTO ||
                !transport_connected(transport)) {
                return true;
            }
        }
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
            return mode == CODEX_MICRO_MODE_NORMAL;
        }
        const esp_err_t result = send_key(
            mapping.vendor_key, event->pressed ? 1 : 0,
            mapping.agent, transport);
        if (result == ESP_OK && new_press &&
            mapping.agent != CODEX_MICRO_AGENT_NONE &&
            (codex_micro_mode() == CODEX_MICRO_MODE_CODEX ||
             (codex_micro_mode() == CODEX_MICRO_MODE_NORMAL &&
              normal_behavior == CONFIG_NORMAL_AGENT_BEHAVIOR_OPEN_CONVERSATION)) &&
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
    if (status != NULL &&
        (mode == CODEX_MICRO_MODE_NORMAL ||
         mode == CODEX_MICRO_MODE_CODEX)) {
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
        bool desktop_active = false;
        if (mode == CODEX_MICRO_MODE_NORMAL) {
            desktop_agent_model_t desktop;
            desktop_agent_status_get(&desktop);
            desktop_active = desktop.active;
            /* Slots in state `codex` keep the Codex light computed above. */
            for (size_t i = 0; desktop.claimed && i < DESKTOP_AGENT_SLOT_COUNT; ++i) {
                if (desktop.states[i] == DESKTOP_AGENT_CODEX) continue;
                const uint32_t rgb = desktop_agent_state_rgb(desktop.states[i]);
                status[i] = (board_rgb_t){
                    .red = ((rgb >> 16) & 0xffu) / 16u,
                    .green = ((rgb >> 8) & 0xffu) / 16u,
                    .blue = (rgb & 0xffu) / 16u,
                };
            }
        }
        status[CODEX_MICRO_CONNECTED_LED_INDEX] = (usb || ble || desktop_active)
            ? (board_rgb_t){.green = 10, .blue = 2} : (board_rgb_t){0};
        status[CODEX_MICRO_LAYER_LED_INDEX] =
            mode == CODEX_MICRO_MODE_NORMAL
                ? (board_rgb_t){.green = 2, .blue = 10}
                : (board_rgb_t){.red = 10, .blue = 10};
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
    desktop_agent_model_t desktop;
    desktop_agent_status_get(&desktop);
    const bool desktop_selected =
        mode == CODEX_MICRO_MODE_NORMAL && desktop.claimed;
    const uint8_t codex_slots = desktop_agent_model_codex_mask(&desktop);
    portENTER_CRITICAL(&s_state_lock);
    const codex_micro_attention_t notifications =
        s_attention_notifications[CODEX_TRANSPORT_USB] |
        s_attention_notifications[CODEX_TRANSPORT_BLE];
    codex_micro_attention_t yielded = CODEX_ATTENTION_NONE;
    for (size_t i = 0; i < CODEX_MICRO_TASK_COUNT; ++i) {
        if (codex_slots & (1u << i)) yielded |= s_attention_slots[i];
    }
    memset(s_attention_notifications, 0, sizeof(s_attention_notifications));
    memset(s_attention_slots, 0, sizeof(s_attention_slots));
    portEXIT_CRITICAL(&s_state_lock);
    const codex_micro_attention_t claude = claude_status_take_attention(
        mode == CODEX_MICRO_MODE_CLAUDE_CODE);
    const codex_micro_attention_t desktop_attention =
        desktop_agent_status_take_attention(desktop_selected);
    static bool previous_desktop_selected;
    const bool desktop_changed = desktop_selected != previous_desktop_selected;
    previous_desktop_selected = desktop_selected;
    /* The desktop model silences its own first snapshot; Codex reminders
     * pass only for slots yielded to Codex (`codex`). The haptic scheduler
     * merges the bits and plays the highest priority. */
    if (desktop_selected) {
        return desktop_attention | yielded;
    }
    if (source_changed || desktop_changed) {
        return CODEX_ATTENTION_NONE;
    }
    return mode == CODEX_MICRO_MODE_CLAUDE_CODE ? claude :
        mode == CODEX_MICRO_MODE_NORMAL || mode == CODEX_MICRO_MODE_CODEX
            ? notifications : CODEX_ATTENTION_NONE;
}

bool codex_micro_status_age_ms(uint32_t *age_ms)
{
    if (!atomic_load(&s_codex_status_seen)) return false;
    *age_ms = (uint32_t)(esp_timer_get_time() / 1000) -
              (uint32_t)atomic_load(&s_codex_status_at_ms);
    return true;
}

static int8_t active_slot_bond_state(int32_t *bond_count_out)
{
    const int bond_count = esp_ble_get_bond_device_num();
    *bond_count_out = bond_count;
    if (bond_count < 0) {
        return -1;
    }
    const codex_micro_ble_peer_t peer =
        peer_for_slot(codex_micro_active_ble_slot());
    if (peer.valid != 1u) {
        return 0;
    }
    if (bond_count == 0) {
        return 0;
    }
    esp_ble_bond_dev_t *bonds = calloc((size_t)bond_count, sizeof(*bonds));
    if (bonds == NULL) {
        return -1;
    }
    int queried_count = bond_count;
    const esp_err_t result =
        esp_ble_get_bond_device_list(&queried_count, bonds);
    int8_t state = result == ESP_OK ? 0 : -1;
    if (result == ESP_OK) {
        for (int index = 0; index < queried_count; ++index) {
            if (memcmp(peer.address, bonds[index].bd_addr,
                       ESP_BD_ADDR_LEN) == 0) {
                state = 1;
                break;
            }
        }
    }
    free(bonds);
    return state;
}

void codex_micro_get_diagnostics(codex_micro_diagnostics_t *diagnostics)
{
    if (diagnostics == NULL) {
        return;
    }
    int32_t bond_count = -1;
    const int8_t bond_state = active_slot_bond_state(&bond_count);
    *diagnostics = (codex_micro_diagnostics_t){
        .connected = codex_micro_connected(),
        .usb_connected = usb_codex_connected(),
        .ble_connected = atomic_load(&s_connected),
        .init_error = s_init_error,
        .ble_recovery_result = ble_recovery_result_name(s_ble_recovery_result),
        .ble_recovery_attempts = s_ble_recovery_attempts,
        .ble_recovery_last_error = s_ble_recovery_last_error,
        .ble_recovery_reset_reason = (int32_t)esp_reset_reason(),
        .ble_crash_loop_count = s_ble_crash_loop_count,
        .ble_services_ready = codex_micro_ble_services_ready(),
        .bond_count = bond_count,
        .active_slot_bond_state = bond_state,
        .auth_fail_events = atomic_load(&s_auth_fail_events),
        .last_auth_fail_reason = atomic_load(&s_last_auth_fail_reason),
        .ble_reject_count = atomic_load(&s_ble_reject_count),
        .ble_last_reject_reason =
            (codex_micro_ble_reject_reason_t)atomic_load(
                &s_ble_last_reject_reason),
        .ble_last_reject_slot = atomic_load(&s_ble_last_reject_slot),
        .ble_last_disconnect_reason =
            atomic_load(&s_ble_last_disconnect_reason),
        .ble_last_disconnect_source =
            (codex_micro_ble_disconnect_source_t)atomic_load(
                &s_ble_last_disconnect_source),
        .ble_last_slot_op =
            (codex_micro_ble_slot_op_t)atomic_load(&s_ble_last_slot_op),
        .ble_last_slot_op_slot = atomic_load(&s_ble_last_slot_op_slot),
        .ble_last_slot_op_error = atomic_load(&s_ble_last_slot_op_error),
        .ble_last_slot_op_timeout =
            atomic_load(&s_ble_last_slot_op_timeout),
        .ble_slot_clear_timeout_count =
            atomic_load(&s_ble_slot_clear_timeout_count),
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
        .subscription_restores = atomic_load(&s_subscription_restores),
        .subscription_default_restores =
            atomic_load(&s_subscription_default_restores),
        .subscription_save_failures =
            atomic_load(&s_subscription_save_failures),
        .hid_reports_dropped_unsubscribed =
            atomic_load(&s_hid_reports_dropped_unsubscribed),
        .hid_report_failures = atomic_load(&s_hid_report_failures),
        .advertising_held = atomic_load(&s_advertising_held),
        .advertising_recoveries = atomic_load(&s_advertising_recoveries),
        .advertising_starts = atomic_load(&s_advertising_starts),
        .advertising_start_failures =
            atomic_load(&s_advertising_start_failures),
        .advertising_last_start_status =
            atomic_load(&s_advertising_last_start_status),
        .advertising_stops = atomic_load(&s_advertising_stops),
        .advertising_retries_exhausted =
            atomic_load(&s_advertising_retries_exhausted),
        .advertising_backoff_level = s_advertising_backoff.consecutive,
        .advertising_backoff_delay_ms =
            ble_adv_backoff_delay_ms(s_advertising_backoff.consecutive == 0
                                         ? 0u
                                         : s_advertising_backoff.consecutive - 1u),
        .advertising_input_restarts = s_advertising_backoff.input_restarts,
        .advertising_stale_flags = atomic_load(&s_advertising_stale_flags),
        .advertising_active = atomic_load(&s_advertising_active),
        .advertising_starting = atomic_load(&s_advertising_starting),
        .advertising_configured = atomic_load(&s_advertising_configured),
        .scan_response_configured =
            atomic_load(&s_scan_response_configured),
        .peer_filter_ready = atomic_load(&s_peer_filter_ready),
        .peer_filter_state = (uint8_t)atomic_load(&s_peer_filter_state),
        .advertising_slow = atomic_load(&s_slow_advertising),
        .advertising_pairing_boosts =
            atomic_load(&s_advertising_pairing_boosts),
        .service_change_sent = atomic_load(&s_service_change_sent),
        .service_change_skipped = atomic_load(&s_service_change_skipped),
        .auth_fail_disconnects = atomic_load(&s_auth_fail_disconnects),
        .reauth_prompts = atomic_load(&s_reauth_prompts),
        .orphan_bonds_removed = atomic_load(&s_orphan_bonds_removed),
        .orphan_cleanup_skipped = atomic_load(&s_orphan_cleanup_skipped),
        .ble_pairing_window_returns =
            atomic_load(&s_pairing_window_returns),
        .ble_active_persist_failures =
            atomic_load(&s_active_slot_persist_failures),
        .ble_connect_last_ms = atomic_load(&s_ble_connect_last_ms),
        .ble_connect_max_ms = atomic_load(&s_ble_connect_max_ms),
        .ble_local_address_set_rejections =
            atomic_load(&s_local_address_set_rejections),
        .ble_local_address_apply_failures =
            atomic_load(&s_local_address_apply_failures),
        .ble_local_address_adopt_mismatches =
            atomic_load(&s_local_address_adopt_mismatches),
        .ble_reject_unknown_host_count = atomic_load(
            &s_ble_reject_reason_counts[CODEX_MICRO_BLE_REJECT_UNKNOWN_HOST]),
        .ble_reject_other_slot_count = atomic_load(
            &s_ble_reject_reason_counts[CODEX_MICRO_BLE_REJECT_OTHER_SLOT]),
        .ble_reject_suspended_count = atomic_load(
            &s_ble_reject_reason_counts[CODEX_MICRO_BLE_REJECT_SUSPENDED]),
    };
    diagnostics->ble_pairing_window_open = codex_micro_ble_pairing_window(
        &diagnostics->ble_pairing_window_slot,
        &diagnostics->ble_pairing_return_slot);
    uint32_t current_subscriptions = 0;
    uint32_t all_subscriptions = 0;
    if (hid_subscriptions(&current_subscriptions) &&
        esp_hidd_ble_get_subscription_mask_all(s_hid, &all_subscriptions) ==
            ESP_OK) {
        diagnostics->hid_subscriptions = current_subscriptions;
        diagnostics->hid_subscriptions_all = all_subscriptions;
    }
    for (uint8_t slot = 1u; slot <= CODEX_MICRO_BLE_SLOT_COUNT; ++slot) {
        const codex_micro_ble_peer_t peer = peer_for_slot(slot);
        const codex_micro_ble_identity_t identity =
            peer_identity_for_slot(slot);
        esp_bd_addr_t local_address;
        portENTER_CRITICAL(&s_state_lock);
        const bool own_address = s_ble_local_address_valid[slot - 1u];
        memcpy(local_address, s_ble_local_addresses[slot - 1u],
               sizeof(local_address));
        portEXIT_CRITICAL(&s_state_lock);
        diagnostics->ble_slots[slot - 1u] =
            (codex_micro_ble_slot_diagnostics_t){
                .slot = slot,
                .paired = peer.valid == 1u,
                .address_kind = ble_address_kind(&peer),
                .identity_known = identity.valid == 1u,
                .own_address = peer.valid == 1u && own_address,
            };
        if (diagnostics->ble_slots[slot - 1u].own_address) {
            memcpy(diagnostics->ble_slots[slot - 1u].local_address,
                   local_address, sizeof(local_address));
        }
    }
    portENTER_CRITICAL(&s_state_lock);
    diagnostics->ble_local_address_asked_random =
        s_advertised_local_address_random;
    memcpy(diagnostics->ble_local_address_asked, s_advertised_local_address,
           sizeof(diagnostics->ble_local_address_asked));
    portEXIT_CRITICAL(&s_state_lock);
    diagnostics->ble_local_address_in_use_known = local_address_in_use(
        diagnostics->ble_local_address_in_use,
        &diagnostics->ble_local_address_in_use_random);
}
