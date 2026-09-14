#pragma once
#include <stdint.h>

typedef struct {
    char character;
    uint8_t width;
    uint8_t rows[5];
} mist_font_glyph_t;

/* The handoff's 44 glyphs are local to MIST; the BORING font remains intact. */
const mist_font_glyph_t *mist_font_glyph(char character);
char mist_font_next(const char **text);
int mist_font_width(const char *text);
