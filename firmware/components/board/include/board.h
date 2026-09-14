#pragma once

/**
 * @file board.h
 * @brief Target-independent board input and feedback contract.
 *
 * Application code consumes logical controls from this interface and must not
 * depend on product GPIO numbers. A selected board target provides exactly one
 * implementation of these functions.
 */

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    /** Stable logical identities shared with the configuration protocol. */
    BOARD_CONTROL_KEY_1 = 0,
    BOARD_CONTROL_KEY_2,
    BOARD_CONTROL_KEY_3,
    BOARD_CONTROL_KEY_4,
    BOARD_CONTROL_KEY_5,
    BOARD_CONTROL_KEY_6,
    BOARD_CONTROL_KEY_7,
    BOARD_CONTROL_ENCODER_CCW,
    BOARD_CONTROL_ENCODER_CW,
    BOARD_CONTROL_ENCODER_PRESS,
    BOARD_CONTROL_JOYSTICK_UP,
    BOARD_CONTROL_JOYSTICK_DOWN,
    BOARD_CONTROL_JOYSTICK_LEFT,
    BOARD_CONTROL_JOYSTICK_RIGHT,
    BOARD_CONTROL_JOYSTICK_PRESS,
    /** Appended to preserve the original numeric IDs for Rev A controls. */
    BOARD_CONTROL_KEY_8,
    BOARD_CONTROL_KEY_9,
    BOARD_CONTROL_KEY_10,
    BOARD_CONTROL_KEY_11,
    BOARD_CONTROL_KEY_12,
    BOARD_CONTROL_COUNT,
} board_control_t;

#define BOARD_MAX_KEY_COUNT 12
#define BOARD_STATUS_RGB_COUNT 8
#define BOARD_MAX_UNDER_KEY_RGB_COUNT 12

static inline bool board_control_key_index(board_control_t control,
                                           size_t *key_index)
{
    size_t index;
    if (control >= BOARD_CONTROL_KEY_1 && control <= BOARD_CONTROL_KEY_7) {
        index = (size_t)(control - BOARD_CONTROL_KEY_1);
    } else if (control >= BOARD_CONTROL_KEY_8 &&
               control <= BOARD_CONTROL_KEY_12) {
        index = 7U + (size_t)(control - BOARD_CONTROL_KEY_8);
    } else {
        return false;
    }
    if (key_index != NULL) {
        *key_index = index;
    }
    return true;
}

static inline bool board_control_from_key_index(size_t key_index,
                                                board_control_t *control)
{
    board_control_t value;
    if (key_index < 7U) {
        value = (board_control_t)(BOARD_CONTROL_KEY_1 + key_index);
    } else if (key_index < BOARD_MAX_KEY_COUNT) {
        value = (board_control_t)(BOARD_CONTROL_KEY_8 + key_index - 7U);
    } else {
        return false;
    }
    if (control != NULL) {
        *control = value;
    }
    return true;
}

typedef struct {
    board_control_t control;
    /** True for press/active and false for release/neutral. */
    bool pressed;
    /** Local tactile edge only; never route this event to host actions. */
    bool feedback_only;
} board_event_t;

typedef struct {
    uint8_t red;
    uint8_t green;
    uint8_t blue;
} board_rgb_t;

typedef struct {
    int minimum_x;
    int center_x;
    int maximum_x;
    int minimum_y;
    int center_y;
    int maximum_y;
    int deadzone_x;
    int deadzone_y;
    bool invert_x;
    bool invert_y;
    uint8_t filter_percent;
} board_joystick_config_t;

/** Read-only snapshot used to diagnose analog joystick input and normalization. */
typedef struct {
    bool supported;
    int raw_x;
    int raw_y;
    int filtered_x;
    int filtered_y;
    int center_x;
    int center_y;
    int minimum_x;
    int maximum_x;
    int minimum_y;
    int maximum_y;
    int deadzone_x;
    int deadzone_y;
    uint8_t directions;
    bool radial_active;
    bool radial_valid;
    float radial_angle_turns;
} board_joystick_diagnostics_t;

/** Initialize the selected board target and leave all outputs in a safe state. */
esp_err_t board_init(void);

/** Poll physical inputs and bounded-duration outputs; call from the main loop. */
void board_poll(void);

