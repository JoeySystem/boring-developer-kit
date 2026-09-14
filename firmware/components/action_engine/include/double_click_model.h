#pragma once

#include <stdbool.h>
#include <stdint.h>

typedef enum {
    DOUBLE_CLICK_ACTION_NONE = 0,
    DOUBLE_CLICK_ACTION_TAP = 1 << 0,
    DOUBLE_CLICK_ACTION_DOUBLE = 1 << 1,
    DOUBLE_CLICK_ACTION_HOLD_PRESS = 1 << 2,
    DOUBLE_CLICK_ACTION_HOLD_RELEASE = 1 << 3,
} double_click_action_t;

typedef struct {
    bool down;
    bool waiting_second;
    bool second_down;
    bool hold_forwarded;
    uint32_t pressed_at_ms;
    uint32_t deadline_ms;
} double_click_model_t;

void double_click_model_init(double_click_model_t *model);
uint8_t double_click_model_press(double_click_model_t *model, uint32_t now_ms);
uint8_t double_click_model_release(double_click_model_t *model, uint32_t now_ms);
uint8_t double_click_model_poll(double_click_model_t *model, uint32_t now_ms);
uint8_t double_click_model_flush(double_click_model_t *model);
