#include "board.h"

/*
 * Shared implementation for the two 12-key matrix hardware targets. Pin and
 * polarity differences remain selected at compile time; MATRIX12_V1 keeps its
 * original contract while MATRIX12_POWER_V2 follows the revised schematic.
 */

#include <ctype.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "board_bmr_icons.h"
#include "board_display_logic.h"
#include "board_input_logic.h"
#include "board_mist_icons.h"
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
#include "board_matrix12_power_v2.h"
#define BOARD_MATRIX12_V1_KEY_COUNT BOARD_MATRIX12_POWER_V2_KEY_COUNT
#define BOARD_MATRIX12_V1_STATUS_RGB_COUNT BOARD_MATRIX12_POWER_V2_STATUS_RGB_COUNT
#define BOARD_MATRIX12_V1_UNDER_KEY_RGB_COUNT BOARD_MATRIX12_POWER_V2_UNDER_KEY_RGB_COUNT
#define BOARD_MATRIX12_V1_MATRIX_ROW_COUNT BOARD_MATRIX12_POWER_V2_MATRIX_ROW_COUNT
#define BOARD_MATRIX12_V1_MATRIX_COLUMN_COUNT BOARD_MATRIX12_POWER_V2_MATRIX_COLUMN_COUNT
#define BOARD_MATRIX12_V1_ROW0_GPIO BOARD_MATRIX12_POWER_V2_ROW0_GPIO
#define BOARD_MATRIX12_V1_ROW1_GPIO BOARD_MATRIX12_POWER_V2_ROW1_GPIO
#define BOARD_MATRIX12_V1_ROW2_GPIO BOARD_MATRIX12_POWER_V2_ROW2_GPIO
#define BOARD_MATRIX12_V1_ROW3_GPIO BOARD_MATRIX12_POWER_V2_ROW3_GPIO
#define BOARD_MATRIX12_V1_COL0_GPIO BOARD_MATRIX12_POWER_V2_COL0_GPIO
#define BOARD_MATRIX12_V1_COL1_GPIO BOARD_MATRIX12_POWER_V2_COL1_GPIO
#define BOARD_MATRIX12_V1_COL2_GPIO BOARD_MATRIX12_POWER_V2_COL2_GPIO
#define BOARD_MATRIX12_V1_COL3_GPIO BOARD_MATRIX12_POWER_V2_COL3_GPIO
#define BOARD_MATRIX12_V1_MOTOR_GPIO BOARD_MATRIX12_POWER_V2_MOTOR_GPIO
#define BOARD_MATRIX12_V1_DISPLAY_BACKLIGHT_GPIO BOARD_MATRIX12_POWER_V2_DISPLAY_BACKLIGHT_GPIO
#define BOARD_MATRIX12_V1_DISPLAY_CS_GPIO BOARD_MATRIX12_POWER_V2_DISPLAY_CS_GPIO
#define BOARD_MATRIX12_V1_DISPLAY_RESET_GPIO BOARD_MATRIX12_POWER_V2_DISPLAY_RESET_GPIO
#define BOARD_MATRIX12_V1_DISPLAY_DC_GPIO BOARD_MATRIX12_POWER_V2_DISPLAY_DC_GPIO
#define BOARD_MATRIX12_V1_DISPLAY_MOSI_GPIO BOARD_MATRIX12_POWER_V2_DISPLAY_MOSI_GPIO
#define BOARD_MATRIX12_V1_DISPLAY_SCLK_GPIO BOARD_MATRIX12_POWER_V2_DISPLAY_SCLK_GPIO
#define BOARD_MATRIX12_V1_UNDER_KEY_RGB_GPIO BOARD_MATRIX12_POWER_V2_UNDER_KEY_RGB_GPIO
#define BOARD_MATRIX12_V1_ENCODER_B_GPIO BOARD_MATRIX12_POWER_V2_ENCODER_B_GPIO
#define BOARD_MATRIX12_V1_ENCODER_A_GPIO BOARD_MATRIX12_POWER_V2_ENCODER_A_GPIO
#define BOARD_MATRIX12_V1_ENCODER_KEY_GPIO BOARD_MATRIX12_POWER_V2_ENCODER_KEY_GPIO
#define BOARD_MATRIX12_V1_JOYSTICK_KEY_GPIO BOARD_MATRIX12_POWER_V2_JOYSTICK_KEY_GPIO
#define BOARD_MATRIX12_V1_JOYSTICK_X_ADC_CHANNEL BOARD_MATRIX12_POWER_V2_JOYSTICK_X_ADC_CHANNEL
#define BOARD_MATRIX12_V1_JOYSTICK_Y_ADC_CHANNEL BOARD_MATRIX12_POWER_V2_JOYSTICK_Y_ADC_CHANNEL
#define BOARD_MATRIX12_V1_DISPLAY_WIDTH BOARD_MATRIX12_POWER_V2_DISPLAY_WIDTH
#define BOARD_MATRIX12_V1_DISPLAY_HEIGHT BOARD_MATRIX12_POWER_V2_DISPLAY_HEIGHT
#define BOARD_MATRIX12_V1_DISPLAY_RAM_WIDTH BOARD_MATRIX12_POWER_V2_DISPLAY_RAM_WIDTH
#define BOARD_MATRIX12_V1_DISPLAY_RAM_HEIGHT BOARD_MATRIX12_POWER_V2_DISPLAY_RAM_HEIGHT
#define BOARD_MATRIX12_V1_DISPLAY_X_GAP BOARD_MATRIX12_POWER_V2_DISPLAY_X_GAP
#define BOARD_MATRIX12_V1_DISPLAY_Y_GAP BOARD_MATRIX12_POWER_V2_DISPLAY_Y_GAP
#define BOARD_MATRIX12_V1_DISPLAY_MOUNT_ROTATION BOARD_MATRIX12_POWER_V2_DISPLAY_MOUNT_ROTATION
#define BOARD_MATRIX12_V1_MOTOR_MAX_STRENGTH_PERCENT BOARD_MATRIX12_POWER_V2_MOTOR_MAX_STRENGTH_PERCENT
#define BOARD_MATRIX12_V1_UNDER_KEY_OUTPUT_PERCENT BOARD_MATRIX12_POWER_V2_UNDER_KEY_OUTPUT_PERCENT
#else
#include "board_matrix12_v1.h"
#endif
#include "driver/gpio.h"
#include "driver/ledc.h"
#include "driver/spi_master.h"
#include "esp_adc/adc_oneshot.h"
#include "esp_check.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_st7735.h"
#include "esp_rom_sys.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "led_strip.h"
#include "power_button.h"
#include "power_control.h"
#include "sdkconfig.h"

#if CONFIG_MACROPAD_BOARD_MATRIX12_V1 || CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2

#define DEBOUNCE_MS 20
#define JOYSTICK_SAMPLE_MS 10
#define JOYSTICK_DEADZONE 650
#define JOYSTICK_RELEASE_DEADZONE 450
#define JOYSTICK_X_HARDWARE_INVERTED true
#define JOYSTICK_Y_HARDWARE_INVERTED true
/*
 * Basic-control bring-up: keep the joystick available while the remaining
 * power-management hardware is diagnosed.
 */
#define MATRIX12_JOYSTICK_FUNCTION_ENABLED true
/*
 * Bench validation now shows stable centering and full left/right travel on
 * switch_AD1/GPIO1, so horizontal and radial output can be enabled again.
 */
#define MATRIX12_JOYSTICK_HORIZONTAL_FUNCTION_ENABLED true
/*
 * Bench validation confirms GPIO41 is low when released and high while the
 * encoder is pressed; PWR_LOAD_EN and SYS_RAW remain stable on a short press.
 */
#define MATRIX12_ENCODER_KEY_FUNCTION_ENABLED true
#define EVENT_QUEUE_CAPACITY 48
#define ENCODER_EDGE_QUEUE_CAPACITY 64
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
/* Return to the resting A/B state; tolerate one missed intermediate edge. */
#define ENCODER_TRANSITIONS_PER_DETENT 2
#else
#define ENCODER_TRANSITIONS_PER_DETENT 4
#endif
#define MOTOR_MAX_STRENGTH_PERCENT BOARD_MATRIX12_V1_MOTOR_MAX_STRENGTH_PERCENT
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
#define MOTOR_MAX_DURATION_MS 250
#else
#define MOTOR_MAX_DURATION_MS 200
#endif
#define DISPLAY_TRANSFER_LINES 10
#define DISPLAY_CONTROLLER_CLEAR_LINES 8
#define DISPLAY_TEXT_BAND_LINES 32
#define ROUND_DISPLAY_TRANSFER_LINES DISPLAY_TEXT_BAND_LINES
#define DISPLAY_MAX_WIDTH BOARD_MATRIX12_V1_DISPLAY_WIDTH
#define MATRIX12_DISPLAY_ACTIVE_AREA_TENTHS_UM UINT32_C(152064)
#define MATRIX12_BOOT_APERTURE_DIAMETER_TENTHS_UM UINT32_C(148000)
#define MATRIX12_BOOT_APERTURE_DIAMETER_PIXELS                              \
    ((MATRIX12_BOOT_APERTURE_DIAMETER_TENTHS_UM * DISPLAY_MAX_WIDTH +      \
      MATRIX12_DISPLAY_ACTIVE_AREA_TENTHS_UM / 2U) /                       \
     MATRIX12_DISPLAY_ACTIVE_AREA_TENTHS_UM)
#define MATRIX12_IDLE_DOT_PITCH 4
#define MATRIX12_IDLE_DOT_SIZE 2
#define MATRIX12_ROUND_BACKGROUND_DOT_SIZE 1
#define POWER_BUTTON_DEBOUNCE_MS 25U
#define POWER_BUTTON_LONG_PRESS_MS 2500U
/* Sample SYS_SW rose at about 6 s on battery power (2026-09-13).
 * Candidate standby margin only; elapsed time is not hardware-latch readback.
 */
#define POWER_BUTTON_WAKE_HOLD_MS 6500U
#define POWER_OFF_PULSE_MS 200U

/*
 * The 0.85-inch 128x128 glass needs its panel-specific ST7735 analogue,
 * frame-rate and gamma setup. The generic 128x160 defaults can leave part of
 * this normally-white panel outside a stable driven region.
 */
static const st7735_lcd_init_cmd_t matrix12_st7735_init_cmds[] = {
    {ST7735_SLPOUT, (uint8_t[]){0x00}, 1, 120},
    {ST7735_FRMCTR1, (uint8_t[]){0x05, 0x3C, 0x3C}, 3, 0},
    {ST7735_FRMCTR2, (uint8_t[]){0x05, 0x3C, 0x3C}, 3, 0},
    {ST7735_FRMCTR3, (uint8_t[]){0x05, 0x3C, 0x3C, 0x05, 0x3C, 0x3C}, 6, 0},
    {ST7735_INVCTR, (uint8_t[]){0x03}, 1, 0},
    {ST7735_PWCTR1, (uint8_t[]){0xA4, 0x04, 0x84}, 3, 0},
    {ST7735_PWCTR2, (uint8_t[]){0xC8}, 1, 0},
    {ST7735_PWCTR3, (uint8_t[]){0x0D, 0x00}, 2, 0},
    {ST7735_PWCTR4, (uint8_t[]){0x8D, 0x2A}, 2, 0},
    {ST7735_PWCTR5, (uint8_t[]){0x8D, 0xEE}, 2, 0},
    {ST7735_VMCTR1, (uint8_t[]){0x1D}, 1, 0},
    {ST7735_GMCTRP1,
     (uint8_t[]){0x0C, 0x0A, 0x06, 0x00, 0x1A, 0x11, 0x0B, 0x0A,
                 0x0B, 0x0C, 0x19, 0x33, 0x00, 0x06, 0x02, 0x10},
     16, 0},
    {ST7735_GMCTRN1,
     (uint8_t[]){0x0D, 0x0F, 0x03, 0x00, 0x11, 0x0A, 0x06, 0x08,
                 0x09, 0x0D, 0x1C, 0x3A, 0x00, 0x09, 0x07, 0x10},
     16, 0},
    {ST7735_TEON, (uint8_t[]){0x00}, 1, 0},
    {ST7735_COLMOD, (uint8_t[]){0x05}, 1, 0},
    {ST7735_MADCTL, (uint8_t[]){0xC8}, 1, 0},
};

typedef struct {
    gpio_num_t gpio;
    board_control_t control;
    bool stable;
    bool sample;
    TickType_t changed_at;
} digital_input_t;

static digital_input_t s_inputs[] = {
#if CONFIG_MACROPAD_BOARD_MATRIX12_V1
    {.gpio = BOARD_MATRIX12_V1_ENCODER_KEY_GPIO, .control = BOARD_CONTROL_ENCODER_PRESS},
#endif
    {.gpio = BOARD_MATRIX12_V1_JOYSTICK_KEY_GPIO, .control = BOARD_CONTROL_JOYSTICK_PRESS},
};

typedef struct {
    uint8_t row;
    uint8_t column;
    board_control_t control;
    bool stable;
    bool sample;
    TickType_t changed_at;
} matrix_key_t;

/*
 * Sparse 4x4 population from the final PCB pad nets. The RGB chain follows
 * this same KEY1 -> KEY12 order, so logical key index and LED index match.
 */
static matrix_key_t s_matrix_keys[BOARD_MATRIX12_V1_KEY_COUNT] = {
    {.row = 0, .column = 1, .control = BOARD_CONTROL_KEY_1},
    {.row = 0, .column = 2, .control = BOARD_CONTROL_KEY_2},
    {.row = 0, .column = 3, .control = BOARD_CONTROL_KEY_3},
    {.row = 1, .column = 0, .control = BOARD_CONTROL_KEY_4},
    {.row = 1, .column = 1, .control = BOARD_CONTROL_KEY_5},
    {.row = 1, .column = 2, .control = BOARD_CONTROL_KEY_6},
    {.row = 1, .column = 3, .control = BOARD_CONTROL_KEY_7},
    {.row = 2, .column = 1, .control = BOARD_CONTROL_KEY_8},
    {.row = 2, .column = 2, .control = BOARD_CONTROL_KEY_9},
    {.row = 2, .column = 3, .control = BOARD_CONTROL_KEY_10},
    {.row = 3, .column = 1, .control = BOARD_CONTROL_KEY_11},
    {.row = 3, .column = 2, .control = BOARD_CONTROL_KEY_12},
};

static const gpio_num_t s_matrix_rows[BOARD_MATRIX12_V1_MATRIX_ROW_COUNT] = {
    BOARD_MATRIX12_V1_ROW0_GPIO, BOARD_MATRIX12_V1_ROW1_GPIO,
    BOARD_MATRIX12_V1_ROW2_GPIO, BOARD_MATRIX12_V1_ROW3_GPIO,
};

static const gpio_num_t s_matrix_columns[BOARD_MATRIX12_V1_MATRIX_COLUMN_COUNT] = {
    BOARD_MATRIX12_V1_COL0_GPIO, BOARD_MATRIX12_V1_COL1_GPIO,
    BOARD_MATRIX12_V1_COL2_GPIO, BOARD_MATRIX12_V1_COL3_GPIO,
};

static board_event_t s_events[EVENT_QUEUE_CAPACITY];
static size_t s_event_read;
static size_t s_event_write;
static adc_oneshot_unit_handle_t s_adc;
static led_strip_handle_t s_under_key_strip;
static esp_lcd_panel_handle_t s_panel;
static uint16_t s_display_buffer[DISPLAY_MAX_WIDTH * DISPLAY_TRANSFER_LINES];
static uint16_t s_controller_clear_buffer[
    BOARD_MATRIX12_V1_DISPLAY_RAM_WIDTH * DISPLAY_CONTROLLER_CLEAR_LINES];
/* Separate storage prevents a status update from overwriting an in-flight full fill. */
static uint16_t s_status_bar_buffer[DISPLAY_MAX_WIDTH * DISPLAY_TRANSFER_LINES];
/*
 * SPI LCD transfers retain their source pointer until DMA completes. With one
 * queued transaction, alternating two buffers guarantees that a band is no
 * longer in flight before it is rendered into again.
 */
static uint16_t s_text_band_buffers[2][DISPLAY_MAX_WIDTH * DISPLAY_TEXT_BAND_LINES];
static uint16_t s_display_width = BOARD_MATRIX12_V1_DISPLAY_WIDTH;
static uint16_t s_display_height = BOARD_MATRIX12_V1_DISPLAY_HEIGHT;
static uint8_t s_display_base_brightness;
static uint8_t s_display_idle_scale = 100;
static TickType_t s_motor_stop_at;
static bool s_motor_running;
static board_encoder_decoder_t s_encoder;
static QueueHandle_t s_encoder_edges;
static TickType_t s_last_joystick_sample;
static int s_joystick_minimum_x = 200;
static int s_joystick_center_x = 2048;
static int s_joystick_maximum_x = 3895;
static int s_joystick_minimum_y = 200;
static int s_joystick_center_y = 2048;
static int s_joystick_maximum_y = 3895;
static int s_joystick_deadzone_x = JOYSTICK_DEADZONE;
static int s_joystick_deadzone_y = JOYSTICK_DEADZONE;
static uint8_t s_joystick_filter = 25;
static bool s_joystick_invert_x;
static bool s_joystick_invert_y;
static int s_joystick_raw_x = 2048;
static int s_joystick_raw_y = 2048;
static int s_joystick_filtered_x = 2048;
static int s_joystick_filtered_y = 2048;
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
static power_button_t s_power_button;
static bool s_shutdown_request_pending;
static bool s_power_button_wake_mode;
#endif
static bool s_joystick_left;
static bool s_joystick_right;
static bool s_joystick_up;
static bool s_joystick_down;
static bool s_joystick_radial_active;
static bool s_joystick_calibration_active;
static bool s_status_page_active;

static bool input_pressed(gpio_num_t gpio)
{
    return gpio_get_level(gpio) == 0;
}

static uint32_t board_ticks_to_ms(TickType_t ticks)
{
    return (uint32_t)(((uint64_t)ticks * 1000U) / configTICK_RATE_HZ);
}

static void enqueue_event(board_control_t control, bool pressed)
{
    const size_t next = (s_event_write + 1) % EVENT_QUEUE_CAPACITY;
    if (next == s_event_read) {
        /* Keep input polling non-blocking; diagnostics will own overflow reporting later. */
        return;
    }
    s_events[s_event_write] = (board_event_t){.control = control, .pressed = pressed};
    s_event_write = next;
}

static void enqueue_feedback_event(board_control_t control)
{
    const size_t next = (s_event_write + 1) % EVENT_QUEUE_CAPACITY;
    if (next == s_event_read) {
        return;
    }
    s_events[s_event_write] = (board_event_t) {
        .control = control,
        .pressed = true,
        .feedback_only = true,
    };
    s_event_write = next;
}

static void encoder_gpio_isr(void *context)
{
    (void)context;
    const uint8_t state =
        (gpio_get_level(BOARD_MATRIX12_V1_ENCODER_A_GPIO) << 1) |
        gpio_get_level(BOARD_MATRIX12_V1_ENCODER_B_GPIO);
    (void)xQueueSendFromISR(s_encoder_edges, &state, NULL);
}

static void scan_matrix(bool pressed[BOARD_MATRIX12_V1_KEY_COUNT])
{
    memset(pressed, 0, sizeof(bool) * BOARD_MATRIX12_V1_KEY_COUNT);
    for (size_t row = 0; row < BOARD_MATRIX12_V1_MATRIX_ROW_COUNT; ++row) {
        /* D2-D13 have their cathodes on ROW0-ROW3, so the selected row sinks. */
        gpio_set_level(s_matrix_rows[row], 0);
        esp_rom_delay_us(3);
        for (size_t index = 0; index < BOARD_MATRIX12_V1_KEY_COUNT; ++index) {
            if (s_matrix_keys[index].row == row) {
                pressed[index] =
                    gpio_get_level(s_matrix_columns[s_matrix_keys[index].column]) == 0;
            }
        }
        /* Open-drain high releases the row before the next one is selected. */
        gpio_set_level(s_matrix_rows[row], 1);
    }
}

