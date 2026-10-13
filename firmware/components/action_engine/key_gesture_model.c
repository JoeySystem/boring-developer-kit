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