/**
 * Copy the oldest pending logical event into @p event.
 *
 * @return true when an event was returned, otherwise false.
 */
bool board_next_event(board_event_t *event);

/** Return the immutable engineering target name used in logs and diagnostics. */
const char *board_target_name(void);

/** Stable configuration identity for this physical board target. */
const char *board_hardware_id(void);

/** Physical key and RGB counts advertised by this selected target. */
size_t board_key_count(void);
size_t board_status_rgb_count(void);
size_t board_under_key_rgb_count(void);

/** Return true for a product PCB target and false for the engineering devkit. */
bool board_is_product_target(void);

/** Stable protocol name for a logical control, or NULL when out of range. */
const char *board_control_id(board_control_t control);

/** Resolve a stable protocol name to its logical control. */
bool board_control_from_id(const char *control_id, board_control_t *control);

/** True when the selected target physically exposes the logical control. */
bool board_control_supported(board_control_t control);

/** Number of logical controls physically exposed by the selected target. */
size_t board_control_count(void);

/** True when all debounced digital and joystick controls are released. */
bool board_inputs_neutral(void);

/** Copy currently active logical controls and return the number copied. */
size_t board_get_active_controls(board_control_t *controls, size_t capacity);

/**
 * Return the latest oriented analog joystick angle while outside its deadzone.
 *
 * The clockwise angle uses turns: right=0, down=0.25, left=0.5, up=0.75.
 * Logical four-direction events remain available independently.
 */
bool board_get_joystick_radial(float *angle_turns);

/** Copy a read-only joystick diagnostic snapshot without changing input state. */
bool board_get_joystick_diagnostics(board_joystick_diagnostics_t *diagnostics);

/**
 * Keep analog sampling active while preventing joystick movement or press
 * events from reaching the host during a desktop calibration session.
 */
void board_set_joystick_calibration_active(bool active);
/** Matrix display ownership: calibration suppresses decorative refresh. */
bool board_joystick_calibration_active(void);

/** Consume one board-level request to begin the normal shutdown sequence. */
bool board_take_shutdown_request(void);

/** Power V2: use the GEK100_35 wake hold while waiting for user ON.
 * Does not reset/re-arm the current physical gesture or read the HW latch.
 */
void board_set_power_button_wake_mode(bool waiting_for_on);

/** Power V2: both sampled and debounced power-button states are released. */
bool board_power_button_released(void);

/** Pulse the hardware power-controller input after application cleanup. */
esp_err_t board_power_request_shutdown(void);

/** Set one top status LED and refresh its physical chain. */
esp_err_t board_set_status_rgb(size_t index, uint8_t red, uint8_t green, uint8_t blue);

/** Set one under-key LED and refresh its physical chain. */
esp_err_t board_set_under_key_rgb(size_t index, uint8_t red, uint8_t green, uint8_t blue);

/** Apply complete RGB chains and refresh each physical chain once. */
esp_err_t board_apply_rgb(
    const board_rgb_t status[BOARD_STATUS_RGB_COUNT],
    const board_rgb_t under_key[BOARD_MAX_UNDER_KEY_RGB_COUNT]);

/** Replace the complete eight-pixel status chain in one refresh cycle. */
esp_err_t board_apply_status_rgb(
    const board_rgb_t status[BOARD_STATUS_RGB_COUNT]);

/**
 * Start a non-blocking motor pulse.
 *
 * Product targets clamp strength and duration to board-specific safety limits.
 */
esp_err_t board_haptic_pulse(uint8_t strength_percent, uint16_t duration_ms);

/** Stop the motor immediately, including a pulse already in progress. */
esp_err_t board_haptic_stop(void);

/** Fill the active display using a native RGB565 color value. */
esp_err_t board_display_fill(uint16_t rgb565);

/** Draw BORING at the supplied progress (MIST column reveal on Power V2). */
esp_err_t board_display_show_boot_logo(uint8_t density_percent);

#include "board_mist_ui.h"
/** Draw one MIST frame through the existing bounded LCD stripe buffers. */
esp_err_t board_display_show_mist(const board_mist_view_t *view);
esp_err_t board_display_show_mist_scene(const mist_glyph_scene_t *scene);
esp_err_t board_display_poll_mist(uint32_t now_ms);
/* Complete display motion without drawing over diagnostics or standby. */
void board_display_finish_mist_motion(void);

