#include "action_engine.h"
#include "boot_guard.h"

#include <limits.h>
#include <stdio.h>
#include <string.h>

#include "codex_micro.h"
#include "codex_attention_haptic_model.h"
#include "codex_micro_controls.h"
#include "codex_micro_protocol.h"
#include "claude_code_shortcuts.h"
#include "claude_status.h"
#include "desktop_agent_status.h"
#include "config_protocol.h"
#include "config_store.h"
#include "battery_guard_model.h"
#include "board_display_logic.h"
#include "diagnostic_capture_model.h"
#include "double_click_model.h"
#include "key_gesture_model.h"
#include "eda_shortcuts.h"
#include "encoder_haptic_model.h"
#include "esp_log.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "battery_display.h"
#include "cw2015.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "home_status_model.h"
#include "idle_standby_model.h"
#include "light_idle_model.h"
#include "firmware_update.h"
#include "joystick_radial_haptic_model.h"
#include "joystick_single_action_model.h"
#include "lighting_output_model.h"
#include "board_mist_ui.h"
#include "pomodoro_display_model.h"
#include "pomodoro_model.h"
#include "prompt_joystick_model.h"
#include "prompt_store.h"
#include "host_action_events.h"
#include "quick_config_model.h"
#include "status_indicator.h"
#include "under_key_self_test.h"
#include "usb_link_model.h"
#include "usb_service.h"

#define LOCAL_LONG_PRESS_MS 2000
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
#define FUNCTION_CENTER_HOLD_MS 1000
#else
#define FUNCTION_CENTER_HOLD_MS 2000
#endif
#define PROMPT_PALETTE_HOLD_MS 800
#define BLE_HOST_HINT_REPEAT_MS 30000
/* Holding the Bluetooth key this long adds a computer instead of switching. */
#define BLE_ADD_HOST_HOLD_MS 2000
#define LOCAL_PAGE_TIMEOUT_MS 10000
#define DOUBLE_CLICK_MOTION_TOLERANCE 1
#define ALERT_HAPTIC_STRENGTH 60
#define ALERT_HAPTIC_DURATION_MS 200
#define CONTROL_HAPTIC_MAX_DURATION_MS 200
#define JOYSTICK_EXCURSION_HAPTIC_MS 100
#define ENCODER_HAPTIC_REARM_MS 300
#define PREVIEW_HAPTIC_DURATION_MS 100
#define SAVE_HAPTIC_DURATION_MS 80
#define STATUS_BOOT_DURATION_MS 1400
#define STATUS_FRAME_INTERVAL_MS 50
#define HOST_LIGHTING_PREVIEW_LEASE_MS 5000
#define BATTERY_SPLASH_RETRY_MS 200
#define BATTERY_SPLASH_MAX_WAIT_MS 3000
#define BATTERY_SPLASH_VALID_DURATION_MS 3000
#define ROUND_FEEDBACK_DURATION_MS 2000
/* Low-battery guard presentation (policy thresholds: battery_guard_model.h). */
#define LOW_BATTERY_NOTICE_MS 3000
#define LOW_BATTERY_NOTICE_REPEAT_MS (5U * 60U * 1000U)
#define LOW_BATTERY_OFF_NOTICE_MS 3000
#define LOW_BATTERY_GUARD_POLL_MS 1000
#define LOW_BATTERY_SAMPLE_MAX_AGE_MS 6000
#define LOW_BATTERY_BREATH_FRAME_MS 60
#define LOW_BATTERY_BREATH_MAX_RED 40
#define LOW_BATTERY_OFF_HAPTIC_STRENGTH 50
#define LOW_BATTERY_OFF_HAPTIC_MS 120
#define EDA_CANVAS_PAN_MOUSE_BUTTON (1u << 1)
#define KEY_STATUS_LED_COUNT 6
#define ARRAY_COUNT(values) (sizeof(values) / sizeof((values)[0]))
#define MACRO_SOURCE ((usb_service_source_t)BOARD_CONTROL_COUNT)
#define MACRO_TRANSIENT_SOURCE ((usb_service_source_t)(BOARD_CONTROL_COUNT + 1))

static const char *TAG = "action_engine";

_Static_assert(BOARD_CONTROL_COUNT + 2 <= USB_SERVICE_SOURCE_COUNT,
               "USB service needs one source per control plus two macro sources");

typedef struct {
    bool active;
    uint8_t id;
    size_t index;
    TickType_t wait_until;
    uint16_t release_usage;
    bool release_shift;
    char text[CONFIG_STORE_MAX_TEXT_BYTES + 1];
    size_t text_index;
} macro_runtime_t;

typedef enum {
    HOST_OUTPUT_ENABLED = 0,
    HOST_OUTPUT_LOCAL_UI,
    HOST_OUTPUT_DIAGNOSTIC,
    HOST_OUTPUT_DIAGNOSTIC_WAIT_NEUTRAL,
    HOST_OUTPUT_CALIBRATION,
    HOST_OUTPUT_WAIT_NEUTRAL,
} host_output_state_t;

typedef enum {
    STATUS_FEEDBACK_SUCCESS = 0,
    STATUS_FEEDBACK_ERROR,
    STATUS_FEEDBACK_WARNING,
    STATUS_FEEDBACK_PLAY,
    STATUS_FEEDBACK_PAUSE,
    STATUS_FEEDBACK_CANCEL,
} status_feedback_t;

static config_feedback_t s_feedback;
static macro_runtime_t s_macro;
static host_output_state_t s_host_output_state;
static bool s_encoder_pressed;
static TickType_t s_encoder_pressed_at;
static bool s_quick_config_active;
static board_round_setting_state_t s_round_setting_state;
static const char *s_quick_config_pending_footer;
static quick_config_model_t s_quick_config;
static bool s_system_select_active;
static config_platform_t s_system_selection = CONFIG_PLATFORM_MACOS;
static uint8_t s_platform_context = UINT8_MAX;
static bool s_platform_setup_needed;
static bool s_system_save_pending;
static bool s_system_return_to_settings;
static bool s_usb_mounted;
static bool s_ble_connected;
static codex_micro_mode_t s_codex_mode;
static TickType_t s_status_started_at;
static TickType_t s_status_last_frame_at;
static bool s_status_frame_valid;
static bool s_codex_key_status_active;
static bool s_firmware_error;
static bool s_shutdown_active;
static bool s_shutdown_pulsed;
static TickType_t s_shutdown_power_at;
static bool s_battery_splash_active;
static bool s_usb_standby_active;
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
static usb_standby_trace_t s_usb_standby_trace;
/* Why the pending shutdown parks in USB standby if the MCU survives it. */
static usb_standby_entry_reason_t s_shutdown_standby_reason;
#endif
static bool s_usb_charge_session_at_boot;
/* Low-battery guard. A protective shutdown keeps the user's ON intent. */
static battery_guard_model_t s_battery_guard;
static TickType_t s_battery_guard_polled_at;
static bool s_low_battery_boot_blocked;
static bool s_low_battery_shutdown;
static bool s_low_battery_locked_standby;
static bool s_low_battery_notice_due;
static TickType_t s_low_battery_notice_next_at;
static TickType_t s_low_battery_breath_frame_at;
static bool s_low_battery_breath_shown;
static TickType_t s_battery_splash_started_at;
static TickType_t s_battery_splash_valid_at;
static TickType_t s_battery_splash_next_retry_at;
static bool s_battery_splash_has_valid_sample;
static int64_t s_battery_sample_time_ms;
static lighting_preview_model_t s_lighting_preview;
static lighting_host_preview_model_t s_host_lighting_preview;
static bool s_boot_animation_active;
static bool s_boot_animation_complete;
static TickType_t s_boot_animation_started_at;
static TickType_t s_boot_animation_last_frame_at;
static uint8_t s_boot_animation_last_density;
static idle_standby_model_t s_idle_standby;
static usb_link_model_t s_usb_link;
static light_idle_model_t s_light_idle = {.percent = 100};
static bool s_idle_standby_wake_wait;
static bool s_round_feedback_active;
static TickType_t s_round_feedback_expires_at;
static board_rgb_t s_status_pixels[STATUS_INDICATOR_LED_COUNT];
static bool s_under_key_self_test_active;
static TickType_t s_under_key_self_test_started_at;
static size_t s_under_key_self_test_last_step;
static config_action_t s_active_actions[BOARD_CONTROL_COUNT];
static bool s_active_action_valid[BOARD_CONTROL_COUNT];
static bool s_function_key_gesture_active;
static bool s_function_key_gesture_combo;
static bool s_function_key_center_opened;
static bool s_function_key_local_navigation;
static bool s_function_key_action_pressed;
static bool s_function_key_press_feedback_sent;
static bool s_prompt_palette_key_gesture_active;
static bool s_prompt_palette_key_action_pressed;
static TickType_t s_prompt_palette_key_pressed_at;
/* The Bluetooth key (K8 while K3 is held), from its press to its release. */
static board_control_t s_ble_host_key_control = BOARD_CONTROL_COUNT;
static TickType_t s_ble_host_key_pressed_at;
static bool s_ble_host_key_hold_done;
static bool s_function_key_release_pending;
static TickType_t s_function_key_pressed_at;
static TickType_t s_function_key_release_at;
static bool s_tap_release_pending[BOARD_CONTROL_COUNT];
static TickType_t s_tap_release_at[BOARD_CONTROL_COUNT];
static double_click_model_t s_encoder_gesture;
static double_click_model_t s_joystick_gesture;
#define CONFIGURABLE_KEY_GESTURE_COUNT 5
static key_gesture_model_t s_key_gestures[CONFIGURABLE_KEY_GESTURE_COUNT];
static key_pulse_model_t s_key_pulses[CONFIGURABLE_KEY_GESTURE_COUNT];
static config_action_t s_key_pulse_actions[CONFIGURABLE_KEY_GESTURE_COUNT];
static config_action_t s_key_gesture_actions[CONFIGURABLE_KEY_GESTURE_COUNT];
static encoder_haptic_model_t s_encoder_haptic;
static joystick_radial_haptic_model_t s_joystick_radial_haptic;
static prompt_joystick_model_t s_prompt_joystick;
static joystick_single_action_model_t s_single_action_joystick;
static bool s_dispatching_single_action_joystick;
static uint8_t s_prompt_joystick_direction_mask;
static bool s_normal_joystick_single_action;
static codex_micro_mode_t s_encoder_gesture_mode;
static unsigned s_encoder_motion_count;
static unsigned s_joystick_excursions;
static bool s_joystick_direction_active;
static bool s_joystick_suspect;
static eda_canvas_pan_model_t s_eda_canvas_pan;
static bool s_encoder_direct_press;
static bool s_joystick_direct_press;
static pomodoro_model_t s_pomodoro;
static bool s_alert_ack_armed;
static bool s_timer_done_hint;
static codex_attention_haptic_model_t s_codex_attention_haptic;
static TickType_t s_local_last_input_at;
static diagnostic_capture_model_t s_diagnostic_capture;
static bool s_diagnostic_encoder_press_active;

typedef enum {
    LOCAL_PAGE_NONE = 0,
    LOCAL_PAGE_FUNCTION,
    LOCAL_PAGE_SETTINGS,
    LOCAL_PAGE_RESTART_CONFIRM,
    LOCAL_PAGE_TIMER_SETUP,
    LOCAL_PAGE_TIMER_DETAIL,
    LOCAL_PAGE_TIMER_CANCEL,
    LOCAL_PAGE_PROMPT_PALETTE,
    LOCAL_PAGE_SYSTEM,
    LOCAL_PAGE_STATUS_DETAIL,
    LOCAL_PAGE_BATTERY,
} local_page_t;

static local_page_t s_local_page;
static uint8_t s_function_cursor;
static uint8_t s_settings_cursor;
static bool s_local_confirm_selected;
static bool s_cancel_yes;
static uint8_t s_prompt_palette_selected_id;
static uint32_t s_last_timer_display_second = UINT32_MAX;

#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
static uint32_t s_mist_last_frame_ms;
static uint32_t s_mist_peek_until_ms;
static bool s_mist_peek_active;
static bool s_mist_connection_visible;
static uint8_t s_mist_connection_output;
static uint8_t s_mist_connection_pending;
static uint32_t s_mist_connection_started_ms;
#define MIST_CONNECTION_NOTICE_MS 2000U
#define MIST_RESTART_NOTICE_MS 600U
static uint16_t s_mist_read_index;
static int s_mist_navigation_delta;
#endif

static const uint8_t LIGHTING_LEVELS[] = {0, 10, 20, 40, 80};
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
/* The Power V2 motor barely registers at 40 and 50 percent: levels 1 and 2
 * reuse the former level 3 and 4 strengths. Stored values below 60 percent
 * are migrated by config_store. */
static const uint8_t HAPTIC_LEVELS[] = {0, 60, 80};
_Static_assert(sizeof(HAPTIC_LEVELS) / sizeof(HAPTIC_LEVELS[0]) ==
                   BOARD_ROUND_HAPTIC_MAX_LEVEL + 1U,
               "haptic table must match the round display range");
#else
static const uint8_t HAPTIC_LEVELS[] = {0, 40, 50, 60, 80};
#endif
/*
 * Idle standby is switched off (2026-11-12): once in it only the
 * joystick woke the device, keys and the knob did not, and it bought little
 * battery life. The minutes a user saved earlier stay in NVS ("idle_min")
 * and are not read, so the model is never armed and the paths below that
 * enter and leave idle standby are not reached. USB standby, the power-off
 * paths and the idle dimming of lights and screen are separate and unchanged.
 */
#define IDLE_STANDBY_MINUTES 0u
static const size_t MATRIX12_AGENT_KEY_INDICES[KEY_STATUS_LED_COUNT] = {
    0, 1, 3, 4, 5, 6,
};
static const char *const QUICK_CONFIG_ITEM_NAMES[QUICK_CONFIG_ITEM_COUNT] = {
    "PROFILE",
    "SYSTEM",
    "KEY PRESET",
    "LIGHTING",
    "HAPTIC",
    "EXIT",
};

static void set_hid_usage(usb_service_source_t source, uint16_t usage, bool pressed);
static uint32_t ticks_to_ms(TickType_t ticks);
static void macro_cancel(void);
static void show_status(const char *hint);
static void show_feedback(status_feedback_t feedback,
                          const char *legacy_hint);
static void render_quick_config(const char *footer);
static void apply_feedback_config(void);
static void update_status_indicator(bool force);
static void update_under_key_self_test(void);
static board_control_t function_center_control(void);
static bool handle_function_key_gesture(const board_event_t *event);
static void poll_function_key_gesture(void);
static bool handle_prompt_palette_key_gesture(const board_event_t *event);
static void poll_prompt_palette_key_gesture(void);
static void poll_function_key_release(void);
static void clear_pending_gestures(bool replay);
static void schedule_tap_release(board_control_t control);
static void poll_tap_releases(void);
static void poll_eda_canvas_pan(void);
static void render_local_page(void);
static void show_function_center(uint8_t selected_index);
static void enter_function_center(void);
static void enter_prompt_palette(void);
static void route_main_event(const board_event_t *event);
static void handle_double_click_actions(board_control_t control, uint8_t actions);
static void poll_pomodoro(void);
static void poll_codex_attention_notification(void);
static void poll_joystick_excursion_haptic(void);
static void poll_joystick_suspect(void);
static void start_boot_animation(void);
static void poll_boot_animation(void);
static void enter_initial_page(void);
static void start_usb_battery_splash(void);
static void enter_usb_standby(usb_standby_entry_reason_t reason);
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
static bool s_restart_on_standby_wake;
#endif
static void poll_usb_standby(void);
static void wake_from_usb_standby(usb_standby_wake_reason_t reason);
static void poll_round_feedback(void);
static void dispatch_latched_standard_action(board_control_t control,
                                             config_action_t action,
                                             const char *short_name,
                                             bool pressed);
static void poll_battery_status(void);
static void poll_battery_guard(void);
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
static bool low_battery_breath_active(void);
#endif
static void begin_shutdown(const board_mist_view_t *, uint32_t, bool,
                           usb_standby_entry_reason_t);
static void sync_connection_and_mode_state(void);
static void reset_mode_sensitive_input_state(void);
static void enter_local_input_ownership(void);
static void leave_local_input_ownership(void);
static void poll_local_input_ownership(void);
static void enter_idle_standby(void);
static void wake_from_idle_standby(void);
static void poll_idle_standby(void);
static void process_diagnostic_capture_request(void);
static void poll_calibration_ownership(void);
static void poll_diagnostic_capture(void);
static void publish_diagnostic_capture_status(void);
static board_round_ble_slot_state_t ble_slot_display_state(uint8_t slot);
static void enter_os_settings(bool from_settings);
static void finish_os_settings_save(void);
static void poll_platform_context(void);
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
static void mist_cancel_motion(void);
static void render_mist_battery(void);
static void render_mist_connection(void);
static void render_mist_timer(void);
static void render_mist_carousel(const uint8_t *items, uint8_t count, uint8_t selected);
static void poll_mist_display(void);
#endif

static int current_battery_percent(int64_t *sample_time_ms)
{
    fuel_gauge_sample_t battery;
    const bool valid = fuel_gauge_latest(&battery);
    if (sample_time_ms != NULL) {
        *sample_time_ms = battery.last_update_time_ms;
    }
    if (!valid) {
        return -1;
    }
    /* The number the user sees; the low-battery guard reads the raw value. */
    return (int)battery_display_percent(battery.soc_percent);
}

#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
static bool encoder_power_button_pressed_at_boot(void)
{
    board_control_t active_controls[BOARD_CONTROL_COUNT];
    const size_t active_count = board_get_active_controls(
        active_controls, ARRAY_COUNT(active_controls));
    for (size_t index = 0; index < active_count; ++index) {
        if (active_controls[index] == BOARD_CONTROL_ENCODER_PRESS) {
            return true;
        }
    }
    return false;
}
#endif

static bool should_start_usb_charge_session(void)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    /* Menu/host restart and CDC OTA retain user intent even when the first
     * fuel-gauge sample is unavailable. */
    if (esp_reset_reason() == ESP_RST_SW) {
        return !config_store_get_user_powered_on();
    }
    if (encoder_power_button_pressed_at_boot()) {
        (void)config_store_set_user_powered_on(true);
        return false;
    }
    /* A battery-backed ON session may restart during USB power handover.
     * Do not turn a missing first sample into a new OFF session. When a
     * sample is available, retain the existing usable-cell check. Explicit
     * user OFF always remains OFF. */
    fuel_gauge_sample_t battery;
    if (config_store_get_user_powered_on()) {
        /* An unavailable first sample is not a user OFF request. Resume the
         * saved ON session; the normal gauge poll will retry independently. */
        if (!fuel_gauge_latest(&battery)) {
            return false;
        }
        if (battery.present && battery.voltage_v >= 2.5f &&
            battery.voltage_v <= 4.5f) {
            return false;
        }
    }
    /* Persist this new off session as well, so an OTA from USB standby
     * restores standby instead of the old pre-power-loss state. */
    (void)config_store_set_user_powered_on(false);
    return true;
#else
    return false;
#endif
}

static bool key_status_index_for_key(size_t key_index, size_t *status_index)
{
    if (codex_micro_active_key_layout() !=
        CODEX_MICRO_KEY_LAYOUT_MATRIX12) {
        return false;
    }
    for (size_t index = 0; index < KEY_STATUS_LED_COUNT; ++index) {
        if (MATRIX12_AGENT_KEY_INDICES[index] == key_index) {
            if (status_index != NULL) {
                *status_index = index;
            }
            return true;
        }
    }
    return false;
}

static bool can_use_key_status_lighting(void)
{
    const size_t last_key_index =
        MATRIX12_AGENT_KEY_INDICES[KEY_STATUS_LED_COUNT - 1];
    return codex_micro_active_key_layout() ==
               CODEX_MICRO_KEY_LAYOUT_MATRIX12 &&
           board_under_key_rgb_count() > last_key_index;
}

static void cancel_codex_attention_haptic(void)
{
    if (s_codex_attention_haptic.active) {
        (void)board_haptic_stop();
    }
    (void)codex_attention_haptic_model_poll(
        &s_codex_attention_haptic, ticks_to_ms(xTaskGetTickCount()),
        CODEX_ATTENTION_NONE, false);
}

static void pulse_navigation_feedback(board_control_t control)
{
    const bool channel_enabled =
        (control == BOARD_CONTROL_ENCODER_CW || control == BOARD_CONTROL_ENCODER_CCW)
            ? s_feedback.haptic_on_encoder
            : (control >= BOARD_CONTROL_JOYSTICK_UP && control <= BOARD_CONTROL_JOYSTICK_RIGHT)
                  ? s_feedback.haptic_on_joystick : s_feedback.haptic_on_press;
    if (s_feedback.haptic_enabled && channel_enabled && s_pomodoro.state != POMODORO_ALERT &&
        !s_codex_attention_haptic.active) {
        (void)board_haptic_pulse(s_feedback.haptic_strength,
                                 s_feedback.haptic_duration_ms);
    }
}

static bool encoder_rotation_control(board_control_t control)
{
    return control == BOARD_CONTROL_ENCODER_CW ||
           control == BOARD_CONTROL_ENCODER_CCW;
}

static bool joystick_direction_control(board_control_t control)
{
    return control >= BOARD_CONTROL_JOYSTICK_UP &&
           control <= BOARD_CONTROL_JOYSTICK_RIGHT;
}

static bool navigation_feedback_due_for_control(board_control_t control)
{
    if (!encoder_rotation_control(control)) {
        return true;
    }
    return encoder_haptic_model_should_pulse(
        &s_encoder_haptic, ticks_to_ms(xTaskGetTickCount()),
        ENCODER_HAPTIC_REARM_MS);
}

static uint8_t wrap_discrete_selection(uint8_t current, uint8_t count,
                                       int direction)
{
    if (count == 0 || direction == 0) {
        return current;
    }
    return direction > 0 ? (uint8_t)((current + 1u) % count)
                         : (uint8_t)((current + count - 1u) % count);
}

static void pulse_navigation_feedback_for_control(board_control_t control)
{
    if (navigation_feedback_due_for_control(control)) {
        pulse_navigation_feedback(control);
    }
}

static void pulse_save_feedback(void)
{
    if (s_feedback.haptic_enabled && s_feedback.haptic_on_press &&
        s_pomodoro.state != POMODORO_ALERT &&
        !s_codex_attention_haptic.active) {
        (void)board_haptic_pulse(s_feedback.haptic_strength,
                                 SAVE_HAPTIC_DURATION_MS);
    }
}

static void pulse_press_feedback(void)
{
    if (s_feedback.haptic_enabled && s_feedback.haptic_on_press &&
        s_pomodoro.state != POMODORO_ALERT &&
        !s_codex_attention_haptic.active) {
        (void)board_haptic_pulse(s_feedback.haptic_strength,
                                 s_feedback.haptic_duration_ms);
    }
}

#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
static void pulse_physical_button_feedback(const board_event_t *event)
{
    const bool physical_press = event->pressed &&
        (event->control == BOARD_CONTROL_ENCODER_PRESS
            ? event->feedback_only
            : !event->feedback_only &&
              (board_control_key_index(event->control, NULL) ||
               event->control == BOARD_CONTROL_JOYSTICK_PRESS));
    if (!physical_press ||
        s_shutdown_active || s_usb_standby_active || s_battery_splash_active ||
        !s_feedback.haptic_enabled || !s_feedback.haptic_on_press ||
        s_feedback.haptic_strength == 0) {
        return;
    }
    /* Direct key feedback takes priority over an unfinished task reminder. */
    cancel_codex_attention_haptic();
    (void)board_haptic_pulse(s_feedback.haptic_strength,
                             s_feedback.haptic_duration_ms);
}
#endif

static void pulse_control_press_feedback(board_control_t control)
{
    if (encoder_rotation_control(control)) {
        pulse_navigation_feedback_for_control(control);
        return;
    }
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    /* Buttons already vibrate at their physical down edge on Power V2. */
    if (control == BOARD_CONTROL_ENCODER_PRESS ||
        control == BOARD_CONTROL_JOYSTICK_PRESS ||
        joystick_direction_control(control) ||
        board_control_key_index(control, NULL)) {
        return;
    }
#endif
    if (joystick_direction_control(control)) {
        pulse_navigation_feedback_for_control(control);
        return;
    }
    if (control == function_center_control() &&
        s_function_key_press_feedback_sent) {
        return;
    }
    pulse_press_feedback();
}

static void preview_haptic_level(void)
{
    cancel_codex_attention_haptic();
    const uint8_t candidate =
        HAPTIC_LEVELS[s_quick_config.haptic_level_index];
    if (candidate == 0) {
        (void)board_haptic_stop();
    } else if (s_pomodoro.state != POMODORO_ALERT) {
        (void)board_haptic_pulse(candidate, PREVIEW_HAPTIC_DURATION_MS);
    }
}

static bool round_level_slice_active(void)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    return s_quick_config_active &&
           (s_quick_config.item == QUICK_CONFIG_ITEM_HAPTIC ||
            s_quick_config.item == QUICK_CONFIG_ITEM_LIGHTING);
#else
    return false;
#endif
}

static void render_system_select(const char *footer)
{
    const size_t selected_index =
        s_system_selection == CONFIG_PLATFORM_MACOS ? 0 : 1;
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    (void)footer;
    (void)board_display_show_round_icon_choice(
        BOARD_ROUND_PAGE_ICON_MACOS, BOARD_ROUND_PAGE_ICON_WINDOWS,
        selected_index);
#else
    (void)board_display_show_system_select(
        selected_index, footer != NULL ? footer : "PRESS TO CONFIRM");
#endif
}

static void enter_system_select(void)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    enter_os_settings(false);
    return;
#endif
    enter_local_input_ownership();
    s_encoder_pressed = false;
    s_quick_config_active = false;
    s_quick_config_pending_footer = NULL;
    s_system_selection = CONFIG_PLATFORM_MACOS;
    s_system_select_active = true;
    render_system_select(NULL);
}

static void confirm_system_select(void)
{
    const esp_err_t error = config_store_select_platform(s_system_selection);
    if (error != ESP_OK) {
        render_system_select(error == ESP_ERR_INVALID_STATE ? "WAIT THEN RETRY" : "SAVE ERROR");
        return;
    }
    if (config_store_has_pending()) {
        render_system_select("RELEASE CONTROLS");
        return;
    }
    s_system_select_active = false;
    pulse_save_feedback();
    show_feedback(STATUS_FEEDBACK_SUCCESS, "SYSTEM SAVED");
}

static void handle_system_select_event(const board_event_t *event)
{
    if (event->control == BOARD_CONTROL_ENCODER_PRESS ||
        event->control == BOARD_CONTROL_JOYSTICK_PRESS) {
        if (!event->pressed) {
            confirm_system_select();
        }
        return;
    }
    if (!event->pressed) {
        return;
    }
    const bool navigation_feedback_due =
        navigation_feedback_due_for_control(event->control);
    int delta = 0;
    if (event->control == BOARD_CONTROL_ENCODER_CCW ||
        event->control == BOARD_CONTROL_JOYSTICK_UP ||
        event->control == BOARD_CONTROL_JOYSTICK_LEFT) {
        delta = -1;
    } else if (event->control == BOARD_CONTROL_ENCODER_CW ||
               event->control == BOARD_CONTROL_JOYSTICK_DOWN ||
               event->control == BOARD_CONTROL_JOYSTICK_RIGHT) {
        delta = 1;
    } else {
        return;
    }
    config_platform_t selection;
    if (encoder_rotation_control(event->control)) {
        const uint8_t current =
            s_system_selection == CONFIG_PLATFORM_MACOS ? 0u : 1u;
        selection = wrap_discrete_selection(current, 2, delta) == 0u
                        ? CONFIG_PLATFORM_MACOS
                        : CONFIG_PLATFORM_WINDOWS_LINUX;
    } else {
        selection = delta < 0 ? CONFIG_PLATFORM_MACOS
                              : CONFIG_PLATFORM_WINDOWS_LINUX;
    }
    if (selection != s_system_selection) {
        s_system_selection = selection;
        if (navigation_feedback_due) {
            pulse_navigation_feedback(event->control);
        }
        render_system_select(NULL);
    }
}

static uint8_t effective_lighting_brightness(void)
{
    const uint8_t saved = s_feedback.lighting_enabled
                              ? s_feedback.lighting_brightness : 0;
    const uint8_t local =
        lighting_preview_effective(&s_lighting_preview, saved);
    const uint8_t brightness = lighting_host_preview_effective_brightness(
        &s_host_lighting_preview, local);
    return lighting_scale_channel(brightness, s_light_idle.percent);
}

static void render_status_output(
    board_rgb_t output[STATUS_INDICATOR_LED_COUNT])
{
    const uint8_t percent =
        lighting_status_percent(effective_lighting_brightness());
    for (size_t index = 0; index < STATUS_INDICATOR_LED_COUNT; ++index) {
        output[index] = (board_rgb_t) {
            .red = lighting_scale_channel(s_status_pixels[index].red, percent),
            .green = lighting_scale_channel(s_status_pixels[index].green, percent),
            .blue = lighting_scale_channel(s_status_pixels[index].blue, percent),
        };
    }
}

