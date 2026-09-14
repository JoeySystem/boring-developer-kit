#include "board.h"

/*
 * DEVKIT_BOOT_BUTTON is the safe Engineering Alpha target. It exposes only the
 * active-low GPIO0 Boot button as BOARD_CONTROL_KEY_1 and deliberately provides
 * no product output peripherals. Keep this target independent of PCB2 wiring so
 * USB/protocol tests remain usable on a generic ESP32-S3 development board.
 */

#include "driver/gpio.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "sdkconfig.h"

#if CONFIG_MACROPAD_BOARD_DEVKIT_BOOT_BUTTON

#define DEMO_BUTTON_GPIO GPIO_NUM_0
#define DEBOUNCE_MS 20

static bool s_stable;
static bool s_sample;
static TickType_t s_changed_at;
static bool s_event_pending;

esp_err_t board_init(void)
{
    const gpio_config_t config = {
        .pin_bit_mask = 1ULL << DEMO_BUTTON_GPIO,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    esp_err_t error = gpio_config(&config);
    if (error != ESP_OK) {
        return error;
    }
    s_stable = gpio_get_level(DEMO_BUTTON_GPIO) == 0;
    s_sample = s_stable;
    s_changed_at = xTaskGetTickCount();
    s_event_pending = false;
    return ESP_OK;
}

void board_poll(void)
{
    const bool next = gpio_get_level(DEMO_BUTTON_GPIO) == 0;
    const TickType_t now = xTaskGetTickCount();
    if (next != s_sample) {
        s_sample = next;
        s_changed_at = now;
    }
    if (s_sample != s_stable && (now - s_changed_at) >= pdMS_TO_TICKS(DEBOUNCE_MS)) {
        /* A single pending slot is sufficient for one human-operated demo key. */
        s_stable = s_sample;
        s_event_pending = true;
    }
}

bool board_next_event(board_event_t *event)
{
    if (!s_event_pending || event == NULL) {
        return false;
    }
    event->control = BOARD_CONTROL_KEY_1;
    event->pressed = s_stable;
    event->feedback_only = false;
    s_event_pending = false;
    return true;
}

const char *board_target_name(void)
{
    return "DEVKIT_BOOT_BUTTON";
}

const char *board_hardware_id(void)
{
    /* Preserve the Engineering Alpha discovery identity. */
    return "WMP-S3-REV-A";
}

size_t board_key_count(void)
{
    return 1;
}

size_t board_status_rgb_count(void)
{
    return 0;
}

size_t board_under_key_rgb_count(void)
{
    return 0;
}

bool board_is_product_target(void)
{
    return false;
}

bool board_inputs_neutral(void)
{
    return !s_stable;
}

size_t board_get_active_controls(board_control_t *controls, size_t capacity)
{
    if (controls == NULL || capacity == 0 || !s_stable) {
        return 0;
    }
    controls[0] = BOARD_CONTROL_KEY_1;
    return 1;
}

bool board_get_joystick_radial(float *angle_turns)
{
    (void)angle_turns;
    return false;
}

esp_err_t board_set_status_rgb(size_t index, uint8_t red, uint8_t green, uint8_t blue)
{
    (void)index;
    (void)red;
    (void)green;
    (void)blue;
    return ESP_ERR_NOT_SUPPORTED;
}

esp_err_t board_set_under_key_rgb(size_t index, uint8_t red, uint8_t green, uint8_t blue)
{
    (void)index;
    (void)red;
    (void)green;
    (void)blue;
    return ESP_ERR_NOT_SUPPORTED;
}

esp_err_t board_apply_rgb(
    const board_rgb_t status[BOARD_STATUS_RGB_COUNT],
    const board_rgb_t under_key[BOARD_MAX_UNDER_KEY_RGB_COUNT])
{
    (void)status;
    (void)under_key;
    return ESP_ERR_NOT_SUPPORTED;
}

esp_err_t board_apply_status_rgb(
    const board_rgb_t status[BOARD_STATUS_RGB_COUNT])
{
    (void)status;
    return ESP_ERR_NOT_SUPPORTED;
}

esp_err_t board_haptic_pulse(uint8_t strength_percent, uint16_t duration_ms)
{
    (void)strength_percent;
    (void)duration_ms;
    return ESP_ERR_NOT_SUPPORTED;
}

esp_err_t board_haptic_stop(void)
{
    return ESP_ERR_NOT_SUPPORTED;
}

esp_err_t board_display_fill(uint16_t rgb565)
{
    (void)rgb565;
    return ESP_ERR_NOT_SUPPORTED;
}

esp_err_t board_display_show_boot_logo(uint8_t density_percent)
{
    (void)density_percent;
    return ESP_ERR_NOT_SUPPORTED;
}

esp_err_t board_set_joystick_config(const board_joystick_config_t *config)
{
    (void)config;
    return ESP_ERR_NOT_SUPPORTED;
}

esp_err_t board_set_display_config(uint8_t brightness_percent, uint16_t rotation)
{
    (void)brightness_percent; (void)rotation;
    return ESP_ERR_NOT_SUPPORTED;
}

esp_err_t board_display_show_status(const char *profile, const char *output,
                                    const char *mode, const char *hint,
                                    bool connected, bool codex_mode)
{
    (void)profile; (void)output; (void)mode; (void)hint; (void)connected;
    (void)codex_mode;
    return ESP_ERR_NOT_SUPPORTED;
}

esp_err_t board_display_show_quick_config(const char *previous_item,
                                          const char *current_item,
                                          const char *next_item,
                                          const char *candidate_value,
                                          const char *saved_value,
                                          bool candidate_saved,
                                          const char *footer)
{
    (void)previous_item; (void)current_item; (void)next_item;
    (void)candidate_value; (void)saved_value; (void)candidate_saved; (void)footer;
    return ESP_ERR_NOT_SUPPORTED;
}

esp_err_t board_display_show_system_select(size_t selected_index,
                                           const char *footer)
{
    (void)selected_index; (void)footer;
    return ESP_ERR_NOT_SUPPORTED;
}

esp_err_t board_display_show_two_choice(const char *title,
                                        const char *first_option,
                                        const char *second_option,
                                        size_t selected_index,
                                        const char *footer,
                                        uint16_t accent_rgb565)
{
    (void)title; (void)first_option; (void)second_option;
    (void)selected_index; (void)footer; (void)accent_rgb565;
    return ESP_ERR_NOT_SUPPORTED;
}

esp_err_t board_display_show_local(const char *title, const char *primary,
                                   const char *secondary, const char *footer,
                                   uint16_t accent_rgb565)
{
    (void)title; (void)primary; (void)secondary; (void)footer;
    (void)accent_rgb565;
    return ESP_ERR_NOT_SUPPORTED;
}

#endif
