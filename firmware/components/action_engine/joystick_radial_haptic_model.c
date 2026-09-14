#include "joystick_radial_haptic_model.h"

#include <string.h>

void joystick_radial_haptic_model_init(
    joystick_radial_haptic_model_t *model)
{
    if (model != NULL) {
        memset(model, 0, sizeof(*model));
    }
}

bool joystick_radial_haptic_model_update(
    joystick_radial_haptic_model_t *model, bool radial_active)
{
    if (model == NULL) {
        return false;
    }
    if (!radial_active) {
        model->active = false;
        return false;
    }
    if (model->active) {
        return false;
    }

    model->active = true;
    return true;
}
