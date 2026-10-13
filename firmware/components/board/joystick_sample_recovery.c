#include "joystick_sample_recovery.h"

#include <stddef.h>

void joystick_sample_recovery_reset(joystick_sample_recovery_t *model)
{
    if (model != NULL) {
        *model = (joystick_sample_recovery_t){0};
    }
}

bool joystick_sample_recovery_failure(joystick_sample_recovery_t *model,
                                      uint32_t now_ticks,
                                      uint32_t timeout_ticks)
{
    if (model == NULL) {
        return false;
    }
    model->centered_samples = 0;
    if (model->has_success &&
        (uint32_t)(now_ticks - model->last_success_at) >= timeout_ticks) {
        model->waiting_for_center = true;
    }
    return model->waiting_for_center;
}

void joystick_sample_recovery_success(joystick_sample_recovery_t *model,
                                      uint32_t now_ticks, bool centered)
{
    if (model == NULL) {
        return;
    }
    model->has_success = true;
    model->last_success_at = now_ticks;
    if (!model->waiting_for_center) {
        return;
    }
    model->centered_samples = centered ? model->centered_samples + 1 : 0;
    if (model->centered_samples >= 2) {
        model->waiting_for_center = false;
        model->centered_samples = 0;
    }
}
