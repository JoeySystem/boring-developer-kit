#include "power_control.h"

#include <stdbool.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static gpio_num_t s_gpio = GPIO_NUM_NC;
static uint8_t s_active_level;
static bool s_initialized;

esp_err_t power_control_init(gpio_num_t gpio, uint8_t active_level)
{
    if (!GPIO_IS_VALID_OUTPUT_GPIO(gpio) || active_level > 1) {
        return ESP_ERR_INVALID_ARG;
    }
    const gpio_config_t config = {
        .pin_bit_mask = 1ULL << gpio,
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_ENABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    esp_err_t result = gpio_config(&config);
    if (result != ESP_OK) {
        return result;
    }
    s_gpio = gpio;
    s_active_level = active_level;
    result = gpio_set_level(s_gpio, !s_active_level);
    s_initialized = result == ESP_OK;
    return result;
}

esp_err_t power_control_request_shutdown(uint32_t pulse_ms)
{
    if (!s_initialized || pulse_ms == 0) {
        return ESP_ERR_INVALID_STATE;
    }
    esp_err_t result = gpio_set_level(s_gpio, s_active_level);
    if (result != ESP_OK) {
        return result;
    }
    vTaskDelay(pdMS_TO_TICKS(pulse_ms));
    return gpio_set_level(s_gpio, !s_active_level);
}
