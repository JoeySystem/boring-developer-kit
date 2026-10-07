#pragma once

/**
 * @file status_indicator.h
 * @brief Pure status-light state selection and animation rendering.
 *
 * The top eight LEDs are a device-status surface, not user-configurable accent
 * lighting. Keeping this renderer independent from ESP-IDF makes every frame
 * deterministic and host-testable.
 */

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define STATUS_INDICATOR_LED_COUNT 8

typedef enum {
    STATUS_INDICATOR_BOOTING = 0,
    STATUS_INDICATOR_USB_OFFLINE,
    STATUS_INDICATOR_USB_READY,
    STATUS_INDICATOR_CONFIG_SESSION,
    STATUS_INDICATOR_ERROR,
} status_indicator_state_t;

typedef struct {
    uint8_t red;
    uint8_t green;
    uint8_t blue;
} status_indicator_rgb_t;

/**
 * Resolve the single visible state. Earlier arguments have intentionally
 * defined priority: error, boot, offline, configurator session, USB ready.
 */
status_indicator_state_t status_indicator_select(bool booting, bool usb_mounted,
                                                 bool config_session,
                                                 bool has_error);

/** Render one eight-pixel frame for the state at the supplied elapsed time. */
void status_indicator_render(
    status_indicator_state_t state, uint32_t elapsed_ms,
    status_indicator_rgb_t output[STATUS_INDICATOR_LED_COUNT]);

#ifdef __cplusplus
}
#endif