static esp_err_t init_inputs(void)
{
    const uint64_t direct_input_mask =
        (1ULL << BOARD_MATRIX12_V1_ENCODER_A_GPIO) |
        (1ULL << BOARD_MATRIX12_V1_ENCODER_B_GPIO) |
        (1ULL << BOARD_MATRIX12_V1_ENCODER_KEY_GPIO) |
        (1ULL << BOARD_MATRIX12_V1_JOYSTICK_KEY_GPIO);
    const gpio_config_t direct_input_config = {
        .pin_bit_mask = direct_input_mask,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    ESP_RETURN_ON_ERROR(gpio_config(&direct_input_config), "board",
                        "configure direct inputs");

    const uint64_t column_mask =
        (1ULL << BOARD_MATRIX12_V1_COL0_GPIO) |
        (1ULL << BOARD_MATRIX12_V1_COL1_GPIO) |
        (1ULL << BOARD_MATRIX12_V1_COL2_GPIO) |
        (1ULL << BOARD_MATRIX12_V1_COL3_GPIO);
    const gpio_config_t column_config = {
        .pin_bit_mask = column_mask,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    ESP_RETURN_ON_ERROR(gpio_config(&column_config), "board",
                        "configure matrix columns");

    const uint64_t row_mask =
        (1ULL << BOARD_MATRIX12_V1_ROW0_GPIO) |
        (1ULL << BOARD_MATRIX12_V1_ROW1_GPIO) |
        (1ULL << BOARD_MATRIX12_V1_ROW2_GPIO) |
        (1ULL << BOARD_MATRIX12_V1_ROW3_GPIO);
    const gpio_config_t row_config = {
        .pin_bit_mask = row_mask,
        .mode = GPIO_MODE_OUTPUT_OD,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    ESP_RETURN_ON_ERROR(gpio_config(&row_config), "board",
                        "configure matrix rows");
    for (size_t row = 0; row < BOARD_MATRIX12_V1_MATRIX_ROW_COUNT; ++row) {
        gpio_set_level(s_matrix_rows[row], 1);
    }

    const TickType_t now = xTaskGetTickCount();
    for (size_t index = 0; index < sizeof(s_inputs) / sizeof(s_inputs[0]); ++index) {
        s_inputs[index].stable = input_pressed(s_inputs[index].gpio);
        s_inputs[index].sample = s_inputs[index].stable;
        s_inputs[index].changed_at = now;
    }
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    power_button_init(
        &s_power_button,
        MATRIX12_ENCODER_KEY_FUNCTION_ENABLED &&
            gpio_get_level(BOARD_MATRIX12_POWER_V2_ENCODER_KEY_GPIO) ==
            BOARD_MATRIX12_POWER_V2_ENCODER_KEY_ACTIVE_LEVEL,
        board_ticks_to_ms(now));
#endif
    bool matrix_pressed[BOARD_MATRIX12_V1_KEY_COUNT];
    scan_matrix(matrix_pressed);
    for (size_t index = 0; index < BOARD_MATRIX12_V1_KEY_COUNT; ++index) {
        s_matrix_keys[index].stable = matrix_pressed[index];
        s_matrix_keys[index].sample = matrix_pressed[index];
        s_matrix_keys[index].changed_at = now;
    }
    s_encoder.state = (gpio_get_level(BOARD_MATRIX12_V1_ENCODER_A_GPIO) << 1) |
                      gpio_get_level(BOARD_MATRIX12_V1_ENCODER_B_GPIO);
    s_encoder.accumulator = 0;
    s_encoder.detent_state = s_encoder.state;
    s_encoder_edges =
        xQueueCreate(ENCODER_EDGE_QUEUE_CAPACITY, sizeof(uint8_t));
    if (s_encoder_edges == NULL) {
        return ESP_ERR_NO_MEM;
    }
    ESP_RETURN_ON_ERROR(
        gpio_set_intr_type(BOARD_MATRIX12_V1_ENCODER_A_GPIO, GPIO_INTR_ANYEDGE),
        "board", "enable encoder A edge interrupt");
    ESP_RETURN_ON_ERROR(
        gpio_set_intr_type(BOARD_MATRIX12_V1_ENCODER_B_GPIO, GPIO_INTR_ANYEDGE),
        "board", "enable encoder B edge interrupt");
    ESP_RETURN_ON_ERROR(gpio_install_isr_service(0), "board",
                        "install GPIO interrupt service");
    ESP_RETURN_ON_ERROR(
        gpio_isr_handler_add(BOARD_MATRIX12_V1_ENCODER_A_GPIO, encoder_gpio_isr, NULL),
        "board", "register encoder A interrupt");
    ESP_RETURN_ON_ERROR(
        gpio_isr_handler_add(BOARD_MATRIX12_V1_ENCODER_B_GPIO, encoder_gpio_isr, NULL),
        "board", "register encoder B interrupt");
    return ESP_OK;
}

static esp_err_t init_adc(void)
{
    const adc_oneshot_unit_init_cfg_t unit_config = {
        .unit_id = ADC_UNIT_1,
        .ulp_mode = ADC_ULP_MODE_DISABLE,
    };
    ESP_RETURN_ON_ERROR(adc_oneshot_new_unit(&unit_config, &s_adc), "board", "create ADC unit");

    const adc_oneshot_chan_cfg_t channel_config = {
        .atten = ADC_ATTEN_DB_12,
        .bitwidth = ADC_BITWIDTH_DEFAULT,
    };
    ESP_RETURN_ON_ERROR(adc_oneshot_config_channel(s_adc, BOARD_MATRIX12_V1_JOYSTICK_X_ADC_CHANNEL,
                                                   &channel_config),
                        "board", "configure joystick X");
    ESP_RETURN_ON_ERROR(adc_oneshot_config_channel(s_adc, BOARD_MATRIX12_V1_JOYSTICK_Y_ADC_CHANNEL,
                                                   &channel_config),
                        "board", "configure joystick Y");

    int total_x = 0;
    int total_y = 0;
    int samples = 0;
    /*
     * Engineering fallback until NVS calibration exists. The joystick must be
     * physically centered during boot or this average will become a bad center.
     */
    for (int index = 0; index < 16; ++index) {
        int x = 0;
        int y = 0;
        if (adc_oneshot_read(s_adc, BOARD_MATRIX12_V1_JOYSTICK_X_ADC_CHANNEL, &x) == ESP_OK &&
            adc_oneshot_read(s_adc, BOARD_MATRIX12_V1_JOYSTICK_Y_ADC_CHANNEL, &y) == ESP_OK) {
            total_x += x;
            total_y += y;
            ++samples;
        }
    }
    if (samples > 0) {
        s_joystick_center_x = total_x / samples;
        s_joystick_center_y = total_y / samples;
        s_joystick_raw_x = s_joystick_center_x;
        s_joystick_raw_y = s_joystick_center_y;
        s_joystick_filtered_x = s_joystick_center_x;
        s_joystick_filtered_y = s_joystick_center_y;
    }
    return ESP_OK;
}

static esp_err_t init_motor(void)
{
    /* 20 kHz keeps PWM switching above the audible range; duty stays zero at init. */
    const ledc_timer_config_t timer = {
        .speed_mode = LEDC_LOW_SPEED_MODE,
        .duty_resolution = LEDC_TIMER_10_BIT,
        .timer_num = LEDC_TIMER_0,
        .freq_hz = 20000,
        .clk_cfg = LEDC_AUTO_CLK,
    };
    ESP_RETURN_ON_ERROR(ledc_timer_config(&timer), "board", "configure motor PWM timer");

    const ledc_channel_config_t channel = {
        .gpio_num = BOARD_MATRIX12_V1_MOTOR_GPIO,
        .speed_mode = LEDC_LOW_SPEED_MODE,
        .channel = LEDC_CHANNEL_0,
        .intr_type = LEDC_INTR_DISABLE,
        .timer_sel = LEDC_TIMER_0,
        .duty = 0,
        .hpoint = 0,
        .flags.output_invert = 0,
    };
    return ledc_channel_config(&channel);
}

static esp_err_t init_display_backlight_pwm(void)
{
    const ledc_timer_config_t timer = {
        .speed_mode = LEDC_LOW_SPEED_MODE,
        .duty_resolution = LEDC_TIMER_10_BIT,
        .timer_num = LEDC_TIMER_1,
        .freq_hz = 20000,
        .clk_cfg = LEDC_AUTO_CLK,
    };
    ESP_RETURN_ON_ERROR(ledc_timer_config(&timer), "board", "configure display PWM timer");
    const ledc_channel_config_t channel = {
        .gpio_num = BOARD_MATRIX12_V1_DISPLAY_BACKLIGHT_GPIO,
        .speed_mode = LEDC_LOW_SPEED_MODE,
        .channel = LEDC_CHANNEL_1,
        .intr_type = LEDC_INTR_DISABLE,
        .timer_sel = LEDC_TIMER_1,
        .duty = 0,
        .hpoint = 0,
        /* Q2 is a PNP high-side switch, so a low LCDBG level turns the LED on. */
        .flags.output_invert = 1,
    };
    return ledc_channel_config(&channel);
}

static esp_err_t new_strip(gpio_num_t gpio, size_t count, led_model_t model,
                           led_strip_handle_t *handle)
{
    /* The switch-integrated RGB devices use one GRB WS2812-compatible chain. */
    const led_strip_config_t strip_config = {
        .strip_gpio_num = gpio,
        .max_leds = count,
        .led_model = model,
        .color_component_format = LED_STRIP_COLOR_COMPONENT_FMT_GRB,
        .flags.invert_out = false,
    };
    const led_strip_rmt_config_t rmt_config = {
        .clk_src = RMT_CLK_SRC_DEFAULT,
        .resolution_hz = 10 * 1000 * 1000,
        .mem_block_symbols = 64,
        .flags.with_dma = false,
    };
    return led_strip_new_rmt_device(&strip_config, &rmt_config, handle);
}

static esp_err_t init_rgb(void)
{
    ESP_RETURN_ON_ERROR(new_strip(BOARD_MATRIX12_V1_UNDER_KEY_RGB_GPIO,
                                  BOARD_MATRIX12_V1_UNDER_KEY_RGB_COUNT, LED_MODEL_WS2812,
                                  &s_under_key_strip),
                        "board", "create under-key RGB strip");
    return led_strip_clear(s_under_key_strip);
}

static esp_err_t display_clear_controller_ram(uint16_t rgb565)
{
    if (s_panel == NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    for (size_t index = 0;
         index < sizeof(s_controller_clear_buffer) /
                     sizeof(s_controller_clear_buffer[0]);
         ++index) {
        s_controller_clear_buffer[index] = rgb565;
    }

    /*
     * Clear the complete, valid ST7735 RAM once with no logical-window gap.
     * This initializes edge rows which are outside the 128x128 canvas without
     * sending any out-of-range overscan coordinates.
     */
    ESP_RETURN_ON_ERROR(esp_lcd_panel_set_gap(s_panel, 0, 0),
                        "board", "select full controller RAM");
    for (int y = 0; y < BOARD_MATRIX12_V1_DISPLAY_RAM_HEIGHT;
         y += DISPLAY_CONTROLLER_CLEAR_LINES) {
        const int lines =
            y + DISPLAY_CONTROLLER_CLEAR_LINES <=
                    BOARD_MATRIX12_V1_DISPLAY_RAM_HEIGHT
                ? DISPLAY_CONTROLLER_CLEAR_LINES
                : BOARD_MATRIX12_V1_DISPLAY_RAM_HEIGHT - y;
        ESP_RETURN_ON_ERROR(
            esp_lcd_panel_draw_bitmap(
                s_panel, 0, y, BOARD_MATRIX12_V1_DISPLAY_RAM_WIDTH,
                y + lines, s_controller_clear_buffer),
            "board", "clear controller RAM");
    }
    return ESP_OK;
}

static esp_err_t init_display(void)
{
    ESP_RETURN_ON_ERROR(init_display_backlight_pwm(), "board", "initialize display backlight");

    const spi_bus_config_t bus_config = {
        .sclk_io_num = BOARD_MATRIX12_V1_DISPLAY_SCLK_GPIO,
        .mosi_io_num = BOARD_MATRIX12_V1_DISPLAY_MOSI_GPIO,
        .miso_io_num = GPIO_NUM_NC,
        .quadwp_io_num = GPIO_NUM_NC,
        .quadhd_io_num = GPIO_NUM_NC,
        .max_transfer_sz = sizeof(s_text_band_buffers[0]),
    };
    ESP_RETURN_ON_ERROR(spi_bus_initialize(SPI2_HOST, &bus_config, SPI_DMA_CH_AUTO),
                        "board", "initialize display SPI bus");

    const esp_lcd_panel_io_spi_config_t io_config = {
        .dc_gpio_num = BOARD_MATRIX12_V1_DISPLAY_DC_GPIO,
        .cs_gpio_num = BOARD_MATRIX12_V1_DISPLAY_CS_GPIO,
        .pclk_hz = 20 * 1000 * 1000,
        .lcd_cmd_bits = 8,
        .lcd_param_bits = 8,
        .spi_mode = 0,
        .trans_queue_depth = 1,
    };
    esp_lcd_panel_io_handle_t panel_io = NULL;
    ESP_RETURN_ON_ERROR(esp_lcd_new_panel_io_spi(SPI2_HOST, &io_config, &panel_io),
                        "board", "create display panel IO");

    st7735_vendor_config_t vendor_config = {
        .init_cmds = matrix12_st7735_init_cmds,
        .init_cmds_size = sizeof(matrix12_st7735_init_cmds) /
                          sizeof(matrix12_st7735_init_cmds[0]),
    };
    const esp_lcd_panel_dev_config_t panel_config = {
        .reset_gpio_num = BOARD_MATRIX12_V1_DISPLAY_RESET_GPIO,
        .rgb_ele_order = LCD_RGB_ELEMENT_ORDER_BGR,
        .bits_per_pixel = 16,
        .vendor_config = &vendor_config,
    };
    ESP_RETURN_ON_ERROR(esp_lcd_new_panel_st7735(panel_io, &panel_config, &s_panel),
                        "board", "create ST7735 panel");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_reset(s_panel), "board", "reset ST7735 panel");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_init(s_panel), "board", "initialize ST7735 panel");
    /* Supplier data omits inversion polarity; this is the bring-up default. */
    ESP_RETURN_ON_ERROR(esp_lcd_panel_invert_color(s_panel, true), "board", "set panel inversion");
    ESP_RETURN_ON_ERROR(board_set_display_config(0, 0), "board",
                        "set mounted display orientation with backlight off");
    ESP_RETURN_ON_ERROR(display_clear_controller_ram(0x0000), "board",
                        "initialize full controller RAM");
    ESP_RETURN_ON_ERROR(board_set_display_config(0, 0), "board",
                        "restore mounted display window after full clear");
    ESP_RETURN_ON_ERROR(board_display_fill(0x0000), "board", "clear display before enabling it");
    return esp_lcd_panel_disp_on_off(s_panel, true);
}

esp_err_t board_init(void)
{
    memset(s_events, 0, sizeof(s_events));
    s_event_read = 0;
    s_event_write = 0;
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    s_shutdown_request_pending = false;
    ESP_RETURN_ON_ERROR(
        power_control_init(BOARD_MATRIX12_POWER_V2_PWR_OFF_GPIO,
                           BOARD_MATRIX12_POWER_V2_PWR_OFF_ACTIVE_LEVEL),
        "board", "initialize shutdown request output");
#endif
    ESP_RETURN_ON_ERROR(init_inputs(), "board", "initialize product inputs");
    ESP_RETURN_ON_ERROR(init_adc(), "board", "initialize joystick ADC");
    ESP_RETURN_ON_ERROR(init_motor(), "board", "initialize motor");
    ESP_RETURN_ON_ERROR(init_rgb(), "board", "initialize RGB");
    ESP_RETURN_ON_ERROR(init_display(), "board", "initialize display");
    return ESP_OK;
}

static void poll_digital_inputs(TickType_t now)
{
    for (size_t index = 0; index < sizeof(s_inputs) / sizeof(s_inputs[0]); ++index) {
        digital_input_t *input = &s_inputs[index];
        if (!MATRIX12_JOYSTICK_FUNCTION_ENABLED &&
            input->control == BOARD_CONTROL_JOYSTICK_PRESS) {
            input->sample = false;
            input->stable = false;
            continue;
        }
        const bool next = input_pressed(input->gpio);
        if (next != input->sample) {
            input->sample = next;
            input->changed_at = now;
        }
        if (input->sample != input->stable &&
            (now - input->changed_at) >= pdMS_TO_TICKS(DEBOUNCE_MS)) {
            input->stable = input->sample;
            if (!(s_joystick_calibration_active &&
                  input->control == BOARD_CONTROL_JOYSTICK_PRESS)) {
                enqueue_event(input->control, input->stable);
            }
        }
    }
}

#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
static void poll_power_button(TickType_t now)
{
    if (!MATRIX12_ENCODER_KEY_FUNCTION_ENABLED) {
        return;
    }
    const bool pressed =
        gpio_get_level(BOARD_MATRIX12_POWER_V2_ENCODER_KEY_GPIO) ==
        BOARD_MATRIX12_POWER_V2_ENCODER_KEY_ACTIVE_LEVEL;
    const power_button_event_t event = power_button_update(
        &s_power_button, pressed, board_ticks_to_ms(now),
        POWER_BUTTON_DEBOUNCE_MS, s_power_button_wake_mode
            ? POWER_BUTTON_WAKE_HOLD_MS : POWER_BUTTON_LONG_PRESS_MS);
    if (event == POWER_BUTTON_EVENT_PRESS) {
        enqueue_feedback_event(BOARD_CONTROL_ENCODER_PRESS);
    } else if (event == POWER_BUTTON_EVENT_SHORT_PRESS) {
        if (s_power_button_wake_mode) {
            /* Hardware may latch ON before the software wake deadline.
             * Cancel that partial wake only after release; never send a tap.
             */
            (void)board_power_request_shutdown();
            return;
        }
        /* A short physical gesture remains one ordinary configurable tap. */
        enqueue_event(BOARD_CONTROL_ENCODER_PRESS, true);
        enqueue_event(BOARD_CONTROL_ENCODER_PRESS, false);
    } else if (event == POWER_BUTTON_EVENT_LONG_PRESS) {
        s_shutdown_request_pending = true;
    }
}
#endif

static void poll_matrix_inputs(TickType_t now)
{
    bool pressed[BOARD_MATRIX12_V1_KEY_COUNT];
    scan_matrix(pressed);
    for (size_t index = 0; index < BOARD_MATRIX12_V1_KEY_COUNT; ++index) {
        matrix_key_t *key = &s_matrix_keys[index];
        if (pressed[index] != key->sample) {
            key->sample = pressed[index];
            key->changed_at = now;
        }
        if (key->sample != key->stable &&
            (now - key->changed_at) >= pdMS_TO_TICKS(DEBOUNCE_MS)) {
            key->stable = key->sample;
            enqueue_event(key->control, key->stable);
        }
    }
}

static void poll_encoder(void)
{
    uint8_t next = 0;
    while (xQueueReceive(s_encoder_edges, &next, 0) == pdTRUE) {
        const int direction = board_encoder_update(
            &s_encoder, next, ENCODER_TRANSITIONS_PER_DETENT);
        /* Rotation is instantaneous, so it has no matching release event. */
        if (direction > 0) {
            enqueue_event(BOARD_CONTROL_ENCODER_CW, true);
        } else if (direction < 0) {
            enqueue_event(BOARD_CONTROL_ENCODER_CCW, true);
        }
    }
}

static void update_direction(bool next, bool *current, board_control_t control)
{
    if (next != *current) {
        *current = next;
        enqueue_event(control, next);
    }
}

static void poll_joystick(TickType_t now)
{
    if ((now - s_last_joystick_sample) < pdMS_TO_TICKS(JOYSTICK_SAMPLE_MS)) {
        return;
    }
    s_last_joystick_sample = now;
    int x = s_joystick_center_x;
    int y = s_joystick_center_y;
    if (adc_oneshot_read(s_adc, BOARD_MATRIX12_V1_JOYSTICK_X_ADC_CHANNEL, &x) != ESP_OK ||
        adc_oneshot_read(s_adc, BOARD_MATRIX12_V1_JOYSTICK_Y_ADC_CHANNEL, &y) != ESP_OK) {
        return;
    }
    s_joystick_raw_x = x;
    s_joystick_raw_y = y;
    /* Higher filter values favor new samples; one percent still tracks slowly. */
    const int weight = s_joystick_filter > 0 ? s_joystick_filter : 1;
    s_joystick_filtered_x += ((x - s_joystick_filtered_x) * weight) / 100;
    s_joystick_filtered_y += ((y - s_joystick_filtered_y) * weight) / 100;
    if (!MATRIX12_JOYSTICK_FUNCTION_ENABLED) {
        update_direction(false, &s_joystick_left, BOARD_CONTROL_JOYSTICK_LEFT);
        update_direction(false, &s_joystick_right, BOARD_CONTROL_JOYSTICK_RIGHT);
        update_direction(false, &s_joystick_up, BOARD_CONTROL_JOYSTICK_UP);
        update_direction(false, &s_joystick_down, BOARD_CONTROL_JOYSTICK_DOWN);
        s_joystick_radial_active = false;
        return;
    }
    if (s_joystick_calibration_active) {
        update_direction(false, &s_joystick_left, BOARD_CONTROL_JOYSTICK_LEFT);
        update_direction(false, &s_joystick_right, BOARD_CONTROL_JOYSTICK_RIGHT);
        update_direction(false, &s_joystick_up, BOARD_CONTROL_JOYSTICK_UP);
        update_direction(false, &s_joystick_down, BOARD_CONTROL_JOYSTICK_DOWN);
        s_joystick_radial_active = false;
        return;
    }
    x = board_joystick_orient_axis(s_joystick_filtered_x, s_joystick_center_x,
                                   JOYSTICK_X_HARDWARE_INVERTED,
                                   s_joystick_invert_x);
    y = board_joystick_orient_axis(s_joystick_filtered_y, s_joystick_center_y,
                                   JOYSTICK_Y_HARDWARE_INVERTED,
                                   s_joystick_invert_y);
    const uint8_t previous_directions =
        (s_joystick_left ? BOARD_JOYSTICK_LEFT : 0) |
        (s_joystick_right ? BOARD_JOYSTICK_RIGHT : 0) |
        (s_joystick_up ? BOARD_JOYSTICK_UP : 0) |
        (s_joystick_down ? BOARD_JOYSTICK_DOWN : 0);
    const uint8_t directions = board_joystick_classify_axes_hysteresis(
        x, y, s_joystick_center_x, s_joystick_center_y,
        s_joystick_deadzone_x, s_joystick_deadzone_x * 3 / 4,
        s_joystick_deadzone_y, s_joystick_deadzone_y * 3 / 4,
        previous_directions);
    if (MATRIX12_JOYSTICK_HORIZONTAL_FUNCTION_ENABLED) {
        update_direction((directions & BOARD_JOYSTICK_LEFT) != 0,
                         &s_joystick_left, BOARD_CONTROL_JOYSTICK_LEFT);
        update_direction((directions & BOARD_JOYSTICK_RIGHT) != 0,
                         &s_joystick_right, BOARD_CONTROL_JOYSTICK_RIGHT);
    } else {
        update_direction(false, &s_joystick_left, BOARD_CONTROL_JOYSTICK_LEFT);
        update_direction(false, &s_joystick_right, BOARD_CONTROL_JOYSTICK_RIGHT);
    }
    update_direction((directions & BOARD_JOYSTICK_UP) != 0,
                     &s_joystick_up, BOARD_CONTROL_JOYSTICK_UP);
    update_direction((directions & BOARD_JOYSTICK_DOWN) != 0,
                     &s_joystick_down, BOARD_CONTROL_JOYSTICK_DOWN);
}

void board_poll(void)
{
    const TickType_t now = xTaskGetTickCount();
    poll_matrix_inputs(now);
    poll_digital_inputs(now);
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    poll_power_button(now);
#endif
    poll_encoder();
    poll_joystick(now);
    if (s_motor_running && (int32_t)(now - s_motor_stop_at) >= 0) {
        ledc_set_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0, 0);
        ledc_update_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0);
        s_motor_running = false;
    }
}

