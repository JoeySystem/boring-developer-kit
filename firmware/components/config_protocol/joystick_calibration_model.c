#include "joystick_calibration_model.h"

#include <limits.h>
#include <stddef.h>
#include <string.h>

static int absolute_value(int value)
{
    return value < 0 ? -value : value;
}

static int maximum(int left, int right)
{
    return left > right ? left : right;
}

static void clear_return_window(joystick_calibration_model_t *model)
{
    model->return_sample_count = 0;
    model->return_sum_x = 0;
    model->return_sum_y = 0;
}

static void restart_center_window(joystick_calibration_model_t *model,
                                  uint32_t now_ms)
{
    model->started_ms = now_ms;
    model->center_sample_count = 0;
    model->center_sum_x = 0;
    model->center_sum_y = 0;
    model->center_min_x = INT_MAX;
    model->center_max_x = INT_MIN;
    model->center_min_y = INT_MAX;
    model->center_max_y = INT_MIN;
}

static void sample_return_window(joystick_calibration_model_t *model,
                                 uint32_t now_ms, int x, int y)
{
    const bool away =
        absolute_value(x - model->center_x) >=
            JOYSTICK_CALIBRATION_MIN_SIDE_TRAVEL ||
        absolute_value(y - model->center_y) >=
            JOYSTICK_CALIBRATION_MIN_SIDE_TRAVEL;
    if (away) {
        model->away_since_return = true;
        clear_return_window(model);
        return;
    }
    const bool near_center =
        absolute_value(x - model->center_x) <=
            JOYSTICK_CALIBRATION_RETURN_NEAR_CENTER &&
        absolute_value(y - model->center_y) <=
            JOYSTICK_CALIBRATION_RETURN_NEAR_CENTER;
    if (!model->away_since_return || !near_center) {
        clear_return_window(model);
        return;
    }
    if (model->return_sample_count == 0) {
        model->return_started_ms = now_ms;
        model->return_min_x = model->return_max_x = x;
        model->return_min_y = model->return_max_y = y;
    }
    model->return_sum_x += x;
    model->return_sum_y += y;
    ++model->return_sample_count;
    if (x < model->return_min_x) model->return_min_x = x;
    if (x > model->return_max_x) model->return_max_x = x;
    if (y < model->return_min_y) model->return_min_y = y;
    if (y > model->return_max_y) model->return_max_y = y;
    if ((uint32_t)(now_ms - model->return_started_ms) <
        JOYSTICK_CALIBRATION_RETURN_WINDOW_MS ||
        model->return_sample_count < 3) {
        return;
    }
    if (model->accepted_return_count == 0) {
        model->accepted_min_x = model->return_min_x;
        model->accepted_max_x = model->return_max_x;
        model->accepted_min_y = model->return_min_y;
        model->accepted_max_y = model->return_max_y;
    } else {
        if (model->return_min_x < model->accepted_min_x)
            model->accepted_min_x = model->return_min_x;
        if (model->return_max_x > model->accepted_max_x)
            model->accepted_max_x = model->return_max_x;
        if (model->return_min_y < model->accepted_min_y)
            model->accepted_min_y = model->return_min_y;
        if (model->return_max_y > model->accepted_max_y)
            model->accepted_max_y = model->return_max_y;
    }
    model->accepted_sum_x += model->return_sum_x;
    model->accepted_sum_y += model->return_sum_y;
    model->accepted_sample_count += model->return_sample_count;
    ++model->accepted_return_count;
    model->away_since_return = false;
    clear_return_window(model);
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
    restart_center_window(model, now_ms);
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
    if (model->state == JOYSTICK_CALIBRATION_CENTERING &&
        model->center_sample_count > 0 &&
        (uint32_t)(now_ms - model->last_sample_ms) >
            JOYSTICK_CALIBRATION_MAX_SAMPLE_GAP_MS) {
        restart_center_window(model, now_ms);
    } else if (model->state == JOYSTICK_CALIBRATION_CAPTURING &&
               model->return_sample_count > 0 &&
               (uint32_t)(now_ms - model->last_sample_ms) >
                   JOYSTICK_CALIBRATION_MAX_SAMPLE_GAP_MS) {
        clear_return_window(model);
    }
    model->last_sample_ms = now_ms;
    if (model->state == JOYSTICK_CALIBRATION_CENTERING) {
        if ((uint32_t)(now_ms - model->started_ms) >=
                JOYSTICK_CALIBRATION_CENTER_WINDOW_MS &&
            model->center_sample_count >=
                JOYSTICK_CALIBRATION_MIN_CENTER_SAMPLES) {
            finish_centering(model);
        } else {
            if ((uint32_t)(now_ms - model->started_ms) >=
                JOYSTICK_CALIBRATION_CENTER_WINDOW_MS) {
                restart_center_window(model, now_ms);
            }
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
    sample_return_window(model, now_ms, raw_x, raw_y);
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

bool joystick_calibration_model_travel_complete(
    const joystick_calibration_model_t *model)
{
    if (model == NULL ||
        model->state != JOYSTICK_CALIBRATION_CAPTURING) {
        return false;
    }
    return model->center_x - model->minimum_x >=
               JOYSTICK_CALIBRATION_MIN_SIDE_TRAVEL &&
           model->maximum_x - model->center_x >=
               JOYSTICK_CALIBRATION_MIN_SIDE_TRAVEL &&
           model->center_y - model->minimum_y >=
               JOYSTICK_CALIBRATION_MIN_SIDE_TRAVEL &&
           model->maximum_y - model->center_y >=
               JOYSTICK_CALIBRATION_MIN_SIDE_TRAVEL;
}

bool joystick_calibration_model_candidate(
    const joystick_calibration_model_t *model,
    joystick_calibration_candidate_t *candidate)
{
    if (candidate == NULL ||
        !joystick_calibration_model_travel_complete(model)) {
        return false;
    }
    if (model->accepted_return_count <
            JOYSTICK_CALIBRATION_REQUIRED_RETURNS ||
        model->accepted_sample_count == 0) {
        return false;
    }
    const int center_x =
        (int)(model->accepted_sum_x / model->accepted_sample_count);
    const int center_y =
        (int)(model->accepted_sum_y / model->accepted_sample_count);
    const int jitter_x = maximum(
        maximum(absolute_value(center_x - model->center_min_x),
                absolute_value(model->center_max_x - center_x)),
        maximum(absolute_value(center_x - model->accepted_min_x),
                absolute_value(model->accepted_max_x - center_x)));
    const int jitter_y = maximum(
        maximum(absolute_value(center_y - model->center_min_y),
                absolute_value(model->center_max_y - center_y)),
        maximum(absolute_value(center_y - model->accepted_min_y),
                absolute_value(model->accepted_max_y - center_y)));
    /* The radial path combines both axes and releases at 3/4 of this limit.
     * Twice the observed axis jitter covers simultaneous X/Y return noise. */
    const int deadzone_x = maximum(
        JOYSTICK_CALIBRATION_MIN_DEADZONE,
        2 * jitter_x + JOYSTICK_CALIBRATION_DEADZONE_MARGIN);
    const int deadzone_y = maximum(
        JOYSTICK_CALIBRATION_MIN_DEADZONE,
        2 * jitter_y + JOYSTICK_CALIBRATION_DEADZONE_MARGIN);
    const bool travel_valid =
        deadzone_x <= JOYSTICK_CALIBRATION_MAX_DEADZONE &&
        deadzone_y <= JOYSTICK_CALIBRATION_MAX_DEADZONE &&
        center_x - model->minimum_x >= 2 * deadzone_x &&
        model->maximum_x - center_x >= 2 * deadzone_x &&
        center_y - model->minimum_y >= 2 * deadzone_y &&
        model->maximum_y - center_y >= 2 * deadzone_y;
    *candidate = (joystick_calibration_candidate_t) {
        .valid = travel_valid,
        .minimum_x = model->minimum_x,
        .center_x = center_x,
        .maximum_x = model->maximum_x,
        .minimum_y = model->minimum_y,
        .center_y = center_y,
        .maximum_y = model->maximum_y,
        .deadzone_x = deadzone_x,
        .deadzone_y = deadzone_y,
    };
    return travel_valid;
}

void joystick_calibration_model_cancel(joystick_calibration_model_t *model)
{
    if (model != NULL) {
        memset(model, 0, sizeof(*model));
    }
}
