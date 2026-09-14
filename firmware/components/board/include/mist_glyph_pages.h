#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "mist_glyph_renderer.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    MIST_PAGE_HOME = 0,
    MIST_PAGE_BOOT,
    MIST_PAGE_BATTERY,
    MIST_PAGE_FUNCTION,
    MIST_PAGE_SETTINGS,
    MIST_PAGE_SYSTEM,
    MIST_PAGE_HAPTIC,
    MIST_PAGE_LIGHTING,
    MIST_PAGE_STANDBY,
    MIST_PAGE_RESTART_CONFIRM,
    MIST_PAGE_QUICK,
    MIST_PAGE_TIMER_SETUP,
    MIST_PAGE_TIMER_RUN,
    MIST_PAGE_TIMER_PAUSE,
    MIST_PAGE_TIMER_CANCEL,
    MIST_PAGE_PROMPT,
    MIST_PAGE_BLE,
    MIST_PAGE_BLE_PROGRESS,
    MIST_PAGE_SUCCESS,
    MIST_PAGE_ERROR,
    MIST_PAGE_WARNING,
    MIST_PAGE_PLAY,
    MIST_PAGE_PAUSE,
    MIST_PAGE_CANCEL,
    MIST_PAGE_RESTART,
    MIST_PAGE_SHUTDOWN,
    MIST_PAGE_SLEEP,
    MIST_PAGE_BMR,
    MIST_PAGE_BLE_CONFIRM,
    MIST_PAGE_ICON_CHOICE,
    MIST_PAGE_ALL_CAROUSEL,
    MIST_PAGE_SYSTEM_TEXT,
    MIST_PAGE_STATUS_TEXT,
    MIST_PAGE_TWO_CHOICE_TEXT,
    MIST_PAGE_LOCAL_TEXT,
    MIST_PAGE_COUNT
} mist_glyph_page_id_t;

typedef enum {
    MIST_MODE_NORMAL = 0,
    MIST_MODE_CODEX,
    MIST_MODE_CLAUDE_CODE
} mist_glyph_mode_t;

typedef enum {
    MIST_LINK_NONE = 0,
    MIST_LINK_USB,
    MIST_LINK_BLE
} mist_glyph_link_t;

typedef enum {
    MIST_STATE_IDLE = 0,
    MIST_STATE_ACTIVE,
    MIST_STATE_APPLYING,
    MIST_STATE_ERROR
} mist_glyph_state_t;

typedef struct {
    mist_glyph_mode_t mode;
    mist_glyph_link_t link;
    mist_glyph_state_t state;
    int16_t battery_percent;
    bool charging;
    uint8_t selected;
    uint8_t previous;
    uint8_t item_count;
    uint8_t level;
    uint8_t slot;
    uint8_t progress_percent;
    uint8_t minutes;
    uint8_t seconds;
    uint8_t total_minutes;
    bool confirm_selected;
    bool timer_done;
    bool exact_time_visible;
    uint8_t animation_phase;
    uint8_t max_level; /* Display range; zero retains the editor's 0..9 default. */
    const char *title;
    const char *primary;
    const char *secondary;
    const char *footer;
} mist_glyph_page_context_t;

typedef struct {
    const char *id;
    uint8_t review_group;
} mist_glyph_page_descriptor_t;

enum {
    MIST_GLYPH_BOOT_LOGO_MS = 2380,
    MIST_GLYPH_BOOT_HOLD_MS = 180,
    MIST_GLYPH_BOOT_COLLAPSE_MS = 300,
    MIST_GLYPH_BOOT_SPREAD_MS = 160,
    MIST_GLYPH_BOOT_LOADER_MS = 1000,
    MIST_GLYPH_BOOT_CLOSE_MS = 160,
    MIST_GLYPH_BOOT_EXPAND_MS = 160,
    MIST_GLYPH_BOOT_TOTAL_MS = 4340,
    MIST_GLYPH_BOOT_FRAME_MS = 35,
};

extern const mist_glyph_page_descriptor_t mist_glyph_page_catalog[MIST_PAGE_COUNT];

void mist_glyph_page_context_default(mist_glyph_page_context_t *context);
bool mist_glyph_page_build(mist_glyph_page_id_t page,
                           const mist_glyph_page_context_t *context,
                           mist_glyph_scene_t *scene);
void mist_glyph_function_carousel_frame(const mist_glyph_page_context_t *context,
                                         float position, mist_glyph_scene_t *scene);
bool mist_glyph_boot_frame(uint32_t elapsed_ms, mist_glyph_mode_t mode,
                           mist_glyph_scene_t *scene);

#ifdef __cplusplus
}
#endif