bool board_next_event(board_event_t *event)
{
    if (event == NULL || s_event_read == s_event_write) {
        return false;
    }
    *event = s_events[s_event_read];
    s_event_read = (s_event_read + 1) % EVENT_QUEUE_CAPACITY;
    return true;
}

const char *board_target_name(void)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    return "WMP-S3-MATRIX12-POWER-V2";
#else
    return "WMP-S3-MATRIX12-V1_2026-08-10";
#endif
}

const char *board_hardware_id(void)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    return "WMP-S3-MATRIX12-POWER-V2";
#else
    return "WMP-S3-MATRIX12-V1";
#endif
}

size_t board_key_count(void)
{
    return BOARD_MATRIX12_V1_KEY_COUNT;
}

size_t board_status_rgb_count(void)
{
    return BOARD_MATRIX12_V1_STATUS_RGB_COUNT;
}

size_t board_under_key_rgb_count(void)
{
    return BOARD_MATRIX12_V1_UNDER_KEY_RGB_COUNT;
}

bool board_is_product_target(void)
{
    return true;
}

bool board_inputs_neutral(void)
{
    for (size_t index = 0; index < BOARD_MATRIX12_V1_KEY_COUNT; ++index) {
        if (s_matrix_keys[index].stable) {
            return false;
        }
    }
    for (size_t index = 0; index < sizeof(s_inputs) / sizeof(s_inputs[0]); ++index) {
        if (s_inputs[index].stable) {
            return false;
        }
    }
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    if (MATRIX12_ENCODER_KEY_FUNCTION_ENABLED &&
        power_button_pressed(&s_power_button)) {
        return false;
    }
#endif
    return !s_joystick_left && !s_joystick_right && !s_joystick_up && !s_joystick_down;
}

size_t board_get_active_controls(board_control_t *controls, size_t capacity)
{
    if (controls == NULL || capacity == 0) {
        return 0;
    }
    size_t count = 0;
    for (size_t index = 0;
         index < BOARD_MATRIX12_V1_KEY_COUNT && count < capacity; ++index) {
        if (s_matrix_keys[index].stable) {
            controls[count++] = s_matrix_keys[index].control;
        }
    }
    for (size_t index = 0; index < sizeof(s_inputs) / sizeof(s_inputs[0]) && count < capacity; ++index) {
        if (s_inputs[index].stable) {
            controls[count++] = s_inputs[index].control;
        }
    }
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    if (MATRIX12_ENCODER_KEY_FUNCTION_ENABLED &&
        power_button_pressed(&s_power_button) && count < capacity) {
        controls[count++] = BOARD_CONTROL_ENCODER_PRESS;
    }
#endif
    const struct {
        bool active;
        board_control_t control;
    } directions[] = {
        {s_joystick_left, BOARD_CONTROL_JOYSTICK_LEFT},
        {s_joystick_right, BOARD_CONTROL_JOYSTICK_RIGHT},
        {s_joystick_up, BOARD_CONTROL_JOYSTICK_UP},
        {s_joystick_down, BOARD_CONTROL_JOYSTICK_DOWN},
    };
    for (size_t index = 0; index < sizeof(directions) / sizeof(directions[0]) && count < capacity; ++index) {
        if (directions[index].active) {
            controls[count++] = directions[index].control;
        }
    }
    return count;
}

bool board_get_joystick_radial(float *angle_turns)
{
    if (s_joystick_calibration_active ||
        !MATRIX12_JOYSTICK_FUNCTION_ENABLED ||
        !MATRIX12_JOYSTICK_HORIZONTAL_FUNCTION_ENABLED) {
        s_joystick_radial_active = false;
        return false;
    }
    const int x = board_joystick_orient_axis(
        s_joystick_filtered_x, s_joystick_center_x,
        JOYSTICK_X_HARDWARE_INVERTED, s_joystick_invert_x);
    const int y = board_joystick_orient_axis(
        s_joystick_filtered_y, s_joystick_center_y,
        JOYSTICK_Y_HARDWARE_INVERTED, s_joystick_invert_y);
    const int oriented_x_endpoint_a = board_joystick_orient_axis(
        s_joystick_minimum_x, s_joystick_center_x,
        JOYSTICK_X_HARDWARE_INVERTED, s_joystick_invert_x);
    const int oriented_x_endpoint_b = board_joystick_orient_axis(
        s_joystick_maximum_x, s_joystick_center_x,
        JOYSTICK_X_HARDWARE_INVERTED, s_joystick_invert_x);
    const int oriented_y_endpoint_a = board_joystick_orient_axis(
        s_joystick_minimum_y, s_joystick_center_y,
        JOYSTICK_Y_HARDWARE_INVERTED, s_joystick_invert_y);
    const int oriented_y_endpoint_b = board_joystick_orient_axis(
        s_joystick_maximum_y, s_joystick_center_y,
        JOYSTICK_Y_HARDWARE_INVERTED, s_joystick_invert_y);
    const int minimum_x = oriented_x_endpoint_a < oriented_x_endpoint_b
                              ? oriented_x_endpoint_a
                              : oriented_x_endpoint_b;
    const int maximum_x = oriented_x_endpoint_a > oriented_x_endpoint_b
                              ? oriented_x_endpoint_a
                              : oriented_x_endpoint_b;
    const int minimum_y = oriented_y_endpoint_a < oriented_y_endpoint_b
                              ? oriented_y_endpoint_a
                              : oriented_y_endpoint_b;
    const int maximum_y = oriented_y_endpoint_a > oriented_y_endpoint_b
                              ? oriented_y_endpoint_a
                              : oriented_y_endpoint_b;
    const int deadzone_x = s_joystick_radial_active
                               ? s_joystick_deadzone_x * 3 / 4
                               : s_joystick_deadzone_x;
    const int deadzone_y = s_joystick_radial_active
                               ? s_joystick_deadzone_y * 3 / 4
                               : s_joystick_deadzone_y;
    s_joystick_radial_active = board_joystick_radial_angle(
        x, y, s_joystick_center_x, s_joystick_center_y,
        minimum_x, maximum_x, minimum_y, maximum_y,
        deadzone_x, deadzone_y, angle_turns);
    return s_joystick_radial_active;
}

bool board_get_joystick_diagnostics(board_joystick_diagnostics_t *diagnostics)
{
    if (diagnostics == NULL) {
        return false;
    }

    const int x = board_joystick_orient_axis(
        s_joystick_filtered_x, s_joystick_center_x,
        JOYSTICK_X_HARDWARE_INVERTED, s_joystick_invert_x);
    const int y = board_joystick_orient_axis(
        s_joystick_filtered_y, s_joystick_center_y,
        JOYSTICK_Y_HARDWARE_INVERTED, s_joystick_invert_y);
    const int oriented_x_endpoint_a = board_joystick_orient_axis(
        s_joystick_minimum_x, s_joystick_center_x,
        JOYSTICK_X_HARDWARE_INVERTED, s_joystick_invert_x);
    const int oriented_x_endpoint_b = board_joystick_orient_axis(
        s_joystick_maximum_x, s_joystick_center_x,
        JOYSTICK_X_HARDWARE_INVERTED, s_joystick_invert_x);
    const int oriented_y_endpoint_a = board_joystick_orient_axis(
        s_joystick_minimum_y, s_joystick_center_y,
        JOYSTICK_Y_HARDWARE_INVERTED, s_joystick_invert_y);
    const int oriented_y_endpoint_b = board_joystick_orient_axis(
        s_joystick_maximum_y, s_joystick_center_y,
        JOYSTICK_Y_HARDWARE_INVERTED, s_joystick_invert_y);
    const int minimum_x = oriented_x_endpoint_a < oriented_x_endpoint_b
                              ? oriented_x_endpoint_a
                              : oriented_x_endpoint_b;
    const int maximum_x = oriented_x_endpoint_a > oriented_x_endpoint_b
                              ? oriented_x_endpoint_a
                              : oriented_x_endpoint_b;
    const int minimum_y = oriented_y_endpoint_a < oriented_y_endpoint_b
                              ? oriented_y_endpoint_a
                              : oriented_y_endpoint_b;
    const int maximum_y = oriented_y_endpoint_a > oriented_y_endpoint_b
                              ? oriented_y_endpoint_a
                              : oriented_y_endpoint_b;
    const int deadzone_x = s_joystick_radial_active
                               ? s_joystick_deadzone_x * 3 / 4
                               : s_joystick_deadzone_x;
    const int deadzone_y = s_joystick_radial_active
                               ? s_joystick_deadzone_y * 3 / 4
                               : s_joystick_deadzone_y;
    float angle_turns = 0.0f;
    const bool radial_valid =
        MATRIX12_JOYSTICK_HORIZONTAL_FUNCTION_ENABLED &&
        board_joystick_radial_angle(
            x, y, s_joystick_center_x, s_joystick_center_y,
            minimum_x, maximum_x, minimum_y, maximum_y,
            deadzone_x, deadzone_y, &angle_turns);

    *diagnostics = (board_joystick_diagnostics_t){
        .supported = true,
        .raw_x = s_joystick_raw_x,
        .raw_y = s_joystick_raw_y,
        .filtered_x = s_joystick_filtered_x,
        .filtered_y = s_joystick_filtered_y,
        .center_x = s_joystick_center_x,
        .center_y = s_joystick_center_y,
        .minimum_x = s_joystick_minimum_x,
        .maximum_x = s_joystick_maximum_x,
        .minimum_y = s_joystick_minimum_y,
        .maximum_y = s_joystick_maximum_y,
        .deadzone_x = s_joystick_deadzone_x,
        .deadzone_y = s_joystick_deadzone_y,
        .directions =
            (s_joystick_left ? BOARD_JOYSTICK_LEFT : 0) |
            (s_joystick_right ? BOARD_JOYSTICK_RIGHT : 0) |
            (s_joystick_up ? BOARD_JOYSTICK_UP : 0) |
            (s_joystick_down ? BOARD_JOYSTICK_DOWN : 0),
        .radial_active = s_joystick_radial_active,
        .radial_valid = radial_valid,
        .radial_angle_turns = angle_turns,
    };
    return true;
}

bool board_joystick_calibration_active(void)
{
    return s_joystick_calibration_active;
}

void board_set_joystick_calibration_active(bool active)
{
    if (active == s_joystick_calibration_active) {
        return;
    }
    if (active) {
        update_direction(false, &s_joystick_left, BOARD_CONTROL_JOYSTICK_LEFT);
        update_direction(false, &s_joystick_right, BOARD_CONTROL_JOYSTICK_RIGHT);
        update_direction(false, &s_joystick_up, BOARD_CONTROL_JOYSTICK_UP);
        update_direction(false, &s_joystick_down, BOARD_CONTROL_JOYSTICK_DOWN);
        for (size_t index = 0;
             index < sizeof(s_inputs) / sizeof(s_inputs[0]); ++index) {
            if (s_inputs[index].control == BOARD_CONTROL_JOYSTICK_PRESS &&
                s_inputs[index].stable) {
                enqueue_event(BOARD_CONTROL_JOYSTICK_PRESS, false);
                break;
            }
        }
        s_joystick_radial_active = false;
    }
    s_joystick_calibration_active = active;
}

#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
bool board_take_shutdown_request(void)
{
    const bool pending = s_shutdown_request_pending;
    s_shutdown_request_pending = false;
    return pending;
}

void board_set_power_button_wake_mode(bool waiting_for_on)
{
    s_power_button_wake_mode = waiting_for_on;
}

bool board_power_button_released(void)
{
    return !s_power_button.raw_pressed && !power_button_pressed(&s_power_button);
}

esp_err_t board_power_request_shutdown(void)
{
    return power_control_request_shutdown(POWER_OFF_PULSE_MS);
}
#endif

esp_err_t board_set_status_rgb(size_t index, uint8_t red, uint8_t green, uint8_t blue)
{
    (void)index;
    (void)red;
    (void)green;
    (void)blue;
    return ESP_ERR_NOT_SUPPORTED;
}

static uint8_t limit_under_key_channel(uint8_t value)
{
    const uint8_t maximum =
        (uint8_t)((255U * BOARD_MATRIX12_V1_UNDER_KEY_OUTPUT_PERCENT) / 100U);
    return value > maximum ? maximum : value;
}

esp_err_t board_set_under_key_rgb(size_t index, uint8_t red, uint8_t green, uint8_t blue)
{
    if (index >= BOARD_MATRIX12_V1_UNDER_KEY_RGB_COUNT) {
        return ESP_ERR_INVALID_ARG;
    }
    ESP_RETURN_ON_ERROR(led_strip_set_pixel(
                            s_under_key_strip, index,
                            limit_under_key_channel(red),
                            limit_under_key_channel(green),
                            limit_under_key_channel(blue)),
                        "board", "set under-key RGB pixel");
    return led_strip_refresh(s_under_key_strip);
}

esp_err_t board_apply_rgb(
    const board_rgb_t status[BOARD_STATUS_RGB_COUNT],
    const board_rgb_t under_key[BOARD_MAX_UNDER_KEY_RGB_COUNT])
{
    (void)status;
    if (under_key == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    for (size_t index = 0; index < BOARD_MATRIX12_V1_UNDER_KEY_RGB_COUNT; ++index) {
        ESP_RETURN_ON_ERROR(led_strip_set_pixel(
                                s_under_key_strip, index,
                                limit_under_key_channel(under_key[index].red),
                                limit_under_key_channel(under_key[index].green),
                                limit_under_key_channel(under_key[index].blue)),
                            "board", "buffer under-key RGB pixel");
    }
    return led_strip_refresh(s_under_key_strip);
}

esp_err_t board_apply_status_rgb(
    const board_rgb_t status[BOARD_STATUS_RGB_COUNT])
{
    (void)status;
    /* This hardware has no populated top status RGB chain. */
    return ESP_OK;
}

esp_err_t board_set_joystick_config(const board_joystick_config_t *config)
{
    if (config == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    const bool preserve_boot_center = config->center_x == -1 && config->center_y == -1;
    const bool bounds_valid =
        config->minimum_x >= 0 && config->minimum_x < config->maximum_x &&
        config->maximum_x <= 4095 &&
        config->minimum_y >= 0 && config->minimum_y < config->maximum_y &&
        config->maximum_y <= 4095;
    if (!bounds_valid ||
        (!preserve_boot_center &&
         (config->center_x <= config->minimum_x ||
          config->center_x >= config->maximum_x ||
          config->center_y <= config->minimum_y ||
          config->center_y >= config->maximum_y)) ||
        config->deadzone_x < 0 || config->deadzone_x > 1024 ||
        config->deadzone_y < 0 || config->deadzone_y > 1024 ||
        config->filter_percent > 100) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!preserve_boot_center) {
        s_joystick_center_x = config->center_x;
        s_joystick_center_y = config->center_y;
        s_joystick_filtered_x = config->center_x;
        s_joystick_filtered_y = config->center_y;
    }
    /*
     * An uncalibrated profile deliberately keeps the center measured at boot.
     * If nominal endpoints do not bracket that measured center, use the ADC
     * limits for radial normalization instead of rejecting all feedback.
     */
    s_joystick_minimum_x =
        config->minimum_x < s_joystick_center_x ? config->minimum_x : 0;
    s_joystick_maximum_x =
        config->maximum_x > s_joystick_center_x ? config->maximum_x : 4095;
    s_joystick_minimum_y =
        config->minimum_y < s_joystick_center_y ? config->minimum_y : 0;
    s_joystick_maximum_y =
        config->maximum_y > s_joystick_center_y ? config->maximum_y : 4095;
    s_joystick_deadzone_x = config->deadzone_x;
    s_joystick_deadzone_y = config->deadzone_y;
    s_joystick_invert_x = config->invert_x;
    s_joystick_invert_y = config->invert_y;
    s_joystick_filter = config->filter_percent;
    s_joystick_radial_active = false;
    return ESP_OK;
}

esp_err_t board_haptic_pulse(uint8_t strength_percent, uint16_t duration_ms)
{
    if (strength_percent == 0 || duration_ms == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    /* Provisional clamps protect the unmeasured motor circuit during bring-up. */
    if (strength_percent > MOTOR_MAX_STRENGTH_PERCENT) {
        strength_percent = MOTOR_MAX_STRENGTH_PERCENT;
    }
    if (duration_ms > MOTOR_MAX_DURATION_MS) {
        duration_ms = MOTOR_MAX_DURATION_MS;
    }
    const uint32_t duty = (1023U * strength_percent) / 100U;
    ESP_RETURN_ON_ERROR(ledc_set_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0, duty),
                        "board", "set motor duty");
    ESP_RETURN_ON_ERROR(ledc_update_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0),
                        "board", "start motor pulse");
    s_motor_stop_at = xTaskGetTickCount() + pdMS_TO_TICKS(duration_ms);
    s_motor_running = true;
    return ESP_OK;
}