static void render_under_key_output(board_rgb_t *output)
{
    const uint8_t brightness =
        lighting_under_key_percent(effective_lighting_brightness());
    for (size_t index = 0;
         index < ARRAY_COUNT(s_feedback.under_key_rgb); ++index) {
        const config_rgb_t saved = s_feedback.under_key_rgb[index];
        const lighting_output_rgb_t color =
            lighting_host_preview_effective_rgb(
                &s_host_lighting_preview, index,
                (lighting_output_rgb_t) {
                    .red = saved.red,
                    .green = saved.green,
                    .blue = saved.blue,
                });
        output[index] = (board_rgb_t) {
            .red = lighting_scale_channel(color.red, brightness),
            .green = lighting_scale_channel(color.green, brightness),
            .blue = lighting_scale_channel(color.blue, brightness),
        };
    }
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    if (low_battery_breath_active()) {
        const uint8_t red = battery_guard_breath_level(
            ticks_to_ms(xTaskGetTickCount()), LOW_BATTERY_BREATH_MAX_RED);
        for (size_t index = 0;
             index < ARRAY_COUNT(s_feedback.under_key_rgb); ++index) {
            output[index] = (board_rgb_t) {.red = red};
        }
    }
#endif
    if (!s_codex_key_status_active) {
        return;
    }
    const uint8_t status_brightness =
        lighting_status_percent(effective_lighting_brightness());
    for (size_t status_index = 0;
         status_index < KEY_STATUS_LED_COUNT; ++status_index) {
        const size_t key_index = MATRIX12_AGENT_KEY_INDICES[status_index];
        output[key_index] = (board_rgb_t) {
            .red = lighting_scale_channel(s_status_pixels[status_index].red,
                                          status_brightness),
            .green = lighting_scale_channel(s_status_pixels[status_index].green,
                                            status_brightness),
            .blue = lighting_scale_channel(s_status_pixels[status_index].blue,
                                           status_brightness),
        };
    }
}

static void apply_status_output(void)
{
    board_rgb_t status[STATUS_INDICATOR_LED_COUNT];
    render_status_output(status);
    (void)board_apply_status_rgb(status);
}

static void apply_lighting_output(void)
{
    board_rgb_t status[STATUS_INDICATOR_LED_COUNT];
    board_rgb_t under_key[ARRAY_COUNT(s_feedback.under_key_rgb)];
    render_status_output(status);
    render_under_key_output(under_key);
    (void)board_apply_rgb(status, under_key);
}

static bool light_idle_outputs_blocked(void)
{
    return s_shutdown_active || s_usb_standby_active || s_battery_splash_active ||
           s_idle_standby.active;
}

static void apply_light_idle_output(void)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    (void)board_set_display_idle_scale(s_light_idle.percent);
    apply_lighting_output();
#endif
}

static void light_idle_note_activity(void)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    if (light_idle_model_note_activity(&s_light_idle, ticks_to_ms(xTaskGetTickCount())) &&
        !light_idle_outputs_blocked()) {
        apply_light_idle_output();
    }
#endif
}

static void poll_light_idle(void)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    fuel_gauge_sample_t battery;
    /* No VBUS sense on this PCB: a charger (or suspended host) can also qualify. */
    const bool battery_without_usb = fuel_gauge_latest(&battery) &&
                                    !usb_service_is_mounted();
    const bool input_context_ok = s_host_output_state == HOST_OUTPUT_ENABLED ||
        (s_host_output_state == HOST_OUTPUT_LOCAL_UI && s_local_page == LOCAL_PAGE_TIMER_DETAIL);
    const bool eligible = battery_without_usb && !light_idle_outputs_blocked() &&
        input_context_ok && !s_boot_animation_active && !s_under_key_self_test_active &&
        !s_round_feedback_active && !s_mist_connection_visible &&
        !s_system_select_active && !s_quick_config_active &&
        (s_local_page == LOCAL_PAGE_NONE || s_local_page == LOCAL_PAGE_TIMER_DETAIL) &&
        s_pomodoro.state != POMODORO_ALERT && !s_macro.active &&
        !s_host_lighting_preview.active && !s_lighting_preview.active &&
        !diagnostic_capture_model_owns_input(&s_diagnostic_capture) &&
        !board_joystick_calibration_active() && !firmware_update_is_active() &&
        !config_protocol_screen_icon_upload_active() && !config_store_has_pending() &&
        board_keys_neutral();
    if (light_idle_model_poll(&s_light_idle, ticks_to_ms(xTaskGetTickCount()), eligible) &&
        !light_idle_outputs_blocked()) {
        apply_light_idle_output();
    }
#endif
}

static void clear_lighting_preview(void)
{
    if (!lighting_preview_clear(&s_lighting_preview)) {
        return;
    }
    apply_lighting_output();
}

static bool host_lighting_preview_output_busy(void)
{
    return s_shutdown_active || s_usb_standby_active ||
           s_battery_splash_active || s_under_key_self_test_active ||
           (s_quick_config_active &&
            s_quick_config.item == QUICK_CONFIG_ITEM_LIGHTING);
}

static bool host_lighting_preview_output_suppressed(void)
{
    return s_shutdown_active || s_usb_standby_active ||
           s_battery_splash_active;
}

static bool host_config_link_active(void)
{
    if (config_protocol_active_transport() ==
        CONFIG_PROTOCOL_TRANSPORT_BLE) {
        return config_protocol_session_active();
    }
    return usb_service_is_mounted();
}

static void clear_host_lighting_preview(void)
{
    if (!lighting_host_preview_clear(&s_host_lighting_preview)) {
        return;
    }
    if (!host_lighting_preview_output_suppressed()) {
        apply_lighting_output();
    }
}

static void apply_host_lighting_preview(
    const config_protocol_lighting_preview_request_t *request)
{
    if (request == NULL ||
        request->operation != CONFIG_PROTOCOL_LIGHTING_PREVIEW_SET ||
        request->under_key_count != board_under_key_rgb_count() ||
        host_lighting_preview_output_busy() ||
        !host_config_link_active()) {
        return;
    }
    if (s_idle_standby.active) {
        wake_from_idle_standby();
    }
    lighting_output_rgb_t
        under_key[LIGHTING_HOST_PREVIEW_MAX_UNDER_KEY_RGB];
    for (size_t index = 0; index < request->under_key_count; ++index) {
        under_key[index] = (lighting_output_rgb_t) {
            .red = request->under_key[index].red,
            .green = request->under_key[index].green,
            .blue = request->under_key[index].blue,
        };
    }
    const uint32_t now_ms = ticks_to_ms(xTaskGetTickCount());
    const bool changed = lighting_host_preview_set(
        &s_host_lighting_preview, request->enabled, request->brightness,
        under_key, request->under_key_count, now_ms,
        HOST_LIGHTING_PREVIEW_LEASE_MS);
    idle_standby_model_note_activity(&s_idle_standby, now_ms);
    if (changed) {
        apply_lighting_output();
    }
}

static void poll_host_lighting_preview(void)
{
    config_protocol_lighting_preview_request_t request;
    if (config_protocol_take_lighting_preview_request(&request)) {
        if (request.operation == CONFIG_PROTOCOL_LIGHTING_PREVIEW_CLEAR) {
            clear_host_lighting_preview();
        } else {
            apply_host_lighting_preview(&request);
        }
    }
    if (!s_host_lighting_preview.active) {
        return;
    }
    if (!host_config_link_active() || codex_micro_mode() != s_codex_mode) {
        clear_host_lighting_preview();
        return;
    }
    if (lighting_host_preview_expire(
            &s_host_lighting_preview,
            ticks_to_ms(xTaskGetTickCount())) &&
        !host_lighting_preview_output_suppressed()) {
        apply_lighting_output();
    }
}

static void preview_lighting_level(void)
{
    const uint8_t candidate =
        LIGHTING_LEVELS[s_quick_config.lighting_level_index];
    const uint8_t saved = s_feedback.lighting_enabled
                              ? s_feedback.lighting_brightness : 0;
    if (!lighting_preview_set_candidate(&s_lighting_preview,
                                        candidate,
                                        saved)) {
        return;
    }
    apply_lighting_output();
}

static void show_status(const char *hint)
{
    if (s_idle_standby.active || s_usb_standby_active) {
        return;
    }
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    if (s_round_feedback_active) {
        return;
    }
    if (diagnostic_capture_model_owns_input(&s_diagnostic_capture) ||
        board_joystick_calibration_active()) {
        return;
    }
    s_mist_connection_visible = false;
    s_mist_read_index = 0;
#endif
    const bool mounted = usb_service_is_mounted();
    const bool ble_connected = codex_micro_ble_connected();
    const codex_micro_mode_t mode = codex_micro_mode();
    const char *effective_hint = hint;
    if (effective_hint == NULL && board_joystick_suspect()) {
        effective_hint = "CALIBRATE JOYSTICK";
    }
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    const int battery_percent = current_battery_percent(NULL);
    const board_round_mode_t round_mode =
        mode == CODEX_MICRO_MODE_CODEX ? BOARD_ROUND_MODE_CODEX
        : mode == CODEX_MICRO_MODE_CLAUDE_CODE
            ? BOARD_ROUND_MODE_CLAUDE_CODE
            : BOARD_ROUND_MODE_KEY;
    const board_round_link_t round_link = mounted ? BOARD_ROUND_LINK_USB
        : ble_connected ? BOARD_ROUND_LINK_BLE
                        : BOARD_ROUND_LINK_DISCONNECTED;
    (void)effective_hint;
    if (boot_guard_safe_mode()) {
        /* Stability item 6, first batch: fixed text until the screen is
         * designed with the UI owner. Restarting tries a normal boot. */
        (void)round_mode;
        (void)round_link;
        (void)battery_percent;
        /* Interim product text: no "safe
         * mode" wording for users; replaced when the final copy arrives.
         * Each row fits the round screen (test_safe_mode_screen_text). */
        (void)board_display_show_local("REPAIR", "PLUG USB", "OPEN APP",
                                       "TO FIX", 0xFD20);
        return;
    }
    (void)board_display_show_round_home(round_mode, round_link,
                                        battery_percent,
                                        board_joystick_suspect());
    return;
#endif
    uint8_t profile_id = 0;
    char profile[CONFIG_STORE_PROFILE_NAME_BYTES + 1] = "General";
    (void)config_store_get_active_profile(&profile_id, profile, sizeof(profile));
    (void)profile_id;
    const home_status_model_t status =
        home_status_model_resolve(mounted, ble_connected, mode);
    uint32_t seconds = 0;
    if (s_pomodoro.state == POMODORO_RUNNING ||
        s_pomodoro.state == POMODORO_PAUSED) {
        seconds =
            (pomodoro_model_remaining_ms(&s_pomodoro,
                                         ticks_to_ms(xTaskGetTickCount())) +
             999U) / 1000U;
    }
    char resolved_hint[CONFIG_STORE_SHORT_NAME_BYTES + 1];
    pomodoro_display_format_home_hint(
        resolved_hint, sizeof(resolved_hint), s_pomodoro.state, seconds,
        s_timer_done_hint, effective_hint,
        status.output_connected ? "READY" : "CHECK CABLE");
    (void)board_display_show_status(profile, status.output_label,
                                    status.mode_label, resolved_hint,
                                    status.output_connected,
                                    mode == CODEX_MICRO_MODE_CODEX);
}

#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
static void start_round_feedback(void)
{
    s_round_feedback_active = true;
    s_round_feedback_expires_at =
        xTaskGetTickCount() + pdMS_TO_TICKS(ROUND_FEEDBACK_DURATION_MS);
}
#endif

static void show_feedback(status_feedback_t feedback,
                          const char *legacy_hint)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    static const board_round_page_icon_t icons[] = {
        [STATUS_FEEDBACK_SUCCESS] = BOARD_ROUND_PAGE_ICON_CONFIRM,
        [STATUS_FEEDBACK_ERROR] = BOARD_ROUND_PAGE_ICON_ERROR,
        [STATUS_FEEDBACK_WARNING] = BOARD_ROUND_PAGE_ICON_WARNING,
        [STATUS_FEEDBACK_PLAY] = BOARD_ROUND_PAGE_ICON_PLAY,
        [STATUS_FEEDBACK_PAUSE] = BOARD_ROUND_PAGE_ICON_PAUSE,
        [STATUS_FEEDBACK_CANCEL] = BOARD_ROUND_PAGE_ICON_CANCEL,
    };
    const board_round_page_icon_t icon = icons[feedback];
    (void)legacy_hint;
    s_mist_connection_visible = false;
    start_round_feedback();
    (void)board_display_show_round_notice(icon);
#else
    (void)feedback;
    show_status(legacy_hint);
#endif
}

static void enter_initial_page(void)
{
    if (s_usb_charge_session_at_boot) {
        start_usb_battery_splash();
        return;
    }
    const bool product_target = board_is_product_target();
    const bool platform_selected =
        config_store_get_platform() != CONFIG_PLATFORM_UNSELECTED;
    /* The repair state runs the built-in defaults, which have no OS yet.
     * A choice made there cannot be saved, so it would only hide the repair
     * instruction. */
    if (product_target && !platform_selected && !boot_guard_safe_mode()) {
        enter_system_select();
    } else if (board_display_boot_should_start(
                   product_target, s_firmware_error, platform_selected)) {
        start_boot_animation();
    } else {
        show_status(NULL);
    }
}

static void finish_battery_splash(void)
{
    if (!s_battery_splash_active) {
        return;
    }
    s_battery_splash_active = false;
    /* Only the boot splash starts a standby period; a splash shown while
     * already parked (USB plug-in, power button on a low cell) does not. */
    enter_usb_standby(USB_STANDBY_ENTRY_BOOT);
}

static void set_usb_standby_outputs(void)
{
    cancel_codex_attention_haptic();
    const board_rgb_t status[STATUS_INDICATOR_LED_COUNT] = {0};
    const board_rgb_t under_key[BOARD_MAX_UNDER_KEY_RGB_COUNT] = {0};
    (void)board_haptic_stop();
    (void)board_apply_rgb(status, under_key);
    (void)board_set_display_config(0, s_feedback.display_rotation);
}

static void start_usb_battery_splash(void)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    board_set_power_button_wake_mode(true);
    s_mist_connection_pending = 0;
    s_mist_connection_visible = false;
    s_usb_standby_active = false;
    s_boot_animation_active = false;
    s_round_feedback_active = false;
    s_battery_splash_active = true;
    const TickType_t now = xTaskGetTickCount();
    s_battery_splash_started_at = now;
    s_battery_splash_valid_at = 0;
    s_battery_splash_next_retry_at =
        now + pdMS_TO_TICKS(BATTERY_SPLASH_RETRY_MS);
    const int battery_percent =
        current_battery_percent(&s_battery_sample_time_ms);
    s_battery_splash_has_valid_sample = battery_percent >= 0;
    if (s_battery_splash_has_valid_sample) {
        s_battery_splash_valid_at = now;
    } else {
        fuel_gauge_request_immediate_poll();
    }
    (void)board_set_display_config(s_feedback.display_brightness,
                                   s_feedback.display_rotation);
    render_mist_battery();
#endif
}

static void sync_ble_to_usb_data_link(void)
{
    /* The battery splash belongs to the OFF state (USB charge session): BLE
     * stays as standby left it. */
    if (s_shutdown_active || s_usb_standby_active ||
        s_idle_standby.active || s_battery_splash_active) {
        return;
    }
    /* A charger is not a USB host: keep BLE available without a live link.
     * The model debounces the raw link so a brief bus suspend, a host
     * re-enumeration or the enumeration window at boot does not drop and
     * re-advertise BLE. */
    const bool usb_owns_link = usb_link_model_update(
        &s_usb_link, ticks_to_ms(xTaskGetTickCount()),
        (usb_link_input_t)usb_service_link_state());
    codex_micro_set_ble_suspended_for_source(
        usb_owns_link, CODEX_MICRO_BLE_DISCONNECT_USB);
}

#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
static uint32_t usb_standby_now_ms(void)
{
    /* Same clock as GET_STATUS usb_standby.uptime_ms. */
    return (uint32_t)(esp_timer_get_time() / 1000);
}
#endif

static void enter_usb_standby(usb_standby_entry_reason_t reason)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    /* With USB power the MCU survives the OFF pulse; restart on the next
     * wake so OFF/ON clears runtime state (an exhausted heap stayed through
     * repeated OFF/ON on a returned unit and on 586EC). */
    s_restart_on_standby_wake = reason == USB_STANDBY_ENTRY_POWER_BUTTON;
    if (usb_standby_trace_enter(&s_usb_standby_trace, reason,
                                usb_standby_now_ms())) {
        ESP_LOGI(TAG, "USB standby #%u entered: %s",
                 (unsigned)s_usb_standby_trace.entries,
                 usb_standby_entry_reason_name(reason));
        config_protocol_set_usb_standby_trace(&s_usb_standby_trace);
    }
    codex_micro_set_ble_suspended_for_source(
        true, CODEX_MICRO_BLE_DISCONNECT_STANDBY);
    board_set_power_button_wake_mode(true);
    s_battery_splash_active = false;
    s_usb_standby_active = true;
    mist_cancel_motion();
    s_mist_connection_visible = false;
    s_mist_connection_pending = 0;
    config_protocol_set_lighting_preview_busy(true);
    clear_host_lighting_preview();
    s_boot_animation_active = false;
    s_round_feedback_active = false;
    s_under_key_self_test_active = false;
    reset_mode_sensitive_input_state();
    usb_service_set_standard_enabled(false);
    codex_micro_set_input_suppressed(true);
    s_usb_mounted = usb_service_is_mounted();
    set_usb_standby_outputs();
#else
    (void)reason;
#endif
}

#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
static void guard_sample_from_gauge(battery_guard_sample_t *sample)
{
    fuel_gauge_sample_t raw;
    const bool valid = fuel_gauge_latest(&raw);
    /* Same clock as cw2015.c's last_update_time_ms. */
    const int64_t age_ms = esp_timer_get_time() / 1000 - raw.last_update_time_ms;
    float millivolts = raw.voltage_v * 1000.0f + 0.5f;
    if (millivolts < 0.0f) millivolts = 0.0f;
    if (millivolts > 65535.0f) millivolts = 65535.0f;
    float percent = raw.soc_percent + 0.5f;
    if (percent < 0.0f) percent = 0.0f;
    if (percent > 100.0f) percent = 100.0f;
    *sample = (battery_guard_sample_t) {
        /* A stale cached value must not keep a critical hold running. */
        .valid = valid && raw.last_update_time_ms > 0 &&
                 age_ms >= 0 && age_ms <= LOW_BATTERY_SAMPLE_MAX_AGE_MS,
        .voltage_mv = (uint16_t)millivolts,
        .soc_percent = (uint8_t)percent,
    };
}
#endif

#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
/* Locked standby holds while the cell reads below the resume threshold. A
 * failed gauge never holds: it must not trap the device in standby. */
static bool low_battery_standby_holds(void)
{
    if (!s_low_battery_locked_standby) {
        return false;
    }
    battery_guard_sample_t sample;
    guard_sample_from_gauge(&sample);
    return battery_guard_sample_usable(&sample) &&
           sample.voltage_mv < BATTERY_GUARD_RESUME_MV;
}
#endif

static void poll_usb_standby(void)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    if (s_low_battery_shutdown) {
        /* The power-off pulse was survived: external power holds the MCU.
         * Park in standby with the saved ON intent until the cell recovers. */
        s_low_battery_shutdown = false;
        s_low_battery_locked_standby = true;
        battery_guard_model_init(&s_battery_guard);
        config_protocol_set_power_guard_state("locked_standby");
    }
    if (s_low_battery_locked_standby) {
        battery_guard_sample_t sample;
        guard_sample_from_gauge(&sample);
        if (battery_guard_model_resume_ready(&s_battery_guard,
                                             ticks_to_ms(xTaskGetTickCount()),
                                             &sample)) {
            ESP_LOGI(TAG, "cell recovered to %u mV: resuming saved ON session",
                     (unsigned)sample.voltage_mv);
            (void)config_store_set_low_battery_lock(false);
            wake_from_usb_standby(USB_STANDBY_WAKE_BATTERY_RECOVERED);
            return;
        }
    }
    const bool mounted = usb_service_is_mounted();
    if (mounted && !s_usb_mounted) {
        s_usb_mounted = true;
        start_usb_battery_splash();
        return;
    }
    s_usb_mounted = mounted;
#endif
}

static void wake_from_usb_standby(usb_standby_wake_reason_t reason)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    bool restart_for_name = false;
#if CONFIG_CODEX_MICRO_BLE_ENABLED
    char saved_name[CODEX_MICRO_BLE_DEVICE_NAME_BYTES];
    const esp_err_t name_result =
        config_store_get_ble_name(saved_name, sizeof(saved_name));
    if (name_result != ESP_OK) {
        ESP_LOGW(TAG, "BLE name unavailable on USB wake: %s",
                 esp_err_to_name(name_result));
    }
    restart_for_name = name_result == ESP_OK &&
        strcmp(saved_name, codex_micro_ble_device_name()) != 0 &&
        !config_protocol_restart_busy();
#endif
    /* Config migrations run only at boot and this wake is not one: restart
     * once so an OFF/ON cycle applies them, as it does a saved BLE name. */
    const bool restart_for_migration = !restart_for_name &&
        !config_protocol_restart_busy() && config_store_boot_migration_pending();
    const bool restart_for_power_cycle = !restart_for_name &&
        !restart_for_migration && s_restart_on_standby_wake &&
        !config_protocol_restart_busy();
    const esp_err_t power_result = config_store_set_user_powered_on(true);
    if (power_result != ESP_OK) {
        ESP_LOGW(TAG, "cannot save ON intent on USB wake: %s",
                 esp_err_to_name(power_result));
    }
    /* Found on 68DE8 (11-11): in safe mode (and any read-only storage) the
     * ON intent cannot be saved, so OFF/ON never restarted and the device
     * could not leave safe mode. The OFF intent was not saved either, so the
     * stored intent is still ON and the restart comes back powered on. */
    config_store_status_t store_status;
    config_store_get_status(&store_status);
    const bool intent_saved_or_read_only =
        power_result == ESP_OK || store_status.storage_degraded;
    if ((restart_for_name || restart_for_migration || restart_for_power_cycle) &&
        intent_saved_or_read_only) {
        /* A restart is not a wake: the reboot shows in FW_STATUS. */
        ESP_LOGI(TAG, "USB standby wake (%s) restarts to apply %s",
                 usb_standby_wake_reason_name(reason),
                 restart_for_name ? "the BLE name"
                 : restart_for_migration ? "config migrations"
                                         : "a clean start after power-off");
        s_restart_on_standby_wake = false;
        action_engine_request_restart();
        return;
    }
    if (usb_standby_trace_wake(&s_usb_standby_trace, reason,
                               usb_standby_now_ms())) {
        ESP_LOGI(TAG, "USB standby wake #%u: %s",
                 (unsigned)s_usb_standby_trace.wakes,
                 usb_standby_wake_reason_name(reason));
        config_protocol_set_usb_standby_trace(&s_usb_standby_trace);
    }
    board_set_power_button_wake_mode(false);
    s_usb_standby_active = false;
    s_battery_splash_active = false;
    usb_service_set_standard_enabled(true);
    codex_micro_set_input_suppressed(false);
    sync_ble_to_usb_data_link();
    apply_feedback_config();
    s_status_frame_valid = false;
    update_status_indicator(true);
    start_boot_animation();
    s_mist_connection_pending = usb_service_is_mounted() ? 1u
        : codex_micro_ble_connected() ? 2u : 0u;
#else
    (void)reason;
#endif
}

static void enter_idle_standby(void)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    macro_cancel();
    clear_pending_gestures(false);
    enter_local_input_ownership();
    usb_service_release_all();
    codex_micro_set_ble_suspended_for_source(
        true, CODEX_MICRO_BLE_DISCONNECT_STANDBY);
    s_idle_standby_wake_wait = false;
    s_boot_animation_active = false;
    s_round_feedback_active = false;
    s_under_key_self_test_active = false;
    set_usb_standby_outputs();
#endif
}

static void wake_from_idle_standby(void)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    idle_standby_model_wake(&s_idle_standby,
                            ticks_to_ms(xTaskGetTickCount()));
    s_low_battery_notice_due = s_battery_guard.level != BATTERY_GUARD_NORMAL;
    s_idle_standby_wake_wait = true;
    sync_ble_to_usb_data_link();
    leave_local_input_ownership();
    apply_feedback_config();
    s_status_frame_valid = false;
    update_status_indicator(true);
    show_status(NULL);
#endif
}

static bool idle_standby_entry_blocked(void)
{
    return s_shutdown_active || s_usb_standby_active ||
           s_battery_splash_active || s_boot_animation_active ||
           s_round_feedback_active || s_system_select_active ||
           s_quick_config_active || s_local_page != LOCAL_PAGE_NONE ||
           s_host_lighting_preview.active ||
           s_host_output_state != HOST_OUTPUT_ENABLED || s_macro.active ||
           s_pomodoro.state == POMODORO_RUNNING ||
           s_pomodoro.state == POMODORO_PAUSED ||
           s_pomodoro.state == POMODORO_ALERT;
}

static void poll_idle_standby(void)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    if (s_idle_standby_wake_wait &&
        s_host_output_state == HOST_OUTPUT_ENABLED) {
        s_idle_standby_wake_wait = false;
    }
    if (idle_standby_model_poll(&s_idle_standby,
                                ticks_to_ms(xTaskGetTickCount()),
                                idle_standby_entry_blocked())) {
        enter_idle_standby();
    }
#endif
}

static void poll_round_feedback(void)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    if (!s_round_feedback_active ||
        (int32_t)(xTaskGetTickCount() - s_round_feedback_expires_at) < 0) {
        return;
    }
    s_round_feedback_active = false;
    if (s_system_select_active) {
        render_system_select(NULL);
        return;
    }
    if (s_local_page != LOCAL_PAGE_NONE) {
        render_local_page();
        return;
    }
    if (s_quick_config_active || s_local_page != LOCAL_PAGE_NONE ||
        s_pomodoro.state == POMODORO_ALERT || s_boot_animation_active ||
        s_battery_splash_active || s_shutdown_active) {
        return;
    }
    show_status(NULL);
#endif
}

static void poll_battery_status(void)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    if (s_usb_standby_active) {
        return;
    }
    if (s_battery_splash_active) {
        const TickType_t now = xTaskGetTickCount();
        int64_t sample_time_ms = 0;
        const int battery_percent = current_battery_percent(&sample_time_ms);
        if (sample_time_ms != s_battery_sample_time_ms) {
            s_battery_sample_time_ms = sample_time_ms;
            render_mist_battery();
            if (battery_percent >= 0 &&
                !s_battery_splash_has_valid_sample) {
                s_battery_splash_has_valid_sample = true;
                s_battery_splash_valid_at = now;
            }
        }
        if (!s_battery_splash_has_valid_sample &&
            (int32_t)(now - s_battery_splash_next_retry_at) >= 0 &&
            now - s_battery_splash_started_at <
                pdMS_TO_TICKS(BATTERY_SPLASH_MAX_WAIT_MS)) {
            fuel_gauge_request_immediate_poll();
            s_battery_splash_next_retry_at =
                now + pdMS_TO_TICKS(BATTERY_SPLASH_RETRY_MS);
        }
        const bool valid_page_finished =
            s_battery_splash_has_valid_sample &&
            now - s_battery_splash_valid_at >=
                pdMS_TO_TICKS(BATTERY_SPLASH_VALID_DURATION_MS);
        const bool invalid_wait_finished =
            !s_battery_splash_has_valid_sample &&
            now - s_battery_splash_started_at >=
                pdMS_TO_TICKS(BATTERY_SPLASH_MAX_WAIT_MS);
        if (valid_page_finished || invalid_wait_finished) {
            finish_battery_splash();
        }
        return;
    }
    fuel_gauge_sample_t battery;
    (void)fuel_gauge_latest(&battery);
    if (battery.last_update_time_ms == s_battery_sample_time_ms) {
        return;
    }
    s_battery_sample_time_ms = battery.last_update_time_ms;
    if (s_local_page == LOCAL_PAGE_BATTERY && !s_round_feedback_active) {
        render_mist_battery();
    }
    /* Home contains only the mode symbol; a fuel-gauge sample cannot dirty it. */
#endif
}

static uint32_t ticks_to_ms(TickType_t ticks)
{
    return (uint32_t)(((uint64_t)ticks * 1000u) / configTICK_RATE_HZ);
}

#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
static void mist_cancel_motion(void)
{
    board_display_finish_mist_motion();
}

static void render_mist_battery(void)
{
    if (board_joystick_calibration_active() ||
        diagnostic_capture_model_owns_input(&s_diagnostic_capture)) return;
    const uint32_t now = ticks_to_ms(xTaskGetTickCount());
    const board_mist_view_t view = {
        .page = BOARD_MIST_BATTERY,
        .a = current_battery_percent(NULL),
        /* The local read-only page is static; retain the USB splash motion. */
        .b = s_battery_splash_active ? 1 : 0,
        .c = s_battery_splash_active && s_low_battery_locked_standby
                 ? BOARD_MIST_BATTERY_EMPTY : BOARD_MIST_BATTERY_PERCENT,
        .phase = 1,
    };
    (void)board_display_show_mist(&view);
    s_mist_last_frame_ms = now;
}

static void render_mist_connection(void)
{
    if (board_joystick_calibration_active() ||
        diagnostic_capture_model_owns_input(&s_diagnostic_capture)) return;
    char profile[CONFIG_STORE_PROFILE_NAME_BYTES + 1] = "General";
    uint8_t profile_id = 0;
    (void)config_store_get_active_profile(&profile_id, profile, sizeof(profile));
    const home_status_model_t status = home_status_model_resolve(
        usb_service_is_mounted(), codex_micro_ble_connected(), codex_micro_mode());
    const bool connected = s_mist_connection_output == 1
        ? usb_service_is_mounted() : s_mist_connection_output == 2
        ? codex_micro_ble_connected() : false;
    const board_mist_view_t view = {
        .page = BOARD_MIST_CONNECTION,
        .a = s_mist_connection_output, .b = connected ? 2 : 0,
        .phase = 1,
        .title = profile, .primary = status.output_label,
        .secondary = status.mode_label,
        .footer = connected ? "READY" : "CHECK CABLE",
        .read_index = s_mist_read_index,
    };
    (void)board_display_show_mist(&view);
}

