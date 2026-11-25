#include "joystick_suspect_model.h"

#include <string.h>

void joystick_suspect_model_reset(joystick_suspect_model_t *model)
{
    if (model != NULL) {
        memset(model, 0, sizeof(*model));
    }
}

bool joystick_suspect_model_update(joystick_suspect_model_t *model,
                                   uint8_t directions,
                                   uint32_t now_ms)
{
    if (model == NULL) {
        return false;
    }
    if (directions == 0) {
        joystick_suspect_model_reset(model);
        return false;
    }
    if (model->suspect) {
        return true;
    }
    if (!model->tracking) {
        model->held_directions = directions;
        model->held_since_ms = now_ms;
        model->tracking = true;
        return false;
    }
    model->held_directions = directions;
    if ((uint32_t)(now_ms - model->held_since_ms) >=
        JOYSTICK_SUSPECT_HOLD_MS) {
        model->suspect = true;
    }
    return model->suspect;
}
