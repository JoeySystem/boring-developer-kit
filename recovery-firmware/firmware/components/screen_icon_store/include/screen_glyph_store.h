#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"
#ifdef __cplusplus
extern "C" {
#endif
#define SCREEN_GLYPH_COUNT 28u
#define SCREEN_GLYPH_MAX_BYTES 225u
typedef struct { const char *id; uint8_t width, height; bool editable; } screen_glyph_definition_t;
typedef struct { uint32_t revision; bool custom; uint8_t pixels[SCREEN_GLYPH_MAX_BYTES]; } screen_glyph_record_t;
extern const screen_glyph_definition_t screen_glyph_catalog[SCREEN_GLYPH_COUNT];
int screen_glyph_find(const char *id);
esp_err_t screen_glyph_store_init(void);
bool screen_glyph_store_ready(void);
esp_err_t screen_glyph_store_get(unsigned id, screen_glyph_record_t *record);
esp_err_t screen_glyph_store_set(unsigned id, uint32_t base, const uint8_t *data, size_t length);
esp_err_t screen_glyph_store_reset(unsigned id, uint32_t base);
bool screen_glyph_store_try_snapshot(uint32_t *epoch, screen_glyph_record_t *records);
esp_err_t screen_glyph_store_erase_all(void);
#ifdef __cplusplus
}
#endif
