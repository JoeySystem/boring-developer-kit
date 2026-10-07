#pragma once

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define CODEX_MICRO_RADIAL_HISTORY 3u

typedef struct {
    float samples[CODEX_MICRO_RADIAL_HISTORY];
    uint8_t sample_count;
    uint8_t sample_index;
    float output;
    float previous_raw;
    int8_t raw_trend;
    bool has_output;
    bool reverse_pending;
} codex_micro_radial_filter_t;

typedef struct {
    int64_t inactive_since_us;
    bool inactive_pending;
    bool release_emitted;
} codex_micro_radial_release_gate_t;

/** Clear all trajectory history. The next sample becomes the new baseline. */
void codex_micro_radial_filter_reset(codex_micro_radial_filter_t *filter);

/**
 * Smooth one real joystick angle using a wrap-safe three-sample circular mean.
 *
 * A single reverse raw step no larger than @p reverse_jitter_turns holds the
 * previous output. A second consecutive reverse step is treated as intentional
 * movement. No samples are inserted or extrapolated.
 */
float codex_micro_radial_filter_update(codex_micro_radial_filter_t *filter,
                                       float angle_turns,
                                       float reverse_jitter_turns);

/** Cancel any pending or previously emitted center-release decision. */
void codex_micro_radial_release_gate_reset(
    codex_micro_radial_release_gate_t *gate);

/**
 * Return true once after the joystick remains inactive for @p grace_us.
 *
 * An active sample cancels a pending release. The caller may therefore retain
 * the last real angle and trajectory history without synthesizing reports
 * during a brief center dropout.
 */
bool codex_micro_radial_release_gate_update(
    codex_micro_radial_release_gate_t *gate, bool sample_active,
    int64_t now_us, uint32_t grace_us);

#ifdef __cplusplus
}
#endif