static void poll_mist_connection_notice(void)
{
    const bool usb = usb_service_is_mounted();
    const bool ble = codex_micro_ble_connected();
    if ((s_mist_connection_pending == 1 && !usb) ||
        (s_mist_connection_pending == 2 && !ble)) {
        s_mist_connection_pending = 0;
    }
    /* Charge standby and input diagnostics must never acquire a success page. */
    if (s_shutdown_active || s_usb_standby_active || s_battery_splash_active ||
        diagnostic_capture_model_owns_input(&s_diagnostic_capture) ||
        board_joystick_calibration_active()) {
        s_mist_connection_pending = 0;
        s_mist_connection_visible = false;
        return;
    }
    const uint32_t now = ticks_to_ms(xTaskGetTickCount());
    if (s_mist_connection_visible &&
        (now - s_mist_connection_started_ms >= MIST_CONNECTION_NOTICE_MS ||
         (s_mist_connection_output == 1 ? !usb : !ble))) {
        s_mist_connection_visible = false;
        mist_cancel_motion();
        show_status(NULL);
    }
    /* One latest, still-live connection waits for the home screen. No page stack
     * or input ownership is changed, so a setting draft/timer stays untouched. */
    if (s_mist_connection_pending == 0 || s_idle_standby.active ||
        s_system_select_active || s_quick_config_active ||
        s_local_page != LOCAL_PAGE_NONE || s_pomodoro.state == POMODORO_ALERT ||
        s_boot_animation_active || s_round_feedback_active) return;
    s_mist_connection_output = s_mist_connection_pending;
    s_mist_connection_pending = 0;
    s_mist_connection_visible = true;
    s_mist_connection_started_ms = now;
    s_mist_read_index = 0;
    render_mist_connection();
}

static void render_mist_timer(void)
{
    if (board_joystick_calibration_active() ||
        diagnostic_capture_model_owns_input(&s_diagnostic_capture)) return;
    const uint32_t now = ticks_to_ms(xTaskGetTickCount());
    const uint32_t remaining = pomodoro_model_remaining_ms(&s_pomodoro, now);
    const uint32_t total_ms = s_pomodoro.total_seconds * 1000U;
    const uint32_t elapsed = total_ms >= remaining ? total_ms - remaining : 0;
    const bool done = s_pomodoro.state == POMODORO_ALERT ||
                      s_pomodoro.state == POMODORO_DONE;
    const board_mist_view_t view = {
        .page = BOARD_MIST_TIMER, .a = s_pomodoro.minutes,
        .d = s_local_page == LOCAL_PAGE_TIMER_CANCEL
            ? s_cancel_yes : s_local_confirm_selected,
        .phase = done ? 1.0f : (float)(elapsed % 1000U) / 1000.0f,
        .timer_state = s_local_page == LOCAL_PAGE_TIMER_SETUP ? BOARD_MIST_TIMER_SETUP
            : done ? BOARD_MIST_TIMER_DONE
            : s_local_page == LOCAL_PAGE_TIMER_CANCEL ? BOARD_MIST_TIMER_CANCEL
            : s_pomodoro.state == POMODORO_PAUSED ? BOARD_MIST_TIMER_PAUSED
            : BOARD_MIST_TIMER_RUNNING,
        .total_seconds = s_pomodoro.total_seconds,
        .remaining_seconds = (remaining + 999U) / 1000U,
        .show_exact_time = s_mist_peek_active &&
            (int32_t)(s_mist_peek_until_ms - now) > 0,
        .confirm_selected = s_local_page == LOCAL_PAGE_TIMER_CANCEL
            ? s_cancel_yes : s_local_confirm_selected,
    };
    (void)board_display_show_mist(&view);
    s_mist_last_frame_ms = now;
}

static void render_mist_carousel(const uint8_t *items, uint8_t count, uint8_t selected)
{
    const uint32_t now = ticks_to_ms(xTaskGetTickCount());
    board_mist_view_t view = {
        .page = BOARD_MIST_CAROUSEL,
        .a = items[selected], .selected_index = selected,
        .b = s_local_page == LOCAL_PAGE_FUNCTION ? s_mist_navigation_delta : 0,
        .position = selected,
        .phase = 1,
        .item_count = count,
        .carousel_label = s_local_page == LOCAL_PAGE_FUNCTION,
    };
    memcpy(view.items, items, count);
    (void)board_display_show_mist(&view);
    s_mist_last_frame_ms = now;
}

static void poll_mist_display(void)
{
    poll_mist_connection_notice();
    if (s_usb_standby_active || s_idle_standby.active || s_shutdown_pulsed ||
        diagnostic_capture_model_owns_input(&s_diagnostic_capture) ||
        board_joystick_calibration_active()) {
        mist_cancel_motion();
        return;
    }
    const uint32_t now = ticks_to_ms(xTaskGetTickCount());
    if (now - s_mist_last_frame_ms < STATUS_FRAME_INTERVAL_MS) return;
    if (s_shutdown_active) {
        (void)board_display_poll_mist(now);
        s_mist_last_frame_ms = now;
        return;
    }
    const bool peek_expired = s_mist_peek_active &&
        (int32_t)(now - s_mist_peek_until_ms) >= 0;
    if (peek_expired) s_mist_peek_active = false;
    if (s_local_page == LOCAL_PAGE_TIMER_DETAIL && !s_round_feedback_active &&
        (s_pomodoro.state == POMODORO_RUNNING || peek_expired)) {
        render_mist_timer();
    } else if (!s_boot_animation_active) {
        /* The renderer owns visual transitions; it never changes input state. */
        (void)board_display_poll_mist(now);
    }
    s_mist_last_frame_ms = now;
}
#endif

static void start_boot_animation(void)
{
    s_boot_animation_started_at = xTaskGetTickCount();
    s_boot_animation_last_frame_at = s_boot_animation_started_at;
    s_boot_animation_last_density = 0;
    s_boot_animation_complete = false;
    s_boot_animation_active = true;
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    mist_cancel_motion();
    s_mist_connection_visible = false;
    const board_mist_view_t view = {.page = BOARD_MIST_BOOT, .phase = 0};
    (void)board_display_show_mist(&view);
#endif
}

static void cancel_boot_animation(void)
{
    if (!s_boot_animation_active) {
        return;
    }
    s_boot_animation_active = false;
    s_boot_animation_complete = false;
    show_status(NULL);
}

static void poll_boot_animation(void)
{
    if (!s_boot_animation_active) {
        return;
    }
    if (s_boot_animation_complete) {
        return;
    }
    const TickType_t now = xTaskGetTickCount();
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    if (s_idle_standby.active || s_usb_standby_active ||
        diagnostic_capture_model_owns_input(&s_diagnostic_capture) ||
        board_joystick_calibration_active() || s_local_page != LOCAL_PAGE_NONE ||
        s_quick_config_active || s_system_select_active) {
        s_boot_animation_active = false;
        return;
    }
    if (now - s_boot_animation_last_frame_at <
        pdMS_TO_TICKS(MIST_GLYPH_BOOT_FRAME_MS)) return;
    const uint32_t elapsed = ticks_to_ms(now - s_boot_animation_started_at);
    if (elapsed >= MIST_GLYPH_BOOT_TOTAL_MS) {
        s_boot_animation_complete = true;
        const board_mist_view_t view = {
            .page = BOARD_MIST_BOOT,
            .phase = (float)MIST_GLYPH_BOOT_LOGO_MS /
                     MIST_GLYPH_BOOT_TOTAL_MS,
        };
        (void)board_display_show_mist(&view);
        return;
    }
    const board_mist_view_t view = {
        .page = BOARD_MIST_BOOT,
        .phase = (float)elapsed / MIST_GLYPH_BOOT_TOTAL_MS,
    };
    (void)board_display_show_mist(&view);
    s_boot_animation_last_frame_at = now;
    return;
#endif
    if (s_boot_animation_last_density == 0) {
        s_boot_animation_started_at = now;
    }
    const uint32_t elapsed_ms =
        ticks_to_ms(now - s_boot_animation_started_at);
    if (elapsed_ms >= BOARD_DISPLAY_BOOT_TOTAL_MS) {
        s_boot_animation_active = false;
        show_status(NULL);
        return;
    }
    if (s_boot_animation_last_density != 0 &&
        now - s_boot_animation_last_frame_at <
        pdMS_TO_TICKS(BOARD_DISPLAY_BOOT_FRAME_MS)) {
        return;
    }
    s_boot_animation_last_frame_at = now;
    const uint8_t density = board_display_boot_density(elapsed_ms);
    if (density == s_boot_animation_last_density) {
        return;
    }
    s_boot_animation_last_density = density;
    if (board_display_show_boot_logo(density) != ESP_OK) {
        s_boot_animation_active = false;
        show_status(NULL);
    }
}

static bool reset_reason_is_error(esp_reset_reason_t reason)
{
    switch (reason) {
    case ESP_RST_PANIC:
    case ESP_RST_INT_WDT:
    case ESP_RST_TASK_WDT:
    case ESP_RST_WDT:
    case ESP_RST_BROWNOUT:
        return true;
    default:
        return false;
    }
}

static void update_status_indicator(bool force)
{
    const bool has_dedicated_status = board_status_rgb_count() > 0;
    const bool can_use_key_status =
        !has_dedicated_status && can_use_key_status_lighting();
    if (!has_dedicated_status && !can_use_key_status) {
        return;
    }
    const TickType_t now = xTaskGetTickCount();
    const TickType_t elapsed = now - s_status_started_at;
    if (!force && s_status_frame_valid &&
        now - s_status_last_frame_at <
            pdMS_TO_TICKS(STATUS_FRAME_INTERVAL_MS)) {
        return;
    }
    board_rgb_t codex_status[STATUS_INDICATOR_LED_COUNT];
    if (codex_micro_status_rgb(codex_status)) {
        const bool key_status_changed =
            can_use_key_status && !s_codex_key_status_active;
        s_codex_key_status_active = can_use_key_status;
        if (force || key_status_changed || !s_status_frame_valid ||
            memcmp(s_status_pixels, codex_status, sizeof(codex_status)) != 0) {
            memcpy(s_status_pixels, codex_status, sizeof(s_status_pixels));
            if (can_use_key_status) {
                apply_lighting_output();
            } else {
                apply_status_output();
            }
        }
        s_status_last_frame_at = now;
        s_status_frame_valid = true;
        return;
    }
    if (s_codex_key_status_active) {
        s_codex_key_status_active = false;
        apply_lighting_output();
    }
    if (!has_dedicated_status) {
        s_status_last_frame_at = now;
        s_status_frame_valid = false;
        return;
    }
    config_store_status_t config_status = {0};
    config_store_get_status(&config_status);
    const status_indicator_state_t state = status_indicator_select(
        elapsed < pdMS_TO_TICKS(STATUS_BOOT_DURATION_MS),
        usb_service_is_mounted(), usb_service_config_session_active(),
        s_firmware_error || config_status.activation_failed);
    status_indicator_rgb_t rendered[STATUS_INDICATOR_LED_COUNT];
    status_indicator_render(state, ticks_to_ms(elapsed), rendered);
    for (size_t index = 0; index < STATUS_INDICATOR_LED_COUNT; ++index) {
        s_status_pixels[index] = (board_rgb_t) {
            .red = rendered[index].red,
            .green = rendered[index].green,
            .blue = rendered[index].blue,
        };
    }
    apply_status_output();
    s_status_last_frame_at = now;
    s_status_frame_valid = true;
}

static void apply_feedback_config(void)
{
    if (!config_store_get_feedback(&s_feedback)) {
        return;
    }
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    light_idle_model_init(&s_light_idle, ticks_to_ms(xTaskGetTickCount()));
    (void)board_set_display_idle_scale(100);
#endif
    if (!s_feedback.haptic_enabled || !s_feedback.haptic_on_task) {
        cancel_codex_attention_haptic();
        if (!s_feedback.haptic_enabled || s_pomodoro.state == POMODORO_ALERT) {
            (void)board_haptic_stop();
        }
    }
    /* Preserve control feedback's existing cap when notifications run longer.
     * This is RAM-only; the saved configuration remains unchanged. */
    if (s_feedback.haptic_duration_ms > CONTROL_HAPTIC_MAX_DURATION_MS) {
        s_feedback.haptic_duration_ms = CONTROL_HAPTIC_MAX_DURATION_MS;
    }
    /*
     * Status colors remain state-machine owned; the LIGHTING value is a master
     * brightness for both physical LED chains.
     */
    apply_lighting_output();
    (void)board_set_display_config(s_feedback.display_brightness, s_feedback.display_rotation);
    const int center_x = s_feedback.joystick_calibrated ? s_feedback.joystick_center_x : -1;
    const int center_y = s_feedback.joystick_calibrated ? s_feedback.joystick_center_y : -1;
    const board_joystick_config_t joystick = {
        .minimum_x = s_feedback.joystick_minimum_x,
        .center_x = center_x,
        .maximum_x = s_feedback.joystick_maximum_x,
        .minimum_y = s_feedback.joystick_minimum_y,
        .center_y = center_y,
        .maximum_y = s_feedback.joystick_maximum_y,
        .deadzone_x = s_feedback.joystick_deadzone_x,
        .deadzone_y = s_feedback.joystick_deadzone_y,
        .invert_x = s_feedback.joystick_invert_x,
        .invert_y = s_feedback.joystick_invert_y,
        .filter_percent = s_feedback.joystick_filter,
    };
    (void)board_set_joystick_config(&joystick);
}

static void update_under_key_self_test(void)
{
    if (!s_under_key_self_test_active) {
        return;
    }
    /* Start the clock on the first main-loop frame, after boot services have
     * initialized, so their startup time cannot consume the LED sequence. */
    if (s_under_key_self_test_last_step == SIZE_MAX) {
        s_under_key_self_test_started_at = xTaskGetTickCount();
    }
    under_key_self_test_rgb_t rendered[UNDER_KEY_SELF_TEST_LED_COUNT];
    const size_t led_count = board_under_key_rgb_count();
    const uint32_t elapsed_ms =
        ticks_to_ms(xTaskGetTickCount() - s_under_key_self_test_started_at);
    const size_t step = elapsed_ms / UNDER_KEY_SELF_TEST_STEP_MS;
    if (step < led_count && step == s_under_key_self_test_last_step) {
        return;
    }
    s_under_key_self_test_last_step = step;
    if (!under_key_self_test_render(elapsed_ms, led_count, rendered)) {
        s_under_key_self_test_active = false;
        apply_feedback_config();
        return;
    }
    board_rgb_t under_key[UNDER_KEY_SELF_TEST_LED_COUNT];
    for (size_t index = 0; index < led_count; ++index) {
        under_key[index] = (board_rgb_t) {
            .red = rendered[index].red,
            .green = rendered[index].green,
            .blue = rendered[index].blue,
        };
    }
    board_rgb_t status[STATUS_INDICATOR_LED_COUNT];
    render_status_output(status);
    (void)board_apply_rgb(status, under_key);
}

static size_t nearest_level(const uint8_t *levels, size_t count, uint8_t value)
{
    size_t nearest = 0;
    unsigned distance = UINT_MAX;
    for (size_t index = 0; index < count; ++index) {
        const unsigned candidate = levels[index] > value ? levels[index] - value
                                                         : value - levels[index];
        if (candidate < distance) {
            nearest = index;
            distance = candidate;
        }
    }
    return nearest;
}

static void format_level(char *output, size_t output_size,
                         const char *prefix, uint8_t level)
{
    if (level == 0) {
        snprintf(output, output_size, "%s OFF", prefix);
    } else {
        snprintf(output, output_size, "%s %u", prefix, (unsigned)level);
    }
}

static void render_quick_config(const char *footer)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    s_mist_connection_visible = false;
#endif
    char current_item[24];
    char candidate[CONFIG_STORE_PROFILE_NAME_BYTES + 8] = "VALUE UNKNOWN";
    char saved[CONFIG_STORE_PROFILE_NAME_BYTES + 8] = "";
    bool candidate_saved = true;
    const quick_config_item_t previous =
        (quick_config_item_t)((s_quick_config.item + QUICK_CONFIG_ITEM_COUNT - 1) %
                              QUICK_CONFIG_ITEM_COUNT);
    const quick_config_item_t next =
        (quick_config_item_t)((s_quick_config.item + 1) % QUICK_CONFIG_ITEM_COUNT);
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    if (round_level_slice_active()) {
        if (s_quick_config.item == QUICK_CONFIG_ITEM_HAPTIC) {
            const uint8_t candidate_level =
                (uint8_t)s_quick_config.haptic_level_index;
            (void)board_display_show_round_haptic(candidate_level,
                                                  s_round_setting_state,
                                                  s_local_confirm_selected);
        } else {
            const uint8_t candidate_level =
                (uint8_t)s_quick_config.lighting_level_index;
            (void)board_display_show_round_lighting(candidate_level,
                                                    s_round_setting_state,
                                                    s_local_confirm_selected);
        }
        return;
    }
#endif
    snprintf(current_item, sizeof(current_item), "%s",
             QUICK_CONFIG_ITEM_NAMES[s_quick_config.item]);

    if (s_quick_config.item == QUICK_CONFIG_ITEM_PROFILE) {
        char candidate_profile[CONFIG_STORE_PROFILE_NAME_BYTES + 1] = "UNKNOWN";
        char saved_profile[CONFIG_STORE_PROFILE_NAME_BYTES + 1] = "UNKNOWN";
        size_t saved_index = 0;
        (void)config_store_get_profile_at(s_quick_config.profile_index, NULL,
                                          candidate_profile, sizeof(candidate_profile));
        (void)config_store_get_active_profile_index(&saved_index);
        (void)config_store_get_profile_at(saved_index, NULL,
                                          saved_profile, sizeof(saved_profile));
        snprintf(candidate, sizeof(candidate), "VALUE %s", candidate_profile);
        snprintf(saved, sizeof(saved), "SAVED %s", saved_profile);
        candidate_saved = s_quick_config.profile_index == saved_index;
    } else if (s_quick_config.item == QUICK_CONFIG_ITEM_SYSTEM) {
        static const char *const PLATFORM_NAMES[] = {"MACOS", "WINDOWS LINUX"};
        const config_platform_t saved_platform = config_store_get_platform();
        const size_t saved_index =
            saved_platform == CONFIG_PLATFORM_WINDOWS_LINUX ? 1 : 0;
        snprintf(candidate, sizeof(candidate), "VALUE %s",
                 PLATFORM_NAMES[s_quick_config.platform_index]);
        snprintf(saved, sizeof(saved), "SAVED %s",
                 saved_platform == CONFIG_PLATFORM_CUSTOM
                     ? "CUSTOM" : PLATFORM_NAMES[saved_index]);
        candidate_saved =
            saved_platform != CONFIG_PLATFORM_CUSTOM &&
            s_quick_config.platform_index == saved_index;
    } else if (s_quick_config.item == QUICK_CONFIG_ITEM_KEY_PRESET) {
        if (!s_quick_config.key_selected) {
            snprintf(candidate, sizeof(candidate), "SELECT KEY 1-%u",
                     (unsigned)board_key_count());
            candidate_saved = false;
        } else {
            size_t saved_index = config_store_get_quick_preset_count();
            board_control_t key_control = BOARD_CONTROL_KEY_1;
            if (board_control_from_key_index(s_quick_config.key_index,
                                             &key_control)) {
                (void)config_store_match_quick_preset(
                    board_control_id(key_control), &saved_index);
            }
            snprintf(candidate, sizeof(candidate), "KEY %u %s",
                     (unsigned)s_quick_config.key_index + 1,
                     config_store_get_quick_preset_name(s_quick_config.preset_index));
            snprintf(saved, sizeof(saved), "SAVED %s",
                     saved_index < config_store_get_quick_preset_count()
                         ? config_store_get_quick_preset_name(saved_index) : "CUSTOM");
            candidate_saved = s_quick_config.preset_index == saved_index;
        }
    } else if (s_quick_config.item == QUICK_CONFIG_ITEM_LIGHTING) {
        config_feedback_t feedback = {0};
        (void)config_store_get_feedback(&feedback);
        const uint8_t level = LIGHTING_LEVELS[s_quick_config.lighting_level_index];
        const uint8_t saved_level =
            feedback.lighting_enabled ? feedback.lighting_brightness : 0;
        format_level(candidate, sizeof(candidate), "VALUE", level);
        format_level(saved, sizeof(saved), "SAVED", saved_level);
        candidate_saved = level == saved_level;
    } else if (s_quick_config.item == QUICK_CONFIG_ITEM_HAPTIC) {
        config_feedback_t feedback = {0};
        (void)config_store_get_feedback(&feedback);
        const uint8_t level = HAPTIC_LEVELS[s_quick_config.haptic_level_index];
        const uint8_t saved_level =
            feedback.haptic_enabled ? feedback.haptic_strength : 0;
        format_level(candidate, sizeof(candidate), "VALUE", level);
        format_level(saved, sizeof(saved), "SAVED", saved_level);
        candidate_saved = level == saved_level;
    } else {
        snprintf(candidate, sizeof(candidate), "PRESS TO EXIT");
    }
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    const board_mist_view_t view = {
        .page = BOARD_MIST_QUICK, .phase = 1,
        .a = candidate_saved, .read_index = s_mist_read_index,
        .previous = QUICK_CONFIG_ITEM_NAMES[previous], .title = current_item,
        .next = QUICK_CONFIG_ITEM_NAMES[next], .primary = candidate,
        .saved = saved[0] != '\0' ? saved : NULL,
        .footer = footer != NULL ? footer :
            (s_quick_config.item == QUICK_CONFIG_ITEM_KEY_PRESET
                 ? (s_quick_config.key_selected ? "TURN THEN PRESS SAVE" : "PRESS PHYSICAL KEY")
                 : "PRESS CONFIRM"),
    };
    (void)board_display_show_mist(&view);
#else
    (void)board_display_show_quick_config(
        QUICK_CONFIG_ITEM_NAMES[previous], current_item, QUICK_CONFIG_ITEM_NAMES[next],
        candidate, saved[0] != '\0' ? saved : NULL, candidate_saved,
        footer != NULL ? footer :
        (s_quick_config.item == QUICK_CONFIG_ITEM_KEY_PRESET
             ? (s_quick_config.key_selected
                    ? "TURN THEN PRESS SAVE" : "PRESS PHYSICAL KEY")
             : "PRESS CONFIRM"));
#endif
}

static void load_quick_config_model(quick_config_item_t item, uint8_t key_index)
{
    config_feedback_t feedback = {0};
    size_t profile_index = 0;
    size_t preset_index = config_store_get_quick_preset_count();
    if (key_index >= board_key_count()) {
        key_index = 0;
    }
    (void)config_store_get_feedback(&feedback);
    (void)config_store_get_active_profile_index(&profile_index);
    board_control_t key_control = BOARD_CONTROL_KEY_1;
    if (board_control_from_key_index(key_index, &key_control)) {
        (void)config_store_match_quick_preset(board_control_id(key_control),
                                              &preset_index);
    }
    const uint8_t lighting = feedback.lighting_enabled ? feedback.lighting_brightness : 0;
    const uint8_t haptic = feedback.haptic_enabled ? feedback.haptic_strength : 0;
    const config_platform_t platform = config_store_get_platform();
    const quick_config_model_init_t initial = {
        .profile_index = profile_index,
        .profile_count = config_store_get_profile_count(),
        .platform_index =
            platform == CONFIG_PLATFORM_WINDOWS_LINUX ? 1 : 0,
        .platform_count = 2,
        .preset_index = preset_index,
        .preset_count = config_store_get_quick_preset_count(),
        .lighting_level_index = nearest_level(LIGHTING_LEVELS,
                                              ARRAY_COUNT(LIGHTING_LEVELS), lighting),
        .lighting_level_count = ARRAY_COUNT(LIGHTING_LEVELS),
        .haptic_level_index = nearest_level(HAPTIC_LEVELS,
                                            ARRAY_COUNT(HAPTIC_LEVELS), haptic),
        .haptic_level_count = ARRAY_COUNT(HAPTIC_LEVELS),
    };
    quick_config_model_begin(&s_quick_config, &initial);
    s_quick_config.item = item;
    s_quick_config.key_index = key_index;
}

static void enter_quick_config(quick_config_item_t item)
{
    enter_local_input_ownership();
    clear_lighting_preview();
    if (item == QUICK_CONFIG_ITEM_LIGHTING) {
        clear_host_lighting_preview();
    }
    load_quick_config_model(item, 0);
    s_quick_config_pending_footer = NULL;
    s_quick_config_active = true;
    s_round_setting_state = BOARD_ROUND_SETTING_EDITING;
    s_local_confirm_selected = true;
    s_local_last_input_at = xTaskGetTickCount();
    render_quick_config(NULL);
}

static void exit_quick_config(void)
{
    clear_lighting_preview();
    s_quick_config_active = false;
    s_quick_config_pending_footer = NULL;
    leave_local_input_ownership();
    show_status(NULL);
}

static void confirm_quick_config(void)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    if (round_level_slice_active() &&
        s_round_setting_state == BOARD_ROUND_SETTING_APPLYING) {
        return;
    }
#endif
    const quick_config_command_t command = quick_config_model_confirm(&s_quick_config);
    esp_err_t error = ESP_OK;
    if (command.type == QUICK_CONFIG_COMMAND_SET_PROFILE) {
        uint8_t profile_id = 0;
        if (!config_store_get_profile_at(command.value, &profile_id, NULL, 0)) {
            error = ESP_ERR_NOT_FOUND;
        } else {
            error = config_store_select_profile(profile_id);
        }
    } else if (command.type == QUICK_CONFIG_COMMAND_SET_PLATFORM) {
        error = config_store_select_platform(
            command.value == 0 ? CONFIG_PLATFORM_MACOS
                               : CONFIG_PLATFORM_WINDOWS_LINUX);
    } else if (command.type == QUICK_CONFIG_COMMAND_SET_KEY_PRESET) {
        error = config_store_set_quick_preset(command.key_index, command.value);
    } else if (command.type == QUICK_CONFIG_COMMAND_SET_LIGHTING) {
        error = config_store_set_lighting_level(LIGHTING_LEVELS[command.value]);
    } else if (command.type == QUICK_CONFIG_COMMAND_SET_HAPTIC) {
        error = config_store_set_haptic_level(HAPTIC_LEVELS[command.value]);
    } else if (command.type == QUICK_CONFIG_COMMAND_EXIT) {
        exit_quick_config();
        return;
    }
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    if (round_level_slice_active()) {
        const bool saved_round_level =
            command.type == QUICK_CONFIG_COMMAND_SET_HAPTIC ||
            command.type == QUICK_CONFIG_COMMAND_SET_LIGHTING;
        if (error != ESP_OK) {
            clear_lighting_preview();
            s_round_setting_state = BOARD_ROUND_SETTING_ERROR;
            render_quick_config(NULL);
            return;
        }
        if (saved_round_level && config_store_has_pending()) {
            s_quick_config_pending_footer = "APPLYING";
            s_round_setting_state = BOARD_ROUND_SETTING_APPLYING;
            render_quick_config(NULL);
            return;
        }
        if (saved_round_level) {
            clear_lighting_preview();
            pulse_save_feedback();
            exit_quick_config();
            return;
        }
    }
#endif
    if (error != ESP_OK && command.type == QUICK_CONFIG_COMMAND_SET_LIGHTING) {
        clear_lighting_preview();
    }
    if (error == ESP_OK && command.type != QUICK_CONFIG_COMMAND_NONE &&
        config_store_has_pending()) {
        s_quick_config_pending_footer = "SAVED";
        return;
    }
    if (error == ESP_OK && command.type == QUICK_CONFIG_COMMAND_NONE &&
        s_quick_config.item == QUICK_CONFIG_ITEM_KEY_PRESET &&
        !s_quick_config.key_selected) {
        render_quick_config(NULL);
        return;
    }
    render_quick_config(error == ESP_OK ?
                        (command.type == QUICK_CONFIG_COMMAND_NONE ? "PRESS AGAIN" : "SAVED") :
                        (error == ESP_ERR_INVALID_STATE ? "WAIT THEN RETRY" : "SAVE ERROR"));
}

static void select_quick_config_action(bool confirm_selected)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    if (!round_level_slice_active() ||
        s_round_setting_state != BOARD_ROUND_SETTING_EDITING ||
        s_local_confirm_selected == confirm_selected) {
        return;
    }
    s_local_confirm_selected = confirm_selected;
    pulse_navigation_feedback(BOARD_CONTROL_JOYSTICK_RIGHT);
    render_quick_config(NULL);
#else
    (void)confirm_selected;
#endif
}

static void activate_quick_config_action(void)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    if (round_level_slice_active() && !s_local_confirm_selected) {
        exit_quick_config();
        return;
    }
#endif
    confirm_quick_config();
}

