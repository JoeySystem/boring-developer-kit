#include "double_click_model.h"

#include <string.h>

#define DOUBLE_CLICK_WINDOW_MS 300U

static bool reached(uint32_t now, uint32_t deadline)
{
    return (int32_t)(now - deadline) >= 0;
}

void double_click_model_init(double_click_model_t *model)
{
    if (model != NULL) {
        memset(model, 0, sizeof(*model));
    }
}

uint8_t double_click_model_press(double_click_model_t *model, uint32_t now_ms)
{
    if (model == NULL || model->down) {
        return DOUBLE_CLICK_ACTION_NONE;
    }
    if (model->waiting_second && reached(now_ms, model->deadline_ms)) {
        memset(model, 0, sizeof(*model));
        model->down = true;
        model->pressed_at_ms = now_ms;
        return DOUBLE_CLICK_ACTION_TAP;
    }
    model->second_down = model->waiting_second;
    model->waiting_second = false;
    model->down = true;
    model->pressed_at_ms = now_ms;
    return DOUBLE_CLICK_ACTION_NONE;
}

uint8_t double_click_model_release(double_click_model_t *model, uint32_t now_ms)
{
    if (model == NULL || !model->down) {
        return DOUBLE_CLICK_ACTION_NONE;
    }
    model->down = false;
    if (model->hold_forwarded) {
        memset(model, 0, sizeof(*model));
        return DOUBLE_CLICK_ACTION_HOLD_RELEASE;
    }
    if (model->second_down) {
        memset(model, 0, sizeof(*model));
        return DOUBLE_CLICK_ACTION_DOUBLE;
    }
    model->waiting_second = true;
    model->deadline_ms = now_ms + DOUBLE_CLICK_WINDOW_MS;
    return DOUBLE_CLICK_ACTION_NONE;
}

uint8_t double_click_model_poll(double_click_model_t *model, uint32_t now_ms)
{
    if (model == NULL) {
        return DOUBLE_CLICK_ACTION_NONE;
    }
    if (model->down && !model->hold_forwarded &&
        reached(now_ms, model->pressed_at_ms + DOUBLE_CLICK_WINDOW_MS)) {
        uint8_t action = DOUBLE_CLICK_ACTION_HOLD_PRESS;
        if (model->second_down) {
            action |= DOUBLE_CLICK_ACTION_TAP;
        }
        model->hold_forwarded = true;
        model->waiting_second = false;
        return action;
    }
    if (model->waiting_second && reached(now_ms, model->deadline_ms)) {
        memset(model, 0, sizeof(*model));
        return DOUBLE_CLICK_ACTION_TAP;
    }
    return DOUBLE_CLICK_ACTION_NONE;
}

uint8_t double_click_model_flush(double_click_model_t *model)
{
    if (model == NULL) {
        return DOUBLE_CLICK_ACTION_NONE;
    }
    uint8_t action = DOUBLE_CLICK_ACTION_NONE;
    if (model->hold_forwarded) {
        action = DOUBLE_CLICK_ACTION_HOLD_RELEASE;
    } else if (model->waiting_second || model->second_down) {
        action = DOUBLE_CLICK_ACTION_TAP;
    }
    memset(model, 0, sizeof(*model));
    return action;
}
