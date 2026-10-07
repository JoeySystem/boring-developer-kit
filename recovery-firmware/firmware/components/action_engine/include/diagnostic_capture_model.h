#pragma once

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    DIAGNOSTIC_CAPTURE_INACTIVE = 0,
    DIAGNOSTIC_CAPTURE_ACTIVE,
    DIAGNOSTIC_CAPTURE_WAIT_NEUTRAL,
} diagnostic_capture_state_t;

typedef struct {
    diagnostic_capture_state_t state;
    uint32_t deadline_ms;
    uint32_t event_sequence;
    uint8_t last_control;
    bool has_last_event;
    bool last_pressed;
} diagnostic_capture_model_t;

/** Initialize an inactive capture session with no accumulated events. */
void diagnostic_capture_model_init(diagnostic_capture_model_t *model);

/**
 * Start a new session or renew the active session lease.
 *
 * A new session clears the previous event snapshot. Renewing an active session
 * preserves it. A session waiting for neutral cannot be restarted yet.
 */
bool diagnostic_capture_model_start(diagnostic_capture_model_t *model,
                                    uint32_t now_ms,
                                    uint32_t timeout_ms);

/** End an active session and require neutral input before output resumes. */
void diagnostic_capture_model_stop(diagnostic_capture_model_t *model);

/** End capture immediately when another local owner takes control. */
void diagnostic_capture_model_yield_to_local(
    diagnostic_capture_model_t *model);

/** Record one logical physical edge while capture is active. */
bool diagnostic_capture_model_record(diagnostic_capture_model_t *model,
                                     uint8_t control, bool pressed);

/** Expire the lease and finish the wait-neutral phase when safe. */
void diagnostic_capture_model_poll(diagnostic_capture_model_t *model,
                                   uint32_t now_ms, bool inputs_neutral);

/** True while diagnostic capture or its wait-neutral barrier owns input. */
bool diagnostic_capture_model_owns_input(
    const diagnostic_capture_model_t *model);

#ifdef __cplusplus
}
#endif
