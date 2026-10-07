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
