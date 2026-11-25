#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "board.h"
#include "codex_agent_press_model.h"
#include "codex_micro_attention_model.h"
#include "codex_micro_mode.h"
#include "esp_err.h"

#define CODEX_MICRO_STATUS_LED_COUNT 8u
#define CODEX_MICRO_BLE_SLOT_COUNT 3u

typedef enum {
    CODEX_MICRO_BLE_ADDRESS_NONE = 0,
    CODEX_MICRO_BLE_ADDRESS_PUBLIC,
    CODEX_MICRO_BLE_ADDRESS_RANDOM_STATIC,
    CODEX_MICRO_BLE_ADDRESS_RPA,
    CODEX_MICRO_BLE_ADDRESS_RANDOM_OTHER,
} codex_micro_ble_address_kind_t;

typedef enum {
    CODEX_MICRO_BLE_REJECT_NONE = 0,
    CODEX_MICRO_BLE_REJECT_UNKNOWN_HOST,
    CODEX_MICRO_BLE_REJECT_OTHER_SLOT,
    CODEX_MICRO_BLE_REJECT_SUSPENDED,
} codex_micro_ble_reject_reason_t;

typedef enum {
    CODEX_MICRO_BLE_DISCONNECT_NONE = 0,
    CODEX_MICRO_BLE_DISCONNECT_STANDBY,
    CODEX_MICRO_BLE_DISCONNECT_USB,
    CODEX_MICRO_BLE_DISCONNECT_SLOT_SWITCH,
    CODEX_MICRO_BLE_DISCONNECT_REJECT,
    CODEX_MICRO_BLE_DISCONNECT_STACK,
    CODEX_MICRO_BLE_DISCONNECT_AUTH_FAIL,
} codex_micro_ble_disconnect_source_t;

typedef enum {
    CODEX_MICRO_BLE_SLOT_OP_NONE = 0,
    CODEX_MICRO_BLE_SLOT_OP_CLEAR,
    CODEX_MICRO_BLE_SLOT_OP_PAIR_SAVE,
    CODEX_MICRO_BLE_SLOT_OP_SELECT,
    /* The pairing window ended and BLE went back to the previous host. */
    CODEX_MICRO_BLE_SLOT_OP_AUTO_RETURN,
} codex_micro_ble_slot_op_t;

typedef struct {
    uint8_t slot;
    bool paired;
    codex_micro_ble_address_kind_t address_kind;
    bool identity_known;
    /* The device shows this slot's host an address of its own; false for a
     * slot paired on the shared public address and for an empty slot. */
    bool own_address;
    /* That address, when own_address is true. */
    uint8_t local_address[6];
} codex_micro_ble_slot_diagnostics_t;

