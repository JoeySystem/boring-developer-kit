#include "mist_motion_model.h"

void mist_motion_reset(mist_motion_model_t *motion, float position)
{
    *motion = (mist_motion_model_t){.from = position, .target = position};
}

float mist_motion_phase(const mist_motion_model_t *motion, uint32_t now_ms)
{
    if (!motion->active || motion->duration_ms == 0) return 1.0f;
    const uint32_t elapsed = now_ms - motion->started_ms;
    return elapsed >= motion->duration_ms
        ? 1.0f : (float)elapsed / (float)motion->duration_ms;
}

float mist_motion_position(const mist_motion_model_t *motion, uint32_t now_ms)
{
    const float t = mist_motion_phase(motion, now_ms);
    const float eased = motion->duration_ms == 560U
        ? t * t * (3.0f - 2.0f * t)
        : 1.0f - (1.0f - t) * (1.0f - t) * (1.0f - t);
    return motion->from + (motion->target - motion->from) * eased;
}

void mist_motion_start(mist_motion_model_t *motion, uint32_t now_ms,
                       uint32_t duration_ms, float from, float target)
{
    *motion = (mist_motion_model_t){
        .started_ms = now_ms, .duration_ms = duration_ms,
        .from = from, .target = target, .active = true,
    };
}

void mist_motion_step(mist_motion_model_t *motion, uint32_t now_ms,
                      uint8_t item_count, int delta)
{
    /* Keep the unwrapped destination so reversing and wrap never jump. */
    const float from = mist_motion_position(motion, now_ms);
    const float target = motion->target + (float)delta;
    mist_motion_start(motion, now_ms, item_count == 2U ? 560U : 280U,
                       from, target);
}

void mist_motion_finish(mist_motion_model_t *motion)
{
    motion->from = motion->target;
    motion->active = false;
}
