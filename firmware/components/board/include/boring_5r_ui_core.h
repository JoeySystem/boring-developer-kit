#ifndef BORING_5R_UI_CORE_H
#define BORING_5R_UI_CORE_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define BORING_5R_ROWS 5
#define BORING_5R_GLYPH_COUNT 52

typedef struct {
    uint8_t codepoint;
    uint8_t width;
    uint8_t rows[BORING_5R_ROWS];
} boring_5r_glyph_t;

typedef void (*boring_5r_draw_dot_fn)(int16_t center_x, int16_t center_y,
                                      uint8_t diameter, uint32_t color,
                                      void *context);

const boring_5r_glyph_t *boring_5r_find(uint8_t codepoint);
int16_t boring_5r_measure(const char *text, uint8_t dot_diameter,
                          uint8_t center_pitch, uint8_t character_gap);
bool boring_5r_draw(const char *text, int16_t x, int16_t y,
                    uint8_t dot_diameter, uint8_t center_pitch,
                    uint8_t character_gap, uint32_t color,
                    boring_5r_draw_dot_fn draw_dot, void *context);

#ifdef __cplusplus
}
#endif
#endif
