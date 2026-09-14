#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "cJSON.h"
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define CONFIG_STORE_DIGEST_HEX_LENGTH 64
#define CONFIG_STORE_MAX_MODIFIERS 8
#define CONFIG_STORE_MAX_TEXT_BYTES 256
/* Config names are Unicode code points; UTF-8 needs up to four bytes each. */
#define CONFIG_STORE_SHORT_NAME_BYTES (12 * 4)
#define CONFIG_STORE_PROFILE_NAME_BYTES (24 * 4)
#define CONFIG_STORE_MAX_UNDER_KEY_RGB 12

typedef enum {
    CONFIG_ACTION_NONE = 0,
    CONFIG_ACTION_KEY,
    CONFIG_ACTION_CONSUMER,
    CONFIG_ACTION_MOUSE,
    CONFIG_ACTION_MACRO,
    CONFIG_ACTION_PROMPT,
    CONFIG_ACTION_PROFILE,
    CONFIG_ACTION_DEVICE,
} config_action_type_t;

typedef enum {
    CONFIG_DEVICE_MACRO_CANCEL = 0,
    CONFIG_DEVICE_LIGHTING_TOGGLE,
    CONFIG_DEVICE_HAPTIC_TOGGLE,
    CONFIG_DEVICE_DISPLAY_NEXT,
} config_device_action_t;

typedef enum {
    CONFIG_QUICK_PRESET_NONE = 0,
    CONFIG_QUICK_PRESET_ENTER,
    CONFIG_QUICK_PRESET_ESCAPE,
    CONFIG_QUICK_PRESET_SPACE,
    CONFIG_QUICK_PRESET_TAB,
    CONFIG_QUICK_PRESET_BACKSPACE,
    CONFIG_QUICK_PRESET_PAGE_UP,
    CONFIG_QUICK_PRESET_PAGE_DOWN,
    CONFIG_QUICK_PRESET_ARROW_UP,
    CONFIG_QUICK_PRESET_ARROW_DOWN,
    CONFIG_QUICK_PRESET_ARROW_LEFT,
    CONFIG_QUICK_PRESET_ARROW_RIGHT,
    CONFIG_QUICK_PRESET_PLAY_PAUSE,
    CONFIG_QUICK_PRESET_MUTE,
    CONFIG_QUICK_PRESET_VOLUME_UP,
    CONFIG_QUICK_PRESET_VOLUME_DOWN,
    CONFIG_QUICK_PRESET_COUNT,
} config_quick_preset_t;

typedef enum {
    CONFIG_PLATFORM_UNSELECTED = 0,
    CONFIG_PLATFORM_MACOS,
    CONFIG_PLATFORM_WINDOWS_LINUX,
    CONFIG_PLATFORM_CUSTOM,
} config_platform_t;

typedef struct {
    config_action_type_t type;
    uint16_t usage;
    uint8_t modifiers[CONFIG_STORE_MAX_MODIFIERS];
    size_t modifier_count;
    uint8_t mouse_buttons;
    int8_t mouse_x;
    int8_t mouse_y;
    int8_t mouse_wheel;
    int8_t mouse_pan;
    uint8_t object_id;
    config_device_action_t device_action;
    char short_name[CONFIG_STORE_SHORT_NAME_BYTES + 1];
} config_action_t;

typedef enum {
    CONFIG_MACRO_PRESS = 0,
    CONFIG_MACRO_RELEASE,
    CONFIG_MACRO_TAP,
    CONFIG_MACRO_TEXT,
    CONFIG_MACRO_DELAY,
} config_macro_op_t;

typedef struct {
    config_macro_op_t op;
    uint16_t usage;
    uint16_t duration_ms;
    char text[CONFIG_STORE_MAX_TEXT_BYTES + 1];
} config_macro_step_t;

typedef struct {
    uint8_t red;
    uint8_t green;
    uint8_t blue;
} config_rgb_t;

typedef struct {
    bool lighting_enabled;
    uint8_t lighting_brightness;
    config_rgb_t status_rgb[8];
    config_rgb_t under_key_rgb[CONFIG_STORE_MAX_UNDER_KEY_RGB];
    bool haptic_enabled;
    uint8_t haptic_strength;
    uint16_t haptic_duration_ms;
    bool haptic_on_press;
    bool haptic_on_profile;
    bool haptic_on_encoder;
    bool haptic_on_joystick;
    bool haptic_on_task;
    uint8_t display_brightness;
    uint16_t display_rotation;
    bool display_show_control_hints;
    bool joystick_calibrated;
    uint8_t joystick_filter;
    int joystick_minimum_x;
    int joystick_center_x;
    int joystick_maximum_x;
    int joystick_minimum_y;
    int joystick_center_y;
    int joystick_maximum_y;
    int joystick_deadzone_x;
    int joystick_deadzone_y;
    bool joystick_invert_x;
    bool joystick_invert_y;
} config_feedback_t;

typedef struct {
    int minimum_x;
    int center_x;
    int maximum_x;
    int minimum_y;
    int center_y;
    int maximum_y;
    int deadzone_x;
    int deadzone_y;
} config_joystick_calibration_t;

