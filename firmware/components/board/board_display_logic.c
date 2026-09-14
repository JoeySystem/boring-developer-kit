#include "board_display_logic.h"
#include "boring_5r_ui_core.h"

#include <stdbool.h>
#include <stddef.h>
#include <string.h>

enum {
    REV_A_DISPLAY_NATIVE_WIDTH = 135,
    REV_A_DISPLAY_NATIVE_HEIGHT = 240,
    REV_A_DISPLAY_NATIVE_X_GAP = 52,
    REV_A_DISPLAY_NATIVE_Y_GAP = 40,
    REV_A_DISPLAY_MOUNT_ROTATION = 270,
    GLYPH_WIDTH = 5,
    GLYPH_ADVANCE = 6,
};

board_display_geometry_t board_display_geometry_for_profile(
    const board_display_profile_t *profile, uint16_t user_rotation)
{
    if (profile == NULL) {
        return (board_display_geometry_t) {0};
    }
    const uint16_t rotation = (profile->mount_rotation + user_rotation) % 360;
    const bool landscape = rotation == 90 || rotation == 270;
    return (board_display_geometry_t) {
        .width = landscape ? profile->native_height : profile->native_width,
        .height = landscape ? profile->native_width : profile->native_height,
        .rotation = rotation,
        .x_gap = landscape ? profile->native_y_gap : profile->native_x_gap,
        .y_gap = landscape ? profile->native_x_gap : profile->native_y_gap,
    };
}

board_display_geometry_t board_display_geometry_for_memory_window(
    const board_display_profile_t *profile, uint16_t ram_width,
    uint16_t ram_height, uint16_t user_rotation)
{
    board_display_geometry_t geometry =
        board_display_geometry_for_profile(profile, user_rotation);
    if (profile == NULL ||
        ram_width < profile->native_width + profile->native_x_gap ||
        ram_height < profile->native_height + profile->native_y_gap) {
        return geometry;
    }

    const uint16_t opposite_x_gap =
        ram_width - profile->native_width - profile->native_x_gap;
    const uint16_t opposite_y_gap =
        ram_height - profile->native_height - profile->native_y_gap;
    switch (geometry.rotation) {
    case 90:
        geometry.x_gap = profile->native_y_gap;
        geometry.y_gap = opposite_x_gap;
        break;
    case 180:
        geometry.x_gap = opposite_x_gap;
        geometry.y_gap = opposite_y_gap;
        break;
    case 270:
        geometry.x_gap = opposite_y_gap;
        geometry.y_gap = profile->native_x_gap;
        break;
    case 0:
    default:
        geometry.x_gap = profile->native_x_gap;
        geometry.y_gap = profile->native_y_gap;
        break;
    }
    return geometry;
}

board_display_geometry_t board_display_geometry(uint16_t user_rotation)
{
    static const board_display_profile_t rev_a_profile = {
        .native_width = REV_A_DISPLAY_NATIVE_WIDTH,
        .native_height = REV_A_DISPLAY_NATIVE_HEIGHT,
        .native_x_gap = REV_A_DISPLAY_NATIVE_X_GAP,
        .native_y_gap = REV_A_DISPLAY_NATIVE_Y_GAP,
        .mount_rotation = REV_A_DISPLAY_MOUNT_ROTATION,
    };
    return board_display_geometry_for_profile(&rev_a_profile, user_rotation);
}

uint16_t board_display_text_width(const char *text, uint8_t scale)
{
    if (text == NULL || *text == '\0' || scale == 0) {
        return 0;
    }
    const size_t length = strlen(text);
    const size_t width = ((length - 1) * GLYPH_ADVANCE + GLYPH_WIDTH) * scale;
    return width > UINT16_MAX ? UINT16_MAX : (uint16_t)width;
}

uint8_t board_display_fit_scale(const char *text, uint16_t available_width,
                                uint8_t preferred_scale)
{
    while (preferred_scale > 1 &&
           board_display_text_width(text, preferred_scale) > available_width) {
        --preferred_scale;
    }
    return preferred_scale == 0 ? 1 : preferred_scale;
}

uint16_t board_display_menu_foreground(board_display_menu_state_t state)
{
    switch (state) {
    case BOARD_DISPLAY_MENU_CURSOR:
        return 0x0000;
    case BOARD_DISPLAY_MENU_SAVED:
        return 0x07E0;
    case BOARD_DISPLAY_MENU_EDITING:
        return 0xFFE0;
    case BOARD_DISPLAY_MENU_UNSELECTED:
    default:
        return 0x7BEF;
    }
}

uint16_t board_display_menu_background(board_display_menu_state_t state)
{
    return state == BOARD_DISPLAY_MENU_CURSOR ? 0x07FF : 0x0000;
}

