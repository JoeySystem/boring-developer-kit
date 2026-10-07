#include "codex_micro_radial_filter.h"

#include <math.h>
#include <string.h>

#define TWO_PI_F 6.28318530717958647692f
#define DIRECTION_EPSILON 0.000001f

static float normalize_turns(float angle)
{
    angle -= floorf(angle);
    return angle >= 1.0f ? 0.0f : angle;
}

static float signed_circular_delta(float current, float previous)
{
    float delta = current - previous;
    if (delta > 0.5f) {
        delta -= 1.0f;
    } else if (delta < -0.5f) {
        delta += 1.0f;
    }
    return delta;
}

static int8_t delta_direction(float delta)
{
    if (delta > DIRECTION_EPSILON) {
        return 1;
    }
    if (delta < -DIRECTION_EPSILON) {
        return -1;
    }
    return 0;
}

static float circular_mean(const codex_micro_radial_filter_t *filter,
                           float fallback)
{
    float sum_sin = 0.0f;
    float sum_cos = 0.0f;
    for (uint8_t index = 0; index < filter->sample_count; ++index) {
        const float radians = filter->samples[index] * TWO_PI_F;
        sum_sin += sinf(radians);
        sum_cos += cosf(radians);
    }
    if (fabsf(sum_sin) < DIRECTION_EPSILON &&
        fabsf(sum_cos) < DIRECTION_EPSILON) {
        return fallback;
    }
    float mean = atan2f(sum_sin, sum_cos) / TWO_PI_F;
    if (mean < 0.0f) {
        mean += 1.0f;
    }
    return mean;
}

void codex_micro_radial_filter_reset(codex_micro_radial_filter_t *filter)
{
    if (filter != NULL) {
        memset(filter, 0, sizeof(*filter));
    }
}

float codex_micro_radial_filter_update(codex_micro_radial_filter_t *filter,
                                       float angle_turns,
                                       float reverse_jitter_turns)
{
    if (filter == NULL) {
        return normalize_turns(angle_turns);
    }

    const float raw = normalize_turns(angle_turns);
    filter->samples[filter->sample_index] = raw;
    filter->sample_index =
        (uint8_t)((filter->sample_index + 1u) % CODEX_MICRO_RADIAL_HISTORY);
    if (filter->sample_count < CODEX_MICRO_RADIAL_HISTORY) {
        filter->sample_count++;
    }
    const float smoothed = circular_mean(filter, raw);

    if (!filter->has_output) {
        filter->output = smoothed;
        filter->previous_raw = raw;
        filter->has_output = true;
        return filter->output;
    }

    const float raw_delta = signed_circular_delta(raw, filter->previous_raw);
    const int8_t raw_direction = delta_direction(raw_delta);
    filter->previous_raw = raw;

    const bool small_reverse =
        filter->raw_trend != 0 && raw_direction == -filter->raw_trend &&
        fabsf(raw_delta) <= reverse_jitter_turns;
    if (small_reverse && !filter->reverse_pending) {
        filter->reverse_pending = true;
        return filter->output;
    }

    if (small_reverse) {
        filter->raw_trend = raw_direction;
        filter->reverse_pending = false;
    } else {
        filter->reverse_pending = false;
        if (raw_direction != 0) {
            filter->raw_trend = raw_direction;
        }
    }
    filter->output = smoothed;
    return filter->output;
}

void codex_micro_radial_release_gate_reset(
    codex_micro_radial_release_gate_t *gate)
{
    if (gate != NULL) {
        memset(gate, 0, sizeof(*gate));
    }
}

bool codex_micro_radial_release_gate_update(
    codex_micro_radial_release_gate_t *gate, bool sample_active,
    int64_t now_us, uint32_t grace_us)
{
    if (gate == NULL) {
        return !sample_active;
    }
    if (sample_active) {
        codex_micro_radial_release_gate_reset(gate);
        return false;
    }
    if (gate->release_emitted) {
        return false;
    }
    if (!gate->inactive_pending || now_us < gate->inactive_since_us) {
        gate->inactive_since_us = now_us;
        gate->inactive_pending = true;
        if (grace_us > 0) {
            return false;
        }
    } else if ((uint64_t)(now_us - gate->inactive_since_us) < grace_us) {
        return false;
    }
    gate->inactive_pending = false;
    gate->release_emitted = true;
    return true;
}
