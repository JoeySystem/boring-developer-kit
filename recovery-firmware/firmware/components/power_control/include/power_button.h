#pragma once

#include <stdbool.h>
#include <stdint.h>

typedef enum {
    POWER_BUTTON_EVENT_NONE = 0,
    POWER_BUTTON_EVENT_SHORT_PRESS,
    POWER_BUTTON_EVENT_LONG_PRESS,
    /** Debounced physical down edge; actions still wait for short/long resolution. */
    POWER_BUTTON_EVENT_PRESS,
} power_button_event_t;

typedef struct {
    bool armed;
    bool raw_pressed;
    bool stable_pressed;
    bool long_press_sent;
    uint32_t raw_changed_at_ms;
    uint32_t pressed_at_ms;
} power_button_t;

void power_button_init(power_button_t *button, bool initially_pressed,
                       uint32_t now_ms);
power_button_event_t power_button_update(power_button_t *button,
                                         bool raw_pressed, uint32_t now_ms,
                                         uint32_t debounce_ms,
                                         uint32_t long_press_ms);
bool power_button_pressed(const power_button_t *button);
