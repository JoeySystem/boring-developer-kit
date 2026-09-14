#pragma once

#include <stdbool.h>
#include <stdint.h>

/**
 * @brief User-facing USB enumeration state shared by the screen and status LEDs.
 */
typedef struct {
    const char *label;
    uint8_t red;
    uint8_t green;
    uint8_t blue;
} usb_connection_feedback_t;

/**
 * @brief Map TinyUSB mount state to one unambiguous label and indicator color.
 */
usb_connection_feedback_t usb_connection_feedback(bool mounted);
