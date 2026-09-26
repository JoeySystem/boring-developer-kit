#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "board_mist_icons.h"
#include "mist_glyph_pages.h"

/* Display-only state for the supplied MIST pages. No persisted configuration. */
typedef enum {
    BOARD_MIST_HOME, BOARD_MIST_BOOT, BOARD_MIST_BATTERY,
    BOARD_MIST_CONNECTION, BOARD_MIST_CAROUSEL, BOARD_MIST_PROMPT,
    BOARD_MIST_HAPTIC, BOARD_MIST_LIGHTING, BOARD_MIST_STANDBY,
    BOARD_MIST_RESTART_CONFIRM, BOARD_MIST_NOTICE,
    BOARD_MIST_TIMER, BOARD_MIST_QUICK, BOARD_MIST_LOCAL,
    BOARD_MIST_TWO_CHOICE, BOARD_MIST_SYSTEM_TEXT,
    BOARD_MIST_BLE, BOARD_MIST_BLE_PROGRESS, BOARD_MIST_BLE_CONFIRM,
    BOARD_MIST_PAGE_COUNT
} board_mist_page_t;

typedef enum {
    BOARD_MIST_TIMER_SETUP, BOARD_MIST_TIMER_RUNNING,
    BOARD_MIST_TIMER_PAUSED, BOARD_MIST_TIMER_DONE, BOARD_MIST_TIMER_CANCEL
} board_mist_timer_state_t;

typedef struct {
    board_mist_page_t page;
    int a, b, c, d; /* Carousel b is the navigation direction (-1/0/+1). */
    float phase; /* 0..1; 1 is the static terminal frame. */
    float position; /* Legacy caller field; selection uses selected_index. */
    uint8_t items[5];
    uint8_t item_count;
    uint8_t selected_index;
    bool carousel_label;
    uint8_t max_level; /* Real product range, currently 0..4. */
    uint16_t read_index;
    uint32_t total_seconds;
    uint32_t remaining_seconds;
    board_mist_timer_state_t timer_state;
    bool show_exact_time;
    bool confirm_selected;
    const char *title;
    const char *primary;
    const char *secondary;
    const char *footer;
    const char *previous;
    const char *next;
    const char *saved;
} board_mist_view_t;

enum { BOARD_MIST_GRID_SIZE = 31, BOARD_MIST_GRID_MIN = -3,
       BOARD_MIST_GRID_MAX = 27, BOARD_MIST_AUX_MAX = 8 };
typedef struct {
    mist_glyph_scene_t scene;
    uint16_t part_count;
} board_mist_frame_t;

/* Prepare the compact logical grid once, then emit the existing LCD stripes. */
void board_mist_ui_prepare(board_mist_frame_t *frame, const board_mist_view_t *view);
void board_mist_ui_render_strip(const board_mist_frame_t *frame,
                               uint16_t *pixels, int y, int lines);
/* Runtime SPI path: custom bitmap only for the settled NORMAL idle scene. */
void board_mist_ui_render_scene_strip(const mist_glyph_scene_t *scene,
                                     uint16_t *pixels, int y, int lines);

/* The display owns animation time and scene storage, with no action side effects. */
bool board_mist_ui_request(const board_mist_view_t *view, uint32_t now_ms);
const mist_glyph_scene_t *board_mist_ui_frame(uint32_t now_ms);
void board_mist_ui_finish_motion(void);

/* Volatile host-provided quota; percentages are remaining, never token counts. */
void board_mist_ui_set_codex_usage(uint8_t weekly, int five_hour,
                                   bool show_on_home, uint32_t now_ms);
bool board_mist_ui_get_codex_usage(uint32_t now_ms, uint8_t *weekly,
                                   int *five_hour);
void board_mist_ui_clear_codex_usage(void);