static void handle_quick_config_event(const board_event_t *event)
{
    if (!event->pressed) {
        return;
    }
    s_local_last_input_at = xTaskGetTickCount();
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    if (round_level_slice_active()) {
        if (s_round_setting_state == BOARD_ROUND_SETTING_APPLYING) {
            return;
        }
        if (event->control == BOARD_CONTROL_JOYSTICK_LEFT) {
            select_quick_config_action(false);
            return;
        }
        if (event->control == BOARD_CONTROL_JOYSTICK_RIGHT) {
            select_quick_config_action(true);
            return;
        }
        if (s_round_setting_state == BOARD_ROUND_SETTING_ERROR) {
            return;
        }
        const quick_config_model_t before = s_quick_config;
        if (event->control == BOARD_CONTROL_ENCODER_CCW) {
            quick_config_model_adjust(&s_quick_config, -1);
        } else if (event->control == BOARD_CONTROL_ENCODER_CW) {
            quick_config_model_adjust(&s_quick_config, 1);
        } else {
            return;
        }
        if (memcmp(&before, &s_quick_config, sizeof(before)) != 0) {
            s_round_setting_state = BOARD_ROUND_SETTING_EDITING;
            if (s_quick_config.item == QUICK_CONFIG_ITEM_HAPTIC) {
                preview_haptic_level();
            } else {
                preview_lighting_level();
            }
            render_quick_config(NULL);
        }
        return;
    }
#endif
    const bool navigation_feedback_due =
        navigation_feedback_due_for_control(event->control);
    const quick_config_model_t before = s_quick_config;
    if (event->control == BOARD_CONTROL_JOYSTICK_UP) {
        quick_config_model_move_item(&s_quick_config, -1);
    } else if (event->control == BOARD_CONTROL_JOYSTICK_DOWN) {
        quick_config_model_move_item(&s_quick_config, 1);
    } else if (event->control == BOARD_CONTROL_JOYSTICK_LEFT ||
               event->control == BOARD_CONTROL_ENCODER_CCW) {
        quick_config_model_adjust(&s_quick_config, -1);
    } else if (event->control == BOARD_CONTROL_JOYSTICK_RIGHT ||
               event->control == BOARD_CONTROL_ENCODER_CW) {
        quick_config_model_adjust(&s_quick_config, 1);
    } else if (s_quick_config.item == QUICK_CONFIG_ITEM_KEY_PRESET) {
        size_t key_index = 0;
        if (!board_control_key_index(event->control, &key_index) ||
            key_index >= board_key_count()) {
            return;
        }
        size_t preset_index = config_store_get_quick_preset_count();
        (void)config_store_match_quick_preset(board_control_id(event->control),
                                              &preset_index);
        quick_config_model_select_key(&s_quick_config, (uint8_t)key_index,
                                      preset_index);
    }
    if (memcmp(&before, &s_quick_config, sizeof(before)) != 0) {
        if (s_quick_config.item == QUICK_CONFIG_ITEM_LIGHTING) {
            preview_lighting_level();
        } else if (before.item == QUICK_CONFIG_ITEM_LIGHTING) {
            clear_lighting_preview();
        }
        if (navigation_feedback_due) {
            pulse_navigation_feedback(event->control);
        }
        render_quick_config(NULL);
    }
}

static void poll_quick_config_save_failure(void)
{
    if (!s_quick_config_active || s_quick_config_pending_footer == NULL) {
        return;
    }
    config_store_status_t status = {0};
    config_store_get_status(&status);
    if (!status.activation_failed) {
        return;
    }
    clear_lighting_preview();
    s_quick_config_pending_footer = NULL;
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    if (round_level_slice_active()) {
        s_round_setting_state = BOARD_ROUND_SETTING_ERROR;
        render_quick_config(NULL);
        return;
    }
#endif
    render_quick_config("SAVE ERROR");
}

static void macro_cancel(void)
{
    memset(&s_macro, 0, sizeof(s_macro));
    usb_service_release_source(MACRO_SOURCE);
    usb_service_release_source(MACRO_TRANSIENT_SOURCE);
}

static void enter_local_input_ownership(void)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    mist_cancel_motion();
    s_mist_connection_visible = false;
    s_mist_read_index = 0;
#endif
    if (diagnostic_capture_model_owns_input(&s_diagnostic_capture)) {
        diagnostic_capture_model_yield_to_local(&s_diagnostic_capture);
        s_diagnostic_encoder_press_active = false;
        publish_diagnostic_capture_status();
    }
    s_round_feedback_active = false;
    s_host_output_state = HOST_OUTPUT_LOCAL_UI;
    reset_mode_sensitive_input_state();
    codex_micro_set_input_suppressed(true);
}

static void leave_local_input_ownership(void)
{
    if (s_host_output_state != HOST_OUTPUT_LOCAL_UI) {
        return;
    }
    s_host_output_state = HOST_OUTPUT_WAIT_NEUTRAL;
    reset_mode_sensitive_input_state();
}

static void poll_local_input_ownership(void)
{
    if (s_host_output_state != HOST_OUTPUT_WAIT_NEUTRAL ||
        s_system_save_pending ||
        config_protocol_calibration_ownership_requested() ||
        !board_keys_neutral()) {
        return;
    }
    if (config_protocol_calibration_output_interlock_active()) {
        config_protocol_release_calibration_output_interlock();
    }
    s_host_output_state = HOST_OUTPUT_ENABLED;
    codex_micro_set_input_suppressed(false);
    /*
     * Local pages replace the panel contents.  Redraw only after every input
     * is neutral and host ownership is restored so a cached/stale local frame
     * cannot remain visible while controls are already operating the host.
     */
    show_status(NULL);
}

/*
 * USB standby skips poll_local_input_ownership(), so a calibration or
 * diagnostic session that ended there would keep its output interlock
 * forever: GET_STATUS then reports inputs_neutral=false and the service
 * tool's idle acceptance fails. Release it once inputs are neutral, without
 * redrawing or re-enabling input: the unit stays OFF.
 */
static void poll_standby_input_ownership(void)
{
    if (s_host_output_state != HOST_OUTPUT_WAIT_NEUTRAL ||
        config_protocol_calibration_ownership_requested() ||
        !board_keys_neutral()) {
        return;
    }
    if (config_protocol_calibration_output_interlock_active()) {
        config_protocol_release_calibration_output_interlock();
    }
    s_host_output_state = HOST_OUTPUT_ENABLED;
}

static void set_hid_usage(usb_service_source_t source, uint16_t usage, bool pressed)
{
    if (usage >= 224 && usage <= 231) {
        usb_service_set_modifier(source, (uint8_t)usage, pressed);
    } else if (usage != 0 && usage <= UINT8_MAX) {
        usb_service_set_key(source, (uint8_t)usage, pressed);
    }
}

static void execute_action(const config_action_t *action, bool pressed,
                           usb_service_source_t source)
{
    if (action->type == CONFIG_ACTION_KEY) {
        /* A chord goes out like a person types it: modifiers down before the
         * key, key up before the modifiers, each in its own report. */
        const bool chord = action->modifier_count > 0 && action->usage != 0;
        if (!pressed && chord) {
            set_hid_usage(source, action->usage, false);
            usb_service_commit_keyboard();
        }
        for (size_t index = 0; index < action->modifier_count; ++index) {
            usb_service_set_modifier(source, action->modifiers[index], pressed);
        }
        if (pressed && chord) {
            usb_service_commit_keyboard();
        }
        if (!(chord && !pressed)) {
            set_hid_usage(source, action->usage, pressed);
        }
        usb_service_commit_keyboard();
    } else if (pressed && action->type == CONFIG_ACTION_CONSUMER) {
        usb_service_tap_consumer(action->usage);
    } else if (action->type == CONFIG_ACTION_MOUSE) {
        usb_service_send_mouse(source, pressed ? action->mouse_buttons : 0,
                               pressed ? action->mouse_x : 0,
                               pressed ? action->mouse_y : 0,
                               pressed ? action->mouse_wheel : 0,
                               pressed ? action->mouse_pan : 0);
    } else if (pressed && action->type == CONFIG_ACTION_MACRO) {
        macro_cancel();
        s_macro.active = true;
        s_macro.id = action->object_id;
    } else if (pressed && action->type == CONFIG_ACTION_HOST_ACTION &&
               codex_micro_mode() == CODEX_MICRO_MODE_NORMAL) {
        const esp_err_t error = host_action_events_queue(action->object_id, action->task_token);
        show_feedback(error == ESP_OK ? STATUS_FEEDBACK_SUCCESS
                                      : STATUS_FEEDBACK_ERROR,
                      error == ESP_OK ? "TASK QUEUED" : "TASK HELPER OFFLINE");
    } else if (pressed && action->type == CONFIG_ACTION_PROMPT) {
        const esp_err_t error = prompt_store_queue_trigger(action->object_id);
        show_feedback(error == ESP_OK ? STATUS_FEEDBACK_SUCCESS
                                      : STATUS_FEEDBACK_ERROR,
                      error == ESP_OK ? "PROMPT QUEUED"
                                      : "PROMPT HELPER OFFLINE");
    } else if (pressed && action->type == CONFIG_ACTION_PROFILE) {
        (void)config_store_select_profile(action->object_id);
    } else if (pressed && action->type == CONFIG_ACTION_DEVICE) {
        if (action->device_action == CONFIG_DEVICE_MACRO_CANCEL) {
            macro_cancel();
        } else {
            (void)config_store_apply_device_action(action->device_action);
        }
    }
}

static bool ascii_usage(char value, uint8_t *usage, bool *shift)
{
    *shift = false;
    if (value >= 'a' && value <= 'z') {
        *usage = (uint8_t)(4 + value - 'a');
        return true;
    }
    if (value >= 'A' && value <= 'Z') {
        *usage = (uint8_t)(4 + value - 'A');
        *shift = true;
        return true;
    }
    if (value >= '1' && value <= '9') {
        *usage = (uint8_t)(30 + value - '1');
        return true;
    }
    if (value == '0') { *usage = 39; return true; }
    if (value == ' ') { *usage = 44; return true; }
    if (value == '\n' || value == '\r') { *usage = 40; return true; }
    if (value == '\t') { *usage = 43; return true; }
    const char unshifted[] = "-=[]\\;',./`";
    const uint8_t usages[] = {45, 46, 47, 48, 49, 51, 52, 54, 55, 56, 53};
    const char *found = strchr(unshifted, value);
    if (found != NULL) {
        *usage = usages[found - unshifted];
        return true;
    }
    const char shifted[] = "_+{}|:\"<>?~!@#$%^&*()";
    const uint8_t shifted_usages[] = {45,46,47,48,49,51,52,54,55,56,53,30,31,32,33,34,35,36,37,38,39};
    found = strchr(shifted, value);
    if (found != NULL) {
        *usage = shifted_usages[found - shifted];
        *shift = true;
        return true;
    }
    return false;
}

static void dispatch_action_for_control(const config_action_t *action,
                                        board_control_t control,
                                        bool pressed,
                                        const char *short_name)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    (void)short_name;
#endif
    execute_action(action, pressed, (usb_service_source_t)control);
    if (pressed) {
        pulse_control_press_feedback(control);
    }
    if (s_feedback.display_show_control_hints && pressed) {
#if !CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
        show_status(short_name);
#endif
    }
    size_t key_index = 0;
    if (board_control_key_index(control, &key_index) &&
        key_index < board_under_key_rgb_count() &&
        !(s_codex_key_status_active &&
          key_status_index_for_key(key_index, NULL)) &&
        s_feedback.lighting_enabled) {
        const size_t index = key_index;
        const config_rgb_t color = s_feedback.under_key_rgb[index];
        const uint8_t brightness = pressed
                                       ? 100
                                       : lighting_under_key_percent(
                                             s_feedback.lighting_brightness);
        (void)board_set_under_key_rgb(
            index, lighting_scale_channel(color.red, brightness),
            lighting_scale_channel(color.green, brightness),
            lighting_scale_channel(color.blue, brightness));
    }
}

static void dispatch_control(board_control_t control, bool pressed)
{
    config_action_t action;
    const char *control_id = board_control_id(control);
    if (control_id == NULL) {
        return;
    }
    if (pressed) {
        if (!config_store_get_action(control_id, &action)) {
            return;
        }
        s_active_actions[control] = action;
        s_active_action_valid[control] = true;
    } else if (s_active_action_valid[control]) {
        action = s_active_actions[control];
        s_active_action_valid[control] = false;
    } else if (!config_store_get_action(control_id, &action)) {
        return;
    }
    dispatch_action_for_control(&action, control, pressed, action.short_name);
}

/* Send one step of a gesture keystroke. Feedback (haptic, key light) follows
 * the key itself; modifier-only steps change nothing but the HID report. */
static void run_key_pulse_step(size_t index, key_pulse_step_t step)
{
    const board_control_t control = (board_control_t)(BOARD_CONTROL_KEY_8 + index);
    const config_action_t *action = &s_key_pulse_actions[index];
    config_action_t part = *action;
    switch (step) {
    case KEY_PULSE_STEP_MODIFIERS_DOWN:
    case KEY_PULSE_STEP_MODIFIERS_UP:
        part.usage = 0;
        execute_action(&part, step == KEY_PULSE_STEP_MODIFIERS_DOWN,
                       (usb_service_source_t)control);
        break;
    case KEY_PULSE_STEP_KEY_DOWN:
        dispatch_action_for_control(action, control, true, action->short_name);
        break;
    case KEY_PULSE_STEP_KEY_UP:
        part.modifier_count = 0;
        dispatch_action_for_control(&part, control, false, action->short_name);
        break;
    case KEY_PULSE_STEP_NONE:
    default:
        break;
    }
}

/* Release whatever a pending gesture keystroke still holds, right away. */
static void finish_key_pulses(void)
{
    for (size_t index = 0; index < CONFIGURABLE_KEY_GESTURE_COUNT; ++index) {
        key_pulse_step_t step;
        while ((step = key_pulse_model_finish(&s_key_pulses[index])) !=
               KEY_PULSE_STEP_NONE) {
            run_key_pulse_step(index, step);
        }
    }
}

static void poll_key_pulses(void)
{
    const uint32_t now_ms = ticks_to_ms(xTaskGetTickCount());
    for (size_t index = 0; index < CONFIGURABLE_KEY_GESTURE_COUNT; ++index) {
        key_pulse_step_t step;
        while ((step = key_pulse_model_poll(&s_key_pulses[index], now_ms)) !=
               KEY_PULSE_STEP_NONE) {
            run_key_pulse_step(index, step);
        }
    }
}

/* Gesture output is one complete keystroke sent like a person types it:
 * modifiers first, key held KEY_PULSE_HOLD_MS, then released in reverse.
 * Only a decided gesture starts it, so a double click cannot briefly start
 * dictation first. */
static void dispatch_key_gesture(board_control_t control,
                                 key_gesture_result_t result)
{
    if (result == KEY_GESTURE_NONE) return;
    const size_t index = (size_t)(control - BOARD_CONTROL_KEY_8);
    config_action_t action = s_key_gesture_actions[index];
    action.type = CONFIG_ACTION_KEY;
    if (result == KEY_GESTURE_DOUBLE) {
        action.usage = action.double_usage;
        action.modifier_count = action.double_modifier_count;
        memcpy(action.modifiers, action.double_modifiers, sizeof(action.modifiers));
    }
    /* A previous keystroke on this key is completed before the next starts. */
    key_pulse_step_t step;
    while ((step = key_pulse_model_finish(&s_key_pulses[index])) != KEY_PULSE_STEP_NONE) {
        run_key_pulse_step(index, step);
    }
    s_key_pulse_actions[index] = action;
    run_key_pulse_step(index, key_pulse_model_start(
        &s_key_pulses[index], action.modifier_count > 0,
        ticks_to_ms(xTaskGetTickCount())));
}

static bool route_key_gesture_action(const board_event_t *event,
                                     const config_action_t *action)
{
    if (action->type != CONFIG_ACTION_KEY_GESTURE) return false;
    if (config_store_has_pending() ||
        (!usb_service_is_mounted() && !codex_micro_ble_connected())) {
        memset(s_key_gestures, 0, sizeof(s_key_gestures));
        finish_key_pulses();
        return true;
    }
    const size_t index = event->control - BOARD_CONTROL_KEY_8;
    if (!s_key_gestures[index].down && !s_key_gestures[index].waiting_second) {
        s_key_gesture_actions[index] = *action;
    }
    dispatch_key_gesture(event->control, key_gesture_model_event(
        &s_key_gestures[index], event->pressed, ticks_to_ms(xTaskGetTickCount())));
    return true;
}

static bool route_key_gesture_event(const board_event_t *event)
{
    if (codex_micro_mode() != CODEX_MICRO_MODE_NORMAL ||
        event->control < BOARD_CONTROL_KEY_8 ||
        event->control > BOARD_CONTROL_KEY_12) return false;
    config_action_t action;
    if (!config_store_get_action(board_control_id(event->control), &action)) {
        return false;
    }
    return route_key_gesture_action(event, &action);
}

/* CODEX key 8 may preserve the existing click/double-click gesture or use a
 * normal key action for hold-to-talk. The normal key path snapshots the action
 * on the first down edge so a staged configuration cannot change its release. */
static bool route_codex_voice_override_event(const board_event_t *event)
{
    if (codex_micro_mode() != CODEX_MICRO_MODE_CODEX ||
        codex_micro_active_key_layout() != CODEX_MICRO_KEY_LAYOUT_MATRIX12 ||
        event->control != BOARD_CONTROL_KEY_8) {
        return false;
    }

    config_action_t action;
    if (!config_store_get_codex_voice_action(&action)) {
        return false;
    }
    if (action.type == CONFIG_ACTION_KEY_GESTURE) {
        return route_key_gesture_action(event, &action);
    }
    if (action.type != CONFIG_ACTION_KEY) {
        return true;
    }

    if (event->pressed) {
        if (s_active_action_valid[event->control] ||
            config_store_has_pending() ||
            (!usb_service_is_mounted() && !codex_micro_ble_connected())) {
            return true;
        }
    } else if (!s_active_action_valid[event->control]) {
        return true;
    }

    dispatch_latched_standard_action(event->control, action,
                                     action.short_name, event->pressed);
    return true;
}

/* A selected Power V2 voice action has one physical meaning in Normal and
 * Codex, regardless of the active Profile. Claude Code keeps its Space key. */
static bool route_global_voice_event(const board_event_t *event)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    const codex_micro_mode_t mode = codex_micro_mode();
    if ((mode != CODEX_MICRO_MODE_NORMAL && mode != CODEX_MICRO_MODE_CODEX) ||
        (event->control != BOARD_CONTROL_KEY_8 &&
         !(mode == CODEX_MICRO_MODE_NORMAL && event->control == BOARD_CONTROL_KEY_11))) {
        return false;
    }
    config_action_t action;
    if (!config_store_get_global_voice_action(&action)) return false;
    if (event->control == BOARD_CONTROL_KEY_11) {
        memset(&action, 0, sizeof(action));
        action.type = CONFIG_ACTION_KEY;
        action.usage = 40; /* Enter: Normal's separate send key. Codex Key 11 already sends. */
    }
    if (action.type == CONFIG_ACTION_KEY_GESTURE) {
        return route_key_gesture_action(event, &action);
    }
    if (event->pressed) {
        if (s_active_action_valid[event->control] || config_store_has_pending() ||
            (!usb_service_is_mounted() && !codex_micro_ble_connected())) return true;
    } else if (!s_active_action_valid[event->control]) {
        return true;
    }
    dispatch_latched_standard_action(event->control, action,
                                     action.short_name, event->pressed);
    return true;
#else
    (void)event;
    return false;
#endif
}

static void poll_key_gestures(void)
{
    if ((codex_micro_mode() != CODEX_MICRO_MODE_NORMAL &&
         codex_micro_mode() != CODEX_MICRO_MODE_CODEX) ||
        config_store_has_pending() ||
        s_host_output_state != HOST_OUTPUT_ENABLED ||
        s_system_select_active || s_quick_config_active ||
        s_local_page != LOCAL_PAGE_NONE || s_pomodoro.state == POMODORO_ALERT ||
        (!usb_service_is_mounted() && !codex_micro_ble_connected())) {
        memset(s_key_gestures, 0, sizeof(s_key_gestures));
        finish_key_pulses();
        return;
    }
    for (size_t index = 0; index < CONFIGURABLE_KEY_GESTURE_COUNT; ++index) {
        dispatch_key_gesture((board_control_t)(BOARD_CONTROL_KEY_8 + index),
            key_gesture_model_poll(&s_key_gestures[index],
                                    ticks_to_ms(xTaskGetTickCount())));
    }
}

static uint8_t joystick_prompt_direction_mask(void)
{
    uint8_t mask = 0u;
    for (board_control_t direction = BOARD_CONTROL_JOYSTICK_UP;
         direction <= BOARD_CONTROL_JOYSTICK_RIGHT; ++direction) {
        config_action_t action;
        const char *control_id = board_control_id(direction);
        if (control_id != NULL &&
            config_store_get_action(control_id, &action) &&
            action.type == CONFIG_ACTION_PROMPT) {
            mask |= (uint8_t)(1u <<
                              (direction - BOARD_CONTROL_JOYSTICK_UP));
        }
    }
    return mask;
}

static bool normal_joystick_single_action_mapping(void)
{
    for (board_control_t direction = BOARD_CONTROL_JOYSTICK_UP;
         direction <= BOARD_CONTROL_JOYSTICK_RIGHT; ++direction) {
        config_action_t action;
        const char *control_id = board_control_id(direction);
        if (control_id != NULL &&
            config_store_get_action(control_id, &action) &&
            action.type == CONFIG_ACTION_MOUSE) {
            return false;
        }
    }
    return true;
}

static board_control_t prompt_joystick_control(uint8_t direction)
{
    return (board_control_t)(BOARD_CONTROL_JOYSTICK_UP + direction);
}

static void dispatch_prompt_joystick_result(prompt_joystick_result_t result)
{
    if (result.direction >= PROMPT_JOYSTICK_DIRECTION_COUNT) {
        return;
    }
    const board_control_t selected =
        prompt_joystick_control(result.direction);
    if (result.trigger) {
        dispatch_control(selected, true);
    }
    if (result.release) {
        dispatch_control(selected, false);
    }
}

static bool route_prompt_joystick_event(const board_event_t *event,
                                        codex_micro_mode_t mode)
{
    if (event->control < BOARD_CONTROL_JOYSTICK_UP ||
        event->control > BOARD_CONTROL_JOYSTICK_RIGHT ||
        mode != CODEX_MICRO_MODE_NORMAL) {
        return false;
    }
    const uint8_t direction =
        (uint8_t)(event->control - BOARD_CONTROL_JOYSTICK_UP);
    const uint8_t direction_mask = (uint8_t)(1u << direction);
    const uint8_t prompt_mask = s_prompt_joystick.locked
                                    ? s_prompt_joystick.eligible_mask
                                    : s_prompt_joystick_direction_mask;
    if ((prompt_mask & direction_mask) == 0u) {
        return false;
    }
    float angle = 0.0f;
    const bool radial_active = board_get_joystick_radial(&angle);
    dispatch_prompt_joystick_result(prompt_joystick_model_update(
        &s_prompt_joystick, direction, event->pressed, radial_active, angle,
        prompt_mask));
    return true;
}

static void poll_prompt_joystick(void)
{
    if (!s_prompt_joystick.locked ||
        s_host_output_state != HOST_OUTPUT_ENABLED ||
        codex_micro_mode() != CODEX_MICRO_MODE_NORMAL) {
        return;
    }
    float angle = 0.0f;
    const bool radial_active = board_get_joystick_radial(&angle);
    dispatch_prompt_joystick_result(
        prompt_joystick_model_poll(&s_prompt_joystick, radial_active));
}

static bool single_action_joystick_context(void)
{
    if (s_host_output_state == HOST_OUTPUT_LOCAL_UI) {
        return s_local_page != LOCAL_PAGE_NONE || s_quick_config_active ||
               s_system_select_active;
    }
    if (s_host_output_state != HOST_OUTPUT_ENABLED ||
        codex_micro_active_key_layout() != CODEX_MICRO_KEY_LAYOUT_MATRIX12) {
        return false;
    }
    const codex_micro_mode_t mode = codex_micro_mode();
    return (mode == CODEX_MICRO_MODE_NORMAL &&
            s_normal_joystick_single_action) ||
           mode == CODEX_MICRO_MODE_CLAUDE_CODE;
}

static void poll_single_action_joystick(void)
{
    if (!single_action_joystick_context() || s_shutdown_active ||
        s_usb_standby_active || s_battery_splash_active || s_idle_standby.active ||
        s_pomodoro.state == POMODORO_ALERT ||
        diagnostic_capture_model_owns_input(&s_diagnostic_capture)) {
        joystick_single_action_model_reset_latched(&s_single_action_joystick);
        return;
    }
    float angle = 0.0f;
    const bool radial_active = board_get_joystick_radial(&angle);
    board_joystick_diagnostics_t diagnostics;
    if (!board_get_joystick_diagnostics(&diagnostics) ||
        !diagnostics.supported) {
        joystick_single_action_model_reset_latched(&s_single_action_joystick);
        return;
    }
    const joystick_single_action_result_t result =
        joystick_single_action_model_update(&s_single_action_joystick,
                                            radial_active, angle,
                                            diagnostics.sample_count);
    if (!result.press && !result.release) {
        return;
    }
    board_event_t event = {
        .control = prompt_joystick_control(result.direction),
        .pressed = result.press,
    };
    s_dispatching_single_action_joystick = true;
    if (result.press || codex_micro_mode() != CODEX_MICRO_MODE_NORMAL) {
        action_engine_handle_event(&event);
    }
    if (result.press && codex_micro_mode() == CODEX_MICRO_MODE_NORMAL) {
        /* Command mappings are one action per excursion, even when held. */
        event.pressed = false;
        action_engine_handle_event(&event);
    }
    s_dispatching_single_action_joystick = false;
}

static void dispatch_latched_standard_action(board_control_t control,
                                             config_action_t action,
                                             const char *short_name,
                                             bool pressed)
{
    if (pressed) {
        s_active_actions[control] = action;
        s_active_action_valid[control] = true;
    } else if (s_active_action_valid[control]) {
        action = s_active_actions[control];
        s_active_action_valid[control] = false;
    }
    dispatch_action_for_control(&action, control, pressed, short_name);
}

static void dispatch_codex_standard_key(
    board_control_t control,
    const codex_micro_key_mapping_t *mapping,
    bool pressed)
{
    const config_action_t action = {
        .type = CONFIG_ACTION_KEY,
        .usage = mapping->hid_usage,
    };
    dispatch_latched_standard_action(control, action, mapping->short_name,
                                     pressed);
}

static void dispatch_eda_standard_key(board_control_t control,
                                      const eda_shortcut_t *shortcut,
                                      bool pressed)
{
    const config_action_t action = {
        .type = CONFIG_ACTION_KEY,
        .usage = shortcut->hid_usage,
        .modifier_count = shortcut->modifier_usage != 0 ? 1 : 0,
        .modifiers = {shortcut->modifier_usage},
    };
    dispatch_latched_standard_action(control, action, shortcut->short_name,
                                     pressed);
}

static void dispatch_claude_code_shortcut(
    board_control_t control,
    const claude_code_shortcut_t *shortcut,
    bool pressed)
{
    config_action_t action = {
        .type = CONFIG_ACTION_KEY,
        .usage = shortcut->hid_usage,
        .modifier_count = shortcut->modifier_count,
    };
    memcpy(action.modifiers, shortcut->modifiers,
           shortcut->modifier_count * sizeof(action.modifiers[0]));
    dispatch_latched_standard_action(control, action, shortcut->short_name,
                                     pressed);
}

static void dispatch_claude_code_encoder_scroll(board_control_t control)
{
    const config_action_t action = {
        .type = CONFIG_ACTION_MOUSE,
        .mouse_wheel = control == BOARD_CONTROL_ENCODER_CCW ? 1 : -1,
    };
    dispatch_latched_standard_action(control, action, "SCROLL", true);
    dispatch_latched_standard_action(control, action, "SCROLL", false);
}

static bool route_claude_code_event(const board_event_t *event,
                                    size_t *key_index)
{
    claude_code_shortcut_t shortcut;
    if (board_control_key_index(event->control, key_index)) {
        const bool macos =
            config_store_get_platform() != CONFIG_PLATFORM_WINDOWS_LINUX;
        if (claude_code_shortcut_for_key(macos, *key_index, &shortcut)) {
            dispatch_claude_code_shortcut(event->control, &shortcut,
                                          event->pressed);
            return true;
        }
    }
    if (event->control >= BOARD_CONTROL_JOYSTICK_UP &&
        event->control <= BOARD_CONTROL_JOYSTICK_RIGHT) {
        const size_t direction =
            (size_t)(event->control - BOARD_CONTROL_JOYSTICK_UP);
        if (claude_code_shortcut_for_joystick_direction(direction,
                                                        &shortcut)) {
            dispatch_claude_code_shortcut(event->control, &shortcut,
                                          event->pressed);
            return true;
        }
    }
    if (event->control == BOARD_CONTROL_JOYSTICK_PRESS &&
        claude_code_shortcut_for_joystick_press(&shortcut)) {
        dispatch_claude_code_shortcut(event->control, &shortcut,
                                      event->pressed);
        return true;
    }
    if (event->control == BOARD_CONTROL_ENCODER_PRESS &&
        claude_code_shortcut_for_encoder_press(&shortcut)) {
        dispatch_claude_code_shortcut(event->control, &shortcut,
                                      event->pressed);
        return true;
    }
    if (event->control == BOARD_CONTROL_ENCODER_CCW ||
        event->control == BOARD_CONTROL_ENCODER_CW) {
        if (event->pressed) {
            dispatch_claude_code_encoder_scroll(event->control);
        }
        return true;
    }
    return false;
}

