#pragma once

/**
 * @file board_input_logic.h
 * @brief Hardware-independent EC11 and joystick classification helpers.
 *
 * This module intentionally has no ESP-IDF dependencies so the state machines
 * can be compiled and exercised by host tests.
 */

#include <stdbool.h>
#include <stdint.h>

typedef struct {
    /** Last two-bit EC11 A/B sample. */
    uint8_t state;
    /** Accumulated valid quarter-steps toward one physical detent. */
    int8_t accumulator;
    /** A/B state sampled while the encoder rests in its current detent. */
    uint8_t detent_state;
} board_encoder_decoder_t;

enum {
    BOARD_JOYSTICK_LEFT = 1 << 0,
    BOARD_JOYSTICK_RIGHT = 1 << 1,
    BOARD_JOYSTICK_UP = 1 << 2,
    BOARD_JOYSTICK_DOWN = 1 << 3,
};

/**
 * Advance the quadrature decoder with one A/B sample.
 *
 * @param transitions_per_detent Minimum directional Gray-code evidence needed
 *        before a return to the resting A/B state counts as one detent.
 * @return -1 for a complete CCW detent, +1 for CW, and 0 otherwise.
 */
int board_encoder_update(board_encoder_decoder_t *decoder, uint8_t next_state,
                         uint8_t transitions_per_detent);

/**
 * Apply the board's physical axis polarity and the user's inversion setting.
 *
 * The user setting is relative to the physically correct direction, so enabling
 * it cancels a board inversion instead of applying a second reflection.
 */
int board_joystick_orient_axis(int sample, int center, bool hardware_inverted,
                               bool user_inverted);

/**
 * Convert an oriented two-axis sample into a clockwise radial angle.
 *
 * Zero is right, 0.25 is down, 0.5 is left, and 0.75 is up. The independent
 * axis deadzones form an ellipse. Each positive and negative axis segment is
 * normalized independently from its calibrated center to its travel endpoint,
 * so unequal X/Y or positive/negative travel does not distort the angle.
 *
 * @return true outside the deadzone, otherwise false.
 */
bool board_joystick_radial_angle(int x, int y, int center_x, int center_y,
                                 int minimum_x, int maximum_x,
                                 int minimum_y, int maximum_y,
                                 int deadzone_x, int deadzone_y,
                                 float *angle_turns);

/** Classify axes with one symmetric deadzone and no retained direction state. */
uint8_t board_joystick_classify(int x, int y, int center_x, int center_y, int deadzone);

/**
 * Classify axes using separate enter and release thresholds.
 *
 * A direction must cross @p enter_deadzone to activate but only needs to move
 * inside @p exit_deadzone to release. This hysteresis prevents ADC noise near a
 * boundary from generating alternating press and release events.
 */
uint8_t board_joystick_classify_hysteresis(int x, int y, int center_x, int center_y,
                                            int enter_deadzone, int exit_deadzone,
                                            uint8_t previous_directions);

/** Classify axes with independent horizontal and vertical hysteresis bands. */
uint8_t board_joystick_classify_axes_hysteresis(int x, int y, int center_x, int center_y,
                                                 int enter_deadzone_x, int exit_deadzone_x,
                                                 int enter_deadzone_y, int exit_deadzone_y,
                                                 uint8_t previous_directions);
