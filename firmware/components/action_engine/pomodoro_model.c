#include "pomodoro_model.h"

#include <string.h>

#define POMODORO_MINUTES_MIN 1
#define POMODORO_MINUTES_MAX 60
#define POMODORO_DEFAULT_MINUTES 25
#define POMODORO_ALERT_PERIOD_MS 1000U
#define POMODORO_ALERT_LIMIT_MS 60000U

static bool reached(uint32_t now, uint32_t deadline)
{
    return (int32_t)(now - deadline) >= 0;
}

void pomodoro_model_init(pomodoro_model_t *model, uint8_t last_minutes)
{
    if (model == NULL) {
        return;
    }
    memset(model, 0, sizeof(*model));
    model->minutes =
        last_minutes >= POMODORO_MINUTES_MIN &&
                last_minutes <= POMODORO_MINUTES_MAX
            ? last_minutes
            : POMODORO_DEFAULT_MINUTES;
}

void pomodoro_model_set_minutes(pomodoro_model_t *model, int minutes)
{
    if (model == NULL) {
        return;
    }
    if (minutes < POMODORO_MINUTES_MIN) minutes = POMODORO_MINUTES_MIN;
    if (minutes > POMODORO_MINUTES_MAX) minutes = POMODORO_MINUTES_MAX;
    model->minutes = (uint8_t)minutes;
}

void pomodoro_model_start(pomodoro_model_t *model, uint32_t now_ms)
{
    if (model == NULL) {
        return;
    }
    model->total_seconds = (uint32_t)model->minutes * 60U;
    model->remaining_ms = model->total_seconds * 1000U;
    model->deadline_ms = now_ms + model->remaining_ms;
    model->state = POMODORO_RUNNING;
}

uint32_t pomodoro_model_remaining_ms(const pomodoro_model_t *model,
                                     uint32_t now_ms)
{
    if (model == NULL) return 0;
    if (model->state == POMODORO_PAUSED) return model->remaining_ms;
    if (model->state != POMODORO_RUNNING || reached(now_ms, model->deadline_ms)) {
        return 0;
    }
    return model->deadline_ms - now_ms;
}

bool pomodoro_model_pause(pomodoro_model_t *model, uint32_t now_ms)
{
    if (model == NULL || model->state != POMODORO_RUNNING) return false;
    model->remaining_ms = pomodoro_model_remaining_ms(model, now_ms);
    model->state = POMODORO_PAUSED;
    return true;
}

bool pomodoro_model_resume(pomodoro_model_t *model, uint32_t now_ms)
{
    if (model == NULL || model->state != POMODORO_PAUSED ||
        model->remaining_ms == 0) return false;
    model->deadline_ms = now_ms + model->remaining_ms;
    model->state = POMODORO_RUNNING;
    return true;
}

void pomodoro_model_cancel(pomodoro_model_t *model)
{
    if (model == NULL) return;
    const uint8_t minutes = model->minutes;
    memset(model, 0, sizeof(*model));
    model->minutes = minutes;
}

void pomodoro_model_acknowledge(pomodoro_model_t *model)
{
    if (model != NULL && (model->state == POMODORO_ALERT ||
                          model->state == POMODORO_DONE)) {
        pomodoro_model_cancel(model);
    }
}

uint8_t pomodoro_model_poll(pomodoro_model_t *model, uint32_t now_ms)
{
    if (model == NULL) return POMODORO_EVENT_NONE;
    if (model->state == POMODORO_RUNNING &&
        reached(now_ms, model->deadline_ms)) {
        model->state = POMODORO_ALERT;
        model->alert_started_ms = now_ms;
        model->next_pulse_ms = now_ms + POMODORO_ALERT_PERIOD_MS;
        return POMODORO_EVENT_EXPIRED | POMODORO_EVENT_ALERT_PULSE;
    }
    if (model->state != POMODORO_ALERT) return POMODORO_EVENT_NONE;
    if (reached(now_ms, model->alert_started_ms + POMODORO_ALERT_LIMIT_MS)) {
        model->state = POMODORO_DONE;
        return POMODORO_EVENT_ALERT_FINISHED;
    }
    if (reached(now_ms, model->next_pulse_ms)) {
        model->next_pulse_ms = now_ms + POMODORO_ALERT_PERIOD_MS;
        return POMODORO_EVENT_ALERT_PULSE;
    }
    return POMODORO_EVENT_NONE;
}