static bool resolve_eda_canvas_pan(board_control_t control,
                                   size_t *direction_index,
                                   eda_canvas_pan_t *pan)
{
    if (control < BOARD_CONTROL_JOYSTICK_UP ||
        control > BOARD_CONTROL_JOYSTICK_RIGHT) {
        return false;
    }
    const size_t index = (size_t)(control - BOARD_CONTROL_JOYSTICK_UP);
    if (!eda_canvas_pan_for_direction(index, pan)) {
        return false;
    }
    if (direction_index != NULL) {
        *direction_index = index;
    }
    return true;
}

static void dispatch_eda_canvas_pan(board_control_t control,
                                    const eda_canvas_pan_t *pan,
                                    size_t direction_index,
                                    bool pressed)
{
    const config_action_t action = {
        .type = CONFIG_ACTION_MOUSE,
        .mouse_buttons = EDA_CANVAS_PAN_MOUSE_BUTTON,
        .mouse_x = pan->mouse_x,
        .mouse_y = pan->mouse_y,
    };
    (void)eda_canvas_pan_model_set_direction(
        &s_eda_canvas_pan, direction_index, pressed,
        ticks_to_ms(xTaskGetTickCount()));
    dispatch_latched_standard_action(control, action, pan->short_name,
                                     pressed);
    if (!pressed) {
        for (board_control_t direction = BOARD_CONTROL_JOYSTICK_UP;
             direction <= BOARD_CONTROL_JOYSTICK_RIGHT; ++direction) {
            usb_service_discard_mouse_source(
                (usb_service_source_t)direction);
        }
    }
}

static void poll_eda_canvas_pan(void)
{
    if (s_host_output_state != HOST_OUTPUT_ENABLED ||
        codex_micro_mode() != CODEX_MICRO_MODE_EDA ||
        codex_micro_active_key_layout() != CODEX_MICRO_KEY_LAYOUT_REV_A ||
        s_system_select_active || s_quick_config_active ||
        s_local_page != LOCAL_PAGE_NONE ||
        s_pomodoro.state == POMODORO_ALERT) {
        if (s_eda_canvas_pan.active_directions != 0) {
            reset_mode_sensitive_input_state();
        }
        return;
    }
    size_t source_direction_index = 0;
    eda_canvas_delta_t delta;
    if (!eda_canvas_pan_model_poll(
            &s_eda_canvas_pan, ticks_to_ms(xTaskGetTickCount()),
            &source_direction_index, &delta)) {
        return;
    }
    const board_control_t source_control = (board_control_t)(
        BOARD_CONTROL_JOYSTICK_UP + source_direction_index);
    usb_service_send_mouse((usb_service_source_t)source_control,
                           EDA_CANVAS_PAN_MOUSE_BUTTON,
                           delta.mouse_x, delta.mouse_y, 0, 0);
}

static void route_main_event(const board_event_t *event)
{
    if (s_host_output_state != HOST_OUTPUT_ENABLED) {
        return;
    }
    size_t key_index = 0;
    const codex_micro_mode_t mode = codex_micro_mode();
    if (route_global_voice_event(event)) return;
    /* External dictation does not depend on the official Codex agent link. */
    if (mode == CODEX_MICRO_MODE_CODEX &&
        route_codex_voice_override_event(event)) return;
    if (mode == CODEX_MICRO_MODE_CODEX && !codex_micro_input_ready()) {
        if (event->pressed && !joystick_direction_control(event->control)) {
            pulse_control_press_feedback(event->control);
        }
        return;
    }
    if (mode == CODEX_MICRO_MODE_CLAUDE_CODE &&
        codex_micro_active_key_layout() == CODEX_MICRO_KEY_LAYOUT_MATRIX12 &&
        route_claude_code_event(event, &key_index)) {
        return;
    }
    if (mode == CODEX_MICRO_MODE_EDA &&
        codex_micro_active_key_layout() == CODEX_MICRO_KEY_LAYOUT_REV_A) {
        size_t direction_index = 0;
        eda_canvas_pan_t pan;
        if (resolve_eda_canvas_pan(event->control, &direction_index, &pan)) {
            dispatch_eda_canvas_pan(event->control, &pan, direction_index,
                                    event->pressed);
            return;
        }
        if (board_control_key_index(event->control, &key_index)) {
            eda_shortcut_t shortcut;
            if (eda_shortcut_for_key(key_index, &shortcut)) {
                dispatch_eda_standard_key(event->control, &shortcut,
                                          event->pressed);
                return;
            }
        }
    }
    if (route_prompt_joystick_event(event, mode)) {
        return;
    }
    if (mode == CODEX_MICRO_MODE_CODEX &&
        board_control_key_index(event->control, &key_index)) {
        const codex_micro_key_mapping_t mapping =
            codex_micro_map_key_index(codex_micro_active_key_layout(),
                                      key_index);
        if (mapping.kind == CODEX_MICRO_KEY_STANDARD_HID) {
            dispatch_codex_standard_key(event->control, &mapping,
                                        event->pressed);
            return;
        }
    }
    if (codex_micro_handle_event(event)) {
        if (event->pressed &&
            (mode == CODEX_MICRO_MODE_NORMAL ||
             mode == CODEX_MICRO_MODE_CODEX) &&
            !joystick_direction_control(event->control)) {
            pulse_control_press_feedback(event->control);
        }
        return;
    }
    if (route_key_gesture_event(event)) return;
    if ((event->control == BOARD_CONTROL_ENCODER_CCW ||
         event->control == BOARD_CONTROL_ENCODER_CW) && event->pressed) {
        dispatch_control(event->control, true);
        dispatch_control(event->control, false);
        return;
    }
    dispatch_control(event->control, event->pressed);
}

/* At least one tick, so a USB service pass always sees the press. */
static TickType_t synthetic_tap_hold_ticks(void)
{
    const TickType_t ticks = pdMS_TO_TICKS(KEY_PULSE_HOLD_MS);
    return ticks > 0 ? ticks : 1;
}

static void route_tap(board_control_t control)
{
    const board_event_t press = {.control = control, .pressed = true};
    route_main_event(&press);
    schedule_tap_release(control);
}

static void schedule_tap_release(board_control_t control)
{
    if (control >= BOARD_CONTROL_COUNT) {
        return;
    }
    s_tap_release_pending[control] = true;
    s_tap_release_at[control] = xTaskGetTickCount() + synthetic_tap_hold_ticks();
}

static void poll_tap_releases(void)
{
    for (board_control_t control = 0; control < BOARD_CONTROL_COUNT; ++control) {
        if (!s_tap_release_pending[control]) {
            continue;
        }
        /* Hold the synthesized press like a person would (KEY_PULSE_HOLD_MS);
         * hotkey listeners miss chords that last a single USB poll. */
        if ((int32_t)(xTaskGetTickCount() - s_tap_release_at[control]) < 0) {
            continue;
        }
        s_tap_release_pending[control] = false;
        const board_event_t release = {.control = control, .pressed = false};
        route_main_event(&release);
    }
}

#if !CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
static const char *mode_change_hint(codex_micro_mode_t mode)
{
    switch (mode) {
    case CODEX_MICRO_MODE_CODEX:
        return "CODEX MODE";
    case CODEX_MICRO_MODE_EDA:
        return "EDA MODE";
    case CODEX_MICRO_MODE_CLAUDE_CODE:
        return "CLAUDE CODE";
    case CODEX_MICRO_MODE_NORMAL:
    default:
        return "KEY MODE";
    }
}
#endif

static void handle_double_click_actions(board_control_t control, uint8_t actions)
{
    if ((actions & DOUBLE_CLICK_ACTION_TAP) != 0) {
        route_tap(control);
    }
    if ((actions & DOUBLE_CLICK_ACTION_HOLD_PRESS) != 0) {
        const board_event_t press = {.control = control, .pressed = true};
        route_main_event(&press);
    }
    if ((actions & DOUBLE_CLICK_ACTION_HOLD_RELEASE) != 0) {
        const board_event_t release = {.control = control, .pressed = false};
        route_main_event(&release);
    }
    if ((actions & DOUBLE_CLICK_ACTION_DOUBLE) == 0) {
        return;
    }
    /* Only the knob switches modes. A double click on the joystick button is
     * uncomfortable (the stick sways sideways when pressed down), so it no
     * longer does, on Matrix12 as on every other layout. */
    if (control == BOARD_CONTROL_ENCODER_PRESS) {
        const codex_micro_mode_t previous_mode = codex_micro_mode();
        const codex_micro_mode_t mode = codex_micro_toggle_mode();
        if (mode == previous_mode) {
            show_feedback(STATUS_FEEDBACK_ERROR, "CODEX NOT CONNECTED");
            return;
        }
        clear_host_lighting_preview();
        reset_mode_sensitive_input_state();
        s_codex_mode = mode;
        pulse_save_feedback();
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
        s_round_feedback_active = false;
        show_status(NULL);
#else
        show_status(mode_change_hint(mode));
#endif
    } else if (control == BOARD_CONTROL_JOYSTICK_PRESS) {
        const uint32_t now_ms = ticks_to_ms(xTaskGetTickCount());
        if (s_pomodoro.state == POMODORO_RUNNING) {
            (void)pomodoro_model_pause(&s_pomodoro, now_ms);
            pulse_navigation_feedback(control);
            show_feedback(STATUS_FEEDBACK_PAUSE, "TIMER PAUSED");
        } else if (s_pomodoro.state == POMODORO_PAUSED) {
            (void)pomodoro_model_resume(&s_pomodoro, now_ms);
            pulse_navigation_feedback(control);
            show_feedback(STATUS_FEEDBACK_PLAY, "TIMER RESUMED");
        }
    }
}

#if !CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
static void format_timer(char output[12])
{
    const uint32_t seconds =
        (pomodoro_model_remaining_ms(&s_pomodoro,
                                     ticks_to_ms(xTaskGetTickCount())) +
         999U) / 1000U;
    snprintf(output, 12, "%02u:%02u", (unsigned)(seconds / 60U),
             (unsigned)(seconds % 60U));
}
#endif

static void render_local_page(void)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    if (board_joystick_calibration_active() ||
        diagnostic_capture_model_owns_input(&s_diagnostic_capture)) return;
    s_mist_connection_visible = false;
    if (s_local_page == LOCAL_PAGE_FUNCTION) {
        static const uint8_t function_icons[] = {
            BOARD_ROUND_PAGE_ICON_TIMER,
            BOARD_ROUND_PAGE_ICON_SETTINGS,
            BOARD_MIST_ICON_BATTERY,
        };
        render_mist_carousel(function_icons, ARRAY_COUNT(function_icons), s_function_cursor);
    } else if (s_local_page == LOCAL_PAGE_BATTERY) {
        render_mist_battery();
    } else if (s_local_page == LOCAL_PAGE_SETTINGS) {
        static const uint8_t settings_icons[] = {
            BOARD_ROUND_PAGE_ICON_SYSTEM,
            BOARD_ROUND_PAGE_ICON_HAPTIC,
            BOARD_ROUND_PAGE_ICON_LIGHTING,
            BOARD_ROUND_PAGE_ICON_RESTART,
        };
        render_mist_carousel(settings_icons, ARRAY_COUNT(settings_icons), s_settings_cursor);
    } else if (s_local_page == LOCAL_PAGE_SYSTEM) {
        const bool windows = s_system_selection == CONFIG_PLATFORM_WINDOWS_LINUX;
        static const uint8_t system_icons[] = {
            BOARD_ROUND_PAGE_ICON_MACOS, BOARD_ROUND_PAGE_ICON_WINDOWS,
        };
        if (s_mist_read_index > 0) {
            const board_mist_view_t view = {
                .page = BOARD_MIST_SYSTEM_TEXT, .a = windows,
                .phase = 1, .read_index = s_mist_read_index,
                .footer = s_system_save_pending ? "RELEASE CONTROLS" : "PRESS TO CONFIRM",
            };
            (void)board_display_show_mist(&view);
        } else {
            render_mist_carousel(system_icons, ARRAY_COUNT(system_icons), windows ? 1u : 0u);
        }
    } else if (s_local_page == LOCAL_PAGE_RESTART_CONFIRM) {
        (void)board_display_show_round_icon_choice(
            BOARD_ROUND_PAGE_ICON_BACK, BOARD_ROUND_PAGE_ICON_RESTART,
            s_local_confirm_selected ? 1 : 0);
    } else if (s_local_page == LOCAL_PAGE_TIMER_SETUP ||
               s_local_page == LOCAL_PAGE_TIMER_DETAIL ||
               s_local_page == LOCAL_PAGE_TIMER_CANCEL) {
        render_mist_timer();
    } else if (s_local_page == LOCAL_PAGE_PROMPT_PALETTE) {
        (void)board_display_show_round_prompt_palette(
            s_prompt_palette_selected_id);
    }
#else
    char value[20];
    if (s_local_page == LOCAL_PAGE_FUNCTION) {
        (void)board_display_show_two_choice(
            "FUNCTION CENTER", "POMODORO", "SETTINGS",
            s_function_cursor, "TURN PRESS SELECT", 0x001F);
    } else if (s_local_page == LOCAL_PAGE_SETTINGS) {
        static const char *const settings_names[] = {
            "HAPTIC", "LIGHTING", "RESTART",
        };
        (void)board_display_show_local(
            "SETTINGS", settings_names[s_settings_cursor],
            "TURN TO SELECT", "PRESS TO OPEN", 0x001F);
    } else if (s_local_page == LOCAL_PAGE_RESTART_CONFIRM) {
        (void)board_display_show_two_choice(
            "RESTART DEVICE", "CANCEL", "RESTART",
            s_local_confirm_selected ? 1 : 0, "PRESS CONFIRM", 0xFD20);
    } else if (s_local_page == LOCAL_PAGE_TIMER_SETUP) {
        snprintf(value, sizeof(value), "%02u:00",
                 (unsigned)s_pomodoro.minutes);
        (void)board_display_show_local("POMODORO", value,
                                       "TURN SET MINUTES",
                                       "PRESS START", 0x07E0);
    } else if (s_local_page == LOCAL_PAGE_TIMER_DETAIL) {
        format_timer(value);
        (void)board_display_show_local(
            s_pomodoro.state == POMODORO_PAUSED ? "PAUSED" : "FOCUS",
            value,
            s_pomodoro.state == POMODORO_PAUSED ? "PRESS RESUME"
                                                : "PRESS PAUSE",
            "HOLD KNOB CANCEL", s_pomodoro.state == POMODORO_PAUSED
                                      ? 0xFD20 : 0x07E0);
    } else if (s_local_page == LOCAL_PAGE_TIMER_CANCEL) {
        (void)board_display_show_two_choice(
            "CANCEL TIMER", "NO", "YES", s_cancel_yes ? 1 : 0,
            "PRESS CONFIRM", 0xF800);
    } else if (s_local_page == LOCAL_PAGE_PROMPT_PALETTE) {
        snprintf(value, sizeof(value), "PROMPT %u",
                 (unsigned)s_prompt_palette_selected_id);
        (void)board_display_show_local(
            "QUICK PROMPTS", value, "JOYSTICK SELECT",
            "KNOB CONFIRM", 0xFD20);
    }
#endif
}

static void clear_pending_gestures(bool replay)
{
    const uint8_t encoder = double_click_model_flush(&s_encoder_gesture);
    const uint8_t joystick = double_click_model_flush(&s_joystick_gesture);
    if (replay) {
        handle_double_click_actions(BOARD_CONTROL_ENCODER_PRESS, encoder);
        handle_double_click_actions(BOARD_CONTROL_JOYSTICK_PRESS, joystick);
    } else {
        handle_double_click_actions(
            BOARD_CONTROL_ENCODER_PRESS,
            encoder & DOUBLE_CLICK_ACTION_HOLD_RELEASE);
        handle_double_click_actions(
            BOARD_CONTROL_JOYSTICK_PRESS,
            joystick & DOUBLE_CLICK_ACTION_HOLD_RELEASE);
    }
    s_encoder_motion_count = 0;
    s_joystick_excursions = 0;
    s_joystick_direction_active = false;
}

static void show_function_center(uint8_t selected_index)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    s_mist_read_index = 0;
#endif
    s_local_page = LOCAL_PAGE_FUNCTION;
    s_function_cursor = selected_index;
    s_local_last_input_at = xTaskGetTickCount();
    render_local_page();
}

static void enter_function_center(void)
{
    enter_local_input_ownership();
    clear_pending_gestures(false);
    s_quick_config_active = false;
    s_quick_config_pending_footer = NULL;
    show_function_center(0);
}

static void enter_prompt_palette(void)
{
    enter_local_input_ownership();
    clear_pending_gestures(false);
    s_quick_config_active = false;
    s_quick_config_pending_footer = NULL;
    s_prompt_palette_selected_id = 1u;
    s_local_page = LOCAL_PAGE_PROMPT_PALETTE;
    s_local_last_input_at = xTaskGetTickCount();
    render_local_page();
}

static void enter_os_settings(bool from_settings)
{
    enter_local_input_ownership();
    clear_pending_gestures(false);
    s_quick_config_active = false;
    s_quick_config_pending_footer = NULL;
    s_encoder_pressed = false;
    s_platform_setup_needed = false;
    s_system_save_pending = false;
    s_system_return_to_settings = from_settings;
    const config_platform_t saved = config_store_get_platform();
    s_system_selection = saved == CONFIG_PLATFORM_WINDOWS_LINUX
        ? CONFIG_PLATFORM_WINDOWS_LINUX : CONFIG_PLATFORM_MACOS;
    s_local_page = LOCAL_PAGE_SYSTEM;
    s_local_last_input_at = xTaskGetTickCount();
    render_local_page();
}

static void finish_os_settings_save(void)
{
    s_system_save_pending = false;
    pulse_save_feedback();
    s_encoder_pressed = false;
    s_local_last_input_at = xTaskGetTickCount();
    if (s_system_return_to_settings) {
        s_local_page = LOCAL_PAGE_SETTINGS;
        s_settings_cursor = 0;
        render_local_page();
    } else {
        s_local_page = LOCAL_PAGE_NONE;
        leave_local_input_ownership();
        show_status(NULL);
    }
}

static void exit_local_page(void)
{
    s_local_page = LOCAL_PAGE_NONE;
    s_encoder_pressed = false;
    leave_local_input_ownership();
    show_status(NULL);
}

static bool local_ui_home_shortcut_active(void)
{
    return s_local_page != LOCAL_PAGE_NONE || s_quick_config_active;
}

static bool local_ui_home_shortcut_blocked(void)
{
    return s_system_save_pending ||
           (s_quick_config_active && s_quick_config_pending_footer != NULL);
}

static void exit_local_ui_to_home(void)
{
    s_cancel_yes = false;
    if (s_quick_config_active) {
        exit_quick_config();
    } else {
        if (s_local_page == LOCAL_PAGE_TIMER_SETUP) {
            pomodoro_model_set_minutes(
                &s_pomodoro, config_store_get_pomodoro_minutes());
        }
        exit_local_page();
    }
}

static void return_local_ui_one_level(void)
{
    s_cancel_yes = false;
    s_local_last_input_at = xTaskGetTickCount();

    if (s_quick_config_active) {
        clear_lighting_preview();
        s_quick_config_active = false;
        s_quick_config_pending_footer = NULL;
        s_local_page = LOCAL_PAGE_SETTINGS;
        render_local_page();
        return;
    }

    if (s_local_page == LOCAL_PAGE_PROMPT_PALETTE) {
        exit_local_page();
    } else if (s_local_page == LOCAL_PAGE_BATTERY) {
        show_function_center(2);
    } else if (s_local_page == LOCAL_PAGE_FUNCTION) {
        exit_local_page();
    } else if (s_local_page == LOCAL_PAGE_SETTINGS) {
        s_local_page = LOCAL_PAGE_FUNCTION;
        s_function_cursor = 1;
        render_local_page();
    } else if (s_local_page == LOCAL_PAGE_SYSTEM) {
        if (s_system_return_to_settings) {
            s_local_page = LOCAL_PAGE_SETTINGS;
            s_settings_cursor = 0;
            render_local_page();
        } else {
            exit_local_page();
        }
    } else if (s_local_page == LOCAL_PAGE_RESTART_CONFIRM) {
        s_local_page = LOCAL_PAGE_SETTINGS;
        render_local_page();
    } else if (s_local_page == LOCAL_PAGE_TIMER_CANCEL) {
        s_local_page = LOCAL_PAGE_TIMER_DETAIL;
        render_local_page();
    } else if (s_local_page == LOCAL_PAGE_TIMER_SETUP) {
        pomodoro_model_set_minutes(
            &s_pomodoro, config_store_get_pomodoro_minutes());
        s_local_page = LOCAL_PAGE_FUNCTION;
        s_function_cursor = 0;
        render_local_page();
    } else if (s_local_page == LOCAL_PAGE_TIMER_DETAIL) {
        s_local_page = LOCAL_PAGE_FUNCTION;
        s_function_cursor = 0;
        render_local_page();
    } else {
        exit_local_page();
    }
}

static void confirm_local_page(void)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    /* The business selection is already the target, even mid-transition. */
    mist_cancel_motion();
    s_mist_read_index = 0;
#endif
    if (s_local_page == LOCAL_PAGE_SYSTEM) {
        if (s_system_save_pending) {
            return;
        }
        const esp_err_t error = config_store_select_platform(s_system_selection);
        if (error != ESP_OK) {
            show_feedback(STATUS_FEEDBACK_ERROR,
                          error == ESP_ERR_NOT_FOUND ? "NO OS PROFILE" : "OS SAVE ERROR");
            return;
        }
        s_system_save_pending = config_store_has_pending();
        if (s_system_save_pending) {
            render_local_page();
        } else {
            finish_os_settings_save();
        }
    } else if (s_local_page == LOCAL_PAGE_PROMPT_PALETTE) {
        const esp_err_t error =
            prompt_store_queue_trigger(s_prompt_palette_selected_id);
        s_local_page = LOCAL_PAGE_NONE;
        leave_local_input_ownership();
        if (error == ESP_OK) {
            pulse_save_feedback();
            show_feedback(STATUS_FEEDBACK_SUCCESS, "PROMPT QUEUED");
        } else if (error == ESP_ERR_NOT_FOUND) {
            show_feedback(STATUS_FEEDBACK_WARNING, "PROMPT EMPTY");
        } else {
            show_feedback(STATUS_FEEDBACK_ERROR, "PROMPT HELPER OFFLINE");
        }
    } else if (s_local_page == LOCAL_PAGE_FUNCTION) {
        if (s_function_cursor == 2) {
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
            s_local_page = LOCAL_PAGE_BATTERY;
            fuel_gauge_request_immediate_poll();
            render_local_page();
#endif
        } else if (s_function_cursor == 1) {
            s_local_page = LOCAL_PAGE_SETTINGS;
            s_settings_cursor = 0;
            render_local_page();
        } else if (s_pomodoro.state == POMODORO_RUNNING ||
                   s_pomodoro.state == POMODORO_PAUSED) {
            s_local_page = LOCAL_PAGE_TIMER_DETAIL;
            render_local_page();
        } else {
            s_local_page = LOCAL_PAGE_TIMER_SETUP;
            s_local_confirm_selected = true;
            render_local_page();
        }
    } else if (s_local_page == LOCAL_PAGE_SETTINGS) {
        uint8_t setting = s_settings_cursor;
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
        if (setting == 0u) {
            enter_os_settings(true);
            return;
        }
        --setting;
#endif
        if (setting == 0) {
            s_local_page = LOCAL_PAGE_NONE;
            enter_quick_config(QUICK_CONFIG_ITEM_HAPTIC);
        } else if (setting == 1) {
            s_local_page = LOCAL_PAGE_NONE;
            enter_quick_config(QUICK_CONFIG_ITEM_LIGHTING);
        } else {
            s_local_page = LOCAL_PAGE_RESTART_CONFIRM;
            s_local_confirm_selected = false;
            render_local_page();
        }
    } else if (s_local_page == LOCAL_PAGE_RESTART_CONFIRM) {
        if (s_local_confirm_selected) {
            action_engine_request_restart();
        } else {
            s_local_page = LOCAL_PAGE_SETTINGS;
            render_local_page();
        }
    } else if (s_local_page == LOCAL_PAGE_TIMER_SETUP) {
        const uint32_t now_ms = ticks_to_ms(xTaskGetTickCount());
        pomodoro_model_start(&s_pomodoro, now_ms);
        s_last_timer_display_second =
            (pomodoro_model_remaining_ms(&s_pomodoro, now_ms) + 999U) / 1000U;
        const esp_err_t saved =
            config_store_set_pomodoro_minutes(s_pomodoro.minutes);
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
        s_local_page = LOCAL_PAGE_TIMER_DETAIL;
        s_mist_peek_active = false;
        pulse_save_feedback();
        render_local_page();
        if (saved != ESP_OK) {
            show_feedback(STATUS_FEEDBACK_ERROR, "TIMER SAVE ERROR");
        }
#else
        s_local_page = LOCAL_PAGE_NONE;
        leave_local_input_ownership();
        pulse_save_feedback();
        show_feedback(saved == ESP_OK ? STATUS_FEEDBACK_PLAY
                                     : STATUS_FEEDBACK_ERROR,
                      saved == ESP_OK ? "TIMER STARTED"
                                      : "TIMER SAVE ERROR");
#endif
    } else if (s_local_page == LOCAL_PAGE_TIMER_DETAIL) {
        const uint32_t now_ms = ticks_to_ms(xTaskGetTickCount());
        if (s_pomodoro.state == POMODORO_RUNNING) {
            (void)pomodoro_model_pause(&s_pomodoro, now_ms);
        } else {
            (void)pomodoro_model_resume(&s_pomodoro, now_ms);
        }
        render_local_page();
    } else if (s_local_page == LOCAL_PAGE_TIMER_CANCEL) {
        if (s_cancel_yes) {
            cancel_codex_attention_haptic();
            pomodoro_model_cancel(&s_pomodoro);
            s_timer_done_hint = false;
            s_local_page = LOCAL_PAGE_NONE;
            leave_local_input_ownership();
            (void)board_haptic_stop();
            show_feedback(STATUS_FEEDBACK_CANCEL, "TIMER CANCELED");
        } else {
            s_local_page = LOCAL_PAGE_TIMER_DETAIL;
            render_local_page();
        }
    }
}

static void select_local_action(bool confirm_selected)
{
    if ((s_local_page != LOCAL_PAGE_TIMER_SETUP &&
         s_local_page != LOCAL_PAGE_RESTART_CONFIRM) ||
        s_local_confirm_selected == confirm_selected) {
        return;
    }
    s_local_confirm_selected = confirm_selected;
    pulse_navigation_feedback(BOARD_CONTROL_JOYSTICK_RIGHT);
    render_local_page();
}

static void activate_local_action(void)
{
    if ((s_local_page == LOCAL_PAGE_TIMER_SETUP ||
         s_local_page == LOCAL_PAGE_RESTART_CONFIRM) &&
        !s_local_confirm_selected) {
        if (s_local_page == LOCAL_PAGE_RESTART_CONFIRM) {
            s_local_page = LOCAL_PAGE_SETTINGS;
            render_local_page();
        } else {
            exit_local_page();
        }
        return;
    }
    confirm_local_page();
}

#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
static bool handle_mist_read_event(const board_event_t *event)
{
    const bool readable = s_local_page == LOCAL_PAGE_SYSTEM ||
        (s_quick_config_active && !round_level_slice_active());
    if (!readable) return false;
    const bool vertical = event->control == BOARD_CONTROL_JOYSTICK_UP ||
                          event->control == BOARD_CONTROL_JOYSTICK_DOWN;
    if (s_mist_read_index == 0 && !vertical) return false;
    s_local_last_input_at = xTaskGetTickCount();
    bool redraw = false;
    if (event->pressed && vertical) {
        if (event->control == BOARD_CONTROL_JOYSTICK_DOWN) {
            ++s_mist_read_index;
        } else if (s_mist_read_index > 0) {
            --s_mist_read_index;
        }
        redraw = true;
    } else if ((!event->pressed && event->control == BOARD_CONTROL_ENCODER_PRESS) ||
               (event->pressed && (encoder_rotation_control(event->control) ||
                                  joystick_direction_control(event->control)))) {
        /* Leave the read-only details first; this event never edits or applies. */
        s_mist_read_index = 0;
        redraw = true;
    }
    if (redraw) {
        if (s_quick_config_active) render_quick_config(s_quick_config_pending_footer);
        else render_local_page();
    }
    return true;
}
#endif

