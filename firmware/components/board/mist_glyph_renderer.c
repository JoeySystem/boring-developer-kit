#include "mist_glyph_renderer.h"
#ifdef ESP_PLATFORM
#include "esp_attr.h"
#else
#define EXT_RAM_BSS_ATTR
#endif

static mist_glyph_icon_t s_custom_icons[MIST_GLYPH_ICON_COUNT];
static uint16_t s_custom_rows[MIST_GLYPH_ICON_COUNT][MIST_GLYPH_ICON_MAX_HEIGHT];
static EXT_RAM_BSS_ATTR uint8_t s_custom_levels[MIST_GLYPH_ICON_COUNT][225];
static bool s_custom_enabled[MIST_GLYPH_ICON_COUNT];

const mist_glyph_icon_t *mist_glyph_icon_resolve(uint8_t id)
{
    if (id >= MIST_GLYPH_ICON_COUNT) return NULL;
    return s_custom_enabled[id] ? &s_custom_icons[id] : mist_glyph_icon_get(id);
}

void mist_glyph_icon_override(uint8_t id, const uint8_t *alpha)
{
    const mist_glyph_icon_t *builtin = mist_glyph_icon_get(id);
    if (!builtin) return;
    s_custom_enabled[id] = alpha != NULL && id != 7 && id != 11;
    if (!s_custom_enabled[id]) return;
    s_custom_icons[id] = *builtin;
    s_custom_icons[id].rows = s_custom_rows[id];
    s_custom_icons[id].levels = s_custom_levels[id];
    for (unsigned y = 0; y < builtin->height; ++y) {
        s_custom_rows[id][y] = 0;
        for (unsigned x = 0; x < builtin->width; ++x) {
            const unsigned offset = y * builtin->width + x;
            s_custom_levels[id][offset] = alpha[offset];
            if (alpha[offset]) s_custom_rows[id][y] |= 1u << (builtin->width - 1u - x);
        }
    }
}

#include <string.h>

static int scene_index(int x, int y)
{
    if (x < MIST_GLYPH_GRID_MIN || x > MIST_GLYPH_GRID_MAX ||
        y < MIST_GLYPH_GRID_MIN || y > MIST_GLYPH_GRID_MAX) {
        return -1;
    }
    return (y - MIST_GLYPH_GRID_MIN) * MIST_GLYPH_GRID_COUNT +
           (x - MIST_GLYPH_GRID_MIN);
}

void mist_glyph_scene_clear(mist_glyph_scene_t *scene, uint16_t grid_background)
{
    if (scene == NULL) return;
    memset(scene, 0, sizeof(*scene));
    scene->background = MIST_GLYPH_BLACK;
    scene->grid_background = grid_background;
}

int mist_glyph_cell_pixel(int logical_coordinate)
{
    return MIST_GLYPH_CELL_ORIGIN + MIST_GLYPH_CELL_PITCH * logical_coordinate;
}

bool mist_glyph_cell_is_valid(int logical_x, int logical_y)
{
    const int x = mist_glyph_cell_pixel(logical_x);
    const int y = mist_glyph_cell_pixel(logical_y);
    const int center_twice = 127;
    const int radius_twice = 125;
    const int corners[2] = {0, MIST_GLYPH_CELL_SIZE - 1};
    for (int yi = 0; yi < 2; ++yi) {
        for (int xi = 0; xi < 2; ++xi) {
            const int dx = 2 * (x + corners[xi]) - center_twice;
            const int dy = 2 * (y + corners[yi]) - center_twice;
            if (dx * dx + dy * dy > radius_twice * radius_twice) return false;
        }
    }
    return true;
}

size_t mist_glyph_valid_cell_count(void)
{
    size_t count = 0;
    for (int y = MIST_GLYPH_GRID_MIN; y <= MIST_GLYPH_GRID_MAX; ++y)
        for (int x = MIST_GLYPH_GRID_MIN; x <= MIST_GLYPH_GRID_MAX; ++x)
            count += mist_glyph_cell_is_valid(x, y) ? 1u : 0u;
    return count;
}

void mist_glyph_scene_set(mist_glyph_scene_t *scene, int logical_x, int logical_y,
                          uint16_t color)
{
    const int index = scene_index(logical_x, logical_y);
    if (scene != NULL && index >= 0 && mist_glyph_cell_is_valid(logical_x, logical_y))
        scene->cells[index] = color;
}

void mist_glyph_scene_mask_black(mist_glyph_scene_t *scene, int logical_x,
                                 int logical_y)
{
    const int index = scene_index(logical_x, logical_y);
    if (scene != NULL && index >= 0 && mist_glyph_cell_is_valid(logical_x, logical_y))
        scene->cells[index] = MIST_GLYPH_MASKED_BLACK;
}

