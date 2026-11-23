#pragma once

#include <stdbool.h>
#include <stdint.h>

typedef enum {
    KEY_GESTURE_NONE = 0,
    KEY_GESTURE_TAP,
    KEY_GESTURE_DOUBLE,
} key_gesture_result_t;

/* A hold produces one click on release, never a held HID key or repeat. */
typedef struct {
    bool down;
    bool waiting_second;
    bool second_down;
    uint32_t deadline_ms;
} key_gesture_model_t;

void key_gesture_model_reset(key_gesture_model_t *model);
key_gesture_result_t key_gesture_model_event(key_gesture_model_t *model,
                                           bool pressed, uint32_t now_ms);
key_gesture_result_t key_gesture_model_poll(key_gesture_model_t *model,
                                          uint32_t now_ms);

/*
 * A gesture result leaves the device as a human-like keystroke instead of a
 * 1 ms report pair: modifiers go down first, the key is held for a while,
 * then key and modifiers are released in reverse order. Hotkey listeners
 * (for example voice-input apps) miss chords that last one USB poll.
 */
#define KEY_PULSE_MODIFIER_LEAD_MS 20u
#define KEY_PULSE_HOLD_MS 60u
#define KEY_PULSE_MODIFIER_TAIL_MS 20u

typedef enum {
    KEY_PULSE_STEP_NONE = 0,
    KEY_PULSE_STEP_MODIFIERS_DOWN,
    KEY_PULSE_STEP_KEY_DOWN,
    KEY_PULSE_STEP_KEY_UP,
    KEY_PULSE_STEP_MODIFIERS_UP,
} key_pulse_step_t;

typedef struct {
    bool active;
    bool has_modifiers;
    key_pulse_step_t next_step;
    uint32_t next_ms;
} key_pulse_model_t;

/* Start a pulse and return the step to send now. */
key_pulse_step_t key_pulse_model_start(key_pulse_model_t *model,
                                       bool has_modifiers, uint32_t now_ms);
/* Return the next due step (call repeatedly until NONE). */
key_pulse_step_t key_pulse_model_poll(key_pulse_model_t *model, uint32_t now_ms);
/* Return the next pending release immediately, ignoring time; NONE when done.
 * A key that was never pressed is not released. */
key_pulse_step_t key_pulse_model_finish(key_pulse_model_t *model);