esp_err_t board_haptic_stop(void)
{
    ESP_RETURN_ON_ERROR(
        ledc_set_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0, 0),
        "board", "stop motor duty");
    ESP_RETURN_ON_ERROR(
        ledc_update_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0),
        "board", "stop motor output");
    s_motor_running = false;
    return ESP_OK;
}

esp_err_t board_display_fill(uint16_t rgb565)
{
    s_status_page_active = false;
    if (s_panel == NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    for (size_t index = 0; index < sizeof(s_display_buffer) / sizeof(s_display_buffer[0]); ++index) {
        s_display_buffer[index] = rgb565;
    }
    /* Transfer in bounded strips to avoid allocating a full 64.8 kB framebuffer. */
    for (int y = 0; y < s_display_height; y += DISPLAY_TRANSFER_LINES) {
        const int lines = (y + DISPLAY_TRANSFER_LINES <= s_display_height)
                              ? DISPLAY_TRANSFER_LINES
                              : s_display_height - y;
        ESP_RETURN_ON_ERROR(esp_lcd_panel_draw_bitmap(s_panel, 0, y,
                                                      s_display_width, y + lines,
                                                      s_display_buffer),
                            "board", "fill display");
    }
    return ESP_OK;
}

static bool boot_dot_pixel_visible(uint8_t x, uint8_t y, uint8_t size)
{
    if (size < 4) {
        return true;
    }
    return !((x == 0 || x == size - 1) &&
             (y == 0 || y == size - 1));
}

esp_err_t board_display_show_boot_logo(uint8_t density_percent)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    if (density_percent > 100) return ESP_ERR_INVALID_ARG;
    const board_mist_view_t view = {
        .page = BOARD_MIST_BOOT, .phase = density_percent / 100.0f,
    };
    return board_display_show_mist(&view);
#endif
    static const char lines[1][7] = {"BORING"};
    enum {
        LINE_COUNT = 1,
        CHARACTER_COUNT = 6,
        CHARACTER_ADVANCE = 6,
        LINE_ADVANCE = 9,
    };
    if (density_percent > 100) {
        return ESP_ERR_INVALID_ARG;
    }
    if (s_panel == NULL || s_display_width > DISPLAY_MAX_WIDTH) {
        return ESP_ERR_INVALID_STATE;
    }

    /* Square panels need the compact pitch; the wide pitch is 139 px. */
    const bool landscape = s_display_width > s_display_height;
    const uint8_t pitch = landscape ? 4 : 3;
    const uint8_t dot_size = landscape ? 3 : 2;
    const uint16_t logo_width =
        ((CHARACTER_COUNT - 1) * CHARACTER_ADVANCE + 4) * pitch + dot_size;
    const uint16_t logo_height =
        ((LINE_COUNT - 1) * LINE_ADVANCE + 6) * pitch + dot_size;
    if (logo_width > s_display_width || logo_height > s_display_height) {
        return ESP_ERR_INVALID_STATE;
    }
    const int origin_x = (s_display_width - logo_width) / 2;
    const int origin_y = (s_display_height - logo_height) / 2;

    s_status_page_active = false;
    size_t strip_index = 0;
    for (int strip_y = 0; strip_y < s_display_height;
         strip_y += DISPLAY_TRANSFER_LINES, ++strip_index) {
        const int strip_lines =
            strip_y + DISPLAY_TRANSFER_LINES <= s_display_height
                ? DISPLAY_TRANSFER_LINES : s_display_height - strip_y;
        uint16_t *strip = s_text_band_buffers[strip_index % 2];
        for (size_t index = 0;
             index < (size_t)s_display_width * strip_lines; ++index) {
            strip[index] = 0x0000;
        }

        for (size_t line = 0; line < LINE_COUNT; ++line) {
            for (size_t character = 0; character < CHARACTER_COUNT;
                 ++character) {
                for (uint8_t row = 0; row < 7; ++row) {
                    const uint8_t row_bits =
                        board_display_module_row(lines[line][character], row);
                    for (uint8_t column = 0; column < 5; ++column) {
                        if ((row_bits & (1U << (4 - column))) == 0) {
                            continue;
                        }
                        const uint16_t grid_x =
                            character * CHARACTER_ADVANCE + column;
                        const uint16_t grid_y = line * LINE_ADVANCE + row;
                        if (!board_display_boot_dot_visible(
                                grid_x, grid_y, density_percent)) {
                            continue;
                        }
                        const int dot_x = origin_x + grid_x * pitch;
                        const int dot_y = origin_y + grid_y * pitch;
                        for (uint8_t dy = 0; dy < dot_size; ++dy) {
                            const int pixel_y = dot_y + dy;
                            if (pixel_y < strip_y ||
                                pixel_y >= strip_y + strip_lines) {
                                continue;
                            }
                            for (uint8_t dx = 0; dx < dot_size; ++dx) {
                                const int pixel_x = dot_x + dx;
                                if (boot_dot_pixel_visible(dx, dy, dot_size) &&
                                    board_display_circular_aperture_contains(
                                        pixel_x, pixel_y,
                                        s_display_width, s_display_height,
                                        MATRIX12_BOOT_APERTURE_DIAMETER_PIXELS)) {
                                    strip[(pixel_y - strip_y) *
                                              s_display_width +
                                          pixel_x] = 0xFFFF;
                                }
                            }
                        }
                    }
                }
            }
        }
        ESP_RETURN_ON_ERROR(
            esp_lcd_panel_draw_bitmap(s_panel, 0, strip_y,
                                      s_display_width,
                                      strip_y + strip_lines, strip),
            "board", "draw circular module-dot boot logo");
    }
    return ESP_OK;
}

typedef enum {
    ROUND_ICON_USB = 0,
    ROUND_ICON_BLE,
    ROUND_ICON_DISCONNECTED,
    ROUND_ICON_HAPTIC,
    ROUND_ICON_BACK,
    ROUND_ICON_CONFIRM,
    ROUND_ICON_APPLYING,
    ROUND_ICON_ERROR,
    ROUND_ICON_TIMER,
    ROUND_ICON_HOURGLASS,
    ROUND_ICON_SETTINGS,
    ROUND_ICON_MACOS,
    ROUND_ICON_WINDOWS,
    ROUND_ICON_PLAY,
    ROUND_ICON_PAUSE,
    ROUND_ICON_CANCEL,
    ROUND_ICON_POWER,
    ROUND_ICON_WARNING,
    ROUND_ICON_PROFILE,
    ROUND_ICON_LIGHTING,
    ROUND_ICON_STANDBY,
    ROUND_ICON_EXIT,
    ROUND_ICON_MODE_KEY,
    ROUND_ICON_MODE_CODEX,
    ROUND_ICON_CHARGING,
    ROUND_ICON_RESTART,
    ROUND_ICON_SYSTEM,
    ROUND_ICON_COUNT,
} round_icon_t;

enum {
    MATRIX12_ROUND_GRID_COUNT = 31,
    MATRIX12_ROUND_FUNCTION_GRID_COUNT = 25,
    MATRIX12_ROUND_FUNCTION_DOT_SIZE = 4,
    MATRIX12_ROUND_FUNCTION_DOT_PITCH = 5,
    MATRIX12_BATTERY_RING_DOT_COUNT = BOARD_DISPLAY_BATTERY_RING_DOT_COUNT,
    MATRIX12_BATTERY_RING_DOT_SIZE = 4,
    MATRIX12_ROUND_BACKGROUND_COLOR = 0x0000,
    MATRIX12_ROUND_WHITE = 0xFFFF,
    /* MIST #626B6A navigation hints, in panel wire order. */
    MATRIX12_ROUND_HINT_GRAY = 0x4D63,
    /* RGB565 #CE864A, byte-swapped because SPI sends this little-endian buffer verbatim. */
    MATRIX12_ROUND_MUTED_ORANGE = 0x29CC,
    /* RGB565 #4E9B62 and #E63B3B, byte-swapped for the panel buffer. */
    MATRIX12_BATTERY_GREEN = 0xCC4C,
    MATRIX12_BATTERY_RED = 0xC7E1,
    /* RGB565 #FF6A00, byte-swapped for the panel transfer buffer. */
    MATRIX12_BMR_ORANGE = 0x40FB,
    MATRIX12_BMR_STANDARD_ICON_PIXELS = 32,
    MATRIX12_BMR_ACTION_ICON_PIXELS = 24,
};

static void draw_round_grid_dot(uint16_t *strip, int strip_y, int strip_lines,
                                int grid_x, int grid_y, uint16_t color,
                                uint8_t dot_size)
{
    const bool function_dot = dot_size == MATRIX12_ROUND_FUNCTION_DOT_SIZE;
    const int grid_count = function_dot ? MATRIX12_ROUND_FUNCTION_GRID_COUNT
                                        : MATRIX12_ROUND_GRID_COUNT;
    const int dot_pitch = function_dot ? MATRIX12_ROUND_FUNCTION_DOT_PITCH
                                       : MATRIX12_IDLE_DOT_PITCH;
    const int field_size =
        (grid_count - 1) * dot_pitch + dot_size;
    const int origin_x = (s_display_width - field_size) / 2;
    const int origin_y = (s_display_height - field_size) / 2;
    const int dot_x = origin_x + grid_x * dot_pitch;
    const int dot_y = origin_y + grid_y * dot_pitch;
    for (uint8_t dy = 0; dy < dot_size; ++dy) {
        const int pixel_y = dot_y + dy;
        if (pixel_y < strip_y || pixel_y >= strip_y + strip_lines) {
            continue;
        }
        for (uint8_t dx = 0; dx < dot_size; ++dx) {
            const int pixel_x = dot_x + dx;
            if (pixel_x >= 0 && pixel_x < s_display_width &&
                board_display_circular_aperture_contains(
                    pixel_x, pixel_y, s_display_width, s_display_height,
                    MATRIX12_BOOT_APERTURE_DIAMETER_PIXELS)) {
                strip[(pixel_y - strip_y) * s_display_width + pixel_x] =
                    board_display_round_dot_color(color, dx, dy, dot_size);
            }
        }
    }
}

static int scale_bmr_offset(int value, int target_size, int source_span)
{
    const int magnitude = value < 0 ? -value : value;
    const int scaled = (magnitude * target_size + source_span / 2) /
                       source_span;
    return value < 0 ? -scaled : scaled;
}

static void draw_round_bmr_dot(uint16_t *strip, int strip_y, int strip_lines,
                               int center_x, int center_y, uint8_t dot_size,
                               uint16_t color)
{
    const int origin_x = center_x - dot_size / 2;
    const int origin_y = center_y - dot_size / 2;
    for (uint8_t dy = 0; dy < dot_size; ++dy) {
        const int pixel_y = origin_y + dy;
        if (pixel_y < strip_y || pixel_y >= strip_y + strip_lines) {
            continue;
        }
        for (uint8_t dx = 0; dx < dot_size; ++dx) {
            const int pixel_x = origin_x + dx;
            if (pixel_x >= 0 && pixel_x < s_display_width &&
                board_display_circular_aperture_contains(
                    pixel_x, pixel_y, s_display_width, s_display_height,
                    MATRIX12_BOOT_APERTURE_DIAMETER_PIXELS)) {
                strip[(pixel_y - strip_y) * s_display_width + pixel_x] =
                    board_display_round_dot_color(color, dx, dy, dot_size);
            }
        }
    }
}

static void draw_round_mist_icon(uint16_t *strip, int strip_y, int strip_lines,
                                 board_mist_icon_t icon, int center_x, int center_y,
                                 uint8_t pitch, uint8_t brightness)
{
    board_mist_draw_icon(strip, s_display_width, s_display_height,
                        strip_y, strip_lines, icon,
                        center_x, center_y, pitch, brightness);
}

static void draw_round_bmr_icon(uint16_t *strip, int strip_y, int strip_lines,
                                board_bmr_screen_icon_t icon, int center_x,
                                int center_y, int target_size,
                                uint16_t primary_color)
{
    /* Local save/cancel controls use the same MIST assets as other pages.
     * Specialized BMR actions without a MIST equivalent retain their artwork. */
    if (icon == BOARD_BMR_SCREEN_ICON_CONFIRM_SEND ||
        icon == BOARD_BMR_SCREEN_ICON_REJECT_CANCEL) {
        draw_round_mist_icon(
            strip, strip_y, strip_lines,
            icon == BOARD_BMR_SCREEN_ICON_CONFIRM_SEND
                ? BOARD_MIST_ICON_CONFIRM : BOARD_MIST_ICON_BACK,
            center_x, center_y, 3,
            primary_color == MATRIX12_ROUND_MUTED_ORANGE ? 255 : 140);
        return;
    }
    const board_bmr_icon_asset_t *asset = board_bmr_icon_asset(icon);
    if (asset == NULL) {
        return;
    }
    const int width = asset->max_x_centi_mm - asset->min_x_centi_mm;
    const int height = asset->max_y_centi_mm - asset->min_y_centi_mm;
    const int source_span = width > height ? width : height;
    const int source_center_x =
        (asset->min_x_centi_mm + asset->max_x_centi_mm) / 2;
    const int source_center_y =
        (asset->min_y_centi_mm + asset->max_y_centi_mm) / 2;
    for (size_t index = 0; index < asset->dot_count; ++index) {
        const board_bmr_dot_t *dot = &asset->dots[index];
        const int pixel_x = center_x + scale_bmr_offset(
            (int)dot->center_x_centi_mm - source_center_x,
            target_size, source_span);
        const int pixel_y = center_y + scale_bmr_offset(
            (int)dot->center_y_centi_mm - source_center_y,
            target_size, source_span);
        int dot_size = ((int)dot->radius_centi_mm * 2 * target_size +
                        source_span / 2) / source_span;
        if (dot_size < 1) {
            dot_size = 1;
        }
        const uint16_t color = dot->role == BOARD_BMR_DOT_ACCENT_ORANGE
                                   ? MATRIX12_BMR_ORANGE
                                   : primary_color;
        draw_round_bmr_dot(strip, strip_y, strip_lines, pixel_x, pixel_y,
                           (uint8_t)dot_size, color);
    }
}

static int round_function_icon_center(int origin_grid)
{
    const int field_size =
        (MATRIX12_ROUND_FUNCTION_GRID_COUNT - 1) *
            MATRIX12_ROUND_FUNCTION_DOT_PITCH +
        MATRIX12_ROUND_FUNCTION_DOT_SIZE;
    const int field_origin = (s_display_width - field_size) / 2;
    return field_origin +
           (origin_grid + 2) * MATRIX12_ROUND_FUNCTION_DOT_PITCH +
           MATRIX12_ROUND_FUNCTION_DOT_SIZE / 2;
}

/* Only the two status marks absent from the MIST set retain local matrices. */
static bool round_icon_dot(round_icon_t icon, uint8_t row, uint8_t column)
{
    static const uint8_t disconnected[] = {0x14, 0x08, 0x00, 0x02, 0x05};
    static const uint8_t charging[] = {0x04, 0x0C, 0x1E, 0x06, 0x04};
    const uint8_t *rows = icon == ROUND_ICON_CHARGING ? charging : disconnected;
    return row < 5 && column < 5 && (rows[row] & (1U << (4 - column))) != 0;
}

static board_mist_icon_t round_mist_icon(round_icon_t icon)
{
    static const board_mist_icon_t icons[ROUND_ICON_COUNT] = {
        [ROUND_ICON_USB] = BOARD_MIST_ICON_USB,
        [ROUND_ICON_BLE] = BOARD_MIST_ICON_BLUETOOTH,
        [ROUND_ICON_HAPTIC] = BOARD_MIST_ICON_HAPTIC,
        [ROUND_ICON_BACK] = BOARD_MIST_ICON_BACK,
        [ROUND_ICON_CONFIRM] = BOARD_MIST_ICON_CONFIRM,
        [ROUND_ICON_APPLYING] = BOARD_MIST_ICON_FOCUS,
        [ROUND_ICON_ERROR] = BOARD_MIST_ICON_ERROR,
        [ROUND_ICON_TIMER] = BOARD_MIST_ICON_FOCUS,
        [ROUND_ICON_HOURGLASS] = BOARD_MIST_ICON_FOCUS,
        [ROUND_ICON_SETTINGS] = BOARD_MIST_ICON_SETTINGS,
        [ROUND_ICON_MACOS] = BOARD_MIST_ICON_MACOS,
        [ROUND_ICON_WINDOWS] = BOARD_MIST_ICON_WINDOWS,
        [ROUND_ICON_PLAY] = BOARD_MIST_ICON_PLAY,
        [ROUND_ICON_PAUSE] = BOARD_MIST_ICON_PAUSE,
        [ROUND_ICON_CANCEL] = BOARD_MIST_ICON_CANCEL,
        [ROUND_ICON_POWER] = BOARD_MIST_ICON_POWER,
        [ROUND_ICON_WARNING] = BOARD_MIST_ICON_WARNING,
        [ROUND_ICON_PROFILE] = BOARD_MIST_ICON_PROFILE,
        [ROUND_ICON_LIGHTING] = BOARD_MIST_ICON_LIGHTING,
        [ROUND_ICON_STANDBY] = BOARD_MIST_ICON_STANDBY,
        [ROUND_ICON_EXIT] = BOARD_MIST_ICON_EXIT,
        [ROUND_ICON_MODE_KEY] = BOARD_MIST_ICON_HOME_NORMAL,
        [ROUND_ICON_MODE_CODEX] = BOARD_MIST_ICON_HOME_CODEX,
        [ROUND_ICON_RESTART] = BOARD_MIST_ICON_RESTART,
        [ROUND_ICON_SYSTEM] = BOARD_MIST_ICON_SYSTEM,
    };
    return icons[icon];
}

/* Existing page anchors remain in the 25-cell text/layout coordinate system.
 * MIST icons have their own whole-cell pitch; never resample their row masks. */
