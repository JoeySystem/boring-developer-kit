#include "joystick_calibration_model.h"

#include <limits.h>
#include <stddef.h>
#include <string.h>

static int absolute_value(int value)
{
    return value < 0 ? -value : value;
}

static int clamp_deadzone(int value)
{
    if (value < JOYSTICK_CALIBRATION_MIN_DEADZONE) {
        return JOYSTICK_CALIBRATION_MIN_DEADZONE;
    }
    if (value > JOYSTICK_CALIBRATION_MAX_DEADZONE) {
        return JOYSTICK_CALIBRATION_MAX_DEADZONE;
    }
    return value;
}

static void finish_centering(joystick_calibration_model_t *model)
{
    if (model->center_sample_count == 0) {
        return;
    }
    model->center_x = (int)(model->center_sum_x / model->center_sample_count);
    model->center_y = (int)(model->center_sum_y / model->center_sample_count);
    model->minimum_x = model->center_x;
    model->maximum_x = model->center_x;
    model->minimum_y = model->center_y;
    model->maximum_y = model->center_y;
    model->state = JOYSTICK_CALIBRATION_CAPTURING;
}

void joystick_calibration_model_start(joystick_calibration_model_t *model,
                                      uint32_t session_id, uint32_t now_ms,
                                      int raw_x, int raw_y)
{
    memset(model, 0, sizeof(*model));
    model->state = JOYSTICK_CALIBRATION_CENTERING;
    model->session_id = session_id;
    model->started_ms = now_ms;
    model->last_activity_ms = now_ms;
    model->center_min_x = INT_MAX;
    model->center_max_x = INT_MIN;
    model->center_min_y = INT_MAX;
    model->center_max_y = INT_MIN;
    joystick_calibration_model_sample(model, now_ms, raw_x, raw_y);
}

void joystick_calibration_model_sample(joystick_calibration_model_t *model,
                                       uint32_t now_ms, int raw_x, int raw_y)
{
    if (model == NULL || model->state == JOYSTICK_CALIBRATION_INACTIVE) {
        return;
    }
    model->raw_x = raw_x;
    model->raw_y = raw_y;
    if (model->state == JOYSTICK_CALIBRATION_CENTERING) {
        if ((uint32_t)(now_ms - model->started_ms) >=
            JOYSTICK_CALIBRATION_CENTER_WINDOW_MS) {
            finish_centering(model);
        } else {
            model->center_sum_x += raw_x;
            model->center_sum_y += raw_y;
            ++model->center_sample_count;
            if (raw_x < model->center_min_x) model->center_min_x = raw_x;
            if (raw_x > model->center_max_x) model->center_max_x = raw_x;
            if (raw_y < model->center_min_y) model->center_min_y = raw_y;
            if (raw_y > model->center_max_y) model->center_max_y = raw_y;
            return;
        }
    }
    if (raw_x < model->minimum_x) model->minimum_x = raw_x;
    if (raw_x > model->maximum_x) model->maximum_x = raw_x;
    if (raw_y < model->minimum_y) model->minimum_y = raw_y;
    if (raw_y > model->maximum_y) model->maximum_y = raw_y;
}

void joystick_calibration_model_touch(joystick_calibration_model_t *model,
                                      uint32_t now_ms)
{
    if (model != NULL && model->state != JOYSTICK_CALIBRATION_INACTIVE) {
        model->last_activity_ms = now_ms;
    }
}

bool joystick_calibration_model_timed_out(
    const joystick_calibration_model_t *model, uint32_t now_ms)
{
    return model != NULL && model->state != JOYSTICK_CALIBRATION_INACTIVE &&
           (uint32_t)(now_ms - model->last_activity_ms) >=
               JOYSTICK_CALIBRATION_TIMEOUT_MS;
}

bool joystick_calibration_model_candidate(
    const joystick_calibration_model_t *model,
    joystick_calibration_candidate_t *candidate)
{
    if (model == NULL || candidate == NULL ||
        model->state != JOYSTICK_CALIBRATION_CAPTURING) {
        return false;
    }
    const bool travel_valid =
        model->center_x - model->minimum_x >=
            JOYSTICK_CALIBRATION_MIN_SIDE_TRAVEL &&
        model->maximum_x - model->center_x >=
            JOYSTICK_CALIBRATION_MIN_SIDE_TRAVEL &&
        model->center_y - model->minimum_y >=
            JOYSTICK_CALIBRATION_MIN_SIDE_TRAVEL &&
        model->maximum_y - model->center_y >=
            JOYSTICK_CALIBRATION_MIN_SIDE_TRAVEL;
    const int jitter_x =
        absolute_value(model->center_x - model->center_min_x) >
                absolute_value(model->center_max_x - model->center_x)
            ? absolute_value(model->center_x - model->center_min_x)
            : absolute_value(model->center_max_x - model->center_x);
    const int jitter_y =
        absolute_value(model->center_y - model->center_min_y) >
                absolute_value(model->center_max_y - model->center_y)
            ? absolute_value(model->center_y - model->center_min_y)
            : absolute_value(model->center_max_y - model->center_y);
    *candidate = (joystick_calibration_candidate_t) {
        .valid = travel_valid,
        .minimum_x = model->minimum_x,
        .center_x = model->center_x,
        .maximum_x = model->maximum_x,
        .minimum_y = model->minimum_y,
        .center_y = model->center_y,
        .maximum_y = model->maximum_y,
        .deadzone_x = clamp_deadzone(
            jitter_x + JOYSTICK_CALIBRATION_DEADZONE_MARGIN),
        .deadzone_y = clamp_deadzone(
            jitter_y + JOYSTICK_CALIBRATION_DEADZONE_MARGIN),
    };
    return travel_valid;
}

void joystick_calibration_model_cancel(joystick_calibration_model_t *model)
{
    if (model != NULL) {
        memset(model, 0, sizeof(*model));
    }
}
