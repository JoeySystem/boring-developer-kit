#pragma once

#include <stdbool.h>
#include <stdint.h>

/* A finite display transition. Business state is never delayed by this model. */
typedef struct {
    uint32_t started_ms;
    uint32_t duration_ms;
    float from;
    float target;
    bool active;
} mist_motion_model_t;

void mist_motion_reset(mist_motion_model_t *motion, float position);
float mist_motion_phase(const mist_motion_model_t *motion, uint32_t now_ms);
float mist_motion_position(const mist_motion_model_t *motion, uint32_t now_ms);
void mist_motion_start(mist_motion_model_t *motion, uint32_t now_ms,
                       uint32_t duration_ms, float from, float target);
void mist_motion_step(mist_motion_model_t *motion, uint32_t now_ms,
                      uint8_t item_count, int delta);
void mist_motion_finish(mist_motion_model_t *motion);