static void draw_round_icon(uint16_t *strip, int strip_y, int strip_lines,
                            round_icon_t icon, int origin_grid_x,
                            int origin_grid_y, uint16_t color)
{
    if (icon == ROUND_ICON_DISCONNECTED || icon == ROUND_ICON_CHARGING) {
        for (uint8_t row = 0; row < 5; ++row) {
            for (uint8_t column = 0; column < 5; ++column) {
                if (round_icon_dot(icon, row, column)) {
                    draw_round_grid_dot(strip, strip_y, strip_lines,
                                        origin_grid_x + column,
                                        origin_grid_y + row, color,
                                        MATRIX12_ROUND_FUNCTION_DOT_SIZE);
                }
            }
        }
        return;
    }
    const board_mist_icon_asset_t *asset = board_mist_icon_asset(round_mist_icon(icon));
    const uint8_t pitch = asset->width > 14 || asset->height > 14 ? 2 : 3;
    draw_round_mist_icon(strip, strip_y, strip_lines, round_mist_icon(icon),
                        round_function_icon_center(origin_grid_x),
                        round_function_icon_center(origin_grid_y), pitch,
                        color == MATRIX12_ROUND_MUTED_ORANGE ? 255 : 140);
}

static int round_centered_icon_origin_x(void)
{
    return (MATRIX12_ROUND_FUNCTION_GRID_COUNT - 5) / 2;
}

static round_icon_t round_page_icon(board_round_page_icon_t icon)
{
    static const round_icon_t icons[] = {
        [BOARD_ROUND_PAGE_ICON_TIMER] = ROUND_ICON_HOURGLASS,
        [BOARD_ROUND_PAGE_ICON_SETTINGS] = ROUND_ICON_SETTINGS,
        [BOARD_ROUND_PAGE_ICON_MACOS] = ROUND_ICON_MACOS,
        [BOARD_ROUND_PAGE_ICON_WINDOWS] = ROUND_ICON_WINDOWS,
        [BOARD_ROUND_PAGE_ICON_BACK] = ROUND_ICON_BACK,
        [BOARD_ROUND_PAGE_ICON_CONFIRM] = ROUND_ICON_CONFIRM,
        [BOARD_ROUND_PAGE_ICON_POWER] = ROUND_ICON_POWER,
        [BOARD_ROUND_PAGE_ICON_WARNING] = ROUND_ICON_WARNING,
        [BOARD_ROUND_PAGE_ICON_PLAY] = ROUND_ICON_PLAY,
        [BOARD_ROUND_PAGE_ICON_PAUSE] = ROUND_ICON_PAUSE,
        [BOARD_ROUND_PAGE_ICON_CANCEL] = ROUND_ICON_CANCEL,
        [BOARD_ROUND_PAGE_ICON_ERROR] = ROUND_ICON_ERROR,
        [BOARD_ROUND_PAGE_ICON_PROFILE] = ROUND_ICON_PROFILE,
        [BOARD_ROUND_PAGE_ICON_LIGHTING] = ROUND_ICON_LIGHTING,
        [BOARD_ROUND_PAGE_ICON_HAPTIC] = ROUND_ICON_HAPTIC,
        [BOARD_ROUND_PAGE_ICON_STANDBY] = ROUND_ICON_STANDBY,
        [BOARD_ROUND_PAGE_ICON_EXIT] = ROUND_ICON_EXIT,
        [BOARD_ROUND_PAGE_ICON_MODE_KEY] = ROUND_ICON_MODE_KEY,
        [BOARD_ROUND_PAGE_ICON_MODE_CODEX] = ROUND_ICON_MODE_CODEX,
        [BOARD_ROUND_PAGE_ICON_BLE] = ROUND_ICON_BLE,
        [BOARD_ROUND_PAGE_ICON_RESTART] = ROUND_ICON_RESTART,
        [BOARD_ROUND_PAGE_ICON_SYSTEM] = ROUND_ICON_SYSTEM,
    };
    return icons[icon];
}

static void draw_round_digit(uint16_t *strip, int strip_y, int strip_lines,
                             uint8_t digit, int origin_grid_x,
                             int origin_grid_y, uint16_t color)
{
    static const uint8_t rows[10][5] = {
        {0x07, 0x05, 0x05, 0x05, 0x07},
        {0x02, 0x06, 0x02, 0x02, 0x07},
        {0x07, 0x01, 0x07, 0x04, 0x07},
        {0x07, 0x01, 0x07, 0x01, 0x07},
        {0x05, 0x05, 0x07, 0x01, 0x01},
        {0x07, 0x04, 0x07, 0x01, 0x07},
        {0x07, 0x04, 0x07, 0x05, 0x07},
        {0x07, 0x01, 0x01, 0x01, 0x01},
        {0x07, 0x05, 0x07, 0x05, 0x07},
        {0x07, 0x05, 0x07, 0x01, 0x07},
    };
    for (uint8_t row = 0; row < 5; ++row) {
        for (uint8_t column = 0; column < 3; ++column) {
            if ((rows[digit][row] & (1U << (2 - column))) != 0) {
                draw_round_grid_dot(strip, strip_y, strip_lines,
                                    origin_grid_x + column,
                                    origin_grid_y + row, color,
                                    MATRIX12_ROUND_FUNCTION_DOT_SIZE);
            }
        }
    }
}

static void draw_round_timer_digits(uint16_t *strip, int strip_y,
                                    int strip_lines, uint8_t minutes,
                                    uint8_t seconds, uint16_t color)
{
    const uint8_t digits[] = {
        (uint8_t)(minutes / 10), (uint8_t)(minutes % 10),
        (uint8_t)(seconds / 10), (uint8_t)(seconds % 10),
    };
    static const int origins[] = {1, 5, 15, 19};
    for (size_t index = 0; index < 4; ++index) {
        draw_round_digit(strip, strip_y, strip_lines, digits[index],
                         origins[index], 10, color);
    }
    draw_round_grid_dot(strip, strip_y, strip_lines, 11, 11, color,
                        MATRIX12_ROUND_FUNCTION_DOT_SIZE);
    draw_round_grid_dot(strip, strip_y, strip_lines, 11, 13, color,
                        MATRIX12_ROUND_FUNCTION_DOT_SIZE);
}

static void draw_round_5r_text(uint16_t *strip, int strip_y, int strip_lines,
                               const char *text, int origin_grid_y,
                               uint16_t color)
{
    const uint16_t text_columns = board_display_5r_text_columns(text);
    int grid_x = ((int)MATRIX12_ROUND_FUNCTION_GRID_COUNT - text_columns) / 2;
    while (text != NULL && *text != '\0') {
        const uint8_t glyph_width = board_display_5r_glyph_width(*text);
        if (glyph_width == 0) {
            ++text;
            continue;
        }
        for (uint8_t row = 0; row < 5; ++row) {
            const uint8_t row_bits = board_display_5r_glyph_row(*text, row);
            for (uint8_t column = 0; column < glyph_width; ++column) {
                if ((row_bits & (1U << (glyph_width - 1 - column))) != 0) {
                    draw_round_grid_dot(strip, strip_y, strip_lines,
                                        grid_x + column, origin_grid_y + row,
                                        color,
                                        MATRIX12_ROUND_FUNCTION_DOT_SIZE);
                }
            }
        }
        grid_x += glyph_width + 1;
        ++text;
    }
}

typedef struct {
    char value;
    uint8_t width;
    uint8_t rows[5];
} round_mode_glyph_t;

static const round_mode_glyph_t *round_mode_glyph(char value)
{
    static const round_mode_glyph_t glyphs[] = {
        {'N', 4, {0x09, 0x0D, 0x0B, 0x09, 0x09}},
        {'O', 3, {0x07, 0x05, 0x05, 0x05, 0x07}},
        {'R', 3, {0x06, 0x05, 0x06, 0x05, 0x05}},
        {'M', 4, {0x09, 0x0F, 0x0F, 0x09, 0x09}},
        {'A', 3, {0x02, 0x05, 0x07, 0x05, 0x05}},
        {'L', 3, {0x04, 0x04, 0x04, 0x04, 0x07}},
        {'C', 3, {0x07, 0x04, 0x04, 0x04, 0x07}},
        {'D', 3, {0x06, 0x05, 0x05, 0x05, 0x06}},
        {'E', 3, {0x07, 0x04, 0x06, 0x04, 0x07}},
        {'X', 3, {0x05, 0x05, 0x02, 0x05, 0x05}},
    };
    for (size_t index = 0; index < sizeof(glyphs) / sizeof(glyphs[0]);
         ++index) {
        if (glyphs[index].value == value) {
            return &glyphs[index];
        }
    }
    return NULL;
}

static void draw_round_mode_label(uint16_t *strip, int strip_y,
                                  int strip_lines, board_round_mode_t mode,
                                  uint16_t color)
{
    static const char *const labels[] = {"NOR", "CODEX", "CC"};
    const char *text = labels[mode];
    int text_columns = 0;
    for (const char *cursor = text; *cursor != '\0'; ++cursor) {
        const round_mode_glyph_t *glyph = round_mode_glyph(*cursor);
        if (glyph != NULL) {
            text_columns += glyph->width + (text_columns > 0 ? 1 : 0);
        }
    }
    int grid_x = (MATRIX12_ROUND_FUNCTION_GRID_COUNT - text_columns) / 2;
    while (*text != '\0') {
        const round_mode_glyph_t *glyph = round_mode_glyph(*text++);
        if (glyph == NULL) {
            continue;
        }
        for (uint8_t row = 0; row < 5; ++row) {
            for (uint8_t column = 0; column < glyph->width; ++column) {
                if ((glyph->rows[row] &
                     (1U << (glyph->width - 1u - column))) != 0) {
                    draw_round_grid_dot(strip, strip_y, strip_lines,
                                        grid_x + column, 10 + row, color,
                                        MATRIX12_ROUND_FUNCTION_DOT_SIZE);
                }
            }
        }
        grid_x += glyph->width + 1;
    }
}

static void draw_round_battery_ring(uint16_t *strip, int strip_y,
                                    int strip_lines, int battery_percent)
{
    const float two_pi = 6.28318530717958647692f;
    const uint8_t visible_dot_count =
        board_display_battery_ring_dot_count(battery_percent);
    if (visible_dot_count == 0) {
        return;
    }
    const board_display_battery_level_t level =
        board_display_battery_level(battery_percent);
    const uint16_t color = level == BOARD_DISPLAY_BATTERY_HIGH
        ? MATRIX12_BATTERY_GREEN
        : level == BOARD_DISPLAY_BATTERY_MEDIUM
            ? MATRIX12_ROUND_MUTED_ORANGE
            : MATRIX12_BATTERY_RED;
    const float center_x = ((float)s_display_width - 1.0f) * 0.5f;
    const float center_y = ((float)s_display_height - 1.0f) * 0.5f;
    const float radius =
        ((float)MATRIX12_BOOT_APERTURE_DIAMETER_PIXELS * 0.5f) - 4.0f;
    for (uint8_t index = 0; index < visible_dot_count; ++index) {
        const float angle = two_pi * (float)index /
                            (float)MATRIX12_BATTERY_RING_DOT_COUNT;
        const int center_pixel_x =
            (int)lroundf(center_x + sinf(angle) * radius);
        const int center_pixel_y =
            (int)lroundf(center_y - cosf(angle) * radius);
        draw_round_bmr_dot(strip, strip_y, strip_lines,
                           center_pixel_x, center_pixel_y,
                           MATRIX12_BATTERY_RING_DOT_SIZE, color);
    }
}

static void draw_round_battery_splash_icon(uint16_t *strip, int strip_y,
                                           int strip_lines,
                                           int battery_percent)
{
    const int origin_x = 6;
    const int origin_y = 3;
    const int filled_columns = battery_percent <= 0
        ? 0 : (battery_percent + 9) / 10;
    for (int row = 0; row < 7; ++row) {
        for (int column = 0; column < 14; ++column) {
            const bool outline =
                (column < 12 &&
                 (row == 0 || row == 6 || column == 0 || column == 11)) ||
                (column >= 12 && row >= 2 && row <= 4);
            const bool fill = battery_percent >= 0 && row >= 1 && row <= 5 &&
                              column >= 1 && column <= filled_columns;
            const bool unknown = battery_percent < 0 &&
                ((row == 1 && column == 3) ||
                 (row == 2 && column == 4) ||
                 (row == 3 && column == 5) ||
                 (row == 4 && column == 6) ||
                 (row == 5 && column == 7));
            if (outline || fill || unknown) {
                draw_round_grid_dot(
                    strip, strip_y, strip_lines, origin_x + column,
                    origin_y + row,
                    fill ? MATRIX12_ROUND_MUTED_ORANGE : MATRIX12_ROUND_WHITE,
                    MATRIX12_ROUND_FUNCTION_DOT_SIZE);
            }
        }
    }
}

static void fill_round_background(uint16_t *strip, int strip_y,
                                  int strip_lines)
{
    const int field_size =
        (MATRIX12_ROUND_GRID_COUNT - 1) * MATRIX12_IDLE_DOT_PITCH +
        MATRIX12_IDLE_DOT_SIZE;
    const int origin_y = (s_display_height - field_size) / 2;
    int first_grid_y = (strip_y - origin_y) / MATRIX12_IDLE_DOT_PITCH;
    int last_grid_y =
        (strip_y + strip_lines - 1 - origin_y) / MATRIX12_IDLE_DOT_PITCH;
    if (first_grid_y < 0) {
        first_grid_y = 0;
    }
    if (last_grid_y >= MATRIX12_ROUND_GRID_COUNT) {
        last_grid_y = MATRIX12_ROUND_GRID_COUNT - 1;
    }
    for (int grid_y = first_grid_y; grid_y <= last_grid_y; ++grid_y) {
        for (int grid_x = 0; grid_x < MATRIX12_ROUND_GRID_COUNT; ++grid_x) {
            draw_round_grid_dot(strip, strip_y, strip_lines,
                                grid_x, grid_y, MATRIX12_ROUND_BACKGROUND_COLOR,
                                MATRIX12_ROUND_BACKGROUND_DOT_SIZE);
        }
    }
}

static esp_err_t display_prepare_page_background(void)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    size_t strip_index = 0;
    for (int strip_y = 0; strip_y < s_display_height;
         strip_y += ROUND_DISPLAY_TRANSFER_LINES, ++strip_index) {
        const int strip_lines =
            strip_y + ROUND_DISPLAY_TRANSFER_LINES <= s_display_height
                ? ROUND_DISPLAY_TRANSFER_LINES
                : s_display_height - strip_y;
        uint16_t *strip = s_text_band_buffers[strip_index % 2];
        memset(strip, 0,
               (size_t)s_display_width * strip_lines * sizeof(*strip));
        fill_round_background(strip, strip_y, strip_lines);
        ESP_RETURN_ON_ERROR(
            esp_lcd_panel_draw_bitmap(s_panel, 0, strip_y, s_display_width,
                                      strip_y + strip_lines, strip),
            "board", "draw black dot page background");
    }
    return ESP_OK;
#else
    return board_display_fill(0x0000);
#endif
}

static uint16_t display_readable_foreground(uint16_t foreground,
                                            uint16_t background)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    (void)background;
    if (foreground == 0x0000) {
        return MATRIX12_ROUND_WHITE;
    }
    return MATRIX12_ROUND_MUTED_ORANGE;
#else
    (void)background;
    return foreground;
#endif
}

esp_err_t board_display_show_mist_scene(const mist_glyph_scene_t *scene)
{
    if (s_panel == NULL || scene == NULL ||
        s_display_width != 128 || s_display_height != 128) {
        return ESP_ERR_INVALID_ARG;
    }
    s_status_page_active = false;
    size_t index = 0;
    for (int y = 0; y < 128; y += ROUND_DISPLAY_TRANSFER_LINES, ++index) {
        const int lines = y + ROUND_DISPLAY_TRANSFER_LINES <= 128
            ? ROUND_DISPLAY_TRANSFER_LINES : 128 - y;
        uint16_t *strip = s_text_band_buffers[index % 2];
        board_mist_ui_render_scene_strip(scene, strip, y, lines);
        ESP_RETURN_ON_ERROR(
            esp_lcd_panel_draw_bitmap(s_panel, 0, y, 128, y + lines, strip),
            "board", "draw MIST frame");
    }
    return ESP_OK;
}

esp_err_t board_display_poll_mist(uint32_t now_ms)
{
    const mist_glyph_scene_t *scene = board_mist_ui_frame(now_ms);
    return scene != NULL ? board_display_show_mist_scene(scene) : ESP_OK;
}

esp_err_t board_display_show_mist(const board_mist_view_t *view)
{
    if (s_panel == NULL || s_display_width != 128 || s_display_height != 128)
        return ESP_ERR_INVALID_ARG;
    const uint32_t now_ms = (uint32_t)xTaskGetTickCount() * portTICK_PERIOD_MS;
    if (!board_mist_ui_request(view, now_ms)) return ESP_ERR_INVALID_ARG;
    return board_display_poll_mist(now_ms);
}

void board_display_finish_mist_motion(void)
{
    board_mist_ui_finish_motion();
}

esp_err_t board_display_show_round_home(board_round_mode_t mode,
                                        board_round_link_t link,
                                        int battery_percent)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    if (mode > BOARD_ROUND_MODE_CLAUDE_CODE || link > BOARD_ROUND_LINK_BLE)
        return ESP_ERR_INVALID_ARG;
    const board_mist_view_t view = {.page = BOARD_MIST_HOME, .a = mode, .phase = 1};
    return board_display_show_mist(&view);
#endif
    if (s_panel == NULL || mode > BOARD_ROUND_MODE_CLAUDE_CODE ||
        link > BOARD_ROUND_LINK_BLE || s_display_width > DISPLAY_MAX_WIDTH) {
        return ESP_ERR_INVALID_ARG;
    }
    s_status_page_active = false;
    size_t strip_index = 0;
    for (int strip_y = 0; strip_y < s_display_height;
         strip_y += ROUND_DISPLAY_TRANSFER_LINES, ++strip_index) {
        const int strip_lines =
            strip_y + ROUND_DISPLAY_TRANSFER_LINES <= s_display_height
                ? ROUND_DISPLAY_TRANSFER_LINES : s_display_height - strip_y;
        uint16_t *strip = s_text_band_buffers[strip_index % 2];
        memset(strip, 0, (size_t)s_display_width * strip_lines * sizeof(*strip));
        fill_round_background(strip, strip_y, strip_lines);

        const round_icon_t link_icon = link == BOARD_ROUND_LINK_USB ? ROUND_ICON_USB
            : link == BOARD_ROUND_LINK_BLE ? ROUND_ICON_BLE : ROUND_ICON_DISCONNECTED;
        if (link == BOARD_ROUND_LINK_DISCONNECTED) {
            draw_round_icon(strip, strip_y, strip_lines, link_icon, 10, 2,
                            MATRIX12_ROUND_WHITE);
        } else {
            draw_round_mist_icon(strip, strip_y, strip_lines, round_mist_icon(link_icon),
                                 s_display_width / 2, 26, 2,
                                 255);
        }
        draw_round_battery_ring(strip, strip_y, strip_lines,
                                battery_percent);
        draw_round_mode_label(strip, strip_y, strip_lines, mode,
                              MATRIX12_ROUND_WHITE);

        ESP_RETURN_ON_ERROR(
            esp_lcd_panel_draw_bitmap(s_panel, 0, strip_y, s_display_width,
                                      strip_y + strip_lines, strip),
            "board", "draw Power V2 circular home");
    }
    return ESP_OK;
}

