#pragma once

#include <stdbool.h>
#include <stdint.h>

typedef struct {
    bool active;
    uint32_t last_motion_ms;
} encoder_haptic_model_t;

void encoder_haptic_model_init(encoder_haptic_model_t *model);

bool encoder_haptic_model_should_pulse(encoder_haptic_model_t *model,
                                       uint32_t now_ms,
                                       uint32_t rearm_ms);
