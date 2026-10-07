#include "power_button.h"

#include <stddef.h>

void power_button_init(power_button_t *button, bool initially_pressed,
                       uint32_t now_ms)
{
    if (button == NULL) {
        return;
    }
    *button = (power_button_t) {
        .armed = !initially_pressed,
        .raw_pressed = initially_pressed,
        .stable_pressed = initially_pressed,
        .raw_changed_at_ms = now_ms,
        .pressed_at_ms = now_ms,
    };
}

power_button_event_t power_button_update(power_button_t *button,
                                         bool raw_pressed, uint32_t now_ms,
                                         uint32_t debounce_ms,
                                         uint32_t long_press_ms)
{
    if (button == NULL) {
        return POWER_BUTTON_EVENT_NONE;
    }
    if (raw_pressed != button->raw_pressed) {
        button->raw_pressed = raw_pressed;
        button->raw_changed_at_ms = now_ms;
    }
    if (button->raw_pressed != button->stable_pressed &&
        now_ms - button->raw_changed_at_ms >= debounce_ms) {
        button->stable_pressed = button->raw_pressed;
        if (!button->armed) {
            if (!button->stable_pressed) {
                button->armed = true;
                button->long_press_sent = false;
            }
            return POWER_BUTTON_EVENT_NONE;
        }
        if (button->stable_pressed) {
            button->pressed_at_ms = now_ms;
            button->long_press_sent = false;
            return POWER_BUTTON_EVENT_PRESS;
        } else if (!button->long_press_sent) {
            return POWER_BUTTON_EVENT_SHORT_PRESS;
        }
    }
    if (button->armed && button->raw_pressed && button->stable_pressed &&
        !button->long_press_sent &&
        now_ms - button->pressed_at_ms >= long_press_ms) {
        button->long_press_sent = true;
        return POWER_BUTTON_EVENT_LONG_PRESS;
    }
    return POWER_BUTTON_EVENT_NONE;
}

bool power_button_pressed(const power_button_t *button)
{
    return button != NULL && button->stable_pressed;
}
