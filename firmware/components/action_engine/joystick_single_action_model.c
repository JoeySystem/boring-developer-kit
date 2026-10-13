#include "joystick_single_action_model.h"

#include <stddef.h>

#include "prompt_joystick_model.h"

void joystick_single_action_model_reset(joystick_single_action_model_t *model)
{
    if (model != NULL) {
        *model = (joystick_single_action_model_t){0};
    }
}

joystick_single_action_result_t joystick_single_action_model_update(
    joystick_single_action_model_t *model, bool radial_active,
    float angle_turns, uint32_t sample_count)
{
    joystick_single_action_result_t result = {0};
    if (model == NULL) {
        return result;
    }
    if (!radial_active) {
        if (model->active) {
            result.release = true;
            result.direction = model->active_direction;
        }
        joystick_single_action_model_reset(model);
        return result;
    }
    if (model->sample_seen && sample_count == model->last_sample_count) {
        return result;
    }
    model->sample_seen = true;
    model->last_sample_count = sample_count;
    if (model->active || angle_turns < 0.0f || angle_turns >= 1.0f) {
        return result;
    }
    const uint8_t direction = prompt_joystick_direction_from_angle(
        angle_turns, PROMPT_JOYSTICK_UP);
    if (model->consistent_samples == 0 ||
        direction != model->pending_direction) {
        model->pending_direction = direction;
        model->consistent_samples = 1;
        return result;
    }
    model->active = true;
    model->active_direction = direction;
    result.press = true;
    result.direction = direction;
    return result;
}
