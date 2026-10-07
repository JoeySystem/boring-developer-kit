#include "light_idle_model.h"

void light_idle_model_init(light_idle_model_t *model, uint32_t now_ms)
{
    model->last_activity_ms = now_ms;
    model->percent = 100;
}

bool light_idle_model_note_activity(light_idle_model_t *model, uint32_t now_ms)
{
    const bool changed = model->percent != 100;
    light_idle_model_init(model, now_ms);
    return changed;
}

bool light_idle_model_poll(light_idle_model_t *model, uint32_t now_ms, bool eligible)
{
    if (!eligible) return light_idle_model_note_activity(model, now_ms);
    const uint32_t elapsed = now_ms - model->last_activity_ms;
    uint8_t percent = 100;
    if (elapsed > LIGHT_IDLE_DELAY_MS) {
        uint32_t fade = elapsed - LIGHT_IDLE_DELAY_MS;
        if (fade > LIGHT_IDLE_FADE_MS) fade = LIGHT_IDLE_FADE_MS;
        percent -= (uint8_t)((100U - LIGHT_IDLE_MIN_PERCENT) * fade / LIGHT_IDLE_FADE_MS);
    }
    const bool changed = model->percent != percent;
    model->percent = percent;
    return changed;
}
