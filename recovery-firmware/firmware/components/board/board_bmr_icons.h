#pragma once

#include <stddef.h>
#include <stdint.h>

#include "board.h"

typedef enum {
    BOARD_BMR_DOT_PRIMARY = 0,
    BOARD_BMR_DOT_ACCENT_ORANGE,
} board_bmr_dot_role_t;

typedef struct {
    uint16_t center_x_centi_mm;
    uint16_t center_y_centi_mm;
    uint8_t radius_centi_mm;
    uint8_t role;
} board_bmr_dot_t;

typedef struct {
    const board_bmr_dot_t *dots;
    uint16_t dot_count;
    uint16_t min_x_centi_mm;
    uint16_t min_y_centi_mm;
    uint16_t max_x_centi_mm;
    uint16_t max_y_centi_mm;
} board_bmr_icon_asset_t;

const board_bmr_icon_asset_t *
board_bmr_icon_asset(board_bmr_screen_icon_t icon);
