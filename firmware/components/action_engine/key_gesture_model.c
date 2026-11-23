#include "key_gesture_model.h"

#include <string.h>

#define KEY_GESTURE_WINDOW_MS 300U

void key_gesture_model_reset(key_gesture_model_t *model)
{
    memset(model, 0, sizeof(*model));
}

key_gesture_result_t key_gesture_model_poll(key_gesture_model_t *model,
                                          uint32_t now_ms)
{
    if (model->waiting_second &&
        (int32_t)(now_ms - model->deadline_ms) >= 0) {
        key_gesture_model_reset(model);
        return KEY_GESTURE_TAP;
    }
    return KEY_GESTURE_NONE;
}

key_gesture_result_t key_gesture_model_event(key_gesture_model_t *model,
                                           bool pressed, uint32_t now_ms)
{
    if (pressed == model->down) return KEY_GESTURE_NONE;
    if (pressed) {
        const key_gesture_result_t expired = key_gesture_model_poll(model, now_ms);
        model->second_down = model->waiting_second;
        model->waiting_second = false;
        model->down = true;
        return expired;
    }
    model->down = false;
    if (model->second_down) {
        key_gesture_model_reset(model);
        return KEY_GESTURE_DOUBLE;
    }
    model->waiting_second = true;
    model->deadline_ms = now_ms + KEY_GESTURE_WINDOW_MS;
    return KEY_GESTURE_NONE;
}

static key_pulse_step_t key_pulse_advance(key_pulse_model_t *model,
                                          key_pulse_step_t step,
                                          uint32_t now_ms)
{
    switch (step) {
    case KEY_PULSE_STEP_MODIFIERS_DOWN:
        model->next_step = KEY_PULSE_STEP_KEY_DOWN;
        model->next_ms = now_ms + KEY_PULSE_MODIFIER_LEAD_MS;
        break;
    case KEY_PULSE_STEP_KEY_DOWN:
        model->next_step = KEY_PULSE_STEP_KEY_UP;
        model->next_ms = now_ms + KEY_PULSE_HOLD_MS;
        break;
    case KEY_PULSE_STEP_KEY_UP:
        if (model->has_modifiers) {
            model->next_step = KEY_PULSE_STEP_MODIFIERS_UP;
            model->next_ms = now_ms + KEY_PULSE_MODIFIER_TAIL_MS;
        } else {
            model->active = false;
            model->next_step = KEY_PULSE_STEP_NONE;
        }
        break;
    case KEY_PULSE_STEP_MODIFIERS_UP:
    default:
        model->active = false;
        model->next_step = KEY_PULSE_STEP_NONE;
        break;
    }
    return step;
}

key_pulse_step_t key_pulse_model_start(key_pulse_model_t *model,
                                       bool has_modifiers, uint32_t now_ms)
{
    memset(model, 0, sizeof(*model));
    model->active = true;
    model->has_modifiers = has_modifiers;
    return key_pulse_advance(model,
                             has_modifiers ? KEY_PULSE_STEP_MODIFIERS_DOWN
                                           : KEY_PULSE_STEP_KEY_DOWN,
                             now_ms);
}

key_pulse_step_t key_pulse_model_poll(key_pulse_model_t *model, uint32_t now_ms)
{
    if (!model->active || (int32_t)(now_ms - model->next_ms) < 0) {
        return KEY_PULSE_STEP_NONE;
    }
    return key_pulse_advance(model, model->next_step, now_ms);
}

key_pulse_step_t key_pulse_model_finish(key_pulse_model_t *model)
{
    if (!model->active) {
        return KEY_PULSE_STEP_NONE;
    }
    key_pulse_step_t step = model->next_step;
    if (step == KEY_PULSE_STEP_KEY_DOWN) {
        /* Only modifiers went down: skip the key entirely. */
        step = model->has_modifiers ? KEY_PULSE_STEP_MODIFIERS_UP
                                    : KEY_PULSE_STEP_NONE;
        if (step == KEY_PULSE_STEP_NONE) {
            model->active = false;
            return step;
        }
        model->active = false;
        model->next_step = KEY_PULSE_STEP_NONE;
        return step;
    }
    return key_pulse_advance(model, step, 0u);
}
