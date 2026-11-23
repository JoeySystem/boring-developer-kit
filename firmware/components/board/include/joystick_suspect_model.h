#pragma once

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define JOYSTICK_SUSPECT_HOLD_MS UINT32_C(10000)

typedef struct {
    uint8_t held_directions;
    uint32_t held_since_ms;
    bool tracking;
    bool suspect;
} joystick_suspect_model_t;

void joystick_suspect_model_reset(joystick_suspect_model_t *model);

/**
 * Track one continuously held direction mask. A centered sample clears both
 * the timer and a latched suspect state. This function never changes stored
 * calibration or filters the diagnostic sample supplied by the caller.
 */
bool joystick_suspect_model_update(joystick_suspect_model_t *model,
                                   uint8_t directions,
                                   uint32_t now_ms);

#ifdef __cplusplus
}
#endif