esp_err_t board_display_show_round_battery(int battery_percent,
                                           bool charging_standby)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    if (battery_percent < -1 || battery_percent > 100) return ESP_ERR_INVALID_ARG;
    const board_mist_view_t view = {
        .page = BOARD_MIST_BATTERY, .a = battery_percent,
        .b = charging_standby, .phase = 1,
    };
    return board_display_show_mist(&view);
#endif
    if (s_panel == NULL || battery_percent < -1 || battery_percent > 100 ||
        s_display_width > DISPLAY_MAX_WIDTH) {
        return ESP_ERR_INVALID_ARG;
    }
    uint8_t digits[3];
    size_t digit_count = 0;
    if (battery_percent >= 100) {
        digits[digit_count++] = 1;
        digits[digit_count++] = 0;
        digits[digit_count++] = 0;
    } else if (battery_percent >= 10) {
        digits[digit_count++] = (uint8_t)(battery_percent / 10);
        digits[digit_count++] = (uint8_t)(battery_percent % 10);
    } else if (battery_percent >= 0) {
        digits[digit_count++] = (uint8_t)battery_percent;
    }

    s_status_page_active = false;
    size_t strip_index = 0;
    for (int strip_y = 0; strip_y < s_display_height;
         strip_y += ROUND_DISPLAY_TRANSFER_LINES, ++strip_index) {
        const int strip_lines =
            strip_y + ROUND_DISPLAY_TRANSFER_LINES <= s_display_height
                ? ROUND_DISPLAY_TRANSFER_LINES : s_display_height - strip_y;
        uint16_t *strip = s_text_band_buffers[strip_index % 2];
        memset(strip, 0, (size_t)s_display_width * strip_lines * sizeof(*strip));
        fill_round_background(strip, strip_y, strip_lines);
        draw_round_battery_splash_icon(strip, strip_y, strip_lines,
                                       battery_percent);
        if (charging_standby) {
            draw_round_icon(strip, strip_y, strip_lines,
                            ROUND_ICON_CHARGING, 10, 10,
                            MATRIX12_ROUND_MUTED_ORANGE);
        }

        if (digit_count > 0) {
            const int total_width = (int)digit_count * 3 +
                                    ((int)digit_count - 1);
            const int origin_x =
                ((int)MATRIX12_ROUND_FUNCTION_GRID_COUNT - total_width) / 2;
            for (size_t index = 0; index < digit_count; ++index) {
                draw_round_digit(strip, strip_y, strip_lines, digits[index],
                                 origin_x + (int)index * 4, 15,
                                 MATRIX12_ROUND_MUTED_ORANGE);
            }
        }

        ESP_RETURN_ON_ERROR(
            esp_lcd_panel_draw_bitmap(s_panel, 0, strip_y, s_display_width,
                                      strip_y + strip_lines, strip),
            "board", "draw Power V2 USB-wake charging-standby battery page");
    }
    return ESP_OK;
}

static esp_err_t show_round_level_setting(round_icon_t icon,
                                          uint8_t candidate_level,
                                          board_round_setting_state_t state,
                                          bool confirm_selected)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    if (candidate_level > 4 || state > BOARD_ROUND_SETTING_ERROR)
        return ESP_ERR_INVALID_ARG;
    const board_mist_view_t view = {
        .page = icon == ROUND_ICON_HAPTIC ? BOARD_MIST_HAPTIC : BOARD_MIST_LIGHTING,
        .a = candidate_level, .b = state, .c = confirm_selected,
        .max_level = 4, .phase = 1,
    };
    return board_display_show_mist(&view);
#endif
    if (s_panel == NULL || candidate_level > 9 ||
        state > BOARD_ROUND_SETTING_ERROR) {
        return ESP_ERR_INVALID_ARG;
    }
    char candidate[2];
    snprintf(candidate, sizeof(candidate), "%u", (unsigned)candidate_level);
    s_status_page_active = false;
    size_t strip_index = 0;
    for (int strip_y = 0; strip_y < s_display_height;
         strip_y += ROUND_DISPLAY_TRANSFER_LINES, ++strip_index) {
        const int strip_lines =
            strip_y + ROUND_DISPLAY_TRANSFER_LINES <= s_display_height
                ? ROUND_DISPLAY_TRANSFER_LINES : s_display_height - strip_y;
        uint16_t *strip = s_text_band_buffers[strip_index % 2];
        memset(strip, 0, (size_t)s_display_width * strip_lines * sizeof(*strip));
        fill_round_background(strip, strip_y, strip_lines);
        draw_round_icon(strip, strip_y, strip_lines, icon,
                        round_centered_icon_origin_x(), 3,
                        MATRIX12_ROUND_MUTED_ORANGE);
        draw_round_5r_text(strip, strip_y, strip_lines, candidate, 10,
                           MATRIX12_ROUND_WHITE);

        if (state == BOARD_ROUND_SETTING_APPLYING) {
            draw_round_icon(strip, strip_y, strip_lines, ROUND_ICON_APPLYING,
                            10, 17, MATRIX12_ROUND_WHITE);
        } else if (state == BOARD_ROUND_SETTING_ERROR) {
            draw_round_icon(strip, strip_y, strip_lines, ROUND_ICON_ERROR,
                            10, 17, MATRIX12_ROUND_MUTED_ORANGE);
        } else {
            const int action_y = round_function_icon_center(17);
            draw_round_bmr_icon(
                strip, strip_y, strip_lines,
                BOARD_BMR_SCREEN_ICON_REJECT_CANCEL,
                round_function_icon_center(5), action_y,
                MATRIX12_BMR_ACTION_ICON_PIXELS,
                confirm_selected ? MATRIX12_ROUND_WHITE
                                 : MATRIX12_ROUND_MUTED_ORANGE);
            draw_round_bmr_icon(
                strip, strip_y, strip_lines,
                BOARD_BMR_SCREEN_ICON_CONFIRM_SEND,
                round_function_icon_center(15), action_y,
                MATRIX12_BMR_ACTION_ICON_PIXELS,
                confirm_selected ? MATRIX12_ROUND_MUTED_ORANGE
                                 : MATRIX12_ROUND_WHITE);
        }

        ESP_RETURN_ON_ERROR(
            esp_lcd_panel_draw_bitmap(s_panel, 0, strip_y, s_display_width,
                                      strip_y + strip_lines, strip),
            "board", "draw Power V2 round level setting");
    }
    return ESP_OK;
}

esp_err_t board_display_show_round_haptic(uint8_t candidate_level,
                                          board_round_setting_state_t state,
                                          bool confirm_selected)
{
    return show_round_level_setting(ROUND_ICON_HAPTIC, candidate_level,
                                    state, confirm_selected);
}

esp_err_t board_display_show_round_lighting(uint8_t candidate_level,
                                            board_round_setting_state_t state,
                                            bool confirm_selected)
{
    return show_round_level_setting(ROUND_ICON_LIGHTING, candidate_level,
                                    state, confirm_selected);
}

esp_err_t board_display_show_round_standby(uint8_t candidate_minutes,
                                           uint8_t saved_minutes,
                                           board_round_setting_state_t state,
                                           bool confirm_selected)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    if (state > BOARD_ROUND_SETTING_ERROR) return ESP_ERR_INVALID_ARG;
    const board_mist_view_t view = {
        .page = BOARD_MIST_STANDBY, .a = candidate_minutes, .b = saved_minutes,
        .c = state, .d = confirm_selected, .phase = 1,
    };
    return board_display_show_mist(&view);
#endif
    if (s_panel == NULL || state > BOARD_ROUND_SETTING_ERROR) {
        return ESP_ERR_INVALID_ARG;
    }
    char candidate[4];
    if (candidate_minutes == 0) {
        snprintf(candidate, sizeof(candidate), "OFF");
    } else {
        snprintf(candidate, sizeof(candidate), "%u", (unsigned)candidate_minutes);
    }
    s_status_page_active = false;
    size_t strip_index = 0;
    for (int strip_y = 0; strip_y < s_display_height;
         strip_y += ROUND_DISPLAY_TRANSFER_LINES, ++strip_index) {
        const int strip_lines =
            strip_y + ROUND_DISPLAY_TRANSFER_LINES <= s_display_height
                ? ROUND_DISPLAY_TRANSFER_LINES : s_display_height - strip_y;
        uint16_t *strip = s_text_band_buffers[strip_index % 2];
        memset(strip, 0, (size_t)s_display_width * strip_lines * sizeof(*strip));
        fill_round_background(strip, strip_y, strip_lines);
        draw_round_icon(strip, strip_y, strip_lines, ROUND_ICON_STANDBY,
                        round_centered_icon_origin_x(), 3,
                        MATRIX12_ROUND_MUTED_ORANGE);
        draw_round_5r_text(strip, strip_y, strip_lines, candidate, 10,
                           candidate_minutes == saved_minutes
                               ? MATRIX12_ROUND_WHITE
                               : MATRIX12_ROUND_MUTED_ORANGE);

        if (state == BOARD_ROUND_SETTING_ERROR) {
            draw_round_icon(strip, strip_y, strip_lines, ROUND_ICON_ERROR,
                            10, 17, MATRIX12_ROUND_MUTED_ORANGE);
        } else {
            const int action_y = round_function_icon_center(17);
            draw_round_bmr_icon(
                strip, strip_y, strip_lines,
                BOARD_BMR_SCREEN_ICON_REJECT_CANCEL,
                round_function_icon_center(5), action_y,
                MATRIX12_BMR_ACTION_ICON_PIXELS,
                confirm_selected ? MATRIX12_ROUND_WHITE
                                 : MATRIX12_ROUND_MUTED_ORANGE);
            draw_round_bmr_icon(
                strip, strip_y, strip_lines,
                BOARD_BMR_SCREEN_ICON_CONFIRM_SEND,
                round_function_icon_center(15), action_y,
                MATRIX12_BMR_ACTION_ICON_PIXELS,
                confirm_selected ? MATRIX12_ROUND_MUTED_ORANGE
                                 : MATRIX12_ROUND_WHITE);
        }

        ESP_RETURN_ON_ERROR(
            esp_lcd_panel_draw_bitmap(s_panel, 0, strip_y, s_display_width,
                                      strip_y + strip_lines, strip),
            "board", "draw Power V2 round standby setting");
    }
    return ESP_OK;
}

esp_err_t board_display_show_round_icon_choice(board_round_page_icon_t first,
                                               board_round_page_icon_t second,
                                               size_t selected_index)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    if (first >= BOARD_ROUND_PAGE_ICON_COUNT || second >= BOARD_ROUND_PAGE_ICON_COUNT ||
        selected_index > 1) return ESP_ERR_INVALID_ARG;
    const board_mist_view_t view = {
        .page = second == BOARD_ROUND_PAGE_ICON_RESTART
            ? BOARD_MIST_RESTART_CONFIRM : BOARD_MIST_TWO_CHOICE,
        .a = (int)selected_index, .c = (int)selected_index,
        .confirm_selected = selected_index == 1,
        .primary = "BACK", .secondary = "YES", .title = "SELECT", .phase = 1,
    };
    return board_display_show_mist(&view);
#endif
    if (s_panel == NULL || first >= BOARD_ROUND_PAGE_ICON_COUNT ||
        second >= BOARD_ROUND_PAGE_ICON_COUNT || selected_index > 1 ||
        s_display_width > DISPLAY_MAX_WIDTH) {
        return ESP_ERR_INVALID_ARG;
    }
    s_status_page_active = false;
    size_t strip_index = 0;
    for (int strip_y = 0; strip_y < s_display_height;
         strip_y += ROUND_DISPLAY_TRANSFER_LINES, ++strip_index) {
        const int strip_lines =
            strip_y + ROUND_DISPLAY_TRANSFER_LINES <= s_display_height
                ? ROUND_DISPLAY_TRANSFER_LINES : s_display_height - strip_y;
        uint16_t *strip = s_text_band_buffers[strip_index % 2];
        memset(strip, 0, (size_t)s_display_width * strip_lines * sizeof(*strip));
        fill_round_background(strip, strip_y, strip_lines);
        draw_round_icon(strip, strip_y, strip_lines, round_page_icon(first),
                        5, 10, selected_index == 0
                                   ? MATRIX12_ROUND_MUTED_ORANGE
                                   : MATRIX12_ROUND_WHITE);
        draw_round_icon(strip, strip_y, strip_lines, round_page_icon(second),
                        15, 10, selected_index == 1
                                    ? MATRIX12_ROUND_MUTED_ORANGE
                                    : MATRIX12_ROUND_WHITE);
        ESP_RETURN_ON_ERROR(
            esp_lcd_panel_draw_bitmap(s_panel, 0, strip_y, s_display_width,
                                      strip_y + strip_lines, strip),
            "board", "draw Power V2 icon-only choice");
    }
    return ESP_OK;
}

static void draw_round_carousel_arrow(uint16_t *strip, int strip_y,
                                      int strip_lines, bool points_right)
{
    static const int8_t chevron_x_offsets[] = {2, 1, 0, 1, 2};
    const int base_x = points_right ? 21 : 1;
    for (size_t index = 0;
         index < sizeof(chevron_x_offsets) / sizeof(chevron_x_offsets[0]);
         ++index) {
        const int grid_x = points_right
                               ? base_x + 2 - chevron_x_offsets[index]
                               : base_x + chevron_x_offsets[index];
        draw_round_grid_dot(strip, strip_y, strip_lines, grid_x,
                            10 + (int)index, MATRIX12_ROUND_HINT_GRAY,
                            MATRIX12_ROUND_FUNCTION_DOT_SIZE);
    }
}

esp_err_t board_display_show_round_icon_carousel(board_round_page_icon_t icon,
                                                 size_t selected_index,
                                                 size_t item_count)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    if (icon >= BOARD_ROUND_PAGE_ICON_COUNT || item_count < 2 || item_count > 5 ||
        selected_index >= item_count) return ESP_ERR_INVALID_ARG;
    board_mist_view_t view = {
        .page = BOARD_MIST_CAROUSEL, .a = icon, .phase = 1,
        .position = (float)selected_index, .selected_index = (uint8_t)selected_index,
        .item_count = (uint8_t)item_count,
        .carousel_label = item_count == 2 && icon != BOARD_ROUND_PAGE_ICON_MACOS &&
            icon != BOARD_ROUND_PAGE_ICON_WINDOWS,
    };
    if (item_count == 5) {
        const uint8_t items[] = {21, 14, 13, 15, 20};
        memcpy(view.items, items, sizeof(items));
    } else if (icon == BOARD_ROUND_PAGE_ICON_MACOS || icon == BOARD_ROUND_PAGE_ICON_WINDOWS) {
        view.items[0] = BOARD_MIST_ICON_MACOS;
        view.items[1] = BOARD_MIST_ICON_WINDOWS;
    } else {
        view.items[0] = BOARD_MIST_ICON_FOCUS;
        view.items[1] = BOARD_MIST_ICON_SETTINGS;
    }
    view.items[selected_index] = (uint8_t)round_mist_icon(round_page_icon(icon));
    return board_display_show_mist(&view);
#endif
    if (s_panel == NULL || icon >= BOARD_ROUND_PAGE_ICON_COUNT ||
        item_count < 2 || item_count > 5 || selected_index >= item_count ||
        s_display_width > DISPLAY_MAX_WIDTH) {
        return ESP_ERR_INVALID_ARG;
    }
    s_status_page_active = false;
    size_t strip_index = 0;
    for (int strip_y = 0; strip_y < s_display_height;
         strip_y += ROUND_DISPLAY_TRANSFER_LINES, ++strip_index) {
        const int strip_lines =
            strip_y + ROUND_DISPLAY_TRANSFER_LINES <= s_display_height
                ? ROUND_DISPLAY_TRANSFER_LINES : s_display_height - strip_y;
        uint16_t *strip = s_text_band_buffers[strip_index % 2];
        memset(strip, 0, (size_t)s_display_width * strip_lines * sizeof(*strip));
        fill_round_background(strip, strip_y, strip_lines);
        draw_round_mist_icon(strip, strip_y, strip_lines, round_mist_icon(round_page_icon(icon)),
                             s_display_width / 2, s_display_height / 2, 4,
                             255);
        draw_round_carousel_arrow(strip, strip_y, strip_lines, false);
        draw_round_carousel_arrow(strip, strip_y, strip_lines, true);
        ESP_RETURN_ON_ERROR(
            esp_lcd_panel_draw_bitmap(s_panel, 0, strip_y, s_display_width,
                                      strip_y + strip_lines, strip),
            "board", "draw Power V2 single-icon arrow carousel");
    }
    return ESP_OK;
}

enum {
    MATRIX12_MIST_GRID_MIN = -3,
    MATRIX12_MIST_GRID_MAX = 27,
    MATRIX12_MIST_GRID_CENTER = 12,
    MATRIX12_MIST_GRID_ORIGIN = 14,
    MATRIX12_MIST_GRID_PITCH = 4,
    MATRIX12_MIST_GRID_CELL_SIZE = 3,
};

static void draw_round_mist_grid_cell(uint16_t *strip, int strip_y,
                                      int strip_lines, int grid_x, int grid_y,
                                      uint8_t level)
{
    const int pixel_x = MATRIX12_MIST_GRID_ORIGIN +
                        grid_x * MATRIX12_MIST_GRID_PITCH;
    const int pixel_y = MATRIX12_MIST_GRID_ORIGIN +
                        grid_y * MATRIX12_MIST_GRID_PITCH;
    const int last_x = pixel_x + MATRIX12_MIST_GRID_CELL_SIZE - 1;
    const int last_y = pixel_y + MATRIX12_MIST_GRID_CELL_SIZE - 1;
    if (pixel_y >= strip_y + strip_lines || last_y < strip_y ||
        pixel_x < 0 || pixel_y < 0 || last_x >= s_display_width ||
        last_y >= s_display_height ||
        !board_display_circular_aperture_contains(
            pixel_x, pixel_y, s_display_width, s_display_height,
            MATRIX12_BOOT_APERTURE_DIAMETER_PIXELS) ||
        !board_display_circular_aperture_contains(
            last_x, pixel_y, s_display_width, s_display_height,
            MATRIX12_BOOT_APERTURE_DIAMETER_PIXELS) ||
        !board_display_circular_aperture_contains(
            pixel_x, last_y, s_display_width, s_display_height,
            MATRIX12_BOOT_APERTURE_DIAMETER_PIXELS) ||
        !board_display_circular_aperture_contains(
            last_x, last_y, s_display_width, s_display_height,
            MATRIX12_BOOT_APERTURE_DIAMETER_PIXELS)) {
        return;
    }
    const uint16_t color = board_mist_gray_color(level);
    for (int dy = 0; dy < MATRIX12_MIST_GRID_CELL_SIZE; ++dy) {
        if (pixel_y + dy < strip_y || pixel_y + dy >= strip_y + strip_lines) {
            continue;
        }
        for (int dx = 0; dx < MATRIX12_MIST_GRID_CELL_SIZE; ++dx) {
            strip[(pixel_y + dy - strip_y) * s_display_width + pixel_x + dx] =
                color;
        }
    }
}

static int round_prompt_sector(int dx, int dy)
{
    const int abs_x = dx < 0 ? -dx : dx;
    const int abs_y = dy < 0 ? -dy : dy;
    if (dy < 0 && 125 * abs_x < 97 * -dy) {
        return 0;
    }
    if (dx > 0 && 125 * abs_y < 97 * dx) {
        return 1;
    }
    if (dy > 0 && 125 * abs_x < 97 * dy) {
        return 2;
    }
    if (dx < 0 && 125 * abs_y < 97 * -dx) {
        return 3;
    }
    return -1;
}