typedef enum {
    BOARD_ROUND_SETTING_EDITING = 0,
    BOARD_ROUND_SETTING_APPLYING,
    BOARD_ROUND_SETTING_ERROR,
} board_round_setting_state_t;

typedef enum {
    BOARD_ROUND_MODE_KEY = 0,
    BOARD_ROUND_MODE_CODEX,
    BOARD_ROUND_MODE_CLAUDE_CODE,
} board_round_mode_t;

typedef enum {
    BOARD_ROUND_LINK_DISCONNECTED = 0,
    BOARD_ROUND_LINK_USB,
    BOARD_ROUND_LINK_BLE,
} board_round_link_t;

typedef enum {
    BOARD_ROUND_PAGE_ICON_TIMER = 0,
    BOARD_ROUND_PAGE_ICON_SETTINGS,
    BOARD_ROUND_PAGE_ICON_MACOS,
    BOARD_ROUND_PAGE_ICON_WINDOWS,
    BOARD_ROUND_PAGE_ICON_BACK,
    BOARD_ROUND_PAGE_ICON_CONFIRM,
    BOARD_ROUND_PAGE_ICON_POWER,
    BOARD_ROUND_PAGE_ICON_WARNING,
    BOARD_ROUND_PAGE_ICON_PLAY,
    BOARD_ROUND_PAGE_ICON_PAUSE,
    BOARD_ROUND_PAGE_ICON_CANCEL,
    BOARD_ROUND_PAGE_ICON_ERROR,
    BOARD_ROUND_PAGE_ICON_PROFILE,
    BOARD_ROUND_PAGE_ICON_LIGHTING,
    BOARD_ROUND_PAGE_ICON_HAPTIC,
    BOARD_ROUND_PAGE_ICON_STANDBY,
    BOARD_ROUND_PAGE_ICON_EXIT,
    BOARD_ROUND_PAGE_ICON_MODE_KEY,
    BOARD_ROUND_PAGE_ICON_MODE_CODEX,
    BOARD_ROUND_PAGE_ICON_BLE,
    BOARD_ROUND_PAGE_ICON_RESTART,
    BOARD_ROUND_PAGE_ICON_SYSTEM,
    BOARD_ROUND_PAGE_ICON_COUNT,
} board_round_page_icon_t;

/** Approved BORING BMR D050 icon masters available to the round-screen UI. */
typedef enum {
    BOARD_BMR_SCREEN_ICON_CONFIRM_SEND = 0,
    BOARD_BMR_SCREEN_ICON_REJECT_CANCEL,
    BOARD_BMR_SCREEN_ICON_HOME,
    BOARD_BMR_SCREEN_ICON_COPY_PASTE,
    BOARD_BMR_SCREEN_ICON_PROMPT_PRESET,
    BOARD_BMR_SCREEN_ICON_VOICE,
    BOARD_BMR_SCREEN_ICON_MULTIFUNCTION_3,
    BOARD_BMR_SCREEN_ICON_MULTIFUNCTION_2,
    BOARD_BMR_SCREEN_ICON_APPLICATION_PROFILE_SELECTOR,
    BOARD_BMR_SCREEN_ICON_PR_SUBMISSION_CONFIRMATION,
    BOARD_BMR_SCREEN_ICON_COUNT,
} board_bmr_screen_icon_t;

typedef enum {
    BOARD_ROUND_TIMER_SETUP = 0,
    BOARD_ROUND_TIMER_RUNNING,
    BOARD_ROUND_TIMER_PAUSED,
} board_round_timer_state_t;

typedef enum {
    BOARD_ROUND_BLE_SLOT_EMPTY = 0,
    BOARD_ROUND_BLE_SLOT_PAIRED,
    BOARD_ROUND_BLE_SLOT_CONNECTED,
} board_round_ble_slot_state_t;

/** Draw the Power V2 mode symbol. Link/battery are displayed on dedicated pages. */
esp_err_t board_display_show_round_home(board_round_mode_t mode,
                                        board_round_link_t link,
                                        int battery_percent);

/**
 * Draw the Power V2 USB-wake battery page without home-page status.
 * charging_standby means USB-powered charging standby was inferred from the
 * product startup path; it is not a charger-current measurement.
 */
esp_err_t board_display_show_round_battery(int battery_percent,
                                           bool charging_standby);

