#pragma once

/**
 * @file usb_service.h
 * @brief Composite TinyUSB HID and CDC service used by all board targets.
 */

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef uint8_t usb_service_source_t;

/** Maximum number of independently owned HID input sources. */
#define USB_SERVICE_SOURCE_COUNT 22

enum {
    /** Stable report IDs shared by the USB and BLE HID transports. */
    USB_SERVICE_REPORT_ID_KEYBOARD = 1,
    USB_SERVICE_REPORT_ID_CONSUMER = 2,
    USB_SERVICE_REPORT_ID_MOUSE = 3,
    USB_SERVICE_REPORT_ID_CODEX_VENDOR = 6,
};

typedef bool (*usb_service_ble_connected_fn)(void *context);
typedef esp_err_t (*usb_service_ble_send_fn)(uint8_t report_id,
                                             const uint8_t *data,
                                             size_t length, void *context);

typedef struct {
    usb_service_ble_connected_fn connected;
    usb_service_ble_send_fn send;
    void *context;
} usb_service_ble_hid_t;

typedef void (*usb_service_vendor_output_fn)(uint8_t report_id,
                                             const uint8_t *data,
                                             size_t length, void *context);

typedef struct {
    usb_service_vendor_output_fn output;
    void *context;
} usb_service_vendor_hid_t;

/** Install TinyUSB and bind the CDC protocol service to the device serial. */
esp_err_t usb_service_init(const char *serial);

/** True only after the host has accepted and configured the USB device. */
bool usb_service_is_mounted(void);

/** True after the configurator completed a compatible HELLO handshake. */
bool usb_service_config_session_active(void);

/**
 * Register the optional BLE standard-HID sink.
 *
 * USB remains the preferred route whenever it is mounted.
 */
void usb_service_register_ble_hid(const usb_service_ble_hid_t *transport);

/** Register the optional Codex Vendor HID Output Report receiver. */
void usb_service_register_vendor_hid(
    const usb_service_vendor_hid_t *transport);

/** Send one 63-byte Codex Vendor HID Input Report over native USB. */
esp_err_t usb_service_send_vendor_report(const uint8_t *data, size_t length);

/** Enable or suppress standard USB/BLE HID delivery. */
void usb_service_set_standard_enabled(bool enabled);

/** Send pending HID work through USB first, otherwise through registered BLE. */
void usb_service_poll(void);

/** Update one keyboard usage owned by a specific physical control or macro. */
void usb_service_set_key(usb_service_source_t source, uint8_t hid_usage, bool pressed);

/** Update one HID modifier usage (224 through 231) for a specific source. */
void usb_service_set_modifier(usb_service_source_t source, uint8_t hid_usage, bool pressed);

/** True until pending keyboard state/reports have been accepted by the HID sink. */
bool usb_service_keyboard_pending(void);

/** Preserve the complete keyboard action after updating its modifiers and key. */
void usb_service_commit_keyboard(void);

/** Queue one consumer-control press followed automatically by its release. */
void usb_service_tap_consumer(uint16_t hid_usage);

/** Update source-owned mouse buttons and queue one relative movement report. */
void usb_service_send_mouse(usb_service_source_t source, uint8_t buttons,
                            int8_t x, int8_t y, int8_t wheel, int8_t pan);

/** Discard queued relative mouse reports produced by one input source. */
void usb_service_discard_mouse_source(usb_service_source_t source);

/** Release only the keys, modifiers, and mouse buttons owned by one source. */
void usb_service_release_source(usb_service_source_t source);

/** Clear keyboard and consumer state so no host input can remain stuck. */
void usb_service_release_all(void);

#ifdef __cplusplus
}
#endif
