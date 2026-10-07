#include "diagnostic_capture_model.h"

#include <limits.h>
#include <string.h>

static bool deadline_reached(uint32_t now_ms, uint32_t deadline_ms)
{
    return (int32_t)(now_ms - deadline_ms) >= 0;
}

void diagnostic_capture_model_init(diagnostic_capture_model_t *model)
{
    if (model == NULL) {
        return;
    }
    memset(model, 0, sizeof(*model));
    model->state = DIAGNOSTIC_CAPTURE_INACTIVE;
    model->last_control = UINT8_MAX;
}

bool diagnostic_capture_model_start(diagnostic_capture_model_t *model,
                                    uint32_t now_ms,
                                    uint32_t timeout_ms)
{
    if (model == NULL || timeout_ms == 0u ||
        model->state == DIAGNOSTIC_CAPTURE_WAIT_NEUTRAL) {
        return false;
    }
    if (model->state == DIAGNOSTIC_CAPTURE_INACTIVE) {
        model->event_sequence = 0u;
        model->last_control = UINT8_MAX;
        model->has_last_event = false;
        model->last_pressed = false;
    }
    model->state = DIAGNOSTIC_CAPTURE_ACTIVE;
    model->deadline_ms = now_ms + timeout_ms;
    return true;
}

void diagnostic_capture_model_stop(diagnostic_capture_model_t *model)
{
    if (model != NULL && model->state == DIAGNOSTIC_CAPTURE_ACTIVE) {
        model->state = DIAGNOSTIC_CAPTURE_WAIT_NEUTRAL;
    }
}

void diagnostic_capture_model_yield_to_local(
    diagnostic_capture_model_t *model)
{
    if (model != NULL) {
        model->state = DIAGNOSTIC_CAPTURE_INACTIVE;
    }
}

bool diagnostic_capture_model_record(diagnostic_capture_model_t *model,
                                     uint8_t control, bool pressed)
{
    if (model == NULL || model->state != DIAGNOSTIC_CAPTURE_ACTIVE) {
        return false;
    }
    ++model->event_sequence;
    model->last_control = control;
    model->has_last_event = true;
    model->last_pressed = pressed;
    return true;
}

void diagnostic_capture_model_poll(diagnostic_capture_model_t *model,
                                   uint32_t now_ms, bool inputs_neutral)
{
    if (model == NULL) {
        return;
    }
    if (model->state == DIAGNOSTIC_CAPTURE_ACTIVE &&
        deadline_reached(now_ms, model->deadline_ms)) {
        model->state = DIAGNOSTIC_CAPTURE_WAIT_NEUTRAL;
        return;
    }
    if (model->state == DIAGNOSTIC_CAPTURE_WAIT_NEUTRAL && inputs_neutral) {
        model->state = DIAGNOSTIC_CAPTURE_INACTIVE;
    }
}

bool diagnostic_capture_model_owns_input(
    const diagnostic_capture_model_t *model)
{
    return model != NULL && model->state != DIAGNOSTIC_CAPTURE_INACTIVE;
}
