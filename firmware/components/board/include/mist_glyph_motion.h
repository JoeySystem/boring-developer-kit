#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "mist_glyph_pages.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    MIST_MOTION_GATHER = 0,
    MIST_MOTION_SCATTER,
    MIST_MOTION_TRACE,
    MIST_MOTION_IMPACT,
    MIST_MOTION_ADJUST,
    MIST_MOTION_SECTOR,
    MIST_MOTION_CONTINUITY,
    MIST_MOTION_NATIVE,
    MIST_MOTION_NONE
} mist_glyph_motion_family_t;

typedef struct {
    mist_glyph_motion_family_t family;
    uint16_t duration_ms;
} mist_glyph_motion_profile_t;

typedef struct {
    mist_glyph_scene_t from;
    mist_glyph_scene_t target;
    mist_glyph_motion_family_t family;
    uint32_t started_at_ms;
    uint16_t duration_ms;
    bool active;
    uint16_t trace_count;
    uint16_t trace_order[MIST_GLYPH_GRID_COUNT * MIST_GLYPH_GRID_COUNT];
} mist_glyph_motion_t;

mist_glyph_motion_profile_t mist_glyph_motion_profile(mist_glyph_page_id_t page);
void mist_glyph_motion_start(mist_glyph_motion_t *motion,
                             const mist_glyph_scene_t *from,
                             const mist_glyph_scene_t *target,
                             mist_glyph_motion_family_t family,
                             uint16_t duration_ms, uint32_t now_ms);
bool mist_glyph_motion_compose(const mist_glyph_motion_t *motion,
                               uint32_t now_ms, mist_glyph_scene_t *output);
bool mist_glyph_motion_finished(const mist_glyph_motion_t *motion,
                                uint32_t now_ms);

#ifdef __cplusplus
}
#endif