static void handle_local_event(const board_event_t *event)
{
    if (s_system_save_pending) {
        return;
    }
    s_local_last_input_at = xTaskGetTickCount();
    if (event->control == BOARD_CONTROL_ENCODER_PRESS) {
        if (event->pressed) {
            s_encoder_pressed = true;
            s_encoder_pressed_at = xTaskGetTickCount();
        } else if (s_encoder_pressed) {
            s_encoder_pressed = false;
            const TickType_t held =
                xTaskGetTickCount() - s_encoder_pressed_at;
            if (held >= pdMS_TO_TICKS(LOCAL_LONG_PRESS_MS)) {
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
                exit_local_page();
#else
                if (s_local_page == LOCAL_PAGE_TIMER_DETAIL) {
                    s_local_page = LOCAL_PAGE_TIMER_CANCEL;
                    s_cancel_yes = false;
                    render_local_page();
                } else {
                    exit_local_page();
                }
#endif
            } else {
                activate_local_action();
            }
        }
        return;
    }
    if (event->control == BOARD_CONTROL_JOYSTICK_PRESS) {
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
        if (!event->pressed && s_local_page == LOCAL_PAGE_TIMER_DETAIL) {
            confirm_local_page();
        }
#endif
        return;
    }
    if (event->control == BOARD_CONTROL_JOYSTICK_DOWN && event->pressed &&
        s_local_page == LOCAL_PAGE_RESTART_CONFIRM) {
        exit_local_page();
        return;
    }
    if (!event->pressed) {
        return;
    }
    if (s_local_page == LOCAL_PAGE_PROMPT_PALETTE) {
        uint8_t prompt_id = 0u;
        if (event->control >= BOARD_CONTROL_JOYSTICK_UP &&
            event->control <= BOARD_CONTROL_JOYSTICK_RIGHT) {
            float angle_turns = 0.0f;
            if (board_get_joystick_radial(&angle_turns)) {
                if (angle_turns < 0.125f || angle_turns >= 0.875f) {
                    prompt_id = 2u;
                } else if (angle_turns < 0.375f) {
                    prompt_id = 3u;
                } else if (angle_turns < 0.625f) {
                    prompt_id = 4u;
                } else {
                    prompt_id = 1u;
                }
            } else {
                switch (event->control) {
                case BOARD_CONTROL_JOYSTICK_UP:
                    prompt_id = 1u;
                    break;
                case BOARD_CONTROL_JOYSTICK_RIGHT:
                    prompt_id = 2u;
                    break;
                case BOARD_CONTROL_JOYSTICK_DOWN:
                    prompt_id = 3u;
                    break;
                case BOARD_CONTROL_JOYSTICK_LEFT:
                    prompt_id = 4u;
                    break;
                default:
                    break;
                }
            }
        }
        if (prompt_id != 0u &&
            prompt_id != s_prompt_palette_selected_id) {
            s_prompt_palette_selected_id = prompt_id;
            pulse_navigation_feedback_for_control(event->control);
            render_local_page();
        }
        return;
    }
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    if (s_local_page == LOCAL_PAGE_TIMER_DETAIL &&
        event->control == BOARD_CONTROL_JOYSTICK_LEFT) {
        s_local_page = LOCAL_PAGE_TIMER_CANCEL;
        s_cancel_yes = false;
        pulse_navigation_feedback(event->control);
        render_local_page();
        return;
    }
    if (s_local_page == LOCAL_PAGE_TIMER_DETAIL &&
        (encoder_rotation_control(event->control) ||
         joystick_direction_control(event->control))) {
        s_mist_peek_active = true;
        s_mist_peek_until_ms = ticks_to_ms(xTaskGetTickCount()) + 2000U;
        render_mist_timer();
        return;
    }
    if (s_local_page == LOCAL_PAGE_TIMER_SETUP &&
        (event->control == BOARD_CONTROL_JOYSTICK_LEFT ||
         event->control == BOARD_CONTROL_JOYSTICK_RIGHT)) {
        if (event->control == BOARD_CONTROL_JOYSTICK_LEFT) {
            select_local_action(false);
        } else {
            select_local_action(true);
        }
        return;
    }
    if (s_local_page == LOCAL_PAGE_RESTART_CONFIRM &&
        (event->control == BOARD_CONTROL_JOYSTICK_LEFT ||
         event->control == BOARD_CONTROL_JOYSTICK_RIGHT)) {
        select_local_action(event->control == BOARD_CONTROL_JOYSTICK_RIGHT);
        return;
    }
#endif
    const bool navigation_feedback_due =
        navigation_feedback_due_for_control(event->control);
    int delta = 0;
    if (event->control == BOARD_CONTROL_ENCODER_CCW ||
        event->control == BOARD_CONTROL_JOYSTICK_LEFT) {
        delta = -1;
    } else if (event->control == BOARD_CONTROL_ENCODER_CW ||
               event->control == BOARD_CONTROL_JOYSTICK_RIGHT) {
        delta = 1;
    } else {
        return;
    }
    bool changed = false;
    if (s_local_page == LOCAL_PAGE_FUNCTION) {
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
        const uint8_t count = 3;
#else
        const uint8_t count = 2;
#endif
        const uint8_t cursor = encoder_rotation_control(event->control)
            ? wrap_discrete_selection(s_function_cursor, count, delta)
            : (delta > 0 && s_function_cursor + 1u < count ? s_function_cursor + 1u
               : delta < 0 && s_function_cursor > 0 ? s_function_cursor - 1u
               : s_function_cursor);
        changed = cursor != s_function_cursor;
        s_function_cursor = cursor;
    } else if (s_local_page == LOCAL_PAGE_SETTINGS) {
        const uint8_t previous = s_settings_cursor;
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
        const uint8_t settings_count = 4u;
#else
        const uint8_t settings_count = 3u;
#endif
        if (encoder_rotation_control(event->control)) {
            s_settings_cursor =
                wrap_discrete_selection(s_settings_cursor, settings_count, delta);
        } else if (delta > 0 && s_settings_cursor + 1u < settings_count) {
            ++s_settings_cursor;
        } else if (delta < 0 && s_settings_cursor > 0) {
            --s_settings_cursor;
        }
        changed = previous != s_settings_cursor;
    } else if (s_local_page == LOCAL_PAGE_SYSTEM) {
        const uint8_t previous = s_system_selection == CONFIG_PLATFORM_WINDOWS_LINUX;
        const uint8_t selected = encoder_rotation_control(event->control)
            ? wrap_discrete_selection(previous, 2u, delta) : (delta > 0 ? 1u : 0u);
        s_system_selection = selected == 0u ? CONFIG_PLATFORM_MACOS : CONFIG_PLATFORM_WINDOWS_LINUX;
        changed = previous != selected;
    } else if (s_local_page == LOCAL_PAGE_TIMER_SETUP) {
        if (event->control != BOARD_CONTROL_ENCODER_CCW &&
            event->control != BOARD_CONTROL_ENCODER_CW) {
            return;
        }
        const uint8_t minutes = s_pomodoro.minutes;
        pomodoro_model_set_minutes(&s_pomodoro,
                                   (int)s_pomodoro.minutes + delta);
        changed = minutes != s_pomodoro.minutes;
    } else if (s_local_page == LOCAL_PAGE_TIMER_CANCEL) {
        const bool cancel_yes = encoder_rotation_control(event->control)
            ? wrap_discrete_selection((uint8_t)s_cancel_yes, 2, delta) != 0u
            : delta > 0;
        changed = cancel_yes != s_cancel_yes;
        s_cancel_yes = cancel_yes;
    } else if (s_local_page == LOCAL_PAGE_RESTART_CONFIRM) {
        const bool restart = encoder_rotation_control(event->control)
            ? wrap_discrete_selection(
                (uint8_t)s_local_confirm_selected, 2, delta) != 0u
            : delta > 0;
        changed = restart != s_local_confirm_selected;
        s_local_confirm_selected = restart;
    }
    if (changed) {
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
        s_mist_read_index = 0;
        s_mist_navigation_delta = delta;
#endif
        if (navigation_feedback_due) {
            pulse_navigation_feedback(event->control);
        }
        render_local_page();
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
        s_mist_navigation_delta = 0;
#endif
    }
}

static void poll_pomodoro(void)
{
    const uint8_t events = pomodoro_model_poll(
        &s_pomodoro, ticks_to_ms(xTaskGetTickCount()));
    if ((events & POMODORO_EVENT_EXPIRED) != 0) {
        light_idle_note_activity();
        cancel_codex_attention_haptic();
        enter_local_input_ownership();
        clear_pending_gestures(false);
        s_local_page = LOCAL_PAGE_NONE;
        s_quick_config_active = false;
        s_alert_ack_armed = false;
        s_timer_done_hint = false;
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
        mist_cancel_motion();
        s_boot_animation_active = false;
        s_round_feedback_active = false;
        render_mist_timer();
#else
        (void)board_display_show_local("TIME UP", "POMODORO DONE",
                                       "PRESS ANY CONTROL", "MAX 60 SECONDS",
                                       0xF800);
#endif
    }
    if ((events & POMODORO_EVENT_ALERT_PULSE) != 0 &&
        s_feedback.haptic_enabled && s_feedback.haptic_on_task) {
        (void)board_haptic_pulse(ALERT_HAPTIC_STRENGTH,
                                 ALERT_HAPTIC_DURATION_MS);
    }
    if ((events & POMODORO_EVENT_ALERT_FINISHED) != 0) {
        (void)board_haptic_stop();
        s_timer_done_hint = true;
        leave_local_input_ownership();
        show_status(NULL);
    }
    if (s_pomodoro.state == POMODORO_ALERT && !s_alert_ack_armed &&
        board_keys_neutral()) {
        s_alert_ack_armed = true;
    }
}

static void poll_codex_attention_notification(void)
{
    static bool previous_claude;
    static bool previous_desktop;
    static uint32_t previous_desktop_reset;
    const bool claude = codex_micro_mode() == CODEX_MICRO_MODE_CLAUDE_CODE;
    claude_status_model_t snapshot = {0};
    if (claude) {
        claude_status_get(&snapshot);
    }
    if (claude != previous_claude || (claude && !snapshot.active)) {
        cancel_codex_attention_haptic();
    }
    previous_claude = claude;
    desktop_agent_model_t desktop;
    desktop_agent_status_get(&desktop);
    const bool desktop_owned =
        codex_micro_mode() == CODEX_MICRO_MODE_NORMAL && desktop.claimed;
    /* A lease ending or restarting cancels its unfinished reminder. */
    if (desktop_owned != previous_desktop ||
        (desktop_owned && desktop.reset_sequence != previous_desktop_reset)) {
        cancel_codex_attention_haptic();
    }
    previous_desktop = desktop_owned;
    previous_desktop_reset = desktop.reset_sequence;
    const bool desktop_visual_changed =
        desktop_owned && desktop_agent_status_take_visual_change(true);
    const codex_micro_attention_t notifications =
        codex_micro_take_attention_notification();
    /* Visible reminders and real desktop state changes wake the lighting even
     * when the motor channel is off; heartbeats alone do not. */
    if (desktop_visual_changed || notifications != CODEX_ATTENTION_NONE) {
        if (s_idle_standby.active) {
            wake_from_idle_standby();
        }
        light_idle_note_activity();
    }
    const bool enabled =
        (claude ? snapshot.active
                : desktop_owned ? desktop.active || codex_micro_connected()
                                : codex_micro_connected()) &&
        !s_shutdown_active && !s_usb_standby_active &&
        !s_battery_splash_active &&
        !diagnostic_capture_model_owns_input(&s_diagnostic_capture) &&
        s_feedback.haptic_enabled && s_feedback.haptic_on_task &&
        s_feedback.haptic_strength > 0 &&
        s_pomodoro.state != POMODORO_ALERT &&
        !(s_quick_config_active &&
          s_quick_config.item == QUICK_CONFIG_ITEM_HAPTIC);
    const codex_attention_haptic_event_t event =
        codex_attention_haptic_model_poll(
            &s_codex_attention_haptic, ticks_to_ms(xTaskGetTickCount()),
            notifications, enabled);
    if (event == CODEX_ATTENTION_HAPTIC_PULSE) {
        if (s_idle_standby.active) {
            wake_from_idle_standby();
        }
        idle_standby_model_note_activity(
            &s_idle_standby, ticks_to_ms(xTaskGetTickCount()));
        (void)board_haptic_pulse(s_feedback.haptic_strength,
                                 CODEX_ATTENTION_HAPTIC_DURATION_MS);
    } else if (event == CODEX_ATTENTION_HAPTIC_STOP) {
        (void)board_haptic_stop();
    }
}

static void poll_joystick_excursion_haptic(void)
{
    const codex_micro_mode_t mode = codex_micro_mode();
    const bool supported_mode =
        mode == CODEX_MICRO_MODE_NORMAL ||
        mode == CODEX_MICRO_MODE_CODEX ||
        mode == CODEX_MICRO_MODE_CLAUDE_CODE;
    const bool owns_radial_input =
        s_host_output_state == HOST_OUTPUT_ENABLED &&
        !s_shutdown_active && !s_usb_standby_active &&
        !s_battery_splash_active && !s_idle_standby.active &&
        !diagnostic_capture_model_owns_input(&s_diagnostic_capture) &&
        !s_system_select_active && !s_quick_config_active &&
        s_local_page == LOCAL_PAGE_NONE &&
        s_pomodoro.state != POMODORO_ALERT &&
        codex_micro_active_key_layout() ==
            CODEX_MICRO_KEY_LAYOUT_MATRIX12 &&
        supported_mode;
    float angle_turns = 0.0f;
    const bool radial_active =
        owns_radial_input && board_get_joystick_radial(&angle_turns);
    const bool start_haptic = joystick_radial_haptic_model_update(
        &s_joystick_radial_haptic, radial_active);
    if (start_haptic &&
        s_feedback.haptic_enabled &&
        s_feedback.haptic_on_joystick &&
        !s_codex_attention_haptic.active) {
        (void)board_haptic_pulse(s_feedback.haptic_strength,
                                 JOYSTICK_EXCURSION_HAPTIC_MS);
    }
}

static board_control_t function_center_control(void)
{
    return codex_micro_active_key_layout() ==
                   CODEX_MICRO_KEY_LAYOUT_MATRIX12
               ? BOARD_CONTROL_KEY_3
               : BOARD_CONTROL_KEY_7;
}

/*
 * One key does all of Bluetooth (2026-11-12): K8 while K3 is held.
 * The user never picks a host number; K9 and K10 are ordinary keys again.
 */
static bool ble_host_key_control(board_control_t control)
{
    return control == BOARD_CONTROL_KEY_8;
}

static void reset_function_key_gesture(void)
{
    s_function_key_gesture_active = false;
    s_function_key_gesture_combo = false;
    s_function_key_center_opened = false;
    s_function_key_local_navigation = false;
    s_function_key_action_pressed = false;
    s_function_key_press_feedback_sent = false;
}

static void reset_prompt_palette_key_gesture(void)
{
    s_prompt_palette_key_gesture_active = false;
    s_prompt_palette_key_action_pressed = false;
}

static board_round_ble_slot_state_t ble_slot_display_state(uint8_t slot)
{
    if (codex_micro_ble_slot_connected(slot)) {
        return BOARD_ROUND_BLE_SLOT_CONNECTED;
    }
    return codex_micro_ble_slot_paired(slot)
               ? BOARD_ROUND_BLE_SLOT_PAIRED
               : BOARD_ROUND_BLE_SLOT_EMPTY;
}

static void show_ble_slot(uint8_t slot)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    (void)board_display_show_round_ble_slot(slot,
                                            ble_slot_display_state(slot));
    start_round_feedback();
#else
    (void)slot;
#endif
}

static void show_ble_slot_progress(uint8_t slot, uint8_t progress_percent)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    (void)board_display_show_round_ble_slot_progress(
        slot, ble_slot_display_state(slot), progress_percent);
    start_round_feedback();
#else
    (void)slot;
    (void)progress_percent;
#endif
}

/* The round text page leaves an empty bottom row blank and rejects NULL. */
#define BLE_HOST_BLANK_ROW ""
/* How the pages name the Bluetooth key. No page names a host number. Wording
 * is interim (2026-11-12); the designer's replaces
 * it in a later build. */
#define BLE_HOST_KEY_NAME "K3+K8"

/* Waiting-to-pair page; with a computer to go back to, the key that does it. */
static void show_ble_pairing_window(bool can_go_back)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    s_mist_connection_visible = false;
    start_round_feedback();
    (void)board_display_show_local(
        "PAIRING", "NEW PC", can_go_back ? BLE_HOST_KEY_NAME : "",
        can_go_back ? "BACK" : BLE_HOST_BLANK_ROW, 0);
#else
    (void)can_go_back;
#endif
}

/* Bluetooth went back to the computer it was on before the pairing window. */
static void show_ble_back_page(void)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    s_mist_connection_visible = false;
    start_round_feedback();
    (void)board_display_show_local("BACK", "LAST PC", "RESTORED",
                                   BLE_HOST_BLANK_ROW, 0);
#endif
}

/* Three computers are remembered: one has to be forgotten in the app. */
static void show_ble_hosts_full(void)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    s_mist_connection_visible = false;
    start_round_feedback();
    (void)board_display_show_local("NEW PC", "FULL", "FORGET 1", "IN APP", 0);
#endif
}

/* After the key selected a slot: the computer's page, or waiting to pair. */
static void show_ble_host_selected(uint8_t slot)
{
    uint8_t return_slot = 0u;
    if (codex_micro_ble_pairing_window(NULL, &return_slot)) {
        show_ble_pairing_window(return_slot != 0u);
    } else if (!codex_micro_ble_slot_paired(slot)) {
        /* No computer is remembered yet, so there is nowhere to go back to. */
        show_ble_pairing_window(false);
    } else {
        show_ble_slot(slot);
    }
}

typedef enum {
    BLE_HOST_HINT_REDIRECT,
    BLE_HOST_HINT_NEW_HOST,
    BLE_HOST_HINT_REAUTH,
    BLE_HOST_HINT_COUNT,
} ble_host_hint_t;

static struct {
    bool shown;
    uint8_t slot;
    TickType_t shown_at;
} s_ble_host_hints[BLE_HOST_HINT_COUNT];

/*
 * A refused computer retries about every two seconds.  Each hint (its kind
 * and the host number it names) shows once per BLE_HOST_HINT_REPEAT_MS, and
 * never while the pairing window is open, where it would cover the
 * waiting-to-pair page.
 */
static bool ble_host_hint_due(ble_host_hint_t hint, uint8_t slot)
{
    if (codex_micro_ble_pairing_window(NULL, NULL)) {
        return false;
    }
    const TickType_t now = xTaskGetTickCount();
    if (s_ble_host_hints[hint].shown && s_ble_host_hints[hint].slot == slot &&
        now - s_ble_host_hints[hint].shown_at <
            pdMS_TO_TICKS(BLE_HOST_HINT_REPEAT_MS)) {
        return false;
    }
    s_ble_host_hints[hint].shown = true;
    s_ble_host_hints[hint].slot = slot;
    s_ble_host_hints[hint].shown_at = now;
    return true;
}

static void poll_ble_slot_redirect(void)
{
    if (s_boot_animation_active || s_battery_splash_active) {
        return;
    }
    uint8_t slot = 0u;
    if (!codex_micro_take_ble_slot_redirect(&slot) ||
        !ble_host_hint_due(BLE_HOST_HINT_REDIRECT, slot)) {
        return;
    }
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    s_mist_connection_visible = false;
    start_round_feedback();
    (void)board_display_show_local("SWITCH", "TAP " BLE_HOST_KEY_NAME, "",
                                   "PAIRED", 0);
#endif
}

/*
 * A computer this device does not know was refused.  Point at the key hold
 * that adds it, or at the desktop app when three are already remembered.
 */
static void poll_ble_peer_rejection(void)
{
    if (s_boot_animation_active || s_battery_splash_active) {
        return;
    }
    uint8_t slot = 0u;
    if (!codex_micro_take_ble_peer_rejection(&slot)) {
        return;
    }
    const uint8_t free_slot = codex_micro_ble_free_slot();
    if (!ble_host_hint_due(BLE_HOST_HINT_NEW_HOST, free_slot)) {
        return;
    }
    if (free_slot == 0u) {
        show_ble_hosts_full();
        return;
    }
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    s_mist_connection_visible = false;
    start_round_feedback();
    (void)board_display_show_local("NEW PC", "HOLD " BLE_HOST_KEY_NAME,
                                   "TO ADD", BLE_HOST_BLANK_ROW, 0);
#endif
}

/*
 * The slot's own host failed to encrypt: its keys no longer match ours.  The
 * way out is on the computer: remove this device there, then connect again.
 */
static void poll_ble_reauth_needed(void)
{
    if (s_boot_animation_active || s_battery_splash_active) {
        return;
    }
    uint8_t slot = 0u;
    if (!codex_micro_take_ble_reauth_needed(&slot) ||
        !ble_host_hint_due(BLE_HOST_HINT_REAUTH, slot)) {
        return;
    }
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    s_mist_connection_visible = false;
    start_round_feedback();
    (void)board_display_show_local("RE-PAIR", "THIS PC", "FORGET ME", "ON PC",
                                   0);
#endif
}

/* The pairing window ran out and Bluetooth went back to the remembered host. */
static void poll_ble_pairing_return(void)
{
    if (s_boot_animation_active || s_battery_splash_active) {
        return;
    }
    uint8_t slot = 0u;
    if (!codex_micro_take_ble_pairing_return(&slot)) {
        return;
    }
    show_ble_back_page();
}

/* Where a new computer goes: the active slot while it is empty (a pairing
 * window, or nothing remembered yet), else the lowest empty slot. 0 when
 * three computers are remembered. */
static uint8_t ble_host_slot_to_add(void)
{
    const uint8_t active = codex_micro_active_ble_slot();
    return codex_micro_ble_slot_paired(active) ? codex_micro_ble_free_slot()
                                               : active;
}

/*
 * The Bluetooth key.  A short press moves to the next remembered computer;
 * with no other computer it waits for a new one, and pressed again while
 * waiting it goes back.  Held (add), it always waits for a new computer.
 * Nothing here forgets a computer.
 */
static void apply_ble_host_key(board_control_t control, bool add)
{
    uint8_t return_slot = 0u;
    const bool waiting = codex_micro_ble_pairing_window(NULL, &return_slot);
    const bool back = !add && waiting && return_slot != 0u;
    uint8_t slot = back ? return_slot : 0u;
    if (!add && !back) {
        slot = codex_micro_ble_next_host_slot();
    }
    if (slot == 0u) {
        slot = ble_host_slot_to_add();
    }
    pulse_navigation_feedback(control);
    if (slot == 0u) {
        show_ble_hosts_full();
        return;
    }
    if (codex_micro_select_ble_slot_from(
            slot, CODEX_MICRO_BLE_SLOT_SOURCE_KEY) != ESP_OK) {
        show_feedback(STATUS_FEEDBACK_ERROR, "BLE SLOT FAILED");
        return;
    }
    if (back) {
        show_ble_back_page();
    } else {
        show_ble_host_selected(slot);
    }
}

/* The Bluetooth key held long enough: add a computer, once per press. */
static void poll_ble_host_key(void)
{
    if (s_ble_host_key_control == BOARD_CONTROL_COUNT ||
        s_ble_host_key_hold_done ||
        xTaskGetTickCount() - s_ble_host_key_pressed_at <
            pdMS_TO_TICKS(BLE_ADD_HOST_HOLD_MS)) {
        return;
    }
    s_ble_host_key_hold_done = true;
    apply_ble_host_key(s_ble_host_key_control, true);
}

static void poll_ble_slot_clear_result(void)
{
    uint8_t slot = 0u;
    esp_err_t error = ESP_OK;
    if (!codex_micro_take_ble_slot_clear_result(&slot, &error)) {
        return;
    }
    if (error == ESP_OK) {
        pulse_save_feedback();
        show_ble_slot_progress(slot, 100u);
    } else {
        show_feedback(STATUS_FEEDBACK_ERROR, "BLE SLOT CLEAR FAILED");
    }
}

static bool standard_control_active_before_function_key(void)
{
    const board_control_t function_control = function_center_control();
    for (size_t control = 0; control < BOARD_CONTROL_COUNT; ++control) {
        if (control != function_control && s_active_action_valid[control]) {
            return true;
        }
    }
    return false;
}

static bool handle_prompt_palette_key_gesture(const board_event_t *event)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    if (event->control == BOARD_CONTROL_KEY_12) {
        if (event->pressed) {
            if (s_local_page != LOCAL_PAGE_NONE || s_quick_config_active ||
                s_system_select_active ||
                s_host_output_state != HOST_OUTPUT_ENABLED ||
                s_function_key_gesture_active ||
                codex_micro_active_key_layout() !=
                    CODEX_MICRO_KEY_LAYOUT_MATRIX12 ||
                codex_micro_has_active_control() ||
                standard_control_active_before_function_key()) {
                return false;
            }
            s_prompt_palette_key_gesture_active = true;
            s_prompt_palette_key_action_pressed = false;
            s_prompt_palette_key_pressed_at = xTaskGetTickCount();
            return true;
        }
        if (!s_prompt_palette_key_gesture_active) {
            return false;
        }
        if (s_prompt_palette_key_action_pressed) {
            route_main_event(event);
        } else {
            route_tap(BOARD_CONTROL_KEY_12);
        }
        reset_prompt_palette_key_gesture();
        return true;
    }

    if (s_prompt_palette_key_gesture_active && event->pressed) {
        if (!s_prompt_palette_key_action_pressed) {
            const board_event_t prompt_key_press = {
                .control = BOARD_CONTROL_KEY_12,
                .pressed = true,
            };
            route_main_event(&prompt_key_press);
            s_prompt_palette_key_action_pressed = true;
        }
    }
#else
    (void)event;
#endif
    return false;
}

static void poll_prompt_palette_key_gesture(void)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    if (!s_prompt_palette_key_gesture_active ||
        s_prompt_palette_key_action_pressed ||
        xTaskGetTickCount() - s_prompt_palette_key_pressed_at <
            pdMS_TO_TICKS(PROMPT_PALETTE_HOLD_MS)) {
        return;
    }
    enter_prompt_palette();
#endif
}

static void route_function_key(bool pressed)
{
    const board_event_t event = {
        .control = function_center_control(),
        .pressed = pressed,
    };
    route_main_event(&event);
}

static void schedule_function_key_release(void)
{
    s_function_key_release_pending = true;
    s_function_key_release_at = xTaskGetTickCount() + synthetic_tap_hold_ticks();
}

static bool handle_function_key_gesture(const board_event_t *event)
{
    if (event->control == s_ble_host_key_control) {
        if (!event->pressed) {
            s_ble_host_key_control = BOARD_CONTROL_COUNT;
            if (!s_ble_host_key_hold_done) {
                apply_ble_host_key(event->control, false);
            }
        }
        return true;
    }

    if (event->control == function_center_control()) {
        if (event->pressed && local_ui_home_shortcut_active()) {
            /*
             * The release that follows the long press used to open the
             * Function Center is already owned by the active gesture.  Only a
             * new standalone press starts local Back/Home navigation.
             */
            if (s_function_key_gesture_active &&
                s_function_key_center_opened) {
                return true;
            }
            s_function_key_gesture_active = true;
            s_function_key_gesture_combo = false;
            s_function_key_center_opened =
                local_ui_home_shortcut_blocked();
            s_function_key_local_navigation =
                !s_function_key_center_opened;
            s_function_key_action_pressed = false;
            s_function_key_pressed_at = xTaskGetTickCount();
            s_local_last_input_at = s_function_key_pressed_at;
            pulse_control_press_feedback(event->control);
            s_function_key_press_feedback_sent = true;
            return true;
        }
        if (event->pressed) {
            s_function_key_gesture_active = true;
            s_function_key_gesture_combo =
                codex_micro_has_active_control() ||
                standard_control_active_before_function_key();
            s_function_key_center_opened = false;
            s_function_key_local_navigation = false;
            s_function_key_action_pressed = false;
            s_function_key_pressed_at = xTaskGetTickCount();
            pulse_control_press_feedback(event->control);
            s_function_key_press_feedback_sent = true;
            if (s_function_key_gesture_combo) {
                route_main_event(event);
                s_function_key_action_pressed = true;
            }
            return true;
        }
        if (!s_function_key_gesture_active) {
            return false;
        }
        if (s_function_key_local_navigation &&
            !s_function_key_center_opened) {
            if (xTaskGetTickCount() - s_function_key_pressed_at >=
                pdMS_TO_TICKS(FUNCTION_CENTER_HOLD_MS)) {
                exit_local_ui_to_home();
            } else {
                return_local_ui_one_level();
            }
        } else if (!s_function_key_center_opened) {
            if (s_function_key_action_pressed) {
                route_main_event(event);
            } else if (!s_function_key_gesture_combo) {
                /* Keep press and release in separate USB poll cycles. */
                route_function_key(true);
                schedule_function_key_release();
            }
        }
        reset_function_key_gesture();
        return true;
    }

    if (!s_function_key_gesture_active) {
        return false;
    }
    if (s_function_key_local_navigation) {
        return true;
    }
    if (s_function_key_center_opened) {
        /*
         * Once the local page is visible, navigation belongs to that page
         * even if the opening Key3 press has not been released yet.  After a
         * Key3 long press has closed the page, keep consuming other input
         * until Key3 is released so the gesture cannot leak to the host.
         */
        return !local_ui_home_shortcut_active();
    }
    if (ble_host_key_control(event->control) && event->pressed) {
        s_function_key_gesture_combo = true;
        /* It acts on release (switch) or once held long enough (add, see
         * poll_ble_host_key), so one press is one action however the press
         * is reported. */
        s_ble_host_key_control = event->control;
        s_ble_host_key_pressed_at = xTaskGetTickCount();
        s_ble_host_key_hold_done = false;
        return true;
    }
    if (event->pressed) {
        s_function_key_gesture_combo = true;
        if (!s_function_key_action_pressed) {
            route_function_key(true);
            s_function_key_action_pressed = true;
        }
    }
    return false;
}

static void poll_function_key_release(void)
{
    if (!s_function_key_release_pending ||
        xTaskGetTickCount() < s_function_key_release_at) {
        return;
    }
    s_function_key_release_pending = false;
    route_function_key(false);
}

static void poll_function_key_gesture(void)
{
    if (!s_function_key_gesture_active) {
        return;
    }
    if (s_function_key_gesture_combo || s_function_key_center_opened ||
        xTaskGetTickCount() - s_function_key_pressed_at <
            pdMS_TO_TICKS(FUNCTION_CENTER_HOLD_MS)) {
        return;
    }
    s_function_key_center_opened = true;
    if (s_function_key_local_navigation) {
        exit_local_ui_to_home();
    } else if (s_local_page != LOCAL_PAGE_NONE) {
        exit_local_page();
    } else {
        enter_function_center();
    }
}