typedef struct {
    bool connected;
    bool usb_connected;
    bool ble_connected;
    int32_t init_error;
    /* BLE start-up recovery, see ble_recovery_model.h. */
    const char *ble_recovery_result;
    uint32_t ble_recovery_attempts;
    int32_t ble_recovery_last_error;
    int32_t ble_recovery_reset_reason;
    uint32_t ble_crash_loop_count;
    bool ble_services_ready;
    int32_t bond_count;
    /* -1 if bond storage cannot be queried, 0 if absent, 1 if present. */
    int8_t active_slot_bond_state;
    uint32_t auth_fail_events;
    uint8_t last_auth_fail_reason;
    codex_micro_ble_slot_diagnostics_t ble_slots[CODEX_MICRO_BLE_SLOT_COUNT];
    uint32_t ble_reject_count;
    codex_micro_ble_reject_reason_t ble_last_reject_reason;
    uint8_t ble_last_reject_slot;
    uint8_t ble_last_disconnect_reason;
    codex_micro_ble_disconnect_source_t ble_last_disconnect_source;
    codex_micro_ble_slot_op_t ble_last_slot_op;
    uint8_t ble_last_slot_op_slot;
    int32_t ble_last_slot_op_error;
    bool ble_last_slot_op_timeout;
    uint32_t ble_slot_clear_timeout_count;
    codex_micro_mode_t mode;
    uint32_t notify_subscribe_events;
    uint32_t notify_unsubscribe_events;
    uint16_t last_subscribe_attr;
    bool last_subscribe_notify;
    uint16_t negotiated_mtu;
    uint32_t notify_tx_events;
    uint32_t notify_tx_successes;
    uint32_t notify_tx_failures;
    uint16_t last_notify_tx_attr;
    int32_t last_notify_tx_status;
    uint32_t rx_output_reports;
    uint32_t rx_parse_errors;
    uint32_t rpc_responses;
    uint32_t tx_attempts;
    uint32_t tx_successes;
    uint32_t tx_failures;
    int32_t last_tx_error;
    /* HID notification subscriptions (one bit per input report, bit 31 =
     * battery) of the current host, and every bit the device exposes. */
    uint32_t hid_subscriptions;
    uint32_t hid_subscriptions_all;
    uint32_t subscription_restores;
    uint32_t subscription_default_restores;
    uint32_t subscription_save_failures;
    uint32_t hid_reports_dropped_unsubscribed;
    uint32_t hid_report_failures;
    bool advertising_held;
    uint32_t advertising_recoveries;
    /* Advertising state machine, to tell "not advertising" from "advertising
     * but not shown by the host" after a slot clear or an empty-slot boot. */
    uint32_t advertising_starts;
    uint32_t advertising_start_failures;
    int32_t advertising_last_start_status;
    uint32_t advertising_stops;
    uint32_t advertising_retries_exhausted;
    /* Watchdog restarts since advertising last worked, the delay before the
     * next one, and restarts brought forward by a key press (item 3). */
    uint32_t advertising_backoff_level;
    uint32_t advertising_backoff_delay_ms;
    uint32_t advertising_input_restarts;
    /* Start or retry flags cleared because they were stuck. */
    uint32_t advertising_stale_flags;
    bool advertising_active;
    bool advertising_starting;
    bool advertising_configured;
    bool scan_response_configured;
    bool peer_filter_ready;
    uint8_t peer_filter_state;
    bool advertising_slow;
    /* Local inputs that switched slow pairing advertising back to fast. */
    uint32_t advertising_pairing_boosts;
    uint32_t service_change_sent;
    uint32_t service_change_skipped;
    uint32_t auth_fail_disconnects;
    uint32_t reauth_prompts;
    uint32_t orphan_bonds_removed;
    uint32_t orphan_cleanup_skipped;
    /* Pairing window (see codex_micro_ble_pairing_window). */
    bool ble_pairing_window_open;
    uint8_t ble_pairing_window_slot;
    uint8_t ble_pairing_return_slot;
    uint32_t ble_pairing_window_returns;
    /* NVS "active" writes that failed after a host paired. */
    uint32_t ble_active_persist_failures;
    /* From the start of advertising to the encrypted link of an accepted
     * host: the last link and the longest since boot; 0 = none yet. */
    uint32_t ble_connect_last_ms;
    uint32_t ble_connect_max_ms;
    /* The Bluetooth address this firmware last asked the stack to advertise
     * on, and the one the stack reports it is on. "random" false = the public
     * address. They differ when the stack refused the request. */
    uint8_t ble_local_address_asked[6];
    bool ble_local_address_asked_random;
    uint8_t ble_local_address_in_use[6];
    bool ble_local_address_in_use_random;
    bool ble_local_address_in_use_known;
    /* Requests for a new address the stack refused. */
    uint32_t ble_local_address_set_rejections;
    /* Advertising starts found on another address than the one asked for. */
    uint32_t ble_local_address_apply_failures;
    /* Hosts recorded with another address than the one asked for. */
    uint32_t ble_local_address_adopt_mismatches;
    /* ble_reject_count split by reason. */
    uint32_t ble_reject_unknown_host_count;
    uint32_t ble_reject_other_slot_count;
    uint32_t ble_reject_suspended_count;
} codex_micro_diagnostics_t;

typedef bool (*codex_micro_usb_connected_fn)(void *context);
typedef esp_err_t (*codex_micro_usb_send_fn)(const uint8_t *data,
                                             size_t length, void *context);

typedef struct {
    codex_micro_usb_connected_fn connected;
    codex_micro_usb_send_fn send;
    void *context;
} codex_micro_usb_hid_t;

/* Send a Service Changed indication to one connected, bonded host. */
typedef esp_err_t (*codex_micro_service_change_fn)(
    const uint8_t address[6], void *context);

/* Return true for events owned by the added service, so HID does not consume them. */
typedef bool (*codex_micro_ble_gatts_observer_fn)(
    int event, uint16_t gatts_if, void *event_data, void *context);

