#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"

typedef esp_err_t (*battery_service_publisher_t)(uint8_t level,
                                                 void *context);

/** Store a measured battery level and publish it when the value changes. */
void battery_service_update(uint8_t level);

/** Return false until the first valid fuel-gauge sample has been supplied. */
bool battery_service_latest(uint8_t *level);

/** Bind a transport publisher. The latest known value is pushed immediately. */
void battery_service_set_publisher(battery_service_publisher_t publisher,
                                   void *context);
