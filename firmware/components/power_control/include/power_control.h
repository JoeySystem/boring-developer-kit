#pragma once

#include <stdint.h>

#include "driver/gpio.h"
#include "esp_err.h"

esp_err_t power_control_init(gpio_num_t gpio, uint8_t active_level);
esp_err_t power_control_request_shutdown(uint32_t pulse_ms);
