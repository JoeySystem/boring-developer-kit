#pragma once

/*
 * USB standby trace (pure logic, host-testable).
 *
 * On USB power the Power V2 power-off pulse cannot remove MCU power, so a
 * user "off" parks in USB standby and "on" is a fast wake without a reset.
 * This trace makes those cycles visible in GET_STATUS. It counts since this
 * boot only; reboots are already recorded by the FW_STATUS boot sequence.
 * Splash pages shown while parked are part of the same standby period.
 */

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    USB_STANDBY_ENTRY_NONE = 0,
    USB_STANDBY_ENTRY_POWER_BUTTON,
    USB_STANDBY_ENTRY_LOW_BATTERY,
    USB_STANDBY_ENTRY_USB_LOW_BATTERY,
    USB_STANDBY_ENTRY_BOOT,
} usb_standby_entry_reason_t;

typedef enum {
    USB_STANDBY_WAKE_NONE = 0,
    USB_STANDBY_WAKE_POWER_BUTTON,
    USB_STANDBY_WAKE_BATTERY_RECOVERED,
} usb_standby_wake_reason_t;

typedef struct {
    bool active;
    uint32_t entries;
    uint32_t wakes;
    usb_standby_entry_reason_t last_entry_reason;
    usb_standby_wake_reason_t last_wake_reason;
    uint32_t last_change_ms;
} usb_standby_trace_t;

void usb_standby_trace_reset(usb_standby_trace_t *trace);

/* Returns true when this starts a new standby period (it was not parked). */
bool usb_standby_trace_enter(usb_standby_trace_t *trace,
                             usb_standby_entry_reason_t reason, uint32_t now_ms);

/* Returns true when this ends a standby period. */
bool usb_standby_trace_wake(usb_standby_trace_t *trace,
                            usb_standby_wake_reason_t reason, uint32_t now_ms);

const char *usb_standby_entry_reason_name(usb_standby_entry_reason_t reason);
const char *usb_standby_wake_reason_name(usb_standby_wake_reason_t reason);

#ifdef __cplusplus
}
#endif
