#pragma once

#include <stddef.h>
#include <stdint.h>

/* Order follows the supplied MIST icon table, not protocol/public page IDs. */
typedef enum {
    BOARD_MIST_ICON_FOCUS,
    BOARD_MIST_ICON_SETTINGS,
    BOARD_MIST_ICON_MACOS,
    BOARD_MIST_ICON_WINDOWS,
    BOARD_MIST_ICON_BACK,
    BOARD_MIST_ICON_CONFIRM,
    BOARD_MIST_ICON_POWER,
    BOARD_MIST_ICON_WARNING,
    BOARD_MIST_ICON_PLAY,
    BOARD_MIST_ICON_PAUSE,
    BOARD_MIST_ICON_CANCEL,
    BOARD_MIST_ICON_ERROR,
    BOARD_MIST_ICON_PROFILE,
    BOARD_MIST_ICON_LIGHTING,
    BOARD_MIST_ICON_HAPTIC,
    BOARD_MIST_ICON_STANDBY,
    BOARD_MIST_ICON_EXIT,
    BOARD_MIST_ICON_MODE_KEY,
    BOARD_MIST_ICON_MODE_CODEX,
    BOARD_MIST_ICON_BLE_LINK,
    BOARD_MIST_ICON_RESTART,
    BOARD_MIST_ICON_SYSTEM,
    BOARD_MIST_ICON_HOME_NORMAL,
    BOARD_MIST_ICON_HOME_CODEX,
    BOARD_MIST_ICON_HOME_CC,
    BOARD_MIST_ICON_USB,
    BOARD_MIST_ICON_BLUETOOTH,
    BOARD_MIST_ICON_BATTERY,
    BOARD_MIST_ICON_COUNT,
} board_mist_icon_t;

typedef struct {
    const char *name;
    uint8_t width;
    uint8_t height;
    const uint16_t *rows;
    const uint8_t *levels;
} board_mist_icon_asset_t;

const board_mist_icon_asset_t *board_mist_icon_asset(board_mist_icon_t icon);

/* MIST #0B0B0B -> #E0F1F0 grayscale in the display's wire-order RGB565. */
uint16_t board_mist_gray_color(uint8_t level);

/* Native: 3px squares/4px pitch; compact: 2px/3px or 1px/2px.
 * No resampling or missing rows; only whole cells inside the aperture.
 * Output is byte-swapped RGB565 for the existing SPI stripe buffers. */
void board_mist_draw_icon(uint16_t *strip, int width, int height,
                          int strip_y, int strip_lines, board_mist_icon_t icon,
                          int center_x, int center_y, uint8_t pitch,
                          uint8_t brightness);
