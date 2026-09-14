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

typedef struct {
    bool connected;
    bool usb_connected;
    bool ble_connected;
    int32_t init_error;
    bool ble_services_ready;
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
} codex_micro_diagnostics_t;

typedef bool (*codex_micro_usb_connected_fn)(void *context);
typedef esp_err_t (*codex_micro_usb_send_fn)(const uint8_t *data,
                                             size_t length, void *context);

typedef struct {
    codex_micro_usb_connected_fn connected;
    codex_micro_usb_send_fn send;
    void *context;
} codex_micro_usb_hid_t;

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
esp_err_t codex_micro_init(const char *serial);
/** Name loaded by this boot's BLE initialization; saves apply after reboot. */
const char *codex_micro_ble_device_name(void);
bool codex_micro_ble_services_ready(void);

/** Add one product GATT service to the existing Bluedroid dispatcher. */
void codex_micro_register_ble_gatts_observer(
    codex_micro_ble_gatts_observer_fn observer, void *context);

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
 * Suspend BLE advertising/connection during device-local power saving.
 * Pairing slots remain stored and advertising resumes on wake.
 */
void codex_micro_set_ble_suspended(bool suspended);

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

/** Send one standard keyboard/consumer/mouse report over the BLE HID link. */
esp_err_t codex_micro_send_standard_report(uint8_t report_id,
                                           const uint8_t *data,
                                           size_t length);

/**
 * Route one physical input through Codex Micro while CODEX mode is active.
 *
 * Returns true when Codex mode owns the event and the standard action must be
 * suppressed. Returns false in NORMAL mode, while disconnected, for an
 * unsupported event, or when the fixed mapping intentionally delegates to the
 * Action Engine's standard HID route.
 */
bool codex_micro_handle_event(const board_event_t *event);

/**
 * Copy the latest six host-supplied task colors plus two transport indicators.
 *
 * Returns false when no compatible transport is connected.
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

/** Copy read-only compatibility runtime counters for fault isolation. */
void codex_micro_get_diagnostics(codex_micro_diagnostics_t *diagnostics);
#ifdef __cplusplus
}
#endif
