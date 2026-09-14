#pragma once

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define MIST_GLYPH_FONT_COUNT 44u
#define MIST_GLYPH_ICON_COUNT 28u
#define MIST_GLYPH_BORING_CELL_COUNT 68u
#define MIST_GLYPH_ICON_MAX_HEIGHT 15u

typedef struct {
    char character;
    uint8_t width;
    uint8_t rows[5];
} mist_glyph_font_glyph_t;

typedef struct {
    const char *name;
    uint8_t width;
    uint8_t height;
    const uint16_t *rows;
    const uint8_t *levels;
} mist_glyph_icon_t;

typedef struct {
    int8_t x;
    int8_t y;
} mist_glyph_point_t;

extern const mist_glyph_font_glyph_t mist_glyph_font[MIST_GLYPH_FONT_COUNT];
extern const mist_glyph_icon_t mist_glyph_icons[MIST_GLYPH_ICON_COUNT];
extern const mist_glyph_point_t
    mist_glyph_boring_cells[MIST_GLYPH_BORING_CELL_COUNT];

const mist_glyph_font_glyph_t *mist_glyph_font_find(char character);
const mist_glyph_icon_t *mist_glyph_icon_get(uint8_t resource_id);

#ifdef __cplusplus
}
#endif