static void draw_round_mist_prompt_digit(uint16_t *strip, int strip_y,
                                         int strip_lines, uint8_t prompt_id,
                                         bool selected)
{
    static const uint8_t rows[4][5] = {
        {0x02, 0x06, 0x02, 0x02, 0x07},
        {0x06, 0x01, 0x02, 0x04, 0x07},
        {0x06, 0x01, 0x02, 0x01, 0x06},
        {0x05, 0x05, 0x07, 0x01, 0x01},
    };
    static const int8_t centers[4][2] = {
        {12, 2}, {22, 12}, {12, 22}, {2, 12},
    };
    for (int row = 0; row < 5; ++row) {
        for (int column = 0; column < 3; ++column) {
            const bool lit =
                (rows[prompt_id - 1u][row] & (1U << (2 - column))) != 0;
            draw_round_mist_grid_cell(
                strip, strip_y, strip_lines,
                centers[prompt_id - 1u][0] + column - 1,
                centers[prompt_id - 1u][1] + row - 2,
                lit ? (selected ? 250 : 110) : (selected ? 56 : 18));
        }
    }
}

esp_err_t board_display_show_round_prompt_palette(uint8_t selected_prompt_id)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    if (selected_prompt_id < 1 || selected_prompt_id > 4) return ESP_ERR_INVALID_ARG;
    const board_mist_view_t view = {
        .page = BOARD_MIST_PROMPT, .a = selected_prompt_id, .phase = 1,
    };
    return board_display_show_mist(&view);
#endif
    if (s_panel == NULL || selected_prompt_id < 1u ||
        selected_prompt_id > 4u || s_display_width > DISPLAY_MAX_WIDTH) {
        return ESP_ERR_INVALID_ARG;
    }
    s_status_page_active = false;
    size_t strip_index = 0;
    for (int strip_y = 0; strip_y < s_display_height;
         strip_y += ROUND_DISPLAY_TRANSFER_LINES, ++strip_index) {
        const int strip_lines =
            strip_y + ROUND_DISPLAY_TRANSFER_LINES <= s_display_height
                ? ROUND_DISPLAY_TRANSFER_LINES : s_display_height - strip_y;
        uint16_t *strip = s_text_band_buffers[strip_index % 2];
        memset(strip, 0, (size_t)s_display_width * strip_lines * sizeof(*strip));
        for (int grid_y = MATRIX12_MIST_GRID_MIN;
             grid_y <= MATRIX12_MIST_GRID_MAX; ++grid_y) {
            for (int grid_x = MATRIX12_MIST_GRID_MIN;
                 grid_x <= MATRIX12_MIST_GRID_MAX; ++grid_x) {
                draw_round_mist_grid_cell(
                    strip, strip_y, strip_lines, grid_x, grid_y, 0);
                const int dx = grid_x - MATRIX12_MIST_GRID_CENTER;
                const int dy = grid_y - MATRIX12_MIST_GRID_CENTER;
                const int radius_squared = dx * dx + dy * dy;
                const int sector = round_prompt_sector(dx, dy);
                if (radius_squared >= 43 && radius_squared <= 196 &&
                    sector >= 0) {
                    const bool selected =
                        sector == (int)selected_prompt_id - 1;
                    const bool outer_edge = radius_squared >= 164;
                    draw_round_mist_grid_cell(
                        strip, strip_y, strip_lines, grid_x, grid_y,
                        selected ? (outer_edge ? 140 : 56)
                                 : (outer_edge ? 41 : 18));
                }
            }
        }
        for (uint8_t prompt_id = 1u; prompt_id <= 4u; ++prompt_id) {
            draw_round_mist_prompt_digit(
                strip, strip_y, strip_lines, prompt_id,
                prompt_id == selected_prompt_id);
        }
        static const int8_t directions[4][2] = {
            {0, -1}, {1, 0}, {0, 1}, {-1, 0},
        };
        static const uint8_t signal_levels[3] = {77, 140, 204};
        for (int step = 0; step < 3; ++step) {
            draw_round_mist_grid_cell(
                strip, strip_y, strip_lines,
                MATRIX12_MIST_GRID_CENTER +
                    directions[selected_prompt_id - 1u][0] * step,
                MATRIX12_MIST_GRID_CENTER +
                    directions[selected_prompt_id - 1u][1] * step,
                signal_levels[step]);
        }
        ESP_RETURN_ON_ERROR(
            esp_lcd_panel_draw_bitmap(s_panel, 0, strip_y, s_display_width,
                                      strip_y + strip_lines, strip),
            "board", "draw Power V2 Quick Prompt palette");
    }
    return ESP_OK;
}

esp_err_t board_display_show_round_timer(uint8_t minutes, uint8_t seconds,
                                         board_round_timer_state_t state,
                                         bool confirm_selected)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    if (minutes > 99 || seconds > 59 || state > BOARD_ROUND_TIMER_PAUSED)
        return ESP_ERR_INVALID_ARG;
    const board_mist_view_t view = {
        .page = BOARD_MIST_TIMER, .a = minutes, .b = seconds,
        .d = confirm_selected, .confirm_selected = confirm_selected, .phase = 0,
        .remaining_seconds = (uint32_t)minutes * 60 + seconds,
        .timer_state = (board_mist_timer_state_t)state,
    };
    return board_display_show_mist(&view);
#endif
    if (s_panel == NULL || minutes > 99 || seconds > 59 ||
        state > BOARD_ROUND_TIMER_PAUSED ||
        s_display_width > DISPLAY_MAX_WIDTH) {
        return ESP_ERR_INVALID_ARG;
    }
    const round_icon_t state_icon = state == BOARD_ROUND_TIMER_SETUP
                                        ? ROUND_ICON_TIMER
                                    : state == BOARD_ROUND_TIMER_RUNNING
                                        ? ROUND_ICON_PLAY
                                        : ROUND_ICON_PAUSE;
    const round_icon_t left_action = state == BOARD_ROUND_TIMER_SETUP
                                         ? ROUND_ICON_ERROR
                                         : ROUND_ICON_CANCEL;
    const round_icon_t right_action = state == BOARD_ROUND_TIMER_SETUP
                                          ? ROUND_ICON_CONFIRM
                                      : state == BOARD_ROUND_TIMER_RUNNING
                                          ? ROUND_ICON_PAUSE
                                          : ROUND_ICON_PLAY;
    s_status_page_active = false;
    size_t strip_index = 0;
    for (int strip_y = 0; strip_y < s_display_height;
         strip_y += ROUND_DISPLAY_TRANSFER_LINES, ++strip_index) {
        const int strip_lines =
            strip_y + ROUND_DISPLAY_TRANSFER_LINES <= s_display_height
                ? ROUND_DISPLAY_TRANSFER_LINES : s_display_height - strip_y;
        uint16_t *strip = s_text_band_buffers[strip_index % 2];
        memset(strip, 0, (size_t)s_display_width * strip_lines * sizeof(*strip));
        fill_round_background(strip, strip_y, strip_lines);
        draw_round_icon(strip, strip_y, strip_lines, state_icon, 10, 3,
                        MATRIX12_ROUND_MUTED_ORANGE);
        draw_round_timer_digits(strip, strip_y, strip_lines, minutes, seconds,
                                MATRIX12_ROUND_MUTED_ORANGE);
        if (state == BOARD_ROUND_TIMER_SETUP) {
            const int action_y = round_function_icon_center(17);
            draw_round_bmr_icon(
                strip, strip_y, strip_lines,
                BOARD_BMR_SCREEN_ICON_REJECT_CANCEL,
                round_function_icon_center(5), action_y,
                MATRIX12_BMR_ACTION_ICON_PIXELS,
                confirm_selected ? MATRIX12_ROUND_WHITE
                                 : MATRIX12_ROUND_MUTED_ORANGE);
            draw_round_bmr_icon(
                strip, strip_y, strip_lines,
                BOARD_BMR_SCREEN_ICON_CONFIRM_SEND,
                round_function_icon_center(15), action_y,
                MATRIX12_BMR_ACTION_ICON_PIXELS,
                confirm_selected ? MATRIX12_ROUND_MUTED_ORANGE
                                 : MATRIX12_ROUND_WHITE);
        } else {
            draw_round_icon(strip, strip_y, strip_lines, left_action, 5, 17,
                            confirm_selected ? MATRIX12_ROUND_WHITE
                                             : MATRIX12_ROUND_MUTED_ORANGE);
            draw_round_icon(strip, strip_y, strip_lines, right_action, 15, 17,
                            confirm_selected ? MATRIX12_ROUND_MUTED_ORANGE
                                             : MATRIX12_ROUND_WHITE);
        }
        ESP_RETURN_ON_ERROR(
            esp_lcd_panel_draw_bitmap(s_panel, 0, strip_y, s_display_width,
                                      strip_y + strip_lines, strip),
            "board", "draw Power V2 icon-only timer");
    }
    return ESP_OK;
}

esp_err_t board_display_show_round_notice(board_round_page_icon_t icon)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    if (icon >= BOARD_ROUND_PAGE_ICON_COUNT) return ESP_ERR_INVALID_ARG;
    const board_mist_view_t view = {.page = BOARD_MIST_NOTICE, .a = icon, .phase = 1};
    return board_display_show_mist(&view);
#endif
    if (s_panel == NULL || icon >= BOARD_ROUND_PAGE_ICON_COUNT ||
        s_display_width > DISPLAY_MAX_WIDTH) {
        return ESP_ERR_INVALID_ARG;
    }
    s_status_page_active = false;
    size_t strip_index = 0;
    for (int strip_y = 0; strip_y < s_display_height;
         strip_y += ROUND_DISPLAY_TRANSFER_LINES, ++strip_index) {
        const int strip_lines =
            strip_y + ROUND_DISPLAY_TRANSFER_LINES <= s_display_height
                ? ROUND_DISPLAY_TRANSFER_LINES : s_display_height - strip_y;
        uint16_t *strip = s_text_band_buffers[strip_index % 2];
        memset(strip, 0, (size_t)s_display_width * strip_lines * sizeof(*strip));
        fill_round_background(strip, strip_y, strip_lines);
        draw_round_mist_icon(strip, strip_y, strip_lines, round_mist_icon(round_page_icon(icon)),
                             s_display_width / 2, s_display_height / 2, 4,
                             255);
        ESP_RETURN_ON_ERROR(
            esp_lcd_panel_draw_bitmap(s_panel, 0, strip_y, s_display_width,
                                      strip_y + strip_lines, strip),
            "board", "draw Power V2 icon-only notice");
    }
    return ESP_OK;
}

esp_err_t board_display_show_round_bmr_icon(board_bmr_screen_icon_t icon)
{
    if (s_panel == NULL || icon >= BOARD_BMR_SCREEN_ICON_COUNT ||
        s_display_width > DISPLAY_MAX_WIDTH) {
        return ESP_ERR_INVALID_ARG;
    }
    s_status_page_active = false;
    size_t strip_index = 0;
    for (int strip_y = 0; strip_y < s_display_height;
         strip_y += ROUND_DISPLAY_TRANSFER_LINES, ++strip_index) {
        const int strip_lines =
            strip_y + ROUND_DISPLAY_TRANSFER_LINES <= s_display_height
                ? ROUND_DISPLAY_TRANSFER_LINES : s_display_height - strip_y;
        uint16_t *strip = s_text_band_buffers[strip_index % 2];
        memset(strip, 0, (size_t)s_display_width * strip_lines * sizeof(*strip));
        fill_round_background(strip, strip_y, strip_lines);
        /* The approved #111111 ink is mapped to white on the black display
         * carrier; Home's approved orange accent remains #FF6A00. */
        draw_round_bmr_icon(strip, strip_y, strip_lines, icon,
                            s_display_width / 2, s_display_height / 2,
                            MATRIX12_BMR_STANDARD_ICON_PIXELS,
                            MATRIX12_ROUND_WHITE);
        ESP_RETURN_ON_ERROR(
            esp_lcd_panel_draw_bitmap(s_panel, 0, strip_y, s_display_width,
                                      strip_y + strip_lines, strip),
            "board", "draw approved BMR round-screen icon");
    }
    return ESP_OK;
}

static bool valid_round_ble_slot(uint8_t slot,
                                 board_round_ble_slot_state_t state)
{
    return s_panel != NULL && slot >= 1u && slot <= 3u &&
           state <= BOARD_ROUND_BLE_SLOT_CONNECTED &&
           s_display_width <= DISPLAY_MAX_WIDTH;
}

static round_icon_t round_ble_slot_status_icon(
    board_round_ble_slot_state_t state)
{
    return state == BOARD_ROUND_BLE_SLOT_CONNECTED ? ROUND_ICON_BLE
           : state == BOARD_ROUND_BLE_SLOT_PAIRED ? ROUND_ICON_DISCONNECTED
                                                  : ROUND_ICON_APPLYING;
}

static void draw_round_ble_slot_body(uint16_t *strip, int strip_y,
                                     int strip_lines, uint8_t slot,
                                     board_round_ble_slot_state_t state,
                                     int origin_y)
{
    const round_icon_t status_icon =
        round_ble_slot_status_icon(state);
    draw_round_digit(strip, strip_y, strip_lines, slot, 6, origin_y,
                     MATRIX12_ROUND_MUTED_ORANGE);
    draw_round_icon(strip, strip_y, strip_lines, status_icon, 14, origin_y,
                    state == BOARD_ROUND_BLE_SLOT_CONNECTED
                        ? MATRIX12_ROUND_MUTED_ORANGE
                        : MATRIX12_ROUND_WHITE);
}

static void draw_round_progress_ring(uint16_t *strip, int strip_y,
                                     int strip_lines,
                                     uint8_t progress_percent)
{
    if (progress_percent == 0) {
        return;
    }
    const float two_pi = 6.28318530717958647692f;
    const int center_x2 = s_display_width - 1;
    const int center_y2 = s_display_height - 1;
    const int outer_radius2 = MATRIX12_BOOT_APERTURE_DIAMETER_PIXELS - 7;
    const int inner_radius2 = outer_radius2 - 5;
    const int outer_squared = outer_radius2 * outer_radius2;
    const int inner_squared = inner_radius2 * inner_radius2;
    const float progress = progress_percent >= 100
        ? two_pi
        : two_pi * (float)progress_percent / 100.0f;
    for (int pixel_y = strip_y; pixel_y < strip_y + strip_lines; ++pixel_y) {
        const int dy2 = pixel_y * 2 - center_y2;
        for (int pixel_x = 0; pixel_x < s_display_width; ++pixel_x) {
            const int dx2 = pixel_x * 2 - center_x2;
            const int distance_squared = dx2 * dx2 + dy2 * dy2;
            if (distance_squared < inner_squared ||
                distance_squared > outer_squared) {
                continue;
            }
            float angle = atan2f((float)dx2, (float)-dy2);
            if (angle < 0.0f) {
                angle += two_pi;
            }
            if (progress_percent >= 100 || angle <= progress) {
                strip[(pixel_y - strip_y) * s_display_width + pixel_x] =
                    MATRIX12_ROUND_MUTED_ORANGE;
            }
        }
    }
}

esp_err_t board_display_show_round_ble_slot(uint8_t slot,
                                            board_round_ble_slot_state_t state)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    if (!valid_round_ble_slot(slot, state)) return ESP_ERR_INVALID_ARG;
    const board_mist_view_t view = {.page = BOARD_MIST_BLE, .a = slot, .b = state, .phase = 1};
    return board_display_show_mist(&view);
#endif
    if (!valid_round_ble_slot(slot, state)) {
        return ESP_ERR_INVALID_ARG;
    }
    s_status_page_active = false;
    size_t strip_index = 0;
    for (int strip_y = 0; strip_y < s_display_height;
         strip_y += ROUND_DISPLAY_TRANSFER_LINES, ++strip_index) {
        const int strip_lines =
            strip_y + ROUND_DISPLAY_TRANSFER_LINES <= s_display_height
                ? ROUND_DISPLAY_TRANSFER_LINES : s_display_height - strip_y;
        uint16_t *strip = s_text_band_buffers[strip_index % 2];
        memset(strip, 0, (size_t)s_display_width * strip_lines * sizeof(*strip));
        fill_round_background(strip, strip_y, strip_lines);
        draw_round_ble_slot_body(strip, strip_y, strip_lines, slot, state, 9);
        ESP_RETURN_ON_ERROR(
            esp_lcd_panel_draw_bitmap(s_panel, 0, strip_y, s_display_width,
                                      strip_y + strip_lines, strip),
            "board", "draw Power V2 BLE host slot");
    }
    return ESP_OK;
}

esp_err_t board_display_show_round_ble_slot_progress(
    uint8_t slot, board_round_ble_slot_state_t state, uint8_t progress_percent)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    if (!valid_round_ble_slot(slot, state) || progress_percent > 100)
        return ESP_ERR_INVALID_ARG;
    const board_mist_view_t view = {
        .page = BOARD_MIST_BLE_PROGRESS, .a = slot, .b = state,
        .c = progress_percent, .phase = 1,
    };
    return board_display_show_mist(&view);
#endif
    if (!valid_round_ble_slot(slot, state) || progress_percent > 100u) {
        return ESP_ERR_INVALID_ARG;
    }
    s_status_page_active = false;
    size_t strip_index = 0;
    for (int strip_y = 0; strip_y < s_display_height;
         strip_y += ROUND_DISPLAY_TRANSFER_LINES, ++strip_index) {
        const int strip_lines =
            strip_y + ROUND_DISPLAY_TRANSFER_LINES <= s_display_height
                ? ROUND_DISPLAY_TRANSFER_LINES : s_display_height - strip_y;
        uint16_t *strip = s_text_band_buffers[strip_index % 2];
        memset(strip, 0, (size_t)s_display_width * strip_lines * sizeof(*strip));
        fill_round_background(strip, strip_y, strip_lines);
        draw_round_ble_slot_body(strip, strip_y, strip_lines, slot, state, 9);
        draw_round_progress_ring(strip, strip_y, strip_lines,
                                 progress_percent);
        ESP_RETURN_ON_ERROR(
            esp_lcd_panel_draw_bitmap(s_panel, 0, strip_y, s_display_width,
                                      strip_y + strip_lines, strip),
            "board", "draw Power V2 BLE slot replacement progress");
    }
    return ESP_OK;
}

esp_err_t board_display_show_round_ble_slot_confirm(
    uint8_t slot, board_round_ble_slot_state_t state, bool confirm_selected)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    if (!valid_round_ble_slot(slot, state)) return ESP_ERR_INVALID_ARG;
    const board_mist_view_t view = {
        .page = BOARD_MIST_BLE_CONFIRM, .a = slot, .b = state,
        .c = confirm_selected, .confirm_selected = confirm_selected, .phase = 1,
    };
    return board_display_show_mist(&view);
#endif
    if (!valid_round_ble_slot(slot, state)) {
        return ESP_ERR_INVALID_ARG;
    }
    s_status_page_active = false;
    size_t strip_index = 0;
    for (int strip_y = 0; strip_y < s_display_height;
         strip_y += ROUND_DISPLAY_TRANSFER_LINES, ++strip_index) {
        const int strip_lines =
            strip_y + ROUND_DISPLAY_TRANSFER_LINES <= s_display_height
                ? ROUND_DISPLAY_TRANSFER_LINES : s_display_height - strip_y;
        uint16_t *strip = s_text_band_buffers[strip_index % 2];
        memset(strip, 0, (size_t)s_display_width * strip_lines * sizeof(*strip));
        fill_round_background(strip, strip_y, strip_lines);
        draw_round_ble_slot_body(strip, strip_y, strip_lines, slot, state, 3);
        const int action_y = round_function_icon_center(17);
        draw_round_bmr_icon(
            strip, strip_y, strip_lines,
            BOARD_BMR_SCREEN_ICON_REJECT_CANCEL,
            round_function_icon_center(5), action_y,
            MATRIX12_BMR_ACTION_ICON_PIXELS,
            confirm_selected ? MATRIX12_ROUND_WHITE
                             : MATRIX12_ROUND_MUTED_ORANGE);
        draw_round_bmr_icon(
            strip, strip_y, strip_lines,
            BOARD_BMR_SCREEN_ICON_CONFIRM_SEND,
            round_function_icon_center(15), action_y,
            MATRIX12_BMR_ACTION_ICON_PIXELS,
            confirm_selected ? MATRIX12_ROUND_MUTED_ORANGE
                             : MATRIX12_ROUND_WHITE);
        ESP_RETURN_ON_ERROR(
            esp_lcd_panel_draw_bitmap(s_panel, 0, strip_y, s_display_width,
                                      strip_y + strip_lines, strip),
            "board", "draw Power V2 BLE slot replacement confirmation");
    }
    return ESP_OK;
}