static void reset_mode_sensitive_input_state(void)
{
    host_action_events_reset();
    memset(s_key_gestures, 0, sizeof(s_key_gestures));
    finish_key_pulses();
    macro_cancel();
    usb_service_release_all();
    memset(s_active_action_valid, 0, sizeof(s_active_action_valid));
    s_function_key_release_pending = false;
    s_ble_host_key_control = BOARD_CONTROL_COUNT;
    memset(s_tap_release_pending, 0, sizeof(s_tap_release_pending));
    reset_function_key_gesture();
    reset_prompt_palette_key_gesture();
    double_click_model_init(&s_encoder_gesture);
    double_click_model_init(&s_joystick_gesture);
    encoder_haptic_model_init(&s_encoder_haptic);
    joystick_radial_haptic_model_init(&s_joystick_radial_haptic);
    prompt_joystick_model_init(&s_prompt_joystick);
    joystick_single_action_model_reset_latched(&s_single_action_joystick);
    s_encoder_direct_press = false;
    s_joystick_direct_press = false;
    s_joystick_direction_active = false;
    eda_canvas_pan_model_reset(&s_eda_canvas_pan);
    s_encoder_motion_count = 0;
    s_joystick_excursions = 0;
}

static void poll_joystick_suspect(void)
{
    const bool suspect = board_joystick_suspect();
    if (suspect == s_joystick_suspect) {
        return;
    }
    s_joystick_suspect = suspect;
    if (suspect) {
        for (board_control_t control = BOARD_CONTROL_JOYSTICK_UP;
             control <= BOARD_CONTROL_JOYSTICK_RIGHT; ++control) {
            const board_event_t release = {
                .control = control,
                .pressed = false,
            };
            route_main_event(&release);
        }
        prompt_joystick_model_init(&s_prompt_joystick);
        joystick_single_action_model_reset_latched(&s_single_action_joystick);
        joystick_radial_haptic_model_init(&s_joystick_radial_haptic);
        eda_canvas_pan_model_reset(&s_eda_canvas_pan);
        s_joystick_direction_active = false;
        s_joystick_excursions = 0;
    }
    if (!s_shutdown_active && !s_usb_standby_active &&
        !s_idle_standby.active && !s_battery_splash_active &&
        !s_boot_animation_active && !s_system_select_active &&
        !s_quick_config_active && s_local_page == LOCAL_PAGE_NONE &&
        s_pomodoro.state != POMODORO_ALERT
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
        && !s_round_feedback_active && !s_mist_connection_visible
#endif
        ) {
        show_status(suspect ? "CALIBRATE JOYSTICK" : NULL);
    }
}

static void publish_diagnostic_capture_status(void)
{
    const char *last_control = NULL;
    if (s_diagnostic_capture.has_last_event &&
        s_diagnostic_capture.last_control < BOARD_CONTROL_COUNT) {
        last_control = board_control_id(
            (board_control_t)s_diagnostic_capture.last_control);
    }
    config_protocol_set_diagnostic_capture_status(
        s_diagnostic_capture.state == DIAGNOSTIC_CAPTURE_ACTIVE,
        s_diagnostic_capture.state == DIAGNOSTIC_CAPTURE_WAIT_NEUTRAL,
        s_diagnostic_capture.event_sequence, last_control,
        s_diagnostic_capture.last_pressed);
}

static bool diagnostic_control_active(board_control_t control)
{
    board_control_t active[BOARD_CONTROL_COUNT];
    const size_t count = board_get_active_controls(active, ARRAY_COUNT(active));
    for (size_t index = 0; index < count; ++index) {
        if (active[index] == control) {
            return true;
        }
    }
    return false;
}

/* USB standby, the charge splash and local pages are allowed, see
 * diagnostic_capture_start_allowed() in config_protocol.c. */
static bool s_diagnostic_borrowed_local_ui;

static bool diagnostic_capture_can_start_now(void)
{
    return (s_host_output_state == HOST_OUTPUT_ENABLED ||
            s_host_output_state == HOST_OUTPUT_DIAGNOSTIC_WAIT_NEUTRAL ||
            s_host_output_state == HOST_OUTPUT_WAIT_NEUTRAL ||
            s_host_output_state == HOST_OUTPUT_LOCAL_UI) &&
           !s_quick_config_active && !s_shutdown_active &&
           !s_idle_standby.active && !firmware_update_is_active() &&
           !config_protocol_calibration_ownership_requested();
}

/*
 * Calibration may borrow a local page's input (first-boot system selection)
 * and run during USB standby or the charge splash (a factory unit that has
 * never been switched on). Neither state changes: the page gets its input
 * back and an OFF unit stays OFF.
 */
static bool s_calibration_borrowed_local_ui;

static void poll_calibration_ownership(void)
{
    const bool requested = config_protocol_calibration_ownership_requested();
    if (requested && s_host_output_state != HOST_OUTPUT_CALIBRATION) {
        if ((s_host_output_state != HOST_OUTPUT_ENABLED &&
             s_host_output_state != HOST_OUTPUT_DIAGNOSTIC &&
             s_host_output_state != HOST_OUTPUT_DIAGNOSTIC_WAIT_NEUTRAL &&
             s_host_output_state != HOST_OUTPUT_WAIT_NEUTRAL &&
             s_host_output_state != HOST_OUTPUT_LOCAL_UI) ||
            s_quick_config_active || s_shutdown_active ||
            s_idle_standby.active || firmware_update_is_active()) {
            return;
        }
        s_calibration_borrowed_local_ui =
            s_host_output_state == HOST_OUTPUT_LOCAL_UI;
        reset_mode_sensitive_input_state();
        codex_micro_set_input_suppressed(true);
        if (diagnostic_capture_model_owns_input(&s_diagnostic_capture)) {
            diagnostic_capture_model_yield_to_local(&s_diagnostic_capture);
            s_diagnostic_encoder_press_active = false;
            publish_diagnostic_capture_status();
        }
        s_host_output_state = HOST_OUTPUT_CALIBRATION;
        config_protocol_set_calibration_ownership(true);
    } else if (!requested && s_host_output_state == HOST_OUTPUT_CALIBRATION &&
               s_calibration_borrowed_local_ui) {
        /* Hand the input back to the page calibration interrupted. Host
         * output stays suppressed there; leaving the page later goes through
         * the normal wait-for-neutral path. */
        s_calibration_borrowed_local_ui = false;
        config_protocol_set_calibration_ownership(false);
        if (config_protocol_calibration_output_interlock_active()) {
            config_protocol_release_calibration_output_interlock();
        }
        s_host_output_state = HOST_OUTPUT_LOCAL_UI;
        reset_mode_sensitive_input_state();
        if (s_local_page != LOCAL_PAGE_NONE) {
            render_local_page();
        } else if (s_system_select_active) {
            render_system_select(NULL);
        }
    } else if (!requested &&
               config_protocol_calibration_output_interlock_active() &&
               s_host_output_state != HOST_OUTPUT_WAIT_NEUTRAL &&
               s_host_output_state != HOST_OUTPUT_DIAGNOSTIC &&
               s_host_output_state != HOST_OUTPUT_DIAGNOSTIC_WAIT_NEUTRAL) {
        config_protocol_set_calibration_ownership(false);
        if (diagnostic_capture_model_owns_input(&s_diagnostic_capture)) {
            diagnostic_capture_model_yield_to_local(&s_diagnostic_capture);
            s_diagnostic_encoder_press_active = false;
            publish_diagnostic_capture_status();
        }
        s_host_output_state = HOST_OUTPUT_WAIT_NEUTRAL;
        reset_mode_sensitive_input_state();
    }
}

static void process_diagnostic_capture_request(void)
{
    config_protocol_diagnostic_capture_request_t request;
    if (!config_protocol_take_diagnostic_capture_request(&request)) {
        return;
    }
    bool status_changed = false;
    if (request.operation == CONFIG_PROTOCOL_DIAGNOSTIC_CAPTURE_START) {
        const bool first_start =
            s_diagnostic_capture.state == DIAGNOSTIC_CAPTURE_INACTIVE;
        if ((first_start || s_diagnostic_capture.state ==
                 DIAGNOSTIC_CAPTURE_WAIT_NEUTRAL) &&
            !diagnostic_capture_can_start_now()) {
            status_changed = true;
        } else if (diagnostic_capture_model_start(
                &s_diagnostic_capture,
                ticks_to_ms(xTaskGetTickCount()),
                CONFIG_PROTOCOL_DIAGNOSTIC_CAPTURE_TIMEOUT_MS)) {
            if (first_start ||
                s_host_output_state == HOST_OUTPUT_DIAGNOSTIC_WAIT_NEUTRAL ||
                s_host_output_state == HOST_OUTPUT_WAIT_NEUTRAL) {
                if (s_host_output_state == HOST_OUTPUT_LOCAL_UI) {
                    s_diagnostic_borrowed_local_ui = true;
                }
                reset_mode_sensitive_input_state();
                codex_micro_set_input_suppressed(true);
                s_host_output_state = HOST_OUTPUT_DIAGNOSTIC;
                s_diagnostic_encoder_press_active = false;
                status_changed = true;
            }
        } else {
            status_changed = true;
        }
    } else {
        diagnostic_capture_model_stop(&s_diagnostic_capture);
        if (s_host_output_state == HOST_OUTPUT_DIAGNOSTIC) {
            s_host_output_state = HOST_OUTPUT_DIAGNOSTIC_WAIT_NEUTRAL;
        }
        status_changed = true;
    }
    if (status_changed) {
        publish_diagnostic_capture_status();
    }
}

static void poll_diagnostic_capture(void)
{
    if (s_diagnostic_capture.state == DIAGNOSTIC_CAPTURE_ACTIVE &&
        s_diagnostic_encoder_press_active &&
        !diagnostic_control_active(BOARD_CONTROL_ENCODER_PRESS)) {
        s_diagnostic_encoder_press_active = false;
        if (diagnostic_capture_model_record(
                &s_diagnostic_capture, BOARD_CONTROL_ENCODER_PRESS, false)) {
            publish_diagnostic_capture_status();
        }
    }
    if (s_diagnostic_capture.state == DIAGNOSTIC_CAPTURE_ACTIVE &&
        !host_config_link_active()) {
        diagnostic_capture_model_stop(&s_diagnostic_capture);
        if (s_host_output_state == HOST_OUTPUT_DIAGNOSTIC) {
            s_host_output_state = HOST_OUTPUT_DIAGNOSTIC_WAIT_NEUTRAL;
        }
        publish_diagnostic_capture_status();
        return;
    }
    const diagnostic_capture_state_t before = s_diagnostic_capture.state;
    diagnostic_capture_model_poll(
        &s_diagnostic_capture, ticks_to_ms(xTaskGetTickCount()),
        board_inputs_neutral());
    if (before == DIAGNOSTIC_CAPTURE_ACTIVE &&
        s_diagnostic_capture.state == DIAGNOSTIC_CAPTURE_WAIT_NEUTRAL &&
        s_host_output_state == HOST_OUTPUT_DIAGNOSTIC) {
        s_host_output_state = HOST_OUTPUT_DIAGNOSTIC_WAIT_NEUTRAL;
    }
    if (before != DIAGNOSTIC_CAPTURE_INACTIVE &&
        s_diagnostic_capture.state == DIAGNOSTIC_CAPTURE_INACTIVE &&
        s_host_output_state == HOST_OUTPUT_DIAGNOSTIC_WAIT_NEUTRAL) {
        s_diagnostic_encoder_press_active = false;
        if (s_diagnostic_borrowed_local_ui) {
            /* Give the interrupted page its input and frame back. */
            s_diagnostic_borrowed_local_ui = false;
            if (config_protocol_calibration_output_interlock_active()) {
                config_protocol_release_calibration_output_interlock();
            }
            s_host_output_state = HOST_OUTPUT_LOCAL_UI;
            if (s_local_page != LOCAL_PAGE_NONE) {
                render_local_page();
            } else if (s_system_select_active) {
                render_system_select(NULL);
            }
        } else {
            if (config_protocol_calibration_output_interlock_active()) {
                s_host_output_state = HOST_OUTPUT_WAIT_NEUTRAL;
            } else {
                s_host_output_state = HOST_OUTPUT_ENABLED;
                codex_micro_set_input_suppressed(false);
            }
            /* A unit that is still OFF keeps its standby outputs. */
            if (s_usb_standby_active) {
                set_usb_standby_outputs();
            } else if (!s_battery_splash_active) {
                show_status(NULL);
            }
        }
    }
    if (before != s_diagnostic_capture.state) {
        publish_diagnostic_capture_status();
    }
}

static void poll_platform_context(void)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    const bool usb = usb_service_is_mounted();
    const bool ble = codex_micro_ble_connected();
    const uint8_t context = usb ? 0u : codex_micro_active_ble_slot();
    if (context != s_platform_context) {
        s_platform_context = context;
        config_store_set_platform_context(context);
    }
    if (s_system_save_pending) {
        config_store_status_t status;
        config_store_get_status(&status);
        if (status.activation_failed) {
            s_system_save_pending = false;
            if (s_local_page != LOCAL_PAGE_SYSTEM) {
                leave_local_input_ownership();
            }
            show_feedback(STATUS_FEEDBACK_ERROR, "OS SAVE ERROR");
        }
        return;
    }
    /* USB and BLE carry the same active mappings. A remembered OS for an old
     * connection must not silently select a different Profile on handoff. */
    if (config_store_get_platform() != CONFIG_PLATFORM_UNSELECTED ||
        boot_guard_safe_mode()) {
        s_platform_setup_needed = false;
        return;
    }
    if ((usb && !s_usb_mounted) || (ble && !s_ble_connected)) {
        s_platform_setup_needed = true;
    }

    if (!s_platform_setup_needed || s_shutdown_active ||
        s_usb_standby_active || s_battery_splash_active ||
        s_boot_animation_active || s_idle_standby.active ||
        diagnostic_capture_model_owns_input(&s_diagnostic_capture) ||
        s_pomodoro.state == POMODORO_ALERT || s_quick_config_active ||
        s_local_page != LOCAL_PAGE_NONE || s_function_key_gesture_active ||
        config_store_has_pending()) {
        return;
    }
    if (usb || ble) {
        enter_os_settings(false);
    }
#endif
}

static void sync_connection_and_mode_state(void)
{
    sync_ble_to_usb_data_link();
    poll_platform_context();
    const bool mounted = usb_service_is_mounted();
    const bool ble_connected = codex_micro_ble_connected();
    const codex_micro_mode_t mode = codex_micro_mode();
    if (mounted == s_usb_mounted && ble_connected == s_ble_connected &&
        mode == s_codex_mode) {
        return;
    }
    const bool connection_changed =
        mounted != s_usb_mounted || ble_connected != s_ble_connected;
    if (connection_changed) {
        memset(s_key_gestures, 0, sizeof(s_key_gestures));
        finish_key_pulses();
    }
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    const bool usb_connected_now = mounted && !s_usb_mounted;
    const bool ble_connected_now = ble_connected && !s_ble_connected;
    if (usb_connected_now || ble_connected_now) {
        s_mist_connection_pending = usb_connected_now ? 1u : 2u;
    }
#endif
    if (mode != s_codex_mode) {
        clear_host_lighting_preview();
    }
    if (mode != s_codex_mode ||
        (connection_changed &&
         (mode == CODEX_MICRO_MODE_EDA ||
          s_codex_mode == CODEX_MICRO_MODE_EDA ||
          mode == CODEX_MICRO_MODE_CODEX ||
          s_codex_mode == CODEX_MICRO_MODE_CODEX))) {
        reset_mode_sensitive_input_state();
    }
    s_usb_mounted = mounted;
    s_ble_connected = ble_connected;
    s_codex_mode = mode;
    if (!s_system_select_active && !s_quick_config_active &&
        s_local_page == LOCAL_PAGE_NONE &&
        s_pomodoro.state != POMODORO_ALERT &&
        !s_boot_animation_active && !s_battery_splash_active) {
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
        poll_mist_connection_notice();
        if (s_mist_connection_visible) return;
#endif
        show_status(NULL);
    }
}

/* ---- Low-battery guard -------------------------------------------------
 * Warning: red under-key breathing + periodic LOW notice (battery only).
 * Critical: OFF notice, short haptic, latch power-off; ON intent kept and a
 * boot lock set. Boot gate: a weak cell or a brownout reset never reaches the
 * full ON boot (white self-test, BLE). Surviving the power-off pulse means
 * external power, which parks in USB standby and resumes once recovered.
 */
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
static bool low_battery_breath_active(void)
{
    return s_battery_guard.level != BATTERY_GUARD_NORMAL &&
           !s_shutdown_active && !s_usb_standby_active &&
           !s_battery_splash_active && !s_idle_standby.active &&
           !usb_service_is_mounted();
}

static const char *battery_guard_state_name(battery_guard_level_t level)
{
    return level == BATTERY_GUARD_CRITICAL ? "critical"
         : level == BATTERY_GUARD_USB_LOW ? "locked_standby"
         : level == BATTERY_GUARD_LOW ? "low" : "normal";
}

static bool low_battery_boot_gate(esp_reset_reason_t reset_reason)
{
    const bool brownout = reset_reason == ESP_RST_BROWNOUT;
    if (brownout) {
        const esp_err_t error = config_store_note_brownout_reset();
        if (error != ESP_OK) {
            ESP_LOGW(TAG, "cannot count brownout reset: %s",
                     esp_err_to_name(error));
        }
    }
    config_store_power_guard_t guard;
    config_store_get_power_guard(&guard);
    battery_guard_sample_t sample;
    guard_sample_from_gauge(&sample);
    /* init has just taken the first sample; its age is a few ms at most. */
    const battery_guard_boot_t decision = battery_guard_boot_decide(
        brownout, guard.low_battery_lock, &sample);
    if (decision.clear_lock) {
        (void)config_store_set_low_battery_lock(false);
    }
    if (decision.block) {
        ESP_LOGW(TAG, "boot blocked: %u mV valid=%d brownout=%d lock=%d",
                 (unsigned)sample.voltage_mv, sample.valid, brownout,
                 guard.low_battery_lock);
    }
    return decision.block;
}

static void start_low_battery_shutdown(bool at_boot)
{
    if (s_shutdown_active) {
        return;
    }
    const esp_err_t error = config_store_note_low_battery_shutdown();
    if (error != ESP_OK) {
        ESP_LOGW(TAG, "cannot record low-battery shutdown: %s",
                 esp_err_to_name(error));
    }
    s_low_battery_shutdown = true;
    config_protocol_set_power_guard_state(at_boot ? "boot_blocked"
                                                  : "critical");
    const board_mist_view_t view = {
        .page = BOARD_MIST_BATTERY, .a = 0, .c = BOARD_MIST_BATTERY_EMPTY,
        .phase = 1,
    };
    /* At boot USB/BLE services are not initialised yet and hold nothing. */
    begin_shutdown(&view, LOW_BATTERY_OFF_NOTICE_MS, !at_boot,
                   USB_STANDBY_ENTRY_LOW_BATTERY);
    if (!at_boot) {
        (void)board_haptic_pulse(LOW_BATTERY_OFF_HAPTIC_STRENGTH,
                                 LOW_BATTERY_OFF_HAPTIC_MS);
    }
}

/*
 * Low cell on USB: show the empty icon, drop every load and park in locked
 * USB standby (poll_usb_standby picks up s_low_battery_shutdown) until the
 * cell recovers. The ON intent is kept. No NVS lock or shutdown count:
 * nothing powered off, and a later battery-only boot uses the normal gate.
 */
static void start_low_battery_usb_standby(void)
{
    if (s_shutdown_active) {
        return;
    }
    s_low_battery_shutdown = true;
    config_protocol_set_power_guard_state("locked_standby");
    const board_mist_view_t view = {
        .page = BOARD_MIST_BATTERY, .a = 0, .c = BOARD_MIST_BATTERY_EMPTY,
        .phase = 1,
    };
    begin_shutdown(&view, LOW_BATTERY_OFF_NOTICE_MS, true,
                   USB_STANDBY_ENTRY_USB_LOW_BATTERY);
}

static bool low_battery_notice_allowed(void)
{
    return s_host_output_state == HOST_OUTPUT_ENABLED &&
           s_local_page == LOCAL_PAGE_NONE && !s_quick_config_active &&
           !s_system_select_active && !s_boot_animation_active &&
           !s_round_feedback_active && !s_mist_connection_visible &&
           !s_idle_standby.active && s_pomodoro.state != POMODORO_ALERT &&
           !s_under_key_self_test_active &&
           !board_joystick_calibration_active() &&
           !diagnostic_capture_model_owns_input(&s_diagnostic_capture);
}

static void poll_low_battery_outputs(TickType_t now)
{
    const bool breathing = low_battery_breath_active();
    const bool lighting_owned = s_under_key_self_test_active ||
        s_host_lighting_preview.active || s_lighting_preview.active ||
        light_idle_outputs_blocked();
    if (breathing && !lighting_owned &&
        now - s_low_battery_breath_frame_at >=
            pdMS_TO_TICKS(LOW_BATTERY_BREATH_FRAME_MS)) {
        s_low_battery_breath_frame_at = now;
        s_low_battery_breath_shown = true;
        apply_lighting_output();
    } else if (!breathing && s_low_battery_breath_shown) {
        s_low_battery_breath_shown = false;
        if (!lighting_owned) apply_lighting_output();
    }
    if (!breathing || !low_battery_notice_allowed()) {
        return;
    }
    if (!s_low_battery_notice_due &&
        (int32_t)(now - s_low_battery_notice_next_at) < 0) {
        return;
    }
    s_low_battery_notice_due = false;
    s_low_battery_notice_next_at =
        now + pdMS_TO_TICKS(LOW_BATTERY_NOTICE_REPEAT_MS);
    mist_cancel_motion();
    s_round_feedback_active = true;
    s_round_feedback_expires_at = now + pdMS_TO_TICKS(LOW_BATTERY_NOTICE_MS);
    const int percent = current_battery_percent(NULL);
    const board_mist_view_t view = {
        .page = BOARD_MIST_BATTERY, .a = percent < 0 ? 0 : percent,
        .c = BOARD_MIST_BATTERY_LOW, .phase = 1,
    };
    (void)board_display_show_mist(&view);
    s_mist_last_frame_ms = ticks_to_ms(now);
}
#endif

static void poll_battery_guard(void)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    if (s_shutdown_active || s_usb_standby_active || s_battery_splash_active) {
        return;
    }
    if (s_low_battery_locked_standby) {
        /* Woken from locked standby (button or recovery): start fresh. */
        s_low_battery_locked_standby = false;
        battery_guard_model_init(&s_battery_guard);
        config_protocol_set_power_guard_state("normal");
    }
    const TickType_t now = xTaskGetTickCount();
    if (now - s_battery_guard_polled_at >=
        pdMS_TO_TICKS(LOW_BATTERY_GUARD_POLL_MS)) {
        s_battery_guard_polled_at = now;
        battery_guard_sample_t sample;
        guard_sample_from_gauge(&sample);
        const battery_guard_level_t before = s_battery_guard.level;
        if (battery_guard_model_update(&s_battery_guard, ticks_to_ms(now),
                                       &sample, usb_service_is_mounted())) {
            config_protocol_set_power_guard_state(
                battery_guard_state_name(s_battery_guard.level));
            ESP_LOGW(TAG, "battery guard %s -> %s at %u mV / %u%%",
                     battery_guard_state_name(before),
                     battery_guard_state_name(s_battery_guard.level),
                     (unsigned)sample.voltage_mv,
                     (unsigned)sample.soc_percent);
            if (s_battery_guard.level == BATTERY_GUARD_CRITICAL) {
                if (s_idle_standby.active) {
                    /* The OFF notice needs the outputs standby cleared. */
                    wake_from_idle_standby();
                }
                start_low_battery_shutdown(false);
                return;
            }
            if (s_battery_guard.level == BATTERY_GUARD_USB_LOW) {
                if (s_idle_standby.active) {
                    wake_from_idle_standby();
                }
                start_low_battery_usb_standby();
                return;
            }
            if (before == BATTERY_GUARD_NORMAL) {
                s_low_battery_notice_due = true;
            }
        }
    }
    poll_low_battery_outputs(now);
#endif
}

esp_err_t action_engine_init(void)
{
    memset(&s_macro, 0, sizeof(s_macro));
    const esp_reset_reason_t reset_reason = esp_reset_reason();
    s_firmware_error = reset_reason_is_error(reset_reason);
    s_shutdown_active = false;
    s_host_output_state = HOST_OUTPUT_ENABLED;
    s_shutdown_pulsed = false;
    s_battery_splash_active = false;
    s_usb_standby_active = false;
    s_usb_charge_session_at_boot = should_start_usb_charge_session();
    battery_guard_model_init(&s_battery_guard);
    s_battery_guard_polled_at = xTaskGetTickCount();
    s_low_battery_shutdown = false;
    s_low_battery_locked_standby = false;
    s_low_battery_notice_due = false;
    s_low_battery_notice_next_at = xTaskGetTickCount();
    s_low_battery_breath_frame_at = xTaskGetTickCount();
    s_low_battery_breath_shown = false;
    config_protocol_set_power_guard_state("normal");
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    if (s_usb_charge_session_at_boot) {
        /* Already the OFF state: only keep the brownout diagnostic. */
        if (reset_reason == ESP_RST_BROWNOUT) {
            (void)config_store_note_brownout_reset();
        }
        s_low_battery_boot_blocked = false;
    } else {
        s_low_battery_boot_blocked = low_battery_boot_gate(reset_reason);
    }
#else
    s_low_battery_boot_blocked = false;
#endif
    s_battery_splash_valid_at = 0;
    s_battery_splash_next_retry_at = 0;
    s_battery_splash_has_valid_sample = false;
    s_round_feedback_active = false;
    s_round_feedback_expires_at = 0;
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    mist_cancel_motion();
    s_mist_connection_visible = false;
    s_mist_peek_active = false;
    s_mist_read_index = 0;
#endif
    s_battery_sample_time_ms = 0;
    s_status_started_at = xTaskGetTickCount();
    s_status_last_frame_at = s_status_started_at;
    s_status_frame_valid = false;
    apply_feedback_config();
    s_usb_mounted = usb_service_is_mounted();
    s_ble_connected = codex_micro_ble_connected();
    usb_link_model_init(&s_usb_link, ticks_to_ms(xTaskGetTickCount()));
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    s_mist_connection_pending = s_usb_mounted ? 1u : s_ble_connected ? 2u : 0u;
#endif
    s_codex_mode = codex_micro_mode();
    s_platform_context = s_usb_mounted ? 0u : codex_micro_active_ble_slot();
    config_store_set_platform_context(s_platform_context);
    s_platform_setup_needed = true;
    s_system_save_pending = false;
    update_status_indicator(true);
    s_under_key_self_test_started_at = xTaskGetTickCount();
    s_under_key_self_test_last_step = SIZE_MAX;
    s_under_key_self_test_active = true;
    diagnostic_capture_model_init(&s_diagnostic_capture);
    publish_diagnostic_capture_status();
    double_click_model_init(&s_encoder_gesture);
    double_click_model_init(&s_joystick_gesture);
    encoder_haptic_model_init(&s_encoder_haptic);
    joystick_radial_haptic_model_init(&s_joystick_radial_haptic);
    prompt_joystick_model_init(&s_prompt_joystick);
    s_prompt_joystick_direction_mask = joystick_prompt_direction_mask();
    s_normal_joystick_single_action = normal_joystick_single_action_mapping();
    pomodoro_model_init(&s_pomodoro,
                        config_store_get_pomodoro_minutes());
    idle_standby_model_init(&s_idle_standby, IDLE_STANDBY_MINUTES,
                            ticks_to_ms(xTaskGetTickCount()));
    s_idle_standby_wake_wait = false;
    s_local_page = LOCAL_PAGE_NONE;
    s_alert_ack_armed = false;
    s_timer_done_hint = false;
    s_joystick_suspect = board_joystick_suspect();
    codex_attention_haptic_model_init(&s_codex_attention_haptic);
    s_function_key_release_pending = false;
    memset(s_tap_release_pending, 0, sizeof(s_tap_release_pending));
    eda_canvas_pan_model_reset(&s_eda_canvas_pan);
    reset_function_key_gesture();
    reset_prompt_palette_key_gesture();
    usb_service_set_standard_enabled(true);
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    if (s_low_battery_boot_blocked) {
        /* No white self-test, boot animation or BLE: icon, then power off. */
        s_under_key_self_test_active = false;
        start_low_battery_shutdown(true);
        return ESP_OK;
    }
#endif
    enter_initial_page();
    return ESP_OK;
}

bool action_engine_ble_start_suspended(void)
{
    return s_usb_charge_session_at_boot || s_low_battery_boot_blocked;
}

