#include "eda_shortcuts.h"

#define EDA_CANVAS_PAN_REPEAT_MS 20

enum {
    HID_KEY_B = 5,
    HID_KEY_V = 25,
    HID_KEY_W = 26,
    HID_KEY_ENTER = 40,
    HID_KEY_ESCAPE = 41,
    HID_MODIFIER_LEFT_SHIFT = 225,
    HID_MODIFIER_LEFT_ALT = 226,
};

/*
 * JLCEDA Pro's default PCB shortcut theme calls this modifier Alt. A USB
 * keyboard reports the same Left Alt usage on every host; macOS presents it
 * to the user as Option. These are held combinations, not text macros.
 */
static const eda_shortcut_t REV_A_EDA_SHORTCUTS[] = {
    {HID_KEY_W, HID_MODIFIER_LEFT_ALT, "ROUTE"},
    {HID_KEY_V, HID_MODIFIER_LEFT_ALT, "PLACE VIA"},
    {HID_KEY_B, HID_MODIFIER_LEFT_SHIFT, "REBUILD COPPER"},
    {HID_KEY_ENTER, 0, "FINISH"},
    {HID_KEY_ESCAPE, 0, "CANCEL"},
    {HID_KEY_W, HID_MODIFIER_LEFT_SHIFT, "NEXT WIDTH"},
    {HID_KEY_V, HID_MODIFIER_LEFT_SHIFT, "NEXT VIA"},
};

/* USB mouse coordinates are positive to the right and down. */
static const eda_canvas_pan_t REV_A_EDA_CANVAS_PANS[] = {
    {0, -8, "PAN UP"},
    {0, 8, "PAN DOWN"},
    {-8, 0, "PAN LEFT"},
    {8, 0, "PAN RIGHT"},
};

bool eda_shortcut_for_key(size_t key_index, eda_shortcut_t *shortcut)
{
    if (shortcut == NULL ||
        key_index >= sizeof(REV_A_EDA_SHORTCUTS) /
                         sizeof(REV_A_EDA_SHORTCUTS[0])) {
        return false;
    }
    *shortcut = REV_A_EDA_SHORTCUTS[key_index];
    return true;
}

bool eda_canvas_pan_for_direction(size_t direction_index,
                                  eda_canvas_pan_t *pan)
{
    if (pan == NULL ||
        direction_index >= sizeof(REV_A_EDA_CANVAS_PANS) /
                                   sizeof(REV_A_EDA_CANVAS_PANS[0])) {
        return false;
    }
    *pan = REV_A_EDA_CANVAS_PANS[direction_index];
    return true;
}

void eda_canvas_pan_model_reset(eda_canvas_pan_model_t *model)
{
    if (model != NULL) {
        *model = (eda_canvas_pan_model_t){0};
    }
}

bool eda_canvas_pan_model_set_direction(eda_canvas_pan_model_t *model,
                                        size_t direction_index,
                                        bool pressed,
                                        uint32_t now_ms)
{
    eda_canvas_pan_t pan;
    if (model == NULL ||
        !eda_canvas_pan_for_direction(direction_index, &pan)) {
        return false;
    }
    const uint8_t mask = (uint8_t)(1u << direction_index);
    if (pressed) {
        model->active_directions |= mask;
        model->repeat_at_ms = now_ms + EDA_CANVAS_PAN_REPEAT_MS;
    } else {
        model->active_directions &= (uint8_t)~mask;
    }
    return true;
}

bool eda_canvas_pan_model_poll(eda_canvas_pan_model_t *model,
                               uint32_t now_ms,
                               size_t *source_direction_index,
                               eda_canvas_delta_t *delta)
{
    if (model == NULL || delta == NULL || model->active_directions == 0 ||
        (int32_t)(now_ms - model->repeat_at_ms) < 0) {
        return false;
    }

    *delta = (eda_canvas_delta_t){0};
    bool source_found = false;
    for (size_t index = 0; index < 4; ++index) {
        if ((model->active_directions & (1u << index)) == 0) {
            continue;
        }
        eda_canvas_pan_t direction;
        if (!eda_canvas_pan_for_direction(index, &direction)) {
            continue;
        }
        if (!source_found) {
            if (source_direction_index != NULL) {
                *source_direction_index = index;
            }
            source_found = true;
        }
        delta->mouse_x = (int8_t)(delta->mouse_x + direction.mouse_x);
        delta->mouse_y = (int8_t)(delta->mouse_y + direction.mouse_y);
    }
    model->repeat_at_ms = now_ms + EDA_CANVAS_PAN_REPEAT_MS;
    return source_found &&
           (delta->mouse_x != 0 || delta->mouse_y != 0);
}
