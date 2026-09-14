#include "board_mist_font.h"
#include <stddef.h>

static const mist_font_glyph_t glyphs[] = {
    {'0',3,{7,5,5,5,7}}, {'1',3,{2,6,2,2,7}},
    {'2',3,{6,1,2,4,7}}, {'3',3,{6,1,2,1,6}},
    {'4',3,{5,5,7,1,1}}, {'5',3,{7,4,6,1,6}},
    {'6',3,{3,4,7,5,7}}, {'7',3,{7,1,2,2,2}},
    {'8',3,{7,5,7,5,7}}, {'9',3,{7,5,7,1,6}},
    {'A',3,{2,5,7,5,5}}, {'B',3,{6,5,6,5,6}},
    {'C',3,{3,4,4,4,3}}, {'D',3,{6,5,5,5,6}},
    {'E',3,{7,4,6,4,7}}, {'F',3,{7,4,6,4,4}},
    {'G',3,{3,4,5,5,3}}, {'H',3,{5,5,7,5,5}},
    {'I',3,{7,2,2,2,7}}, {'J',3,{1,1,1,5,2}},
    {'K',3,{5,5,6,5,5}}, {'L',3,{4,4,4,4,7}},
    {'M',5,{17,27,21,17,17}}, {'N',4,{9,13,11,9,9}},
    {'O',3,{2,5,5,5,2}}, {'P',3,{6,5,6,4,4}},
    {'Q',3,{2,5,5,7,3}}, {'R',3,{6,5,6,5,5}},
    {'S',3,{3,4,2,1,6}}, {'T',3,{7,2,2,2,2}},
    {'U',3,{5,5,5,5,7}}, {'V',3,{5,5,5,5,2}},
    {'W',5,{17,17,21,27,17}}, {'X',3,{5,5,2,5,5}},
    {'Y',3,{5,5,2,2,2}}, {'Z',3,{7,1,2,4,7}},
    {':',1,{0,1,0,1,0}}, {'%',3,{5,1,2,4,5}},
    {'?',3,{6,1,2,0,2}}, {'-',3,{0,0,7,0,0}},
    {'/',3,{1,1,2,4,4}}, {' ',2,{0,0,0,0,0}},
    {'.',1,{0,0,0,0,1}}, {'+',3,{0,2,7,2,0}},
};

const mist_font_glyph_t *mist_font_glyph(char character)
{
    if (character >= 'a' && character <= 'z') character -= 'a' - 'A';
    for (size_t i = 0; i < sizeof(glyphs) / sizeof(glyphs[0]); i++) {
        if (glyphs[i].character == character) return &glyphs[i];
    }
    return &glyphs[38];
}

char mist_font_next(const char **text)
{
    if (!text || !*text || !**text) return '\0';
    const unsigned char *p = (const unsigned char *)*text;
    unsigned char c = *p++;
    if (c >= 0x80) {
        /* Unsupported UTF-8 characters use one fallback cell glyph each. */
        while ((*p & 0xc0) == 0x80) p++;
        c = '?';
    }
    *text = (const char *)p;
    return c >= 'a' && c <= 'z' ? (char)(c - ('a' - 'A')) : (char)c;
}

int mist_font_width(const char *text)
{
    int width = -1;
    char c;
    while ((c = mist_font_next(&text))) width += mist_font_glyph(c)->width + 1;
    return width;
}