uint8_t board_display_module_row(char value, uint8_t row)
{
    static const uint8_t glyphs[26][7] = {
        {0x0E,0x11,0x11,0x1F,0x11,0x11,0x11},
        {0x1E,0x11,0x11,0x1E,0x11,0x11,0x1E},
        {0x0F,0x10,0x10,0x10,0x10,0x10,0x0F},
        {0x1E,0x11,0x11,0x11,0x11,0x11,0x1E},
        {0x1F,0x10,0x10,0x1E,0x10,0x10,0x1F},
        {0x1F,0x10,0x10,0x1E,0x10,0x10,0x10},
        {0x0F,0x10,0x10,0x17,0x11,0x11,0x0F},
        {0x11,0x11,0x11,0x1F,0x11,0x11,0x11},
        {0x1F,0x04,0x04,0x04,0x04,0x04,0x1F},
        {0x07,0x02,0x02,0x02,0x12,0x12,0x0C},
        {0x11,0x12,0x14,0x18,0x14,0x12,0x11},
        {0x10,0x10,0x10,0x10,0x10,0x10,0x1F},
        {0x11,0x1B,0x15,0x15,0x11,0x11,0x11},
        {0x11,0x19,0x15,0x13,0x11,0x11,0x11},
        {0x0E,0x11,0x11,0x11,0x11,0x11,0x0E},
        {0x1E,0x11,0x11,0x1E,0x10,0x10,0x10},
        {0x0E,0x11,0x11,0x11,0x15,0x12,0x0D},
        {0x1E,0x11,0x11,0x1E,0x14,0x12,0x11},
        {0x0F,0x10,0x10,0x0E,0x01,0x01,0x1E},
        {0x1F,0x04,0x04,0x04,0x04,0x04,0x04},
        {0x11,0x11,0x11,0x11,0x11,0x11,0x0E},
        {0x11,0x11,0x11,0x11,0x11,0x0A,0x04},
        {0x11,0x11,0x11,0x15,0x15,0x1B,0x11},
        {0x11,0x11,0x0A,0x04,0x0A,0x11,0x11},
        {0x11,0x11,0x0A,0x04,0x04,0x04,0x04},
        {0x1F,0x01,0x02,0x04,0x08,0x10,0x1F},
    };
    if (row >= 7) {
        return 0;
    }
    if (value >= 'a' && value <= 'z') {
        value = (char)(value - 'a' + 'A');
    }
    if (value < 'A' || value > 'Z') {
        return 0;
    }
    return glyphs[value - 'A'][row];
}

uint8_t board_display_5r_glyph_width(char value)
{
    const boring_5r_glyph_t *glyph = boring_5r_find((uint8_t)value);
    return glyph == NULL ? 0 : glyph->width;
}

uint8_t board_display_5r_glyph_row(char value, uint8_t row)
{
    const boring_5r_glyph_t *glyph = boring_5r_find((uint8_t)value);
    return glyph == NULL || row >= BORING_5R_ROWS ? 0 : glyph->rows[row];
}

uint16_t board_display_5r_text_columns(const char *text)
{
    const int16_t width = boring_5r_measure(text, 1, 1, 1);
    return width < 0 ? 0 : (uint16_t)width;
}

bool board_display_boot_dot_visible(uint16_t grid_x, uint16_t grid_y,
                                    uint8_t density_percent)
{
    if (density_percent >= 100) {
        return true;
    }
    const uint32_t seed = 97;
    uint32_t hash =
        ((uint32_t)grid_x + seed) * UINT32_C(374761393) +
        ((uint32_t)grid_y + seed) * UINT32_C(668265263);
    hash ^= hash >> 13;
    return (uint64_t)hash * 100U <=
           (uint64_t)density_percent * UINT32_MAX;
}

uint8_t board_display_boot_density(uint32_t elapsed_ms)
{
    if (elapsed_ms >= BOARD_DISPLAY_BOOT_GROWTH_MS) {
        return 100;
    }
    const uint32_t range = 100U - BOARD_DISPLAY_BOOT_START_DENSITY_PERCENT;
    return (uint8_t)(BOARD_DISPLAY_BOOT_START_DENSITY_PERCENT +
                     elapsed_ms * range / BOARD_DISPLAY_BOOT_GROWTH_MS);
}

uint8_t board_display_battery_ring_dot_count(int battery_percent)
{
    if (battery_percent <= 0) {
        return 0;
    }
    if (battery_percent >= BOARD_DISPLAY_BATTERY_FULL_RING_PERCENT) {
        return BOARD_DISPLAY_BATTERY_RING_DOT_COUNT;
    }
    uint8_t count = (uint8_t)(
        (battery_percent * BOARD_DISPLAY_BATTERY_RING_DOT_COUNT + 50) / 100);
    return count == 0 ? 1 : count;
}

board_display_battery_level_t board_display_battery_level(int battery_percent)
{
    if (battery_percent >= BOARD_DISPLAY_BATTERY_HIGH_MIN_PERCENT) {
        return BOARD_DISPLAY_BATTERY_HIGH;
    }
    if (battery_percent > BOARD_DISPLAY_BATTERY_LOW_MAX_PERCENT) {
        return BOARD_DISPLAY_BATTERY_MEDIUM;
    }
    return BOARD_DISPLAY_BATTERY_LOW;
}

bool board_display_boot_should_start(bool product_target, bool firmware_error,
                                     bool platform_selected)
{
    return product_target && !firmware_error && platform_selected;
}

bool board_display_circular_aperture_contains(uint16_t pixel_x,
                                              uint16_t pixel_y,
                                              uint16_t canvas_width,
                                              uint16_t canvas_height,
                                              uint16_t diameter_pixels)
{
    if (diameter_pixels == 0 || pixel_x >= canvas_width ||
        pixel_y >= canvas_height) {
        return false;
    }

    /* Double every coordinate so odd diameters stay centered at half pixels. */
    const int32_t dx = (int32_t)pixel_x * 2 + 1 - canvas_width;
    const int32_t dy = (int32_t)pixel_y * 2 + 1 - canvas_height;
    const int64_t distance_squared =
        (int64_t)dx * dx + (int64_t)dy * dy;
    const int64_t radius_squared =
        (int64_t)diameter_pixels * diameter_pixels;
    return distance_squared <= radius_squared;
}

uint16_t board_display_round_dot_color(uint16_t color, uint8_t pixel_x,
                                       uint8_t pixel_y, uint8_t dot_size)
{
    const bool horizontal_edge = pixel_x == 0 || pixel_x == 3;
    const bool vertical_edge = pixel_y == 0 || pixel_y == 3;
    return dot_size == 4 && horizontal_edge && vertical_edge ? 0x0000 : color;
}
