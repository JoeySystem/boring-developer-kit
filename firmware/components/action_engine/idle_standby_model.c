#include "idle_standby_model.h"

#include <stddef.h>

#define MINUTE_MS 60000U

void idle_standby_model_init(idle_standby_model_t *model,
                             uint8_t timeout_minutes,
                             uint32_t now_ms)
{
    if (model == NULL) {
        return;
    }
    model->timeout_ms = (uint32_t)timeout_minutes * MINUTE_MS;
    model->last_activity_ms = now_ms;
    model->active = false;
}

void idle_standby_model_set_timeout(idle_standby_model_t *model,
                                    uint8_t timeout_minutes,
                                    uint32_t now_ms)
{
    if (model == NULL) {
        return;
    }
    model->timeout_ms = (uint32_t)timeout_minutes * MINUTE_MS;
    model->last_activity_ms = now_ms;
    if (model->timeout_ms == 0) {
        model->active = false;
    }
}

void idle_standby_model_note_activity(idle_standby_model_t *model,
                                      uint32_t now_ms)
{
    if (model == NULL || model->active) {
        return;
    }
    model->last_activity_ms = now_ms;
}

bool idle_standby_model_poll(idle_standby_model_t *model,
                             uint32_t now_ms,
                             bool blocked)
{
    if (model == NULL || model->active || model->timeout_ms == 0 || blocked) {
        return false;
    }
    if ((uint32_t)(now_ms - model->last_activity_ms) < model->timeout_ms) {
        return false;
    }
    model->active = true;
    return true;
}

void idle_standby_model_wake(idle_standby_model_t *model, uint32_t now_ms)
{
    if (model == NULL) {
        return;
    }
    model->active = false;
    model->last_activity_ms = now_ms;
}
