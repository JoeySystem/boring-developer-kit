#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"

#define CW2015_I2C_ADDRESS 0x62
#define CW2015_REG_VCELL 0x02
#define CW2015_REG_SOC 0x04
#define CW2015_REG_MODE 0x0A

typedef struct {
    bool present;
    bool valid;
    float voltage_v;
    float soc_percent;
    esp_err_t last_error;
    int64_t last_update_time_ms;
} fuel_gauge_sample_t;

void cw2015_decode_measurement(const uint8_t vcell[2], const uint8_t soc[2],
                               fuel_gauge_sample_t *sample);

esp_err_t fuel_gauge_init(void);
esp_err_t fuel_gauge_read(fuel_gauge_sample_t *sample);
void fuel_gauge_poll(void);
void fuel_gauge_request_immediate_poll(void);
bool fuel_gauge_latest(fuel_gauge_sample_t *sample);
esp_err_t fuel_gauge_sleep(void);
esp_err_t fuel_gauge_wake(void);