void action_engine_handle_event(const board_event_t *event)
{
    if (event == NULL || event->control >= BOARD_CONTROL_COUNT) {
        return;
    }
    const bool isolated_joystick =
        joystick_direction_control(event->control) &&
        board_joystick_suspect();
    if (!isolated_joystick) {
        light_idle_note_activity();
    }
    process_diagnostic_capture_request();
    poll_calibration_ownership();
    if (s_host_output_state == HOST_OUTPUT_CALIBRATION) {
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
        pulse_physical_button_feedback(event);
#endif
        return;
    }
    if (diagnostic_capture_model_owns_input(&s_diagnostic_capture)) {
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
        pulse_physical_button_feedback(event);
#endif
        if (isolated_joystick) {
            light_idle_note_activity();
        }
        bool record_event = !event->feedback_only;
        if (event->control == BOARD_CONTROL_ENCODER_PRESS) {
            if (event->feedback_only && event->pressed &&
                !s_diagnostic_encoder_press_active) {
                s_diagnostic_encoder_press_active = true;
                record_event = true;
            } else if (!event->feedback_only && event->pressed &&
                       s_diagnostic_encoder_press_active) {
                /* Power V2 repeats the down edge when a short press resolves. */
                record_event = false;
            } else if (!event->feedback_only && !event->pressed) {
                s_diagnostic_encoder_press_active = false;
            }
        }
        if (record_event && diagnostic_capture_model_record(
                                &s_diagnostic_capture,
                                (uint8_t)event->control,
                                event->pressed)) {
            publish_diagnostic_capture_status();
        }
        return;
    }
    if (isolated_joystick) {
        return;
    }
    if (s_shutdown_active || s_usb_standby_active) {
        return;
    }
    if (s_battery_splash_active) {
        /* A short input dismisses the charge display without powering on. */
        if (event->pressed) finish_battery_splash();
        return;
    }
    if (s_idle_standby.active) {
        wake_from_idle_standby();
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
        pulse_physical_button_feedback(event);
#endif
        return;
    }
    if (s_idle_standby_wake_wait) {
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
        pulse_physical_button_feedback(event);
#endif
        return;
    }
    idle_standby_model_note_activity(&s_idle_standby,
                                     ticks_to_ms(xTaskGetTickCount()));
    /* A joystick direction can drift; only deliberate inputs boost pairing. */
    if (event->pressed && !joystick_direction_control(event->control)) {
        codex_micro_note_local_input();
    }
    /* Transport callbacks can reset the mode before this queued event runs. */
    sync_connection_and_mode_state();
    if (!s_dispatching_single_action_joystick &&
        joystick_direction_control(event->control) &&
        single_action_joystick_context()) {
        return;
    }
    cancel_boot_animation();
    /* Resolve an exact-deadline race before routing this physical event. */
    poll_pomodoro();
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    if (event->pressed && s_pomodoro.state != POMODORO_ALERT) {
        if (s_mist_connection_visible || s_round_feedback_active) {
            s_mist_connection_visible = false;
            s_round_feedback_active = false;
            mist_cancel_motion();
            if (s_local_page != LOCAL_PAGE_NONE) render_local_page();
            else if (s_quick_config_active) render_quick_config(s_quick_config_pending_footer);
            else show_status(NULL);
        }
        if ((s_local_page == LOCAL_PAGE_FUNCTION ||
             s_local_page == LOCAL_PAGE_SETTINGS || s_local_page == LOCAL_PAGE_SYSTEM) &&
            !encoder_rotation_control(event->control) &&
            event->control != BOARD_CONTROL_JOYSTICK_LEFT &&
            event->control != BOARD_CONTROL_JOYSTICK_RIGHT) {
            mist_cancel_motion();
            render_local_page();
        }
    }
#endif
    if (s_pomodoro.state == POMODORO_ALERT) {
        if (event->pressed && s_alert_ack_armed) {
            (void)board_haptic_stop();
            pomodoro_model_acknowledge(&s_pomodoro);
            s_alert_ack_armed = false;
            leave_local_input_ownership();
            show_status(NULL);
        }
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
        pulse_physical_button_feedback(event);
#endif
        return;
    }
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    pulse_physical_button_feedback(event);
#endif
    if (event->feedback_only) {
        if (event->control == BOARD_CONTROL_ENCODER_PRESS && event->pressed) {
            (void)handle_prompt_palette_key_gesture(event);
#if !CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
            pulse_press_feedback();
#endif
        }
        return;
    }
    if (s_system_select_active) {
        handle_system_select_event(event);
        return;
    }
    if (s_timer_done_hint && event->pressed) {
        s_timer_done_hint = false;
    }
    if (event->pressed && board_control_key_index(event->control, NULL)) {
        handle_double_click_actions(
            BOARD_CONTROL_ENCODER_PRESS,
            double_click_model_flush(&s_encoder_gesture));
        handle_double_click_actions(
            BOARD_CONTROL_JOYSTICK_PRESS,
            double_click_model_flush(&s_joystick_gesture));
    } else if (event->pressed &&
               event->control == BOARD_CONTROL_JOYSTICK_PRESS) {
        handle_double_click_actions(
            BOARD_CONTROL_ENCODER_PRESS,
            double_click_model_flush(&s_encoder_gesture));
    } else if (event->pressed &&
               event->control == BOARD_CONTROL_ENCODER_PRESS) {
        handle_double_click_actions(
            BOARD_CONTROL_JOYSTICK_PRESS,
            double_click_model_flush(&s_joystick_gesture));
    }
    if (handle_prompt_palette_key_gesture(event)) {
        return;
    }
    if (handle_function_key_gesture(event)) {
        return;
    }
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    if (handle_mist_read_event(event)) return;
#endif
    if (s_local_page != LOCAL_PAGE_NONE) {
        handle_local_event(event);
        return;
    }
    if (s_quick_config_active &&
        event->control == BOARD_CONTROL_ENCODER_PRESS) {
        s_local_last_input_at = xTaskGetTickCount();
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
        if (round_level_slice_active() &&
            s_round_setting_state == BOARD_ROUND_SETTING_APPLYING) {
            return;
        }
        /*
         * Power V2's power-button driver emits a complete synthetic
         * press/release pair only after it has classified a physical gesture
         * as a short press.  Activate on that release directly: requiring a
         * second Action Engine press latch can lose the confirmation when the
         * earlier physical down edge was feedback-only.
         */
        if (!event->pressed) {
            activate_quick_config_action();
        }
        return;
#else
        if (event->pressed) {
            s_encoder_pressed = true;
            s_encoder_pressed_at = xTaskGetTickCount();
        } else if (s_encoder_pressed) {
            s_encoder_pressed = false;
            const TickType_t held = xTaskGetTickCount() - s_encoder_pressed_at;
            if (held >= pdMS_TO_TICKS(LOCAL_LONG_PRESS_MS)) {
                exit_quick_config();
            } else {
                activate_quick_config_action();
            }
        }
        return;
#endif
    }
    if (s_quick_config_active) {
        if (event->control == BOARD_CONTROL_JOYSTICK_PRESS) {
#if !CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
            if (!event->pressed) {
                confirm_quick_config();
            }
#endif
        } else {
            handle_quick_config_event(event);
        }
        return;
    }

    if ((event->control == BOARD_CONTROL_ENCODER_CCW ||
         event->control == BOARD_CONTROL_ENCODER_CW) && event->pressed) {
        if (s_encoder_gesture.down || s_encoder_gesture.waiting_second) {
            ++s_encoder_motion_count;
            if (s_encoder_motion_count > DOUBLE_CLICK_MOTION_TOLERANCE) {
                handle_double_click_actions(
                    BOARD_CONTROL_ENCODER_PRESS,
                    double_click_model_flush(&s_encoder_gesture));
            }
        }
        route_main_event(event);
        return;
    }

    if (event->control >= BOARD_CONTROL_JOYSTICK_UP &&
        event->control <= BOARD_CONTROL_JOYSTICK_RIGHT &&
        (s_joystick_gesture.down || s_joystick_gesture.waiting_second)) {
        if (event->pressed) {
            s_joystick_direction_active = true;
        } else if (s_joystick_direction_active) {
            s_joystick_direction_active = false;
            ++s_joystick_excursions;
            if (s_joystick_excursions > DOUBLE_CLICK_MOTION_TOLERANCE) {
                handle_double_click_actions(
                    BOARD_CONTROL_JOYSTICK_PRESS,
                    double_click_model_flush(&s_joystick_gesture));
            }
        }
        route_main_event(event);
        return;
    }

    if (event->control == BOARD_CONTROL_ENCODER_PRESS) {
        if (s_encoder_direct_press) {
            route_main_event(event);
            if (!event->pressed) s_encoder_direct_press = false;
            return;
        }
        if (event->pressed &&
            (codex_micro_has_active_control() ||
             standard_control_active_before_function_key())) {
            s_encoder_direct_press = true;
            route_main_event(event);
            return;
        }
        if (event->pressed && !s_encoder_gesture.down &&
            !s_encoder_gesture.waiting_second) {
            s_encoder_gesture_mode = codex_micro_mode();
            s_encoder_motion_count = 0;
        }
        const uint8_t actions = event->pressed
            ? double_click_model_press(&s_encoder_gesture,
                                       ticks_to_ms(xTaskGetTickCount()))
            : double_click_model_release(&s_encoder_gesture,
                                         ticks_to_ms(xTaskGetTickCount()));
        handle_double_click_actions(event->control, actions);
        return;
    }

    if (event->control == BOARD_CONTROL_JOYSTICK_PRESS &&
        (s_pomodoro.state == POMODORO_RUNNING ||
         s_pomodoro.state == POMODORO_PAUSED)) {
        if (s_joystick_direct_press) {
            route_main_event(event);
            if (!event->pressed) s_joystick_direct_press = false;
            return;
        }
        if (event->pressed && s_joystick_direction_active) {
            handle_double_click_actions(
                BOARD_CONTROL_JOYSTICK_PRESS,
                double_click_model_flush(&s_joystick_gesture));
            route_main_event(event);
            s_joystick_direct_press = true;
            return;
        }
        if (event->pressed && !s_joystick_gesture.down &&
            !s_joystick_gesture.waiting_second) {
            s_joystick_excursions = 0;
        }
        const uint8_t actions = event->pressed
            ? double_click_model_press(&s_joystick_gesture,
                                       ticks_to_ms(xTaskGetTickCount()))
            : double_click_model_release(&s_joystick_gesture,
                                         ticks_to_ms(xTaskGetTickCount()));
        handle_double_click_actions(event->control, actions);
        return;
    }
    route_main_event(event);
}

void action_engine_request_restart(void)
{
    cancel_codex_attention_haptic();
    config_protocol_set_lighting_preview_busy(true);
    clear_host_lighting_preview();
    macro_cancel();
    usb_service_release_all();
    codex_micro_set_input_suppressed(true);
    memset(s_active_action_valid, 0, sizeof(s_active_action_valid));
    memset(s_tap_release_pending, 0, sizeof(s_tap_release_pending));
    s_function_key_release_pending = false;
    (void)board_haptic_stop();
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    mist_cancel_motion();
    /* Sample the supplied nine-cell pulse within the existing restart window. */
    static const uint8_t restart_phases[] = {1, 64, 128, 192, 254};
    const TickType_t restart_started_at = xTaskGetTickCount();
    codex_micro_prepare_soft_restart();
    for (unsigned index = 0; index < ARRAY_COUNT(restart_phases); ++index) {
        if (xTaskGetTickCount() - restart_started_at >=
            pdMS_TO_TICKS(MIST_RESTART_NOTICE_MS)) break;
        const board_mist_view_t view = {
            .page = BOARD_MIST_NOTICE, .a = BOARD_ROUND_PAGE_ICON_RESTART,
            .phase = restart_phases[index] / 255.0f,
        };
        (void)board_display_show_mist(&view);
        const TickType_t next_frame_at = restart_started_at +
            pdMS_TO_TICKS((index + 1U) * MIST_RESTART_NOTICE_MS /
                          ARRAY_COUNT(restart_phases));
        const int32_t wait_ticks = (int32_t)(next_frame_at - xTaskGetTickCount());
        if (wait_ticks > 0) vTaskDelay((TickType_t)wait_ticks);
    }
#else
    (void)board_display_show_local("SYSTEM", "RESTARTING", "", "PLEASE WAIT",
                                   0xFD20);
    codex_micro_prepare_soft_restart();
    vTaskDelay(pdMS_TO_TICKS(80));
#endif
    esp_restart();
}

void action_engine_request_shutdown(void)
{
    process_diagnostic_capture_request();
    poll_calibration_ownership();
    if (s_host_output_state == HOST_OUTPUT_CALIBRATION) {
        board_power_button_note_ignored("calibration");
        return;
    }
    if (diagnostic_capture_model_owns_input(&s_diagnostic_capture)) {
        board_power_button_note_ignored("diagnostic_capture");
        if (s_diagnostic_capture.state == DIAGNOSTIC_CAPTURE_ACTIVE &&
            !s_diagnostic_encoder_press_active) {
            s_diagnostic_encoder_press_active = true;
            if (diagnostic_capture_model_record(
                    &s_diagnostic_capture, BOARD_CONTROL_ENCODER_PRESS, true)) {
                publish_diagnostic_capture_status();
            }
        }
        return;
    }
    if (s_usb_standby_active || s_battery_splash_active) {
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
        if (low_battery_standby_holds()) {
            /* Until the cell recovers the button only shows why. */
            start_usb_battery_splash();
            return;
        }
#endif
        wake_from_usb_standby(USB_STANDBY_WAKE_POWER_BUTTON);
        return;
    }
    if (s_shutdown_active) {
        board_power_button_note_ignored("shutdown_active");
#if !CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
        if (s_shutdown_pulsed) {
            /* USB can keep the MCU powered after the hardware-off pulse. */
            codex_micro_prepare_soft_restart();
            esp_restart();
        }
#endif
        return;
    }
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    (void)config_store_set_user_powered_on(false);
    const board_mist_view_t view = {
        .page = BOARD_MIST_NOTICE, .a = BOARD_ROUND_PAGE_ICON_POWER, .phase = 0,
    };
    begin_shutdown(&view, 250, true, USB_STANDBY_ENTRY_POWER_BUTTON);
#else
    begin_shutdown(NULL, 250, true, USB_STANDBY_ENTRY_POWER_BUTTON);
#endif
}

/* Common power-off sequence. The caller decides the user's ON intent. */
static void begin_shutdown(const board_mist_view_t *view, uint32_t delay_ms,
                           bool services_running,
                           usb_standby_entry_reason_t standby_reason)
{
    s_shutdown_active = true;
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    s_shutdown_standby_reason = standby_reason;
#else
    (void)standby_reason;
#endif
    config_protocol_set_lighting_preview_busy(true);
    s_shutdown_pulsed = false;
    if (services_running) {
        cancel_codex_attention_haptic();
        clear_host_lighting_preview();
        macro_cancel();
        usb_service_release_all();
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
        codex_micro_set_ble_suspended_for_source(
            true, CODEX_MICRO_BLE_DISCONNECT_STANDBY);
#endif
        memset(s_active_action_valid, 0, sizeof(s_active_action_valid));
        reset_mode_sensitive_input_state();
    }
    s_under_key_self_test_active = false;
    (void)board_haptic_stop();
    const board_rgb_t status[STATUS_INDICATOR_LED_COUNT] = {0};
    const board_rgb_t under_key[BOARD_MAX_UNDER_KEY_RGB_COUNT] = {0};
    (void)board_apply_rgb(status, under_key);
    s_shutdown_power_at = xTaskGetTickCount() + pdMS_TO_TICKS(delay_ms);
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    mist_cancel_motion();
    s_mist_last_frame_ms = ticks_to_ms(xTaskGetTickCount());
    (void)board_display_show_mist(view);
#else
    (void)view;
    (void)board_display_show_local("POWER", "SHUTTING", "DOWN", "PLEASE WAIT",
                                   0xF800);
#endif
}

void action_engine_poll(void)
{
    process_diagnostic_capture_request();
    poll_calibration_ownership();
    poll_diagnostic_capture();
    poll_codex_attention_notification();
    poll_joystick_suspect();
    poll_light_idle();
    poll_joystick_excursion_haptic();
    if (s_shutdown_active) {
        if (!s_shutdown_pulsed &&
            (int32_t)(xTaskGetTickCount() - s_shutdown_power_at) >= 0
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
            /* RST after release prevents this same hold re-latching ON. */
            && board_power_button_released()
#endif
            ) {
            (void)board_set_display_config(0, s_feedback.display_rotation);
            s_shutdown_pulsed = true;
            (void)board_power_request_shutdown();
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
            /* Always clear GEK100, including USB/charger power. If execution
             * survives the pulse, remain OFF and accept a new qualified hold.
             * No USB enumeration or battery-SOC inference is needed here.
             */
            s_shutdown_active = false;
            s_shutdown_pulsed = false;
            enter_usb_standby(s_shutdown_standby_reason);
            return;
#endif
        }
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
        poll_mist_display();
#endif
        return;
    }
    if (s_usb_standby_active) {
        poll_standby_input_ownership();
        poll_usb_standby();
        return;
    }
    poll_platform_context();
    poll_local_input_ownership();
    poll_host_lighting_preview();
    poll_idle_standby();
    poll_battery_guard();
    if (s_shutdown_active) {
        return;
    }
    if (s_idle_standby.active) {
        sync_connection_and_mode_state();
        return;
    }
    poll_pomodoro();
    poll_round_feedback();
    poll_battery_status();
    /* Always release a deferred short press, even while another UI is active. */
    poll_function_key_release();
    poll_tap_releases();
    const uint32_t now_ms = ticks_to_ms(xTaskGetTickCount());
    poll_boot_animation();
    if (s_pomodoro.state == POMODORO_ALERT) {
        reset_function_key_gesture();
        reset_prompt_palette_key_gesture();
    } else if (!s_system_select_active) {
        poll_prompt_palette_key_gesture();
        poll_function_key_gesture();
        poll_ble_host_key();
    }
    if (s_local_page == LOCAL_PAGE_NONE && !s_quick_config_active &&
        s_pomodoro.state != POMODORO_ALERT) {
        if ((s_encoder_gesture.down || s_encoder_gesture.waiting_second) &&
            codex_micro_mode() != s_encoder_gesture_mode) {
            handle_double_click_actions(
                BOARD_CONTROL_ENCODER_PRESS,
                double_click_model_flush(&s_encoder_gesture));
        }
        handle_double_click_actions(
            BOARD_CONTROL_ENCODER_PRESS,
            double_click_model_poll(&s_encoder_gesture, now_ms));
        handle_double_click_actions(
            BOARD_CONTROL_JOYSTICK_PRESS,
            double_click_model_poll(&s_joystick_gesture, now_ms));
    }
    if (local_ui_home_shortcut_active() &&
        !local_ui_home_shortcut_blocked() &&
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
        !(s_local_page == LOCAL_PAGE_TIMER_DETAIL &&
          (s_pomodoro.state == POMODORO_RUNNING ||
           s_pomodoro.state == POMODORO_PAUSED)) &&
#endif
        xTaskGetTickCount() - s_local_last_input_at >=
            pdMS_TO_TICKS(LOCAL_PAGE_TIMEOUT_MS)) {
        exit_local_ui_to_home();
    }
#if !CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    if (s_pomodoro.state == POMODORO_RUNNING ||
        s_pomodoro.state == POMODORO_PAUSED) {
        const uint32_t second =
            (pomodoro_model_remaining_ms(&s_pomodoro, now_ms) + 999U) / 1000U;
        if (second != s_last_timer_display_second) {
            s_last_timer_display_second = second;
            if (s_local_page == LOCAL_PAGE_TIMER_DETAIL) {
                render_local_page();
            } else if (s_local_page == LOCAL_PAGE_NONE &&
                       !s_quick_config_active && !s_system_select_active &&
                       !s_boot_animation_active) {
                show_status(NULL);
            }
        }
    } else {
        s_last_timer_display_second = UINT32_MAX;
    }
#endif
    sync_connection_and_mode_state();
    poll_single_action_joystick();
    poll_key_gestures();
    poll_key_pulses();
    poll_ble_slot_redirect();
    poll_ble_peer_rejection();
    poll_ble_reauth_needed();
    poll_ble_pairing_return();
    poll_ble_slot_clear_result();
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    poll_mist_display();
#endif
    poll_prompt_joystick();
    poll_eda_canvas_pan();
    update_status_indicator(false);
    update_under_key_self_test();
    poll_quick_config_save_failure();
    if (s_system_select_active &&
        config_store_get_platform() != CONFIG_PLATFORM_UNSELECTED) {
        s_system_select_active = false;
        leave_local_input_ownership();
        show_feedback(STATUS_FEEDBACK_SUCCESS, "SYSTEM SAVED");
    }
    if (!s_macro.active || (int32_t)(xTaskGetTickCount() - s_macro.wait_until) < 0) {
        return;
    }
    /* A timer/poll elapsed is not proof that the host report was submitted. */
    if (usb_service_keyboard_pending()) {
        return;
    }
    if (s_macro.release_usage != 0) {
        set_hid_usage(MACRO_TRANSIENT_SOURCE, s_macro.release_usage, false);
        if (s_macro.release_shift) {
            usb_service_set_modifier(MACRO_TRANSIENT_SOURCE, 225, false);
            s_macro.release_shift = false;
        }
        s_macro.release_usage = 0;
        return;
    }
    if (s_macro.text[s_macro.text_index] != '\0') {
        uint8_t usage = 0;
        bool shift = false;
        const char value = s_macro.text[s_macro.text_index++];
        if (ascii_usage(value, &usage, &shift)) {
            if (shift) {
                usb_service_set_modifier(MACRO_TRANSIENT_SOURCE, 225, true);
            }
            usb_service_set_key(MACRO_TRANSIENT_SOURCE, usage, true);
            s_macro.release_usage = usage;
            s_macro.release_shift = shift;
            s_macro.wait_until = xTaskGetTickCount() + pdMS_TO_TICKS(10);
        }
        return;
    }
    config_macro_step_t step;
    size_t count = 0;
    if (!config_store_get_macro_step(s_macro.id, s_macro.index, &step, &count) ||
        s_macro.index >= count) {
        macro_cancel();
        return;
    }
    if (step.op == CONFIG_MACRO_PRESS) {
        set_hid_usage(MACRO_SOURCE, step.usage, true);
        ++s_macro.index;
    } else if (step.op == CONFIG_MACRO_RELEASE) {
        set_hid_usage(MACRO_SOURCE, step.usage, false);
        ++s_macro.index;
    } else if (step.op == CONFIG_MACRO_TAP) {
        set_hid_usage(MACRO_TRANSIENT_SOURCE, step.usage, true);
        s_macro.release_usage = step.usage;
        s_macro.wait_until = xTaskGetTickCount() + pdMS_TO_TICKS(10);
        ++s_macro.index;
    } else if (step.op == CONFIG_MACRO_DELAY) {
        ++s_macro.index;
        s_macro.wait_until = xTaskGetTickCount() + pdMS_TO_TICKS(step.duration_ms);
    } else {
        snprintf(s_macro.text, sizeof(s_macro.text), "%s", step.text);
        s_macro.text_index = 0;
        ++s_macro.index;
    }
}

bool action_engine_config_activation_ready(void)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    return round_level_slice_active() &&
           s_round_setting_state == BOARD_ROUND_SETTING_APPLYING;
#else
    return false;
#endif
}

void action_engine_get_diagnostics(action_engine_diagnostics_t *diagnostics)
{
    if (diagnostics == NULL) {
        return;
    }
    static const char *const host_output_names[] = {
        [HOST_OUTPUT_ENABLED] = "enabled",
        [HOST_OUTPUT_LOCAL_UI] = "local_ui",
        [HOST_OUTPUT_DIAGNOSTIC] = "diagnostic_capture",
        [HOST_OUTPUT_DIAGNOSTIC_WAIT_NEUTRAL] = "wait_neutral",
        [HOST_OUTPUT_CALIBRATION] = "calibration",
        [HOST_OUTPUT_WAIT_NEUTRAL] = "wait_neutral",
    };
    static const char *const local_page_names[] = {
        [LOCAL_PAGE_NONE] = "none",
        [LOCAL_PAGE_FUNCTION] = "function",
        [LOCAL_PAGE_SETTINGS] = "settings",
        [LOCAL_PAGE_RESTART_CONFIRM] = "restart_confirm",
        [LOCAL_PAGE_TIMER_SETUP] = "timer_setup",
        [LOCAL_PAGE_TIMER_DETAIL] = "timer_detail",
        [LOCAL_PAGE_TIMER_CANCEL] = "timer_cancel",
        [LOCAL_PAGE_PROMPT_PALETTE] = "prompt_palette",
        [LOCAL_PAGE_SYSTEM] = "system",
        [LOCAL_PAGE_STATUS_DETAIL] = "status_detail",
        [LOCAL_PAGE_BATTERY] = "battery",
    };
    static const char *const round_setting_names[] = {
        [BOARD_ROUND_SETTING_EDITING] = "editing",
        [BOARD_ROUND_SETTING_APPLYING] = "applying",
        [BOARD_ROUND_SETTING_ERROR] = "error",
    };
    *diagnostics = (action_engine_diagnostics_t) {
        .host_output_state = host_output_names[s_host_output_state],
        .local_page = local_page_names[s_local_page],
        .round_setting_state = round_setting_names[s_round_setting_state],
        .quick_config_active = s_quick_config_active,
        .local_confirm_selected = s_local_confirm_selected,
        .round_feedback_active = s_round_feedback_active,
        .idle_circle_active = false,
        .idle_standby_active = s_idle_standby.active,
        .usb_standby_active = s_usb_standby_active,
        .lighting_preview_busy = host_lighting_preview_output_busy(),
    };
}

void action_engine_config_activated(void)
{
    host_action_events_reset();
    const bool boot_logo_waiting =
        s_boot_animation_active && s_boot_animation_complete;
    memset(s_key_gestures, 0, sizeof(s_key_gestures));
    finish_key_pulses();
    macro_cancel();
    s_prompt_joystick_direction_mask = joystick_prompt_direction_mask();
    s_normal_joystick_single_action = normal_joystick_single_action_mapping();
    if (!boot_logo_waiting) {
        s_boot_animation_active = false;
    }
    (void)lighting_preview_clear(&s_lighting_preview);
    (void)lighting_host_preview_clear(&s_host_lighting_preview);
    if (config_store_take_factory_reset_activated()) {
        const esp_err_t pairing_error = codex_micro_factory_reset();
        const esp_err_t prompt_error = prompt_store_ready()
                                           ? prompt_store_erase_all()
                                           : ESP_OK;
        /* Reserve the icon store for restart only after the earlier resets
         * succeeded; their error path stays running and must remain usable. */
        const esp_err_t icon_error = pairing_error == ESP_OK && prompt_error == ESP_OK
            ? config_protocol_screen_icon_factory_default() : ESP_OK;
        if (pairing_error != ESP_OK || prompt_error != ESP_OK ||
            icon_error != ESP_OK) {
            show_feedback(STATUS_FEEDBACK_ERROR, "FACTORY RESET FAILED");
            return;
        }
        action_engine_request_restart();
        return;
    }
    const bool local_save =
        s_system_save_pending || s_system_select_active ||
        (s_quick_config_active && s_quick_config_pending_footer != NULL);
    if (local_save && !s_system_save_pending) {
        pulse_save_feedback();
    }
    apply_feedback_config();
    if (s_usb_standby_active) {
        /* Activation can finish after the user powers off while holding a
         * control. Do not leave an already-committed OS save blocking wake. */
        if (s_system_save_pending) {
            s_system_save_pending = false;
            s_platform_setup_needed = true;
            s_local_page = LOCAL_PAGE_NONE;
            s_encoder_pressed = false;
            leave_local_input_ownership();
        }
        set_usb_standby_outputs();
        return;
    }
    s_usb_mounted = usb_service_is_mounted();
    s_ble_connected = codex_micro_ble_connected();
    s_codex_mode = codex_micro_mode();
    s_status_frame_valid = false;
    update_status_indicator(true);
    if (s_system_save_pending) {
        finish_os_settings_save();
        return;
    }
    if (!local_save && s_pomodoro.state != POMODORO_ALERT &&
        s_feedback.haptic_enabled && s_feedback.haptic_on_press &&
        s_feedback.haptic_on_profile &&
        !s_codex_attention_haptic.active) {
        (void)board_haptic_pulse(s_feedback.haptic_strength, s_feedback.haptic_duration_ms);
    }
    if (board_is_product_target() &&
        config_store_get_platform() == CONFIG_PLATFORM_UNSELECTED) {
        cancel_codex_attention_haptic();
        pomodoro_model_cancel(&s_pomodoro);
        s_timer_done_hint = false;
        s_local_page = LOCAL_PAGE_NONE;
        clear_pending_gestures(false);
        (void)board_haptic_stop();
        s_quick_config_active = false;
        s_quick_config_pending_footer = NULL;
        enter_system_select();
        return;
    }
    if (s_system_select_active) {
        s_system_select_active = false;
        leave_local_input_ownership();
        show_feedback(STATUS_FEEDBACK_SUCCESS, "SYSTEM SAVED");
        return;
    }
    if (s_pomodoro.state == POMODORO_ALERT) {
        return;
    }
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    if (round_level_slice_active()) {
        clear_lighting_preview();
        s_quick_config_active = false;
        s_quick_config_pending_footer = NULL;
        s_round_setting_state = BOARD_ROUND_SETTING_EDITING;
        s_local_page = LOCAL_PAGE_SETTINGS;
        s_encoder_pressed = false;
        s_local_last_input_at = xTaskGetTickCount();
        show_feedback(STATUS_FEEDBACK_SUCCESS, "SYSTEM SAVED");
        return;
    }
#endif
    if (s_quick_config_active) {
        const char *footer = s_quick_config_pending_footer;
        const quick_config_item_t item = s_quick_config.item;
        const uint8_t key_index = s_quick_config.key_index;
        s_quick_config_pending_footer = NULL;
        load_quick_config_model(item, key_index);
        render_quick_config(footer);
    } else if (s_local_page != LOCAL_PAGE_NONE) {
        render_local_page();
    } else if (!boot_logo_waiting) {
        show_status(NULL);
    }
}
