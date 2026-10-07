#include "cw2015.h"

#include <string.h>

#include "driver/i2c_master.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "sdkconfig.h"

#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
#include "board_matrix12_power_v2.h"
#endif

#define CW2015_POLL_INTERVAL_MS 2000
#define CW2015_ERROR_BACKOFF_MS 5000
#define CW2015_TIMEOUT_MS 100
#define CW2015_MODE_NORMAL 0x00
#define CW2015_MODE_SLEEP 0xC0

static const char *TAG = "cw2015";
static i2c_master_bus_handle_t s_bus;
static i2c_master_dev_handle_t s_device;
static fuel_gauge_sample_t s_latest;
static int64_t s_next_poll_ms;

static int64_t now_ms(void)
{
    return esp_timer_get_time() / 1000;
}

#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
static esp_err_t read_register(uint8_t address, uint8_t *value, size_t length)
{
    return i2c_master_transmit_receive(s_device, &address, 1, value, length,
                                       CW2015_TIMEOUT_MS);
}

static esp_err_t write_register(uint8_t address, uint8_t value)
{
    const uint8_t data[2] = {address, value};
    return i2c_master_transmit(s_device, data, sizeof(data), CW2015_TIMEOUT_MS);
}
#endif

esp_err_t fuel_gauge_init(void)
{
    memset(&s_latest, 0, sizeof(s_latest));
    s_next_poll_ms = 0;
#if !CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    s_latest.last_error = ESP_ERR_NOT_SUPPORTED;
    return ESP_ERR_NOT_SUPPORTED;
#else
    const i2c_master_bus_config_t bus_config = {
        .i2c_port = I2C_NUM_0,
        .sda_io_num = BOARD_MATRIX12_POWER_V2_FG_SDA_GPIO,
        .scl_io_num = BOARD_MATRIX12_POWER_V2_FG_SCL_GPIO,
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true,
    };
    esp_err_t result = i2c_new_master_bus(&bus_config, &s_bus);
    if (result != ESP_OK) {
        s_latest.last_error = result;
        ESP_LOGE(TAG, "create fuel-gauge I2C bus: %s",
                 esp_err_to_name(result));
        return result;
    }
    const i2c_device_config_t device_config = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = CW2015_I2C_ADDRESS,
        .scl_speed_hz = 100000,
    };
    result = i2c_master_bus_add_device(s_bus, &device_config, &s_device);
    if (result != ESP_OK) {
        s_latest.last_error = result;
        ESP_LOGE(TAG, "attach CW2015: %s", esp_err_to_name(result));
        return result;
    }
    result = i2c_master_probe(s_bus, CW2015_I2C_ADDRESS,
                              CW2015_TIMEOUT_MS);
    if (result != ESP_OK) {
        s_latest.last_error = result;
        ESP_LOGW(TAG, "CW2015 not detected at 0x%02X: %s",
                 CW2015_I2C_ADDRESS, esp_err_to_name(result));
        s_next_poll_ms = now_ms() + CW2015_ERROR_BACKOFF_MS;
        return result;
    }
    s_latest.present = true;
    result = fuel_gauge_wake();
    if (result != ESP_OK) {
        s_latest.last_error = result;
        ESP_LOGW(TAG, "CW2015 wake failed: %s", esp_err_to_name(result));
        s_next_poll_ms = now_ms() + CW2015_ERROR_BACKOFF_MS;
        return result;
    }
    return fuel_gauge_read(&s_latest);
#endif
}

esp_err_t fuel_gauge_read(fuel_gauge_sample_t *sample)
{
    if (sample == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
#if !CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    memset(sample, 0, sizeof(*sample));
    sample->last_error = ESP_ERR_NOT_SUPPORTED;
    return ESP_ERR_NOT_SUPPORTED;
#else
    const int64_t attempt_time_ms = now_ms();
    if (s_device == NULL) {
        fuel_gauge_sample_t next = s_latest;
        next.valid = false;
        next.last_error = ESP_ERR_INVALID_STATE;
        s_latest = next;
        *sample = next;
        s_next_poll_ms = attempt_time_ms + CW2015_ERROR_BACKOFF_MS;
        return ESP_ERR_INVALID_STATE;
    }
    uint8_t vcell[2];
    uint8_t soc[2];
    esp_err_t result = read_register(CW2015_REG_VCELL, vcell, sizeof(vcell));
    const bool present = result == ESP_OK;
    if (result == ESP_OK) {
        result = read_register(CW2015_REG_SOC, soc, sizeof(soc));
    }
    fuel_gauge_sample_t next = s_latest;
    next.present = present;
    next.valid = false;
    next.last_error = result;
    if (result == ESP_OK) {
        cw2015_decode_measurement(vcell, soc, &next);
        next.last_update_time_ms = attempt_time_ms;
    }
    s_latest = next;
    *sample = next;
    s_next_poll_ms = attempt_time_ms +
                     (result == ESP_OK ? CW2015_POLL_INTERVAL_MS
                                       : CW2015_ERROR_BACKOFF_MS);
    return result;
#endif
}

void fuel_gauge_poll(void)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    const int64_t poll_time_ms = now_ms();
    if (poll_time_ms < s_next_poll_ms) {
        return;
    }
    if (!s_latest.valid) {
        const esp_err_t wake_result = fuel_gauge_wake();
        if (wake_result != ESP_OK) {
            s_latest.last_error = wake_result;
            s_next_poll_ms = poll_time_ms + CW2015_ERROR_BACKOFF_MS;
            ESP_LOGW(TAG, "CW2015 wake retry failed: %s",
                     esp_err_to_name(wake_result));
            return;
        }
    }
    fuel_gauge_sample_t sample;
    const esp_err_t result = fuel_gauge_read(&sample);
    if (result != ESP_OK) {
        ESP_LOGW(TAG, "CW2015 sample failed: %s", esp_err_to_name(result));
    }
#endif
}

void fuel_gauge_request_immediate_poll(void)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    s_next_poll_ms = 0;
#endif
}

bool fuel_gauge_latest(fuel_gauge_sample_t *sample)
{
    if (sample == NULL) {
        return false;
    }
    *sample = s_latest;
    return sample->valid;
}

esp_err_t fuel_gauge_sleep(void)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    return s_device != NULL ? write_register(CW2015_REG_MODE, CW2015_MODE_SLEEP)
                            : ESP_ERR_INVALID_STATE;
#else
    return ESP_ERR_NOT_SUPPORTED;
#endif
}

esp_err_t fuel_gauge_wake(void)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    return s_device != NULL ? write_register(CW2015_REG_MODE, CW2015_MODE_NORMAL)
                            : ESP_ERR_INVALID_STATE;
#else
    return ESP_ERR_NOT_SUPPORTED;
#endif
}
