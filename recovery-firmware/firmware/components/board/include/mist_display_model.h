#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "mist_glyph_motion.h"

typedef enum {
    MIST_DISPLAY_EVENT_DEFAULT = 0,
    MIST_DISPLAY_EVENT_PARAMETER,
    MIST_DISPLAY_EVENT_RETURN,
    MIST_DISPLAY_EVENT_ERROR,
    MIST_DISPLAY_EVENT_POWER_OFF,
    MIST_DISPLAY_EVENT_NEXT,
    MIST_DISPLAY_EVENT_PREVIOUS
} mist_display_event_t;

typedef struct {
    mist_glyph_page_id_t page;
    mist_glyph_page_context_t context;
    mist_glyph_scene_t current;
    mist_glyph_motion_t motion;
    bool initialized;
    bool dirty;
    char text[4][MIST_GLYPH_OVERLAY_TEXT_LENGTH];
    uint32_t timer_updated_at_ms;
    bool carousel_active;
    float carousel_from;
    float carousel_target;
} mist_display_model_t;

void mist_display_model_init(mist_display_model_t *model,
                             mist_glyph_page_id_t page,
                             const mist_glyph_page_context_t *context,
                             uint32_t now_ms);
bool mist_display_model_request(mist_display_model_t *model,
                                mist_glyph_page_id_t page,
                                const mist_glyph_page_context_t *context,
                                mist_display_event_t event,
                                uint32_t now_ms);
bool mist_display_model_frame(mist_display_model_t *model, uint32_t now_ms,
                              mist_glyph_scene_t *scene);
void mist_display_model_cancel(mist_display_model_t *model,
                               mist_glyph_page_id_t terminal_page,
                               const mist_glyph_page_context_t *context);
void mist_display_model_finish(mist_display_model_t *model);