uint16_t mist_glyph_scene_get(const mist_glyph_scene_t *scene, int logical_x,
                              int logical_y)
{
    const int index = scene_index(logical_x, logical_y);
    return scene != NULL && index >= 0 ? scene->cells[index] : 0;
}

static uint16_t level_color(uint16_t color, uint8_t level)
{
    const uint16_t native = (uint16_t)((color << 8) | (color >> 8));
    const uint32_t r = ((native >> 11) & 31u) * level / 255u;
    const uint32_t g = ((native >> 5) & 63u) * level / 255u;
    const uint32_t b = (native & 31u) * level / 255u;
    const uint16_t scaled = (uint16_t)((r << 11) | (g << 5) | b);
    return (uint16_t)((scaled << 8) | (scaled >> 8));
}

static int gray_index(uint16_t color)
{
    for (int level = 0; level <= 255; ++level)
        if (mist_glyph_gray_level((uint8_t)level) == color) return level;
    return -1;
}

uint16_t mist_glyph_gray_level(uint8_t level)
{
    const uint32_t red8 = (11u * 255u + (224u - 11u) * level + 127u) / 255u;
    const uint32_t green8 = (11u * 255u + (241u - 11u) * level + 127u) / 255u;
    const uint32_t blue8 = (11u * 255u + (240u - 11u) * level + 127u) / 255u;
    const uint16_t native = (uint16_t)((((red8 * 31u + 127u) / 255u) << 11) |
        (((green8 * 63u + 127u) / 255u) << 5) |
        ((blue8 * 31u + 127u) / 255u));
    return (uint16_t)((native << 8) | (native >> 8));
}

void mist_glyph_scene_draw_icon(mist_glyph_scene_t *scene, uint8_t resource_id,
                                int logical_x, int logical_y, uint16_t color)
{
    const mist_glyph_icon_t *icon = mist_glyph_icon_resolve(resource_id);
    if (scene == NULL || icon == NULL) return;
    const int base_gray = gray_index(color);
    for (uint8_t row = 0; row < icon->height; ++row) {
        for (uint8_t column = 0; column < icon->width; ++column) {
            const uint16_t bit = (uint16_t)(1u << (icon->width - 1u - column));
            if ((icon->rows[row] & bit) == 0) continue;
            uint16_t pixel = color;
            if (icon->levels != NULL) {
                const uint8_t icon_level = icon->levels[row * icon->width + column];
                pixel = base_gray >= 0
                    ? mist_glyph_gray_level((uint8_t)((base_gray * icon_level + 127) / 255))
                    : level_color(color, icon_level);
            }
            mist_glyph_scene_set(scene, logical_x + column, logical_y + row, pixel);
        }
    }
}

int mist_glyph_text_columns(const char *text)
{
    int columns = 0;
    if (text == NULL) return 0;
    for (; *text != '\0'; ++text) {
        const mist_glyph_font_glyph_t *glyph = mist_glyph_font_find(*text);
        if (glyph == NULL) glyph = mist_glyph_font_find('?');
        if (glyph != NULL) columns += glyph->width + (columns > 0 ? 1 : 0);
    }
    return columns;
}

void mist_glyph_scene_draw_text(mist_glyph_scene_t *scene, const char *text,
                                int logical_x, int logical_y, uint16_t color)
{
    if (scene == NULL || text == NULL) return;
    int x = logical_x;
    for (; *text != '\0'; ++text) {
        const mist_glyph_font_glyph_t *glyph = mist_glyph_font_find(*text);
        if (glyph == NULL) glyph = mist_glyph_font_find('?');
        if (glyph == NULL) continue;
        for (uint8_t row = 0; row < 5; ++row)
            for (uint8_t column = 0; column < glyph->width; ++column)
                if (glyph->rows[row] & (1u << (glyph->width - 1u - column)))
                    mist_glyph_scene_set(scene, x + column, logical_y + row, color);
        x += glyph->width + 1;
    }
}

int mist_glyph_aux_text_width(const char *text)
{
    int width = 0;
    if (text == NULL) return 0;
    for (; *text != '\0'; ++text) {
        const mist_glyph_font_glyph_t *glyph = mist_glyph_font_find(*text);
        if (glyph == NULL) glyph = mist_glyph_font_find('?');
        if (glyph != NULL) width += (glyph->width + 1) * 3;
    }
    return width > 0 ? width - 1 : 0;
}

