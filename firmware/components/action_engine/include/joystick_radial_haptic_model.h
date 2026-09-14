#pragma once

#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    bool active;
} joystick_radial_haptic_model_t;

/** Reset the current radial excursion. */
void joystick_radial_haptic_model_init(
    joystick_radial_haptic_model_t *model);

/**
 * Return true exactly once when one radial excursion begins.
 *
 * Continuous radial motion remains silent. An inactive sample rearms the next
 * excursion without producing feedback.
 */
bool joystick_radial_haptic_model_update(
    joystick_radial_haptic_model_t *model, bool radial_active);

#ifdef __cplusplus
}
#endif