typedef struct {
    uint32_t active_generation;
    char active_digest[CONFIG_STORE_DIGEST_HEX_LENGTH + 1];
    bool pending;
    bool activation_failed;
    uint32_t pending_generation;
    char pending_digest[CONFIG_STORE_DIGEST_HEX_LENGTH + 1];
} config_store_status_t;

/**
 * Initialize the dedicated NVS partition and load the active or default config.
 *
 * The normal boot path never erases configuration implicitly. Recovery from a
 * damaged partition remains an explicit firmware-maintenance operation.
 */
esp_err_t config_store_init(void);

/** Return a heap-owned canonical JSON copy. Caller releases it with free(). */
esp_err_t config_store_get_json(char **json, uint32_t *generation,
                                char digest[CONFIG_STORE_DIGEST_HEX_LENGTH + 1]);

/** Validate a candidate against schema-v1 device constraints without persisting it. */
esp_err_t config_store_validate(const cJSON *candidate, const char *expected_digest,
                                char *reason, size_t reason_size);

/** Persist a validated candidate in the inactive slot and mark it pending. */
esp_err_t config_store_stage(const cJSON *candidate, const char *expected_digest,
                             uint32_t base_generation, char *reason, size_t reason_size);

/** Activate a pending candidate once all physical controls are neutral. */
bool config_store_poll(bool inputs_neutral);

/** Consume the one-shot notice that a staged factory reset just activated. */
bool config_store_take_factory_reset_activated(void);

/** Fast lock-free hint used to avoid polling the store while no activation is pending. */
bool config_store_has_pending(void);

void config_store_get_status(config_store_status_t *status);

/** Read or atomically select the operating-system preference and matching Profile. */
config_platform_t config_store_get_platform(void);
esp_err_t config_store_select_platform(config_platform_t platform);

/** Physical output contexts: USB = 0, Bluetooth slots = 1..3. Setter is RAM-only. */
#define CONFIG_PLATFORM_CONTEXT_COUNT 4
void config_store_set_platform_context(uint8_t context);
config_platform_t config_store_get_context_platform(uint8_t context);
/** Clear only a connection's OS preference; retry if a config activation is pending. */
esp_err_t config_store_clear_context_platform(uint8_t context);

/** Read/write the last successfully started Pomodoro duration (1-60 min). */
uint8_t config_store_get_pomodoro_minutes(void);
esp_err_t config_store_set_pomodoro_minutes(uint8_t minutes);

/** Read/write the local inactivity standby timeout: OFF, 5, 15, or 30 min. */
uint8_t config_store_get_standby_minutes(void);
esp_err_t config_store_set_standby_minutes(uint8_t minutes);

/** Read the saved BLE name (default when absent); capacity includes the NUL. */
esp_err_t config_store_get_ble_name(char *out, size_t capacity);
/** Save for the next normal boot; pending configuration activation returns INVALID_STATE. */
esp_err_t config_store_set_ble_name(const char *name);

/**
 * Remember whether the user explicitly left the product formally powered on.
 * A missing value defaults to on so firmware updates and first boot remain usable.
 */
bool config_store_get_user_powered_on(void);
esp_err_t config_store_set_user_powered_on(bool powered_on);

/** Stage the built-in default config after checking generation and confirmation. */
esp_err_t config_store_factory_default(uint32_t base_generation, const char *confirmation,
                                       char *reason, size_t reason_size);

/** Stage the built-in default config for a trusted local UI request. */
esp_err_t config_store_stage_factory_default(uint32_t base_generation,
                                             char *reason, size_t reason_size);

/** Recovery path used by the reserved boot chord; bypasses generation confirmation. */
esp_err_t config_store_force_factory_default(void);

bool config_store_get_action(const char *control_id, config_action_t *action);
bool config_store_get_macro_step(uint8_t macro_id, size_t index, config_macro_step_t *step,
                                 size_t *step_count);
bool config_store_get_feedback(config_feedback_t *feedback);
bool config_store_get_active_profile(uint8_t *profile_id, char *name, size_t name_size);
size_t config_store_get_profile_count(void);
bool config_store_get_profile_at(size_t index, uint8_t *profile_id, char *name, size_t name_size);
bool config_store_get_active_profile_index(size_t *index);

size_t config_store_get_quick_preset_count(void);
const char *config_store_get_quick_preset_name(size_t index);
bool config_store_match_quick_preset(const char *control_id, size_t *index);

/** Persistently change the active profile or a device-level toggle. */
esp_err_t config_store_select_profile(uint8_t profile_id);
esp_err_t config_store_select_next_profile(void);
esp_err_t config_store_apply_device_action(config_device_action_t action);
esp_err_t config_store_set_quick_preset(uint8_t key_index, size_t preset_index);
esp_err_t config_store_set_lighting_level(uint8_t brightness_percent);
esp_err_t config_store_set_haptic_level(uint8_t strength_percent);

/** Stage one validated joystick calibration through the normal config transaction. */
esp_err_t config_store_set_joystick_calibration(
    const config_joystick_calibration_t *calibration,
    uint32_t base_generation, char *reason, size_t reason_size);

#ifdef __cplusplus
}
#endif
