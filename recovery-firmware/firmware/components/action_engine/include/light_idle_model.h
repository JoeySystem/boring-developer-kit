#pragma once

#include <stdbool.h>
#include <stdint.h>

#define LIGHT_IDLE_DELAY_MS 60000U
#define LIGHT_IDLE_FADE_MS 1500U
#define LIGHT_IDLE_MIN_PERCENT 30U

typedef struct {
    uint32_t last_activity_ms;
    uint8_t percent;
} light_idle_model_t;

void light_idle_model_init(light_idle_model_t *model, uint32_t now_ms);
bool light_idle_model_note_activity(light_idle_model_t *model, uint32_t now_ms);
/* Eligibility is supplied by the existing application owners, not a new router. */
bool light_idle_model_poll(light_idle_model_t *model, uint32_t now_ms, bool eligible);
