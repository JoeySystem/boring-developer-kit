#pragma once

#include <stdbool.h>
#include <stdint.h>

typedef enum {
    POMODORO_IDLE = 0,
    POMODORO_RUNNING,
    POMODORO_PAUSED,
    POMODORO_ALERT,
    POMODORO_DONE,
} pomodoro_state_t;

typedef enum {
    POMODORO_EVENT_NONE = 0,
    POMODORO_EVENT_EXPIRED = 1 << 0,
    POMODORO_EVENT_ALERT_PULSE = 1 << 1,
    POMODORO_EVENT_ALERT_FINISHED = 1 << 2,
} pomodoro_event_t;

typedef struct {
    pomodoro_state_t state;
    uint8_t minutes;
    uint32_t deadline_ms;
    uint32_t remaining_ms;
    uint32_t alert_started_ms;
    uint32_t next_pulse_ms;
    uint32_t total_seconds;
} pomodoro_model_t;

void pomodoro_model_init(pomodoro_model_t *model, uint8_t last_minutes);
void pomodoro_model_set_minutes(pomodoro_model_t *model, int minutes);
void pomodoro_model_start(pomodoro_model_t *model, uint32_t now_ms);
bool pomodoro_model_pause(pomodoro_model_t *model, uint32_t now_ms);
bool pomodoro_model_resume(pomodoro_model_t *model, uint32_t now_ms);
void pomodoro_model_cancel(pomodoro_model_t *model);
void pomodoro_model_acknowledge(pomodoro_model_t *model);
uint8_t pomodoro_model_poll(pomodoro_model_t *model, uint32_t now_ms);
uint32_t pomodoro_model_remaining_ms(const pomodoro_model_t *model,
                                     uint32_t now_ms);
