#pragma once

#include <stdbool.h>

#include "codex_micro_mode.h"

typedef struct {
    const char *output_label;
    const char *mode_label;
    bool output_connected;
} home_status_model_t;

/**
 * Resolve display-only home-page state without changing HID routing.
 *
 * All three modes follow the existing USB-first output policy. The selected
 * mode only changes the label shown on the home screen.
 */
home_status_model_t home_status_model_resolve(bool usb_mounted,
                                              bool ble_connected,
                                              codex_micro_mode_t mode);