#ifdef __cplusplus
extern "C" {
#endif

/**
 * Initialize the optional Codex-compatible transport.
 *
 * In the normal no-BLE build this is a no-op so the original firmware target
 * keeps exactly one USB HID + CDC transport.
 */
esp_err_t codex_micro_init(const char *serial, bool initially_suspended);
/** Name loaded by this boot's BLE initialization; saves apply after reboot. */
const char *codex_micro_ble_device_name(void);
bool codex_micro_ble_services_ready(void);

/** Add one product GATT service to the existing Bluedroid dispatcher. */
void codex_micro_register_ble_gatts_observer(
    codex_micro_ble_gatts_observer_fn observer, void *context);

/**
 * Keep BLE from advertising until codex_micro_release_advertising_hold().
 * Call before codex_micro_init() when another GATT service is appended after
 * HID, so a host never discovers (and caches) an incomplete database. The
 * hold is released automatically 5 s after HID is ready.
 */
void codex_micro_hold_advertising_for_services(void);
void codex_micro_release_advertising_hold(void);

/**
 * Service Changed is sent once per bonded host after the firmware changed,
 * through @sender, after the host re-establishes encryption. Report the
 * indication's outcome with codex_micro_note_service_change_result().
 */
void codex_micro_register_service_change_sender(
    codex_micro_service_change_fn sender, void *context);
void codex_micro_note_service_change_result(bool delivered);

/** True once when the active slot's host can no longer encrypt (keys lost). */
bool codex_micro_take_ble_reauth_needed(uint8_t *slot);

/** Bind the optional native-USB Vendor HID transport. */
void codex_micro_register_usb_hid(const codex_micro_usb_hid_t *transport);

/** Accept one normalized USB Vendor HID Output Report body. */
void codex_micro_receive_usb_output(uint8_t report_id, const uint8_t *data,
                                    size_t length);

/** Poll USB attachment transitions and reset volatile mode on reconnect. */
void codex_micro_poll(void);

/** Preserve the current mode for the next software restart only. */
void codex_micro_prepare_soft_restart(void);

/**
 * Suppress physical control reports while a device-local interface owns input.
 * Entering suppression releases any active Vendor HID controls immediately.
 */
void codex_micro_set_input_suppressed(bool suppressed);

/**
 * A local key or knob input (not a joystick direction). While the active BLE
 * slot is empty and unconnected, it restarts the pairing fast-advertising
 * window. Safe to call from any task.
 */
void codex_micro_note_local_input(void);

/**
 * Suspend BLE advertising/connection during device-local power saving.
 * Pairing slots remain stored and advertising resumes on wake.
 */
void codex_micro_set_ble_suspended(bool suspended);

/** Suspend BLE while recording why an active link was disconnected. */
void codex_micro_set_ble_suspended_for_source(
    bool suspended, codex_micro_ble_disconnect_source_t source);

/** True while either the USB or BLE Codex-compatible transport is connected. */
bool codex_micro_connected(void);
/** Host input is ready after connection and all controls have returned neutral. */
bool codex_micro_input_ready(void);

/** True only while the encrypted BLE HID transport is connected. */
bool codex_micro_ble_connected(void);

/**
 * Select one of the three persistent BLE host slots (1..3).
 *
 * An empty slot enters pairing advertising. A populated slot accepts only its
 * saved peer and disconnects the currently active peer when necessary.
 */
typedef enum {
    CODEX_MICRO_BLE_SLOT_SOURCE_UNKNOWN = 0,
    CODEX_MICRO_BLE_SLOT_SOURCE_PROTOCOL,
    CODEX_MICRO_BLE_SLOT_SOURCE_KEY,
    CODEX_MICRO_BLE_SLOT_SOURCE_PAIRING,
    /* Done by the firmware itself (pairing window return). */
    CODEX_MICRO_BLE_SLOT_SOURCE_AUTO,
} codex_micro_ble_slot_source_t;

/* As codex_micro_select_ble_slot / codex_micro_clear_ble_slot, recording who
 * asked in the persisted slot history; the plain forms record PROTOCOL. */
esp_err_t codex_micro_select_ble_slot_from(uint8_t slot,
                                           codex_micro_ble_slot_source_t source);
esp_err_t codex_micro_clear_ble_slot_from(uint8_t slot,
                                          codex_micro_ble_slot_source_t source);

typedef struct {
    uint8_t op;
    uint8_t slot;
    uint8_t source;
    bool timeout;
    int32_t error;
    uint32_t uptime_ms;
    uint32_t boots_ago;
} codex_micro_ble_slot_history_entry_t;

/* Newest first; returns the number of entries copied. */
size_t codex_micro_ble_slot_history(codex_micro_ble_slot_history_entry_t *entries,
                                    size_t capacity);

esp_err_t codex_micro_select_ble_slot(uint8_t slot);

/** Forget the bonded peer in one slot and start pairing on that slot. */
esp_err_t codex_micro_clear_ble_slot(uint8_t slot);

/** Clear every BLE host slot and restore the operating mode to Normal. */
esp_err_t codex_micro_factory_reset(void);

/** Return the selected persistent BLE host slot (1..3). */
uint8_t codex_micro_active_ble_slot(void);

/** True when the requested BLE host slot already contains a bonded peer. */
bool codex_micro_ble_slot_paired(uint8_t slot);

/** True when the requested slot is the active encrypted BLE connection. */
bool codex_micro_ble_slot_connected(uint8_t slot);

/**
 * True while the pairing window is open: the active slot is an empty slot
 * selected in RAM only, while another slot still holds a host. Writes that
 * empty slot to slot and the slot BLE returns to when the window ends to
 * return_slot (0 = none). Either pointer may be NULL.
 */
bool codex_micro_ble_pairing_window(uint8_t *slot, uint8_t *return_slot);

/** Consume notice that the pairing window ended and BLE is back on slot. */
bool codex_micro_take_ble_pairing_return(uint8_t *slot);

/** Lowest empty BLE host slot (1..3), 0 when every slot holds a host. */
uint8_t codex_micro_ble_free_slot(void);

/**
 * The remembered host the switch key moves to from the active slot: the next
 * slot in the order 1, 2, 3, 1, ... that holds a host. 0 when no slot other
 * than the active one holds a host.
 */
uint8_t codex_micro_ble_next_host_slot(void);

/**
 * Consume the saved slot for a peer rejected from a different empty slot.
 *
 * Returns true once for each detected mismatch and writes the existing slot
 * when slot is non-NULL. This is display guidance only; it never switches or
 * clears a pairing slot.
 */
bool codex_micro_take_ble_slot_redirect(uint8_t *slot);

/** Consume notice that an occupied slot rejected an unknown BLE host. */
bool codex_micro_take_ble_peer_rejection(uint8_t *slot);

/** Consume completion of an asynchronous BLE slot-clear operation. */
bool codex_micro_take_ble_slot_clear_result(uint8_t *slot, esp_err_t *error);

/** Consume BLE slot-clear completion for the configuration protocol. */
bool codex_micro_take_ble_slot_clear_protocol_result(
    uint8_t *slot, esp_err_t *error);

/** Return the operating mode; cold boot and ordinary reconnect reset to NORMAL. */
codex_micro_mode_t codex_micro_mode(void);

/** Cycle the modes supported by this board and the current Codex connection. */
codex_micro_mode_t codex_micro_toggle_mode(void);

/** True while any Codex Vendor HID control is held down. */
bool codex_micro_has_active_control(void);

/** True only for transports with six physical Codex Agent controls. */
bool codex_micro_agent_focus_supported(void);
/** Thread-safe, non-consuming latest physical Agent press notification. */
codex_agent_press_snapshot_t codex_micro_get_agent_press(void);
/** Invalidate an unconsumed focus notification after a behavior change. */
void codex_micro_clear_agent_press(void);

/** Send one standard keyboard/consumer/mouse report over the BLE HID link. */
esp_err_t codex_micro_send_standard_report(uint8_t report_id,
                                           const uint8_t *data,
                                           size_t length);

/**
 * Route one physical input through Codex Micro. CODEX owns its fixed layout;
 * NORMAL owns only the six Agent controls on compatible hardware.
 *
 * Returns true when Codex owns the event and the standard action must be
 * suppressed. Returns false for unsupported events or when the fixed mapping
 * intentionally delegates to the Action Engine's standard HID route.
 */
bool codex_micro_handle_event(const board_event_t *event);

/**
 * Copy the latest six host-supplied task colors plus two transport indicators.
 *
 * NORMAL and CODEX always provide a status frame; without a valid task source,
 * the Agent LEDs are white and the connection indicator is cleared. Returns
 * false for modes without a Codex status frame or when status is NULL.
 */
bool codex_micro_status_rgb(
    board_rgb_t status[CODEX_MICRO_STATUS_LED_COUNT]);

/**
 * Consume one coalesced Codex attention notification.
 *
 * Notifications are derived from host task-status transitions and are cleared
 * on session reset. Returns a bitmask of reasons since the previous read;
 * repeated reads return CODEX_ATTENTION_NONE until a new status transition.
 */
codex_micro_attention_t codex_micro_take_attention_notification(void);

/** Milliseconds since Codex last sent task status (USB or BLE); false
 *  until the first one this boot. */
bool codex_micro_status_age_ms(uint32_t *age_ms);

/** Copy read-only compatibility runtime counters for fault isolation. */
void codex_micro_get_diagnostics(codex_micro_diagnostics_t *diagnostics);
#ifdef __cplusplus
}
#endif
