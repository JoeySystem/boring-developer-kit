#pragma once

#include <stdbool.h>
#include <stdint.h>

typedef struct {
    uint8_t pending_direction;
    uint8_t consistent_samples;
    uint8_t active_direction;
    bool active;
    bool wait_center;
    bool sample_seen;
    uint32_t last_sample_count;
} joystick_single_action_model_t;

typedef struct {
    bool press;
    bool release;
    uint8_t direction;
} joystick_single_action_result_t;

void joystick_single_action_model_reset_to_idle(
    joystick_single_action_model_t *model);
void joystick_single_action_model_reset_latched(
    joystick_single_action_model_t *model);

/* One cardinal action per excursion, after two matching radial samples. */
joystick_single_action_result_t joystick_single_action_model_update(
    joystick_single_action_model_t *model, bool radial_active,
    float angle_turns, uint32_t sample_count);
