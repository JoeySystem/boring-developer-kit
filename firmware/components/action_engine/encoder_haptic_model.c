#include "encoder_haptic_model.h"

#include <stddef.h>

void encoder_haptic_model_init(encoder_haptic_model_t *model)
{
    if (model == NULL) {
        return;
    }
    *model = (encoder_haptic_model_t) {0};
}

bool encoder_haptic_model_should_pulse(encoder_haptic_model_t *model,
                                       uint32_t now_ms,
                                       uint32_t rearm_ms)
{
    if (model == NULL) {
        return false;
    }
    const bool should_pulse =
        !model->active || (uint32_t)(now_ms - model->last_motion_ms) >= rearm_ms;
    model->active = true;
    model->last_motion_ms = now_ms;
    return should_pulse;
}
