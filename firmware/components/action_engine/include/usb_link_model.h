#pragma once

/*
 * Decides when USB owns the host link (BLE suspended) and when BLE may take
 * over. Raw USB state flaps: a bus can suspend for a moment, a host can
 * re-enumerate, and the board has no VBUS sense, so a sleeping host and an
 * unplugged cable look the same. Every hand-over therefore needs the new
 * state to hold for a while.
 */

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Mirrors usb_service_link_state_t; kept separate for host-side tests. */
typedef enum {
    USB_LINK_INPUT_NONE = 0,
    USB_LINK_INPUT_ENUMERATING,
    USB_LINK_INPUT_READY,
    USB_LINK_INPUT_SUSPENDED,
} usb_link_input_t;

/* USB must be ready this long before BLE is dropped. */
#define USB_LINK_ATTACH_STABLE_MS 1000u
/* A detached (never re-reset) bus releases BLE after this long. */
#define USB_LINK_DETACH_MS 500u
/* A suspended or re-enumerating bus may be a brief host power event. */
#define USB_LINK_SUSPEND_GRACE_MS 2000u
/* At boot, wait this long for a host to reset the bus before starting BLE. */
#define USB_LINK_BOOT_WAIT_MS 1500u
/* Once a host has reset the bus at boot, wait this long for configuration. */
#define USB_LINK_BOOT_ENUMERATION_MS 5000u

typedef struct {
    bool usb_owns_link;
    bool booting;
    uint32_t boot_started_ms;
    bool ready_seen;
    uint32_t ready_since_ms;
    bool lost_seen;
    uint32_t lost_since_ms;
} usb_link_model_t;

/* Starts owned (BLE held back) so a cable present at boot never lets BLE
 * advertise briefly before enumeration finishes. */
void usb_link_model_init(usb_link_model_t *model, uint32_t now_ms);

/* Returns true while USB owns the link (BLE must stay suspended). */
bool usb_link_model_update(usb_link_model_t *model, uint32_t now_ms,
                           usb_link_input_t input);

#ifdef __cplusplus
}
#endif
