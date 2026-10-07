#include "board_input_logic.h"

#include <math.h>

#define TWO_PI_F 6.28318530717958647692f

/*
 * Index = previous A/B state followed by next A/B state. Legal Gray-code
 * transitions contribute one quarter-step; unchanged and two-bit jumps are
 * ignored. A detent is complete only after A/B returns to the state sampled at
 * rest. This prevents two half-cycle events while still allowing one missed
 * intermediate state. The sign matches the PCB2 A/B assignment and must be
 * verified mechanically.
 */
static const int8_t TRANSITIONS[16] = {
    0, -1, 1, 0,
    1, 0, 0, -1,
    -1, 0, 0, 1,
    0, 1, -1, 0,
};

int board_encoder_update(board_encoder_decoder_t *decoder, uint8_t next_state,
                         uint8_t transitions_per_detent)
{
    if (decoder == 0 || transitions_per_detent == 0 || next_state > 3 ||
        next_state == decoder->state) {
        return 0;
    }
    decoder->accumulator += TRANSITIONS[(decoder->state << 2) | next_state];
    decoder->state = next_state;
    if (next_state != decoder->detent_state) {
        return 0;
    }
    int direction = 0;
    if (decoder->accumulator >= (int8_t)transitions_per_detent) {
        direction = 1;
    } else if (decoder->accumulator <= -(int8_t)transitions_per_detent) {
        direction = -1;
    }
    decoder->accumulator = 0;
    return direction;
}

int board_joystick_orient_axis(int sample, int center, bool hardware_inverted,
                               bool user_inverted)
{
    return hardware_inverted != user_inverted ? 2 * center - sample : sample;
}

bool board_joystick_radial_angle(int x, int y, int center_x, int center_y,
                                 int minimum_x, int maximum_x,
                                 int minimum_y, int maximum_y,
                                 int deadzone_x, int deadzone_y,
                                 float *angle_turns)
{
    if (angle_turns == 0 || deadzone_x <= 0 || deadzone_y <= 0 ||
        minimum_x >= center_x || center_x >= maximum_x ||
        minimum_y >= center_y || center_y >= maximum_y) {
        return false;
    }
    const int delta_x = x - center_x;
    const int delta_y = y - center_y;
    const float deadzone_normalized_x = (float)delta_x / (float)deadzone_x;
    const float deadzone_normalized_y = (float)delta_y / (float)deadzone_y;
    if (deadzone_normalized_x * deadzone_normalized_x +
            deadzone_normalized_y * deadzone_normalized_y <=
        1.0f) {
        return false;
    }
    float normalized_x =
        delta_x >= 0 ? (float)delta_x / (float)(maximum_x - center_x)
                     : (float)delta_x / (float)(center_x - minimum_x);
    float normalized_y =
        delta_y >= 0 ? (float)delta_y / (float)(maximum_y - center_y)
                     : (float)delta_y / (float)(center_y - minimum_y);
    normalized_x = fmaxf(-1.0f, fminf(1.0f, normalized_x));
    normalized_y = fmaxf(-1.0f, fminf(1.0f, normalized_y));
    float angle = atan2f(normalized_y, normalized_x) / TWO_PI_F;
    if (angle < 0.0f) {
        angle += 1.0f;
    }
    *angle_turns = angle;
    return true;
}

uint8_t board_joystick_classify(int x, int y, int center_x, int center_y, int deadzone)
{
    return board_joystick_classify_hysteresis(x, y, center_x, center_y,
                                               deadzone, deadzone, 0);
}

uint8_t board_joystick_classify_hysteresis(int x, int y, int center_x, int center_y,
                                            int enter_deadzone, int exit_deadzone,
                                            uint8_t previous_directions)
{
    return board_joystick_classify_axes_hysteresis(
        x, y, center_x, center_y, enter_deadzone, exit_deadzone,
        enter_deadzone, exit_deadzone, previous_directions);
}

uint8_t board_joystick_classify_axes_hysteresis(int x, int y, int center_x, int center_y,
                                                 int enter_deadzone_x, int exit_deadzone_x,
                                                 int enter_deadzone_y, int exit_deadzone_y,
                                                 uint8_t previous_directions)
{
    uint8_t directions = 0;
    /* Retain an active direction until the axis crosses the narrower exit band. */
    const int left_deadzone = (previous_directions & BOARD_JOYSTICK_LEFT)
                                  ? exit_deadzone_x
                                  : enter_deadzone_x;
    const int right_deadzone = (previous_directions & BOARD_JOYSTICK_RIGHT)
                                   ? exit_deadzone_x
                                   : enter_deadzone_x;
    const int up_deadzone = (previous_directions & BOARD_JOYSTICK_UP)
                                ? exit_deadzone_y
                                : enter_deadzone_y;
    const int down_deadzone = (previous_directions & BOARD_JOYSTICK_DOWN)
                                  ? exit_deadzone_y
                                  : enter_deadzone_y;
    if (x < center_x - left_deadzone) {
        directions |= BOARD_JOYSTICK_LEFT;
    } else if (x > center_x + right_deadzone) {
        directions |= BOARD_JOYSTICK_RIGHT;
    }
    if (y < center_y - up_deadzone) {
        directions |= BOARD_JOYSTICK_UP;
    } else if (y > center_y + down_deadzone) {
        directions |= BOARD_JOYSTICK_DOWN;
    }
    return directions;
}