/** Draw the Power V2 haptic-level vertical slice using single-digit levels. */
esp_err_t board_display_show_round_haptic(uint8_t candidate_level,
                                          board_round_setting_state_t state,
                                          bool confirm_selected);

/** Draw the Power V2 key-light brightness vertical slice using single-digit levels. */
esp_err_t board_display_show_round_lighting(uint8_t candidate_level,
                                            board_round_setting_state_t state,
                                            bool confirm_selected);

/** Draw the Power V2 inactivity-standby timeout setting (0 means OFF). */
esp_err_t board_display_show_round_standby(uint8_t candidate_minutes,
                                           uint8_t saved_minutes,
                                           board_round_setting_state_t state,
                                           bool confirm_selected);

/** Draw two icon-only choices and mark the active choice without text. */
esp_err_t board_display_show_round_icon_choice(board_round_page_icon_t first,
                                               board_round_page_icon_t second,
                                               size_t selected_index);

/** Draw the static terminal frame of a 2..5 item MIST carousel. */
esp_err_t board_display_show_round_icon_carousel(board_round_page_icon_t icon,
                                                 size_t selected_index,
                                                 size_t item_count);

/** Draw the four-direction Quick Prompt wheel with one selected prompt. */
esp_err_t board_display_show_round_prompt_palette(uint8_t selected_prompt_id);

/** Draw an icon-led timer page with numeric time and icon-only actions. */
esp_err_t board_display_show_round_timer(uint8_t minutes, uint8_t seconds,
                                         board_round_timer_state_t state,
                                         bool confirm_selected);

/** Draw one centered icon-only notice. */
esp_err_t board_display_show_round_notice(board_round_page_icon_t icon);

/** Draw one approved BMR icon at the standard 32 px screen-check size. */
esp_err_t board_display_show_round_bmr_icon(board_bmr_screen_icon_t icon);

/** Draw one BLE host-slot digit together with its pairing/connection state. */
esp_err_t board_display_show_round_ble_slot(uint8_t slot,
                                            board_round_ble_slot_state_t state);

/** Draw a BLE slot plus the progress of the 3-second replace gesture. */
esp_err_t board_display_show_round_ble_slot_progress(
    uint8_t slot, board_round_ble_slot_state_t state, uint8_t progress_percent);

/** Draw a BLE slot and an explicit cancel/confirm replacement choice. */
esp_err_t board_display_show_round_ble_slot_confirm(
    uint8_t slot, board_round_ble_slot_state_t state, bool confirm_selected);

/** Apply persisted joystick travel, center, deadzone, inversion, and filter settings. */
esp_err_t board_set_joystick_config(const board_joystick_config_t *config);

/** Apply persisted display brightness and orientation. */
esp_err_t board_set_display_config(uint8_t brightness_percent, uint16_t rotation);
/** Matrix12 RAM-only backlight multiplier; preserves configured off and orientation. */
esp_err_t board_set_display_idle_scale(uint8_t percent);

/** Draw the home page with profile, active output, mode, hint, and menu entry. */
esp_err_t board_display_show_status(const char *profile, const char *output,
                                    const char *mode, const char *hint,
                                    bool connected, bool codex_mode);

/** Draw the quick-config cursor, candidate value, and persisted value. */
esp_err_t board_display_show_quick_config(const char *previous_item,
                                          const char *current_item,
                                          const char *next_item,
                                          const char *candidate_value,
                                          const char *saved_value,
                                          bool candidate_saved,
                                          const char *footer);

/** Draw the blocking first-run operating-system selection page. */
esp_err_t board_display_show_system_select(size_t selected_index,
                                           const char *footer);

/**
 * Draw a two-option menu with stable typography and an explicit selection.
 *
 * Both options use the same text scale; the selected row is highlighted in
 * addition to any cursor marker included in its label.
 */
esp_err_t board_display_show_two_choice(const char *title,
                                        const char *first_option,
                                        const char *second_option,
                                        size_t selected_index,
                                        const char *footer,
                                        uint16_t accent_rgb565);

/** Draw a generic local feature page using four centered text rows. */
esp_err_t board_display_show_local(const char *title, const char *primary,
                                   const char *secondary, const char *footer,
                                   uint16_t accent_rgb565);

#ifdef __cplusplus
}
#endif
