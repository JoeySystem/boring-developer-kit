#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    uint8_t hid_usage;
    uint8_t modifier_usage;
    const char *short_name;
} eda_shortcut_t;

typedef struct {
    int8_t mouse_x;
    int8_t mouse_y;
    const char *short_name;
} eda_canvas_pan_t;

typedef struct {
    int8_t mouse_x;
    int8_t mouse_y;
} eda_canvas_delta_t;

typedef struct {
    uint8_t active_directions;
    uint32_t repeat_at_ms;
} eda_canvas_pan_model_t;

/** Resolve one fixed shortcut for the zero-based Rev A physical key index. */
bool eda_shortcut_for_key(size_t key_index, eda_shortcut_t *shortcut);

/** Resolve one fixed pan step for Up, Down, Left, or Right (indices 0-3). */
bool eda_canvas_pan_for_direction(size_t direction_index,
                                  eda_canvas_pan_t *pan);

/** Reset all held EDA pan directions and repeat timing. */
void eda_canvas_pan_model_reset(eda_canvas_pan_model_t *model);

/** Update one held direction. A press starts a new 20 ms repeat interval. */
bool eda_canvas_pan_model_set_direction(eda_canvas_pan_model_t *model,
                                        size_t direction_index,
                                        bool pressed,
                                        uint32_t now_ms);

/** Resolve the next due combined relative movement for all held directions. */
bool eda_canvas_pan_model_poll(eda_canvas_pan_model_t *model,
                               uint32_t now_ms,
                               size_t *source_direction_index,
                               eda_canvas_delta_t *delta);

#ifdef __cplusplus
}
#endif