bool mist_glyph_scene_add_overlay(mist_glyph_scene_t *scene, const char *text,
                                  int pixel_x, int pixel_y, uint16_t foreground,
                                  uint16_t background)
{
    if (scene == NULL || text == NULL ||
        scene->overlay_count >= MIST_GLYPH_OVERLAY_COUNT) return false;
    mist_glyph_overlay_t *overlay = &scene->overlays[scene->overlay_count++];
    overlay->x = (int8_t)pixel_x;
    overlay->y = (int8_t)pixel_y;
    overlay->foreground = foreground;
    overlay->background = background;
    strncpy(overlay->text, text, sizeof(overlay->text) - 1);
    overlay->text[sizeof(overlay->text) - 1] = '\0';
    return true;
}

static void put_pixel(uint16_t *pixels, int stride, int band_y, int band_lines,
                      int x, int y, uint16_t color)
{
    const int dx = 2 * x + 1 - 128;
    const int dy = 2 * y + 1 - 128;
    if (x >= 0 && x < MIST_GLYPH_SCREEN_SIZE && y >= band_y &&
        y < band_y + band_lines && dx * dx + dy * dy <= 125 * 125)
        pixels[(y - band_y) * stride + x] = color;
}

static void render_overlay(const mist_glyph_overlay_t *overlay, uint16_t *pixels,
                           int stride, int band_y, int band_lines)
{
    if (overlay->y - 1 >= band_y + band_lines || overlay->y + 14 < band_y) return;
    const int width = mist_glyph_aux_text_width(overlay->text);
    const int top = overlay->y - 1 < band_y ? band_y : overlay->y - 1;
    const int bottom = overlay->y + 14 >= band_y + band_lines
        ? band_y + band_lines - 1 : overlay->y + 14;
    for (int y = top; y <= bottom; ++y)
        for (int x = overlay->x - 1; x <= overlay->x + width; ++x)
            put_pixel(pixels, stride, band_y, band_lines, x, y, overlay->background);
    int x = overlay->x;
    for (const char *cursor = overlay->text; *cursor; ++cursor) {
        const mist_glyph_font_glyph_t *glyph = mist_glyph_font_find(*cursor);
        if (glyph == NULL) glyph = mist_glyph_font_find('?');
        if (glyph == NULL) continue;
        for (uint8_t row = 0; row < 5; ++row) {
            const int row_y = overlay->y + row * 3;
            if (row_y >= band_y + band_lines || row_y + 1 < band_y) continue;
            for (uint8_t column = 0; column < glyph->width; ++column)
                if (glyph->rows[row] & (1u << (glyph->width - 1u - column)))
                    for (int dy = 0; dy < 2; ++dy)
                        for (int dx = 0; dx < 2; ++dx)
                            put_pixel(pixels, stride, band_y, band_lines,
                                      x + column * 3 + dx,
                                      overlay->y + row * 3 + dy,
                                      overlay->foreground);
        }
        x += (glyph->width + 1) * 3;
    }
}

void mist_glyph_render_band(const mist_glyph_scene_t *scene, uint16_t *pixels,
                            int stride, int band_y, int band_lines)
{
    if (scene == NULL || pixels == NULL || stride < MIST_GLYPH_SCREEN_SIZE ||
        band_y < 0 || band_lines <= 0 ||
        band_y + band_lines > MIST_GLYPH_SCREEN_SIZE) return;
    for (int y = 0; y < band_lines; ++y)
        for (int x = 0; x < MIST_GLYPH_SCREEN_SIZE; ++x)
            pixels[y * stride + x] = scene->background;
    for (int logical_y = MIST_GLYPH_GRID_MIN; logical_y <= MIST_GLYPH_GRID_MAX;
         ++logical_y) {
        const int py = mist_glyph_cell_pixel(logical_y);
        if (py >= band_y + band_lines || py + MIST_GLYPH_CELL_SIZE <= band_y) continue;
        for (int logical_x = MIST_GLYPH_GRID_MIN; logical_x <= MIST_GLYPH_GRID_MAX;
             ++logical_x) {
            if (!mist_glyph_cell_is_valid(logical_x, logical_y)) continue;
            uint16_t color = mist_glyph_scene_get(scene, logical_x, logical_y);
            if (color == MIST_GLYPH_MASKED_BLACK) color = MIST_GLYPH_BLACK;
            else if (color == 0) color = scene->grid_background;
            const int px = mist_glyph_cell_pixel(logical_x);
            for (int dy = 0; dy < MIST_GLYPH_CELL_SIZE; ++dy)
                for (int dx = 0; dx < MIST_GLYPH_CELL_SIZE; ++dx)
                    put_pixel(pixels, stride, band_y, band_lines,
                              px + dx, py + dy, color);
        }
    }
    for (uint8_t index = 0; index < scene->overlay_count; ++index)
        render_overlay(&scene->overlays[index], pixels, stride, band_y, band_lines);
}
