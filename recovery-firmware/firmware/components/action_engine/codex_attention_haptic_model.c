#include "codex_attention_haptic_model.h"

#include <string.h>

void codex_attention_haptic_model_init(codex_attention_haptic_model_t *model)
{
    memset(model, 0, sizeof(*model));
}

codex_attention_haptic_event_t codex_attention_haptic_model_poll(
    codex_attention_haptic_model_t *model, uint32_t now_ms,
    codex_micro_attention_t notifications, bool enabled)
{
    if (!enabled) {
        const bool stop = model->pulse_on;
        if (model->active) {
            model->quiet_until_ms = now_ms + CODEX_ATTENTION_HAPTIC_QUIET_MS;
            model->cooldown = true;
        }
        model->active = model->pulse_on = model->collecting = false;
        model->pending = model->remaining = 0u;
        return stop ? CODEX_ATTENTION_HAPTIC_STOP : CODEX_ATTENTION_HAPTIC_IDLE;
    }
    if (model->active) {
        if ((int32_t)(now_ms - model->deadline_ms) < 0) {
            return CODEX_ATTENTION_HAPTIC_IDLE;
        }
        if (model->pulse_on) {
            model->pulse_on = false;
            model->deadline_ms = now_ms + CODEX_ATTENTION_HAPTIC_GAP_MS;
            if (model->remaining == 0u) {
                model->active = false;
                model->cooldown = true;
                model->quiet_until_ms = now_ms + CODEX_ATTENTION_HAPTIC_QUIET_MS;
            }
            return CODEX_ATTENTION_HAPTIC_STOP;
        }
    }
    if (!model->active) {
        if (model->cooldown) {
            if ((int32_t)(now_ms - model->quiet_until_ms) < 0) {
                return CODEX_ATTENTION_HAPTIC_IDLE;
            }
            model->cooldown = false;
        }
        model->pending |= (uint8_t)notifications;
        if (model->pending == 0u) {
            return CODEX_ATTENTION_HAPTIC_IDLE;
        }
        if (!model->collecting) {
            model->collecting = true;
            model->deadline_ms = now_ms + CODEX_ATTENTION_HAPTIC_MERGE_MS;
            return CODEX_ATTENTION_HAPTIC_IDLE;
        }
        if ((int32_t)(now_ms - model->deadline_ms) < 0) {
            return CODEX_ATTENTION_HAPTIC_IDLE;
        }
        model->collecting = false;
        model->remaining = (model->pending & CODEX_ATTENTION_ERROR) ? 3u :
            (model->pending & CODEX_ATTENTION_ACTION_REQUIRED) ? 2u : 1u;
        model->pending = 0u;
        model->active = true;
    }
    --model->remaining;
    model->pulse_on = true;
    model->deadline_ms = now_ms + CODEX_ATTENTION_HAPTIC_DURATION_MS;
    return CODEX_ATTENTION_HAPTIC_PULSE;
}
