#include "usb_standby_trace.h"

#include <stddef.h>
#include <stdint.h>

void usb_standby_trace_reset(usb_standby_trace_t *trace)
{
    if (trace == NULL) return;
    *trace = (usb_standby_trace_t) {0};
}

bool usb_standby_trace_enter(usb_standby_trace_t *trace,
                             usb_standby_entry_reason_t reason, uint32_t now_ms)
{
    if (trace == NULL || trace->active) return false;
    trace->active = true;
    if (trace->entries < UINT32_MAX) ++trace->entries;
    trace->last_entry_reason = reason;
    trace->last_change_ms = now_ms;
    return true;
}

bool usb_standby_trace_wake(usb_standby_trace_t *trace,
                            usb_standby_wake_reason_t reason, uint32_t now_ms)
{
    if (trace == NULL || !trace->active) return false;
    trace->active = false;
    if (trace->wakes < UINT32_MAX) ++trace->wakes;
    trace->last_wake_reason = reason;
    trace->last_change_ms = now_ms;
    return true;
}

const char *usb_standby_entry_reason_name(usb_standby_entry_reason_t reason)
{
    switch (reason) {
    case USB_STANDBY_ENTRY_NONE: return "none";
    case USB_STANDBY_ENTRY_POWER_BUTTON: return "power_button";
    case USB_STANDBY_ENTRY_LOW_BATTERY: return "low_battery";
    case USB_STANDBY_ENTRY_USB_LOW_BATTERY: return "usb_low_battery";
    case USB_STANDBY_ENTRY_BOOT: return "boot";
    }
    return "unknown";
}

const char *usb_standby_wake_reason_name(usb_standby_wake_reason_t reason)
{
    switch (reason) {
    case USB_STANDBY_WAKE_NONE: return "none";
    case USB_STANDBY_WAKE_POWER_BUTTON: return "power_button";
    case USB_STANDBY_WAKE_BATTERY_RECOVERED: return "battery_recovered";
    }
    return "unknown";
}
