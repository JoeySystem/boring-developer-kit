#pragma once

/**
 * @file board_display_logic.h
 * @brief Hardware-independent display geometry and text sizing helpers.
 *
 * Display controllers and physical mounts differ between board targets. Keeping
 * target geometry separate from the user rotation prevents factory defaults and
 * saved profiles from having to know how a panel is physically installed.
 */

#include <stdbool.h>
#include <stdint.h>

enum {
    BOARD_DISPLAY_BOOT_START_DENSITY_PERCENT = 35,
    BOARD_DISPLAY_BOOT_GROWTH_MS = 1200,
    BOARD_DISPLAY_BOOT_HOLD_MS = 1000,
    BOARD_DISPLAY_BOOT_TOTAL_MS =
        BOARD_DISPLAY_BOOT_GROWTH_MS + BOARD_DISPLAY_BOOT_HOLD_MS,
    BOARD_DISPLAY_BOOT_FRAME_MS = 50,
    BOARD_DISPLAY_BATTERY_RING_DOT_COUNT = 60,
    BOARD_DISPLAY_BATTERY_LOW_MAX_PERCENT = 30,
    BOARD_DISPLAY_BATTERY_HIGH_MIN_PERCENT = 70,
    BOARD_DISPLAY_BATTERY_FULL_RING_PERCENT = 97,
};

typedef enum {
    BOARD_DISPLAY_BATTERY_LOW = 0,
    BOARD_DISPLAY_BATTERY_MEDIUM,
    BOARD_DISPLAY_BATTERY_HIGH,
} board_display_battery_level_t;

typedef struct {
    uint16_t width;
    uint16_t height;
    uint16_t rotation;
    uint16_t x_gap;
    uint16_t y_gap;
} board_display_geometry_t;

typedef struct {
    uint16_t native_width;
    uint16_t native_height;
    uint16_t native_x_gap;
    uint16_t native_y_gap;
    uint16_t mount_rotation;
} board_display_profile_t;

typedef enum {
    BOARD_DISPLAY_MENU_UNSELECTED = 0,
    BOARD_DISPLAY_MENU_CURSOR,
    BOARD_DISPLAY_MENU_SAVED,
    BOARD_DISPLAY_MENU_EDITING,
} board_display_menu_state_t;

/** Resolve the Rev A ST7789 mount correction plus the user-selected rotation. */
board_display_geometry_t board_display_geometry(uint16_t user_rotation);

/** Resolve target-specific panel geometry plus the user-selected rotation. */
board_display_geometry_t board_display_geometry_for_profile(
    const board_display_profile_t *profile, uint16_t user_rotation);

/**
 * Resolve geometry when the visible panel is a window inside larger controller
 * RAM. Mirrored rotations use the complementary edge gap instead of reusing
 * the native top/left gap.
 */
board_display_geometry_t board_display_geometry_for_memory_window(
    const board_display_profile_t *profile, uint16_t ram_width,
    uint16_t ram_height, uint16_t user_rotation);

/** Return the pixel width of 5x7 text rendered at an integer scale. */
uint16_t board_display_text_width(const char *text, uint8_t scale);

/** Reduce a preferred scale until the text fits the available width. */
uint8_t board_display_fit_scale(const char *text, uint16_t available_width,
                                uint8_t preferred_scale);

/** Return a high-contrast foreground for one semantic menu state. */
uint16_t board_display_menu_foreground(board_display_menu_state_t state);

/** Return a high-contrast background for one semantic menu state. */
uint16_t board_display_menu_background(board_display_menu_state_t state);

/** Return one left-to-right 5-bit row from the 06 module-dot alphabet. */
uint8_t board_display_module_row(char value, uint8_t row);

/** Return the active-column width of one BORING 5R slice glyph. */
uint8_t board_display_5r_glyph_width(char value);

/** Return one left-to-right row from the approved BORING 5R specimen. */
uint8_t board_display_5r_glyph_row(char value, uint8_t row);

/** Return the proportional grid-column width of BORING 5R text. */
uint16_t board_display_5r_text_columns(const char *text);

/** Select a deterministic subset of module dots for one density frame. */
bool board_display_boot_dot_visible(uint16_t grid_x, uint16_t grid_y,
                                    uint8_t density_percent);

/** Resolve the monotonic 35%-to-100% density ramp for elapsed boot time. */
uint8_t board_display_boot_density(uint32_t elapsed_ms);

/** Decide whether a normal boot is eligible to show the logo animation. */
bool board_display_boot_should_start(bool product_target, bool firmware_error,
                                     bool platform_selected);

/** Test whether a pixel center falls inside a centered circular aperture. */
bool board_display_circular_aperture_contains(uint16_t pixel_x,
                                              uint16_t pixel_y,
                                              uint16_t canvas_width,
                                              uint16_t canvas_height,
                                              uint16_t diameter_pixels);

/** Remove the four corners of a 4x4 RGB565 dot; keep other sizes solid. */
uint16_t board_display_round_dot_color(uint16_t color, uint8_t pixel_x,
                                       uint8_t pixel_y, uint8_t dot_size);

/** Resolve visible battery dots; 97% and above is presented as a full ring. */
uint8_t board_display_battery_ring_dot_count(int battery_percent);

/** Resolve the product battery color band: red, orange, or green. */
board_display_battery_level_t board_display_battery_level(int battery_percent);
