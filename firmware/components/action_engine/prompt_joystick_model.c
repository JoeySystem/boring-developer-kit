#include "prompt_joystick_model.h"

#include <stddef.h>

static prompt_joystick_result_t no_result(void)
{
    return (prompt_joystick_result_t) {0};
}

void prompt_joystick_model_init(prompt_joystick_model_t *model)
{
    if (model == NULL) {
        return;
    }
    *model = (prompt_joystick_model_t) {0};
}

static uint8_t direction_from_angle(float angle_turns,
                                    uint8_t fallback_direction)
{
    if (angle_turns < 0.0f || angle_turns >= 1.0f) {
        return fallback_direction;
    }
    if (angle_turns < 0.125f || angle_turns >= 0.875f) {
        return PROMPT_JOYSTICK_RIGHT;
    }
    if (angle_turns < 0.375f) {
        return PROMPT_JOYSTICK_DOWN;
    }
    if (angle_turns < 0.625f) {
        return PROMPT_JOYSTICK_LEFT;
    }
    return PROMPT_JOYSTICK_UP;
}

static prompt_joystick_result_t finish_if_centered(
    prompt_joystick_model_t *model, bool radial_active)
{
    if (model == NULL || !model->locked || model->active_mask != 0u ||
        radial_active) {
        return no_result();
    }
    const uint8_t selected = model->selected_direction;
    prompt_joystick_model_init(model);
    return (prompt_joystick_result_t) {
        .release = true,
        .direction = selected,
    };
}

prompt_joystick_result_t prompt_joystick_model_update(
    prompt_joystick_model_t *model, uint8_t direction, bool pressed,
    bool radial_active, float angle_turns, uint8_t eligible_mask)
{
    if (model == NULL || direction >= PROMPT_JOYSTICK_DIRECTION_COUNT) {
        return no_result();
    }
    const uint8_t mask = (uint8_t)(1u << direction);
    if (pressed) {
        if (!model->locked && (eligible_mask & mask) == 0u) {
            return no_result();
        }
        model->active_mask |= mask;
        if (model->locked) {
            return no_result();
        }
        model->locked = true;
        model->eligible_mask = eligible_mask;
        model->selected_direction = radial_active
                                        ? direction_from_angle(angle_turns,
                                                               direction)
                                        : direction;
        if ((eligible_mask &
             (uint8_t)(1u << model->selected_direction)) == 0u) {
            model->selected_direction = direction;
        }
        return (prompt_joystick_result_t) {
            .trigger = true,
            .direction = model->selected_direction,
        };
    }
    model->active_mask &= (uint8_t)~mask;
    return finish_if_centered(model, radial_active);
}

prompt_joystick_result_t prompt_joystick_model_poll(
    prompt_joystick_model_t *model, bool radial_active)
{
    return finish_if_centered(model, radial_active);
}
