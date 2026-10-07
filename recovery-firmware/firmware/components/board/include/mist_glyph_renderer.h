#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "mist_glyph_assets.h"

#ifdef __cplusplus
extern "C" {
#endif

#define MIST_GLYPH_SCREEN_SIZE 128
/* Display-task-owned overrides. NULL restores that resource only. */
void mist_glyph_icon_override(uint8_t resource_id, const uint8_t *alpha);
const mist_glyph_icon_t *mist_glyph_icon_resolve(uint8_t resource_id);
#define MIST_GLYPH_GRID_COUNT 31
#define MIST_GLYPH_GRID_MIN (-3)
#define MIST_GLYPH_GRID_MAX 27
#define MIST_GLYPH_CELL_SIZE 3
#define MIST_GLYPH_CELL_PITCH 4
#define MIST_GLYPH_CELL_ORIGIN 14
#define MIST_GLYPH_VALID_CELL_COUNT 737
#define MIST_GLYPH_OVERLAY_COUNT 8
#define MIST_GLYPH_OVERLAY_TEXT_LENGTH 24

#define MIST_GLYPH_BLACK UINT16_C(0x0000)
/* Non-symmetric RGB565 values are byte-swapped once for the SPI panel buffer. */
#define MIST_GLYPH_GRID_DIM UINT16_C(0x6108)
#define MIST_GLYPH_GRAY_DARK UINT16_C(0x39e7)
#define MIST_GLYPH_GRAY UINT16_C(0xef73)
#define MIST_GLYPH_WHITE UINT16_C(0x3cd7)
#define MIST_GLYPH_GREEN UINT16_C(0xb247)
#define MIST_GLYPH_ORANGE UINT16_C(0x08fd)
#define MIST_GLYPH_RED UINT16_C(0xaafa)
#define MIST_GLYPH_MASKED_BLACK UINT16_C(0x0001)

typedef struct {
    int8_t x;
    int8_t y;
    uint16_t foreground;
    uint16_t background;
    char text[MIST_GLYPH_OVERLAY_TEXT_LENGTH];
} mist_glyph_overlay_t;

typedef struct {
    uint16_t background;
    uint16_t grid_background;
    uint16_t cells[MIST_GLYPH_GRID_COUNT * MIST_GLYPH_GRID_COUNT];
    uint8_t overlay_count;
    mist_glyph_overlay_t overlays[MIST_GLYPH_OVERLAY_COUNT];
} mist_glyph_scene_t;

void mist_glyph_scene_clear(mist_glyph_scene_t *scene, uint16_t grid_background);
bool mist_glyph_cell_is_valid(int logical_x, int logical_y);
int mist_glyph_cell_pixel(int logical_coordinate);
size_t mist_glyph_valid_cell_count(void);
void mist_glyph_scene_set(mist_glyph_scene_t *scene, int logical_x, int logical_y,
                          uint16_t color);
void mist_glyph_scene_mask_black(mist_glyph_scene_t *scene, int logical_x,
                                 int logical_y);
uint16_t mist_glyph_scene_get(const mist_glyph_scene_t *scene, int logical_x,
                              int logical_y);
void mist_glyph_scene_draw_icon(mist_glyph_scene_t *scene, uint8_t resource_id,
                                int logical_x, int logical_y, uint16_t color);
uint16_t mist_glyph_gray_level(uint8_t level);
int mist_glyph_text_columns(const char *text);
int mist_glyph_aux_text_width(const char *text);
void mist_glyph_scene_draw_text(mist_glyph_scene_t *scene, const char *text,
                                int logical_x, int logical_y, uint16_t color);
bool mist_glyph_scene_add_overlay(mist_glyph_scene_t *scene, const char *text,
                                  int pixel_x, int pixel_y, uint16_t foreground,
                                  uint16_t background);
void mist_glyph_render_band(const mist_glyph_scene_t *scene, uint16_t *pixels,
                            int stride, int band_y, int band_lines);

#ifdef __cplusplus
}
#endif
