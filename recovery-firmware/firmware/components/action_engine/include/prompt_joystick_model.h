#pragma once

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    PROMPT_JOYSTICK_UP = 0,
    PROMPT_JOYSTICK_DOWN,
    PROMPT_JOYSTICK_LEFT,
    PROMPT_JOYSTICK_RIGHT,
    PROMPT_JOYSTICK_DIRECTION_COUNT,
} prompt_joystick_direction_t;

typedef struct {
    uint8_t active_mask;
    uint8_t eligible_mask;
    uint8_t selected_direction;
    bool locked;
} prompt_joystick_model_t;

typedef struct {
    bool trigger;
    bool release;
    uint8_t direction;
} prompt_joystick_result_t;

void prompt_joystick_model_init(prompt_joystick_model_t *model);

/**
 * Track one board direction transition. The first press selects one direction
 * for the entire physical excursion; no new selection is allowed until every
 * direction is released and the analog stick is back inside its deadzone.
 */
prompt_joystick_result_t prompt_joystick_model_update(
    prompt_joystick_model_t *model, uint8_t direction, bool pressed,
    bool radial_active, float angle_turns, uint8_t eligible_mask);

/** Finish a locked excursion after the analog stick has fully returned. */
prompt_joystick_result_t prompt_joystick_model_poll(
    prompt_joystick_model_t *model, bool radial_active);

#ifdef __cplusplus
}
#endif
