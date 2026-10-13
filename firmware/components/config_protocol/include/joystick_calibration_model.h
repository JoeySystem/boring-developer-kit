#pragma once

#include <stdbool.h>
#include <stdint.h>

enum {
    JOYSTICK_CALIBRATION_CENTER_WINDOW_MS = 500,
    JOYSTICK_CALIBRATION_TIMEOUT_MS = 120000,
    JOYSTICK_CALIBRATION_MIN_SIDE_TRAVEL = 400,
    JOYSTICK_CALIBRATION_MIN_DEADZONE = 180,
    JOYSTICK_CALIBRATION_MAX_DEADZONE = 512,
    JOYSTICK_CALIBRATION_DEADZONE_MARGIN = 32,
    JOYSTICK_CALIBRATION_RETURN_NEAR_CENTER = 180,
    JOYSTICK_CALIBRATION_RETURN_WINDOW_MS = 200,
    JOYSTICK_CALIBRATION_REQUIRED_RETURNS = 2,
    JOYSTICK_CALIBRATION_MAX_SAMPLE_GAP_MS = 60,
    JOYSTICK_CALIBRATION_MIN_CENTER_SAMPLES = 10,
};

typedef enum {
    JOYSTICK_CALIBRATION_INACTIVE = 0,
    JOYSTICK_CALIBRATION_CENTERING,
    JOYSTICK_CALIBRATION_CAPTURING,
} joystick_calibration_state_t;

typedef struct {
    bool valid;
    int minimum_x;
    int center_x;
    int maximum_x;
    int minimum_y;
    int center_y;
    int maximum_y;
    int deadzone_x;
    int deadzone_y;
} joystick_calibration_candidate_t;

typedef struct {
    joystick_calibration_state_t state;
    uint32_t session_id;
    uint32_t started_ms;
    uint32_t last_activity_ms;
    uint32_t last_sample_ms;
    uint32_t center_sample_count;
    int64_t center_sum_x;
    int64_t center_sum_y;
    int center_min_x;
    int center_max_x;
    int center_min_y;
    int center_max_y;
    int center_x;
    int center_y;
    int minimum_x;
    int maximum_x;
    int minimum_y;
    int maximum_y;
    int raw_x;
    int raw_y;
    bool away_since_return;
    uint32_t return_started_ms;
    uint32_t return_sample_count;
    int64_t return_sum_x;
    int64_t return_sum_y;
    int return_min_x;
    int return_max_x;
    int return_min_y;
    int return_max_y;
    uint32_t accepted_return_count;
    uint32_t accepted_sample_count;
    int64_t accepted_sum_x;
    int64_t accepted_sum_y;
    int accepted_min_x;
    int accepted_max_x;
    int accepted_min_y;
    int accepted_max_y;
} joystick_calibration_model_t;

void joystick_calibration_model_start(joystick_calibration_model_t *model,
                                      uint32_t session_id, uint32_t now_ms,
                                      int raw_x, int raw_y);
void joystick_calibration_model_sample(joystick_calibration_model_t *model,
                                       uint32_t now_ms, int raw_x, int raw_y);
void joystick_calibration_model_touch(joystick_calibration_model_t *model,
                                      uint32_t now_ms);
bool joystick_calibration_model_timed_out(
    const joystick_calibration_model_t *model, uint32_t now_ms);
bool joystick_calibration_model_travel_complete(
    const joystick_calibration_model_t *model);
bool joystick_calibration_model_candidate(
    const joystick_calibration_model_t *model,
    joystick_calibration_candidate_t *candidate);
void joystick_calibration_model_cancel(joystick_calibration_model_t *model);
