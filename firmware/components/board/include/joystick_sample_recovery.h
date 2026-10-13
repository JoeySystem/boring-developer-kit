#pragma once

#include <stdbool.h>
#include <stdint.h>

typedef struct {
    uint32_t last_success_at;
    uint8_t centered_samples;
    bool has_success;
    bool waiting_for_center;
} joystick_sample_recovery_t;

void joystick_sample_recovery_reset(joystick_sample_recovery_t *model);

/* Returns true once the last successful ADC pair is older than timeout_ticks. */
bool joystick_sample_recovery_failure(joystick_sample_recovery_t *model,
                                      uint32_t now_ticks,
                                      uint32_t timeout_ticks);

/* Two consecutive centered ADC pairs are required after an outage. */
void joystick_sample_recovery_success(joystick_sample_recovery_t *model,
                                      uint32_t now_ticks, bool centered);