static esp_err_t display_fill_status_bar(uint16_t rgb565)
{
    if (s_panel == NULL) {
        return ESP_ERR_INVALID_STATE;
    }
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    (void)rgb565;
    memset(s_status_bar_buffer, 0, sizeof(s_status_bar_buffer));
    fill_round_background(s_status_bar_buffer, 0,
                          DISPLAY_TRANSFER_LINES);
#else
    for (size_t index = 0; index < sizeof(s_status_bar_buffer) / sizeof(s_status_bar_buffer[0]); ++index) {
        s_status_bar_buffer[index] = rgb565;
    }
#endif
    return esp_lcd_panel_draw_bitmap(s_panel, 0, 0, s_display_width,
                                     DISPLAY_TRANSFER_LINES, s_status_bar_buffer);
}

esp_err_t board_set_display_config(uint8_t brightness_percent, uint16_t rotation)
{
    if (brightness_percent > 100 ||
        !(rotation == 0 || rotation == 90 || rotation == 180 || rotation == 270)) {
        return ESP_ERR_INVALID_ARG;
    }
    static const board_display_profile_t display_profile = {
        .native_width = BOARD_MATRIX12_V1_DISPLAY_WIDTH,
        .native_height = BOARD_MATRIX12_V1_DISPLAY_HEIGHT,
        .native_x_gap = BOARD_MATRIX12_V1_DISPLAY_X_GAP,
        .native_y_gap = BOARD_MATRIX12_V1_DISPLAY_Y_GAP,
        .mount_rotation = BOARD_MATRIX12_V1_DISPLAY_MOUNT_ROTATION,
    };
    const board_display_geometry_t geometry =
        board_display_geometry_for_memory_window(
            &display_profile, BOARD_MATRIX12_V1_DISPLAY_RAM_WIDTH,
            BOARD_MATRIX12_V1_DISPLAY_RAM_HEIGHT, rotation);
    ESP_RETURN_ON_ERROR(esp_lcd_panel_set_gap(s_panel, geometry.x_gap, geometry.y_gap),
                        "board", "set mounted display gap");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_swap_xy(s_panel,
                                              geometry.rotation == 90 ||
                                              geometry.rotation == 270),
                        "board", "set display axis orientation");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_mirror(s_panel,
                                             geometry.rotation == 180 ||
                                             geometry.rotation == 270,
                                             geometry.rotation == 90 ||
                                             geometry.rotation == 180),
                        "board", "set display mirror orientation");
    s_display_width = geometry.width;
    s_display_height = geometry.height;
    s_status_page_active = false;
    /* Power V2 ships at 60%; preserve the persisted 0..100% setting. */
    s_display_base_brightness = brightness_percent;
    return board_set_display_idle_scale(s_display_idle_scale);
}

esp_err_t board_set_display_idle_scale(uint8_t percent)
{
    if (percent > 100) return ESP_ERR_INVALID_ARG;
    s_display_idle_scale = percent;
    const uint32_t duty = (1023U * s_display_base_brightness * percent) / 10000U;
    ESP_RETURN_ON_ERROR(ledc_set_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_1, duty),
                        "board", "set display brightness");
    return ledc_update_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_1);
}

static const uint8_t *glyph_for(char value)
{
    static const uint8_t glyphs[36][5] = {
        {0x7E,0x11,0x11,0x11,0x7E},{0x7F,0x49,0x49,0x49,0x36},
        {0x3E,0x41,0x41,0x41,0x22},{0x7F,0x41,0x41,0x22,0x1C},
        {0x7F,0x49,0x49,0x49,0x41},{0x7F,0x09,0x09,0x09,0x01},
        {0x3E,0x41,0x49,0x49,0x7A},{0x7F,0x08,0x08,0x08,0x7F},
        {0x00,0x41,0x7F,0x41,0x00},{0x20,0x40,0x41,0x3F,0x01},
        {0x7F,0x08,0x14,0x22,0x41},{0x7F,0x40,0x40,0x40,0x40},
        {0x7F,0x02,0x0C,0x02,0x7F},{0x7F,0x04,0x08,0x10,0x7F},
        {0x3E,0x41,0x41,0x41,0x3E},{0x7F,0x09,0x09,0x09,0x06},
        {0x3E,0x41,0x51,0x21,0x5E},{0x7F,0x09,0x19,0x29,0x46},
        {0x46,0x49,0x49,0x49,0x31},{0x01,0x01,0x7F,0x01,0x01},
        {0x3F,0x40,0x40,0x40,0x3F},{0x1F,0x20,0x40,0x20,0x1F},
        {0x3F,0x40,0x38,0x40,0x3F},{0x63,0x14,0x08,0x14,0x63},
        {0x07,0x08,0x70,0x08,0x07},{0x61,0x51,0x49,0x45,0x43},
        {0x3E,0x51,0x49,0x45,0x3E},{0x00,0x42,0x7F,0x40,0x00},
        {0x42,0x61,0x51,0x49,0x46},{0x21,0x41,0x45,0x4B,0x31},
        {0x18,0x14,0x12,0x7F,0x10},{0x27,0x45,0x45,0x45,0x39},
        {0x3C,0x4A,0x49,0x49,0x30},{0x01,0x71,0x09,0x05,0x03},
        {0x36,0x49,0x49,0x49,0x36},{0x06,0x49,0x49,0x29,0x1E},
    };
    static const uint8_t colon[5] = {0x00,0x36,0x36,0x00,0x00};
    static const uint8_t greater_than[5] = {0x00,0x41,0x22,0x14,0x08};
    value = (char)toupper((unsigned char)value);
    if (value == ':') return colon;
    if (value == '>') return greater_than;
    if (value >= 'A' && value <= 'Z') return glyphs[value - 'A'];
    if (value >= '0' && value <= '9') return glyphs[26 + value - '0'];
    return NULL;
}

static esp_err_t draw_text_slot_style(size_t slot, size_t slot_count, const char *text,
                                      uint16_t foreground, uint16_t background,
                                      uint8_t preferred_scale)
{
    if (s_panel == NULL || text == NULL || slot_count == 0 || slot >= slot_count ||
        s_display_height <= DISPLAY_TRANSFER_LINES) {
        return ESP_ERR_INVALID_ARG;
    }
    const uint16_t content_height = s_display_height - DISPLAY_TRANSFER_LINES;
    const uint16_t slot_height = content_height / slot_count;
    const uint16_t band_height = slot_height < DISPLAY_TEXT_BAND_LINES
                                     ? slot_height : DISPLAY_TEXT_BAND_LINES;
    const uint16_t y = DISPLAY_TRANSFER_LINES + slot * slot_height +
                       (slot_height - band_height) / 2;
    uint16_t *text_band = s_text_band_buffers[slot % 2];
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    memset(text_band, 0,
           (size_t)s_display_width * band_height * sizeof(*text_band));
    fill_round_background(text_band, y, band_height);
    foreground = display_readable_foreground(foreground, background);
#else
    for (size_t index = 0; index < s_display_width * band_height; ++index) {
        text_band[index] = background;
    }
#endif

    const uint16_t horizontal_margin = 8;
    const uint16_t available_width =
        s_display_width > horizontal_margin ? s_display_width - horizontal_margin
                                            : s_display_width;
    const uint8_t scale =
        board_display_fit_scale(text, available_width, preferred_scale);
    const uint16_t text_width = board_display_text_width(text, scale);
    int x = text_width < s_display_width ? (s_display_width - text_width) / 2 : 0;
    const int text_height = 7 * scale;
    const int text_y = text_height < band_height ? (band_height - text_height) / 2 : 0;

    while (*text != '\0' && x + 5 * scale <= s_display_width) {
        const uint8_t *glyph = glyph_for(*text++);
        if (glyph != NULL) {
            for (int column = 0; column < 5; ++column) {
                for (int row = 0; row < 7; ++row) {
                    if ((glyph[column] & (1u << row)) != 0) {
                        for (int dx = 0; dx < scale; ++dx) {
                            for (int dy = 0; dy < scale; ++dy) {
                                const int pixel_x = x + column * scale + dx;
                                const int pixel_y = text_y + row * scale + dy;
                                text_band[pixel_y * s_display_width + pixel_x] = foreground;
                            }
                        }
                    }
                }
            }
        }
        x += 6 * scale;
    }
    return esp_lcd_panel_draw_bitmap(s_panel, 0, y, s_display_width,
                                     y + band_height, text_band);
}

static esp_err_t draw_text_slot(size_t slot, size_t slot_count, const char *text,
                                uint16_t color, uint8_t preferred_scale)
{
    return draw_text_slot_style(slot, slot_count, text, color, 0x0000,
                                preferred_scale);
}

esp_err_t board_display_show_status(const char *profile, const char *output,
                                    const char *mode, const char *hint,
                                    bool connected, bool codex_mode)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    if (profile == NULL || output == NULL || mode == NULL) return ESP_ERR_INVALID_ARG;
    const board_mist_view_t view = {
        .page = BOARD_MIST_CONNECTION,
        .a = strcmp(output, "USB") == 0 ? 1 : strcmp(output, "BLE") == 0 ? 2 : 0,
        .b = connected ? 2 : 0, .title = profile, .primary = output,
        .secondary = mode, .footer = hint, .phase = 1,
    };
    return board_display_show_mist(&view);
#endif
    /* Scaled line bands keep text readable without a full-frame framebuffer. */
    if (profile == NULL || output == NULL || mode == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!s_status_page_active) {
        ESP_RETURN_ON_ERROR(display_prepare_page_background(), "board",
                            "prepare status page background");
        s_status_page_active = true;
    }
    ESP_RETURN_ON_ERROR(display_fill_status_bar(connected ? 0x07E0 : 0xFD20),
                        "board", "draw output status bar");
    ESP_RETURN_ON_ERROR(draw_text_slot(0, 5, profile, 0x07FF, 2),
                        "board", "draw profile name");
    ESP_RETURN_ON_ERROR(draw_text_slot(1, 5, output,
                                       connected ? 0x07E0 : 0xFD20, 2),
                        "board", "draw active output");
    ESP_RETURN_ON_ERROR(draw_text_slot(2, 5, mode,
                                       codex_mode ? 0x001F : 0x07FF, 2),
                        "board", "draw operating mode");
    ESP_RETURN_ON_ERROR(draw_text_slot(3, 5, hint != NULL ? hint : "READY",
                                       0xFFE0, 2),
                        "board", "draw status hint");
    return draw_text_slot(4, 5, "HOLD KEY3 FUNCTION", 0xFFFF, 2);
}

esp_err_t board_display_show_quick_config(const char *previous_item,
                                          const char *current_item,
                                          const char *next_item,
                                          const char *candidate_value,
                                          const char *saved_value,
                                          bool candidate_saved,
                                          const char *footer)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    if (current_item == NULL || candidate_value == NULL || footer == NULL)
        return ESP_ERR_INVALID_ARG;
    const board_mist_view_t view = {
        .page = BOARD_MIST_QUICK, .title = current_item, .primary = candidate_value,
        .previous = previous_item, .next = next_item, .saved = saved_value,
        .footer = footer, .a = candidate_saved, .phase = 1,
    };
    return board_display_show_mist(&view);
#endif
    if (previous_item == NULL || current_item == NULL || next_item == NULL ||
        candidate_value == NULL || footer == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!s_status_page_active) {
        ESP_RETURN_ON_ERROR(display_prepare_page_background(), "board",
                            "prepare quick config page background");
        s_status_page_active = true;
    }
    ESP_RETURN_ON_ERROR(display_fill_status_bar(0x001F), "board", "draw quick config bar");
    ESP_RETURN_ON_ERROR(draw_text_slot(0, 7, "QUICK CONFIG", 0xFFFF, 2),
                        "board", "draw quick config title");
    ESP_RETURN_ON_ERROR(
        draw_text_slot_style(1, 7, previous_item,
                             board_display_menu_foreground(BOARD_DISPLAY_MENU_UNSELECTED),
                             board_display_menu_background(BOARD_DISPLAY_MENU_UNSELECTED), 1),
        "board", "draw previous quick config item");
    ESP_RETURN_ON_ERROR(
        draw_text_slot_style(2, 7, current_item,
                             board_display_menu_foreground(BOARD_DISPLAY_MENU_CURSOR),
                             board_display_menu_background(BOARD_DISPLAY_MENU_CURSOR), 2),
        "board", "draw selected quick config item");
    ESP_RETURN_ON_ERROR(
        draw_text_slot_style(3, 7, next_item,
                             board_display_menu_foreground(BOARD_DISPLAY_MENU_UNSELECTED),
                             board_display_menu_background(BOARD_DISPLAY_MENU_UNSELECTED), 1),
        "board", "draw next quick config item");
    const board_display_menu_state_t value_state =
        candidate_saved ? BOARD_DISPLAY_MENU_SAVED : BOARD_DISPLAY_MENU_EDITING;
    ESP_RETURN_ON_ERROR(
        draw_text_slot_style(4, 7, candidate_value,
                             board_display_menu_foreground(value_state),
                             board_display_menu_background(value_state), 2),
        "board", "draw candidate quick config value");
    ESP_RETURN_ON_ERROR(
        draw_text_slot_style(5, 7, saved_value != NULL ? saved_value : "",
                             board_display_menu_foreground(BOARD_DISPLAY_MENU_SAVED),
                             board_display_menu_background(BOARD_DISPLAY_MENU_SAVED), 1),
        "board", "draw saved quick config value");
    return draw_text_slot(6, 7, footer, 0xFFFF, 1);
}

esp_err_t board_display_show_system_select(size_t selected_index,
                                           const char *footer)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    if (selected_index > 1 || footer == NULL) return ESP_ERR_INVALID_ARG;
    const board_mist_view_t view = {
        .page = BOARD_MIST_SYSTEM_TEXT, .a = (int)selected_index,
        .footer = footer, .phase = 1,
    };
    return board_display_show_mist(&view);
#endif
    if (selected_index > 1 || footer == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!s_status_page_active) {
        ESP_RETURN_ON_ERROR(display_prepare_page_background(), "board",
                            "prepare system select page background");
        s_status_page_active = true;
    }
    ESP_RETURN_ON_ERROR(display_fill_status_bar(0x07FF), "board", "draw system select bar");
    ESP_RETURN_ON_ERROR(draw_text_slot(0, 4, "SELECT SYSTEM", 0xFFFF, 2),
                        "board", "draw system select title");
    const board_display_menu_state_t macos_state =
        selected_index == 0 ? BOARD_DISPLAY_MENU_CURSOR : BOARD_DISPLAY_MENU_UNSELECTED;
    const board_display_menu_state_t windows_state =
        selected_index == 1 ? BOARD_DISPLAY_MENU_CURSOR : BOARD_DISPLAY_MENU_UNSELECTED;
    ESP_RETURN_ON_ERROR(
        draw_text_slot_style(1, 4, "MACOS",
                             board_display_menu_foreground(macos_state),
                             board_display_menu_background(macos_state), 3),
        "board", "draw macOS option");
    ESP_RETURN_ON_ERROR(
        draw_text_slot_style(2, 4, "WINDOWS LINUX",
                             board_display_menu_foreground(windows_state),
                             board_display_menu_background(windows_state), 2),
        "board", "draw Windows option");
    ESP_RETURN_ON_ERROR(draw_text_slot(3, 4, footer, 0x07E0, 1),
                        "board", "draw system select footer");
    return ESP_OK;
}

esp_err_t board_display_show_two_choice(const char *title,
                                        const char *first_option,
                                        const char *second_option,
                                        size_t selected_index,
                                        const char *footer,
                                        uint16_t accent_rgb565)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    if (title == NULL || first_option == NULL || second_option == NULL || selected_index > 1)
        return ESP_ERR_INVALID_ARG;
    const board_mist_view_t view = {
        .page = BOARD_MIST_TWO_CHOICE, .a = (int)selected_index,
        .title = title, .primary = first_option, .secondary = second_option,
        .footer = footer, .phase = 1,
    };
    return board_display_show_mist(&view);
#endif
    if (title == NULL || first_option == NULL || second_option == NULL ||
        selected_index > 1 || footer == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!s_status_page_active) {
        ESP_RETURN_ON_ERROR(display_prepare_page_background(), "board",
                            "prepare two-choice page background");
        s_status_page_active = true;
    }
    ESP_RETURN_ON_ERROR(display_fill_status_bar(accent_rgb565), "board",
                        "draw two-choice page bar");
    ESP_RETURN_ON_ERROR(draw_text_slot(0, 4, title, 0xFFFF, 2), "board",
                        "draw two-choice title");

    const board_display_menu_state_t first_state =
        selected_index == 0 ? BOARD_DISPLAY_MENU_CURSOR
                            : BOARD_DISPLAY_MENU_UNSELECTED;
    const board_display_menu_state_t second_state =
        selected_index == 1 ? BOARD_DISPLAY_MENU_CURSOR
                            : BOARD_DISPLAY_MENU_UNSELECTED;
    char first_label[32];
    char second_label[32];
    snprintf(first_label, sizeof(first_label), "%s%s",
             selected_index == 0 ? "> " : "", first_option);
    snprintf(second_label, sizeof(second_label), "%s%s",
             selected_index == 1 ? "> " : "", second_option);
    ESP_RETURN_ON_ERROR(
        draw_text_slot_style(
            1, 4, first_label, board_display_menu_foreground(first_state),
            board_display_menu_background(first_state),
            2),
        "board", "draw first two-choice option");
    ESP_RETURN_ON_ERROR(
        draw_text_slot_style(
            2, 4, second_label, board_display_menu_foreground(second_state),
            board_display_menu_background(second_state),
            2),
        "board", "draw second two-choice option");
    return draw_text_slot(3, 4, footer, 0xFFFF, 1);
}

esp_err_t board_display_show_local(const char *title, const char *primary,
                                   const char *secondary, const char *footer,
                                   uint16_t accent_rgb565)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    if (title == NULL || primary == NULL || secondary == NULL || footer == NULL)
        return ESP_ERR_INVALID_ARG;
    const board_mist_view_t view = {
        .page = BOARD_MIST_LOCAL, .title = title, .primary = primary,
        .secondary = secondary, .footer = footer, .phase = 1,
    };
    return board_display_show_mist(&view);
#endif
    if (title == NULL || primary == NULL || secondary == NULL ||
        footer == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!s_status_page_active) {
        ESP_RETURN_ON_ERROR(display_prepare_page_background(), "board",
                            "prepare local page background");
        s_status_page_active = true;
    }
    ESP_RETURN_ON_ERROR(display_fill_status_bar(accent_rgb565), "board",
                        "draw local page bar");
    ESP_RETURN_ON_ERROR(draw_text_slot(0, 4, title, 0xFFFF, 2), "board",
                        "draw local title");
    ESP_RETURN_ON_ERROR(draw_text_slot(1, 4, primary, accent_rgb565, 3),
                        "board", "draw local primary");
    ESP_RETURN_ON_ERROR(draw_text_slot(2, 4, secondary, 0xFFFF, 2),
                        "board", "draw local secondary");
    return draw_text_slot(3, 4, footer, 0xFFFF, 1);
}

#endif
