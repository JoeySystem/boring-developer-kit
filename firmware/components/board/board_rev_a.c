#include "board.h"

/*
 * Product-board implementation for WMP-S3-REV-A / PCB2_2026-07-18.
 * Pin ownership comes exclusively from board_rev_a.h and pcb2-pin-map.md.
 *
 * This target is build-verified but not yet validated on a manufactured PCB.
 * Direction, polarity, panel geometry, RGB chain order, motor current, and the
 * combined power budget must be recorded through pcb2-bring-up-checklist.md.
 */

#include <ctype.h>
#include <string.h>

#include "board_display_logic.h"
#include "board_input_logic.h"
#include "board_rev_a.h"
#include "driver/gpio.h"
#include "driver/ledc.h"
#include "driver/spi_master.h"
#include "esp_adc/adc_oneshot.h"
#include "esp_check.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_panel_vendor.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "led_strip.h"
#include "sdkconfig.h"

#if CONFIG_MACROPAD_BOARD_TARGET_REV_A

#define DEBOUNCE_MS 20
#define JOYSTICK_SAMPLE_MS 10
#define JOYSTICK_DEADZONE 650
#define JOYSTICK_RELEASE_DEADZONE 450
#define JOYSTICK_X_HARDWARE_INVERTED true
#define JOYSTICK_Y_HARDWARE_INVERTED true
#define EVENT_QUEUE_CAPACITY 32
#define ENCODER_EDGE_QUEUE_CAPACITY 64
#define ENCODER_TRANSITIONS_PER_DETENT 4
#define MOTOR_MAX_STRENGTH_PERCENT 60
#define MOTOR_MAX_DURATION_MS 200
#define DISPLAY_TRANSFER_LINES 10
#define DISPLAY_TEXT_BAND_LINES 32
/* This ST7789 module exposes one bottom row outside the 135x240 logical area. */
#define DISPLAY_BOTTOM_OVERSCAN_LINES 1
#define DISPLAY_MAX_WIDTH BOARD_REV_A_DISPLAY_HEIGHT

typedef struct {
    gpio_num_t gpio;
    board_control_t control;
    bool stable;
    bool sample;
    TickType_t changed_at;
} digital_input_t;

static digital_input_t s_inputs[] = {
    /*
     * PCB key nets already follow the user-facing physical layout:
     *
     *                 Key 1             Key 2
     *      Key 3 wide        Key 4      Key 5
     *                        Key 6      Key 7
     *
     * Keep this identity mapping stable. Product behavior belongs in the
     * active Profile; remapping GPIOs here makes old and factory-default NVS
     * configurations disagree about which physical key a control ID names.
     */
    {.gpio = BOARD_REV_A_KEY1_GPIO, .control = BOARD_CONTROL_KEY_1},
    {.gpio = BOARD_REV_A_KEY2_GPIO, .control = BOARD_CONTROL_KEY_2},
    {.gpio = BOARD_REV_A_KEY3_GPIO, .control = BOARD_CONTROL_KEY_3},
    {.gpio = BOARD_REV_A_KEY4_GPIO, .control = BOARD_CONTROL_KEY_4},
    {.gpio = BOARD_REV_A_KEY5_GPIO, .control = BOARD_CONTROL_KEY_5},
    {.gpio = BOARD_REV_A_KEY6_GPIO, .control = BOARD_CONTROL_KEY_6},
    {.gpio = BOARD_REV_A_KEY7_GPIO, .control = BOARD_CONTROL_KEY_7},
    {.gpio = BOARD_REV_A_ENCODER_KEY_GPIO, .control = BOARD_CONTROL_ENCODER_PRESS},
    {.gpio = BOARD_REV_A_JOYSTICK_KEY_GPIO, .control = BOARD_CONTROL_JOYSTICK_PRESS},
};

static board_event_t s_events[EVENT_QUEUE_CAPACITY];
static size_t s_event_read;
static size_t s_event_write;
static adc_oneshot_unit_handle_t s_adc;
static led_strip_handle_t s_status_strip;
static led_strip_handle_t s_under_key_strip;
static esp_lcd_panel_handle_t s_panel;
static uint16_t s_display_buffer[DISPLAY_MAX_WIDTH * DISPLAY_TRANSFER_LINES];
/* Separate storage prevents a status update from overwriting an in-flight full fill. */
static uint16_t s_status_bar_buffer[DISPLAY_MAX_WIDTH * DISPLAY_TRANSFER_LINES];
/*
 * SPI LCD transfers retain their source pointer until DMA completes. With one
 * queued transaction, alternating two buffers guarantees that a band is no
 * longer in flight before it is rendered into again.
 */
static uint16_t s_text_band_buffers[2][DISPLAY_MAX_WIDTH * DISPLAY_TEXT_BAND_LINES];
static uint16_t s_display_width = BOARD_REV_A_DISPLAY_WIDTH;
static uint16_t s_display_height = BOARD_REV_A_DISPLAY_HEIGHT;
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
static bool s_joystick_left;
static bool s_joystick_right;
static bool s_joystick_up;
static bool s_joystick_down;
static bool s_joystick_radial_active;
static bool s_joystick_calibration_active;
static bool s_status_page_active;

static esp_err_t display_fill_bottom_overscan(uint16_t rgb565)
{
    if (s_panel == NULL || s_display_width > DISPLAY_MAX_WIDTH) {
        return ESP_ERR_INVALID_STATE;
    }
    for (size_t index = 0; index < s_display_width * DISPLAY_BOTTOM_OVERSCAN_LINES; ++index) {
        s_display_buffer[index] = rgb565;
    }
    return esp_lcd_panel_draw_bitmap(s_panel, 0, s_display_height,
                                     s_display_width,
                                     s_display_height + DISPLAY_BOTTOM_OVERSCAN_LINES,
                                     s_display_buffer);
}

static bool input_pressed(gpio_num_t gpio)
{
    return gpio_get_level(gpio) == 0;
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

static void encoder_gpio_isr(void *context)
{
    (void)context;
    const uint8_t state =
        (gpio_get_level(BOARD_REV_A_ENCODER_A_GPIO) << 1) |
        gpio_get_level(BOARD_REV_A_ENCODER_B_GPIO);
    (void)xQueueSendFromISR(s_encoder_edges, &state, NULL);
}

static esp_err_t init_inputs(void)
{
    const uint64_t mask =
        (1ULL << BOARD_REV_A_KEY1_GPIO) |
        (1ULL << BOARD_REV_A_KEY2_GPIO) |
        (1ULL << BOARD_REV_A_KEY3_GPIO) |
        (1ULL << BOARD_REV_A_KEY4_GPIO) |
        (1ULL << BOARD_REV_A_KEY5_GPIO) |
        (1ULL << BOARD_REV_A_KEY6_GPIO) |
        (1ULL << BOARD_REV_A_KEY7_GPIO) |
        (1ULL << BOARD_REV_A_ENCODER_A_GPIO) |
        (1ULL << BOARD_REV_A_ENCODER_B_GPIO) |
        (1ULL << BOARD_REV_A_ENCODER_KEY_GPIO) |
        (1ULL << BOARD_REV_A_JOYSTICK_KEY_GPIO);
    const gpio_config_t config = {
        .pin_bit_mask = mask,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    ESP_RETURN_ON_ERROR(gpio_config(&config), "board", "configure inputs");

    const TickType_t now = xTaskGetTickCount();
    for (size_t index = 0; index < sizeof(s_inputs) / sizeof(s_inputs[0]); ++index) {
        s_inputs[index].stable = input_pressed(s_inputs[index].gpio);
        s_inputs[index].sample = s_inputs[index].stable;
        s_inputs[index].changed_at = now;
    }
    s_encoder.state = (gpio_get_level(BOARD_REV_A_ENCODER_A_GPIO) << 1) |
                      gpio_get_level(BOARD_REV_A_ENCODER_B_GPIO);
    s_encoder.accumulator = 0;
    s_encoder.detent_state = s_encoder.state;
    s_encoder_edges =
        xQueueCreate(ENCODER_EDGE_QUEUE_CAPACITY, sizeof(uint8_t));
    if (s_encoder_edges == NULL) {
        return ESP_ERR_NO_MEM;
    }
    ESP_RETURN_ON_ERROR(
        gpio_set_intr_type(BOARD_REV_A_ENCODER_A_GPIO, GPIO_INTR_ANYEDGE),
        "board", "enable encoder A edge interrupt");
    ESP_RETURN_ON_ERROR(
        gpio_set_intr_type(BOARD_REV_A_ENCODER_B_GPIO, GPIO_INTR_ANYEDGE),
        "board", "enable encoder B edge interrupt");
    ESP_RETURN_ON_ERROR(gpio_install_isr_service(0), "board",
                        "install GPIO interrupt service");
    ESP_RETURN_ON_ERROR(
        gpio_isr_handler_add(BOARD_REV_A_ENCODER_A_GPIO, encoder_gpio_isr, NULL),
        "board", "register encoder A interrupt");
    ESP_RETURN_ON_ERROR(
        gpio_isr_handler_add(BOARD_REV_A_ENCODER_B_GPIO, encoder_gpio_isr, NULL),
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
    ESP_RETURN_ON_ERROR(adc_oneshot_config_channel(s_adc, BOARD_REV_A_JOYSTICK_X_ADC_CHANNEL,
                                                   &channel_config),
                        "board", "configure joystick X");
    ESP_RETURN_ON_ERROR(adc_oneshot_config_channel(s_adc, BOARD_REV_A_JOYSTICK_Y_ADC_CHANNEL,
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
        if (adc_oneshot_read(s_adc, BOARD_REV_A_JOYSTICK_X_ADC_CHANNEL, &x) == ESP_OK &&
            adc_oneshot_read(s_adc, BOARD_REV_A_JOYSTICK_Y_ADC_CHANNEL, &y) == ESP_OK) {
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
        .gpio_num = BOARD_REV_A_MOTOR_GPIO,
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
        .gpio_num = BOARD_REV_A_DISPLAY_BACKLIGHT_GPIO,
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
    /* PCB2 uses independent GRB chains with different one-wire timings. */
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
    ESP_RETURN_ON_ERROR(new_strip(BOARD_REV_A_STATUS_RGB_GPIO,
                                  BOARD_REV_A_STATUS_RGB_COUNT, LED_MODEL_WS2812,
                                  &s_status_strip),
                        "board", "create status RGB strip");
    ESP_RETURN_ON_ERROR(new_strip(BOARD_REV_A_UNDER_KEY_RGB_GPIO,
                                  BOARD_REV_A_UNDER_KEY_RGB_COUNT, LED_MODEL_SK6812,
                                  &s_under_key_strip),
                        "board", "create under-key RGB strip");
    ESP_RETURN_ON_ERROR(led_strip_clear(s_status_strip), "board", "clear status RGB strip");
    return led_strip_clear(s_under_key_strip);
}

static esp_err_t init_display(void)
{
    ESP_RETURN_ON_ERROR(init_display_backlight_pwm(), "board", "initialize display backlight");

    const spi_bus_config_t bus_config = {
        .sclk_io_num = BOARD_REV_A_DISPLAY_SCLK_GPIO,
        .mosi_io_num = BOARD_REV_A_DISPLAY_MOSI_GPIO,
        .miso_io_num = GPIO_NUM_NC,
        .quadwp_io_num = GPIO_NUM_NC,
        .quadhd_io_num = GPIO_NUM_NC,
        .max_transfer_sz = sizeof(s_display_buffer),
    };
    ESP_RETURN_ON_ERROR(spi_bus_initialize(SPI2_HOST, &bus_config, SPI_DMA_CH_AUTO),
                        "board", "initialize display SPI bus");

    const esp_lcd_panel_io_spi_config_t io_config = {
        .dc_gpio_num = BOARD_REV_A_DISPLAY_DC_GPIO,
        .cs_gpio_num = BOARD_REV_A_DISPLAY_CS_GPIO,
        .pclk_hz = 20 * 1000 * 1000,
        .lcd_cmd_bits = 8,
        .lcd_param_bits = 8,
        .spi_mode = 0,
        .trans_queue_depth = 1,
    };
    esp_lcd_panel_io_handle_t panel_io = NULL;
    ESP_RETURN_ON_ERROR(esp_lcd_new_panel_io_spi(SPI2_HOST, &io_config, &panel_io),
                        "board", "create display panel IO");

    const esp_lcd_panel_dev_config_t panel_config = {
        .reset_gpio_num = BOARD_REV_A_DISPLAY_RESET_GPIO,
        .rgb_ele_order = LCD_RGB_ELEMENT_ORDER_RGB,
        .bits_per_pixel = 16,
    };
    ESP_RETURN_ON_ERROR(esp_lcd_new_panel_st7789(panel_io, &panel_config, &s_panel),
                        "board", "create ST7789 panel");
    /* Inversion remains a panel-specific assumption; geometry is corrected below. */
    ESP_RETURN_ON_ERROR(esp_lcd_panel_reset(s_panel), "board", "reset ST7789 panel");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_init(s_panel), "board", "initialize ST7789 panel");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_invert_color(s_panel, true), "board", "set panel inversion");
    ESP_RETURN_ON_ERROR(board_set_display_config(0, 0), "board",
                        "set mounted display orientation with backlight off");
    ESP_RETURN_ON_ERROR(board_display_fill(0x0000), "board", "clear display before enabling it");
    return esp_lcd_panel_disp_on_off(s_panel, true);
}

esp_err_t board_init(void)
{
    memset(s_events, 0, sizeof(s_events));
    s_event_read = 0;
    s_event_write = 0;
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
    if (adc_oneshot_read(s_adc, BOARD_REV_A_JOYSTICK_X_ADC_CHANNEL, &x) != ESP_OK ||
        adc_oneshot_read(s_adc, BOARD_REV_A_JOYSTICK_Y_ADC_CHANNEL, &y) != ESP_OK) {
        return;
    }
    s_joystick_raw_x = x;
    s_joystick_raw_y = y;
    /* Higher filter values favor new samples; one percent still tracks slowly. */
    const int weight = s_joystick_filter > 0 ? s_joystick_filter : 1;
    s_joystick_filtered_x += ((x - s_joystick_filtered_x) * weight) / 100;
    s_joystick_filtered_y += ((y - s_joystick_filtered_y) * weight) / 100;
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
    update_direction((directions & BOARD_JOYSTICK_LEFT) != 0,
                     &s_joystick_left, BOARD_CONTROL_JOYSTICK_LEFT);
    update_direction((directions & BOARD_JOYSTICK_RIGHT) != 0,
                     &s_joystick_right, BOARD_CONTROL_JOYSTICK_RIGHT);
    update_direction((directions & BOARD_JOYSTICK_UP) != 0,
                     &s_joystick_up, BOARD_CONTROL_JOYSTICK_UP);
    update_direction((directions & BOARD_JOYSTICK_DOWN) != 0,
                     &s_joystick_down, BOARD_CONTROL_JOYSTICK_DOWN);
}

void board_poll(void)
{
    const TickType_t now = xTaskGetTickCount();
    poll_digital_inputs(now);
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
    return "WMP-S3-REV-A_PCB2_2026-07-18";
}

const char *board_hardware_id(void)
{
    return "WMP-S3-REV-A";
}

size_t board_key_count(void)
{
    return BOARD_REV_A_KEY_COUNT;
}

size_t board_status_rgb_count(void)
{
    return BOARD_REV_A_STATUS_RGB_COUNT;
}

size_t board_under_key_rgb_count(void)
{
    return BOARD_REV_A_UNDER_KEY_RGB_COUNT;
}

bool board_is_product_target(void)
{
    return true;
}

bool board_inputs_neutral(void)
{
    for (size_t index = 0; index < sizeof(s_inputs) / sizeof(s_inputs[0]); ++index) {
        if (s_inputs[index].stable) {
            return false;
        }
    }
    return !s_joystick_left && !s_joystick_right && !s_joystick_up && !s_joystick_down;
}

size_t board_get_active_controls(board_control_t *controls, size_t capacity)
{
    if (controls == NULL || capacity == 0) {
        return 0;
    }
    size_t count = 0;
    for (size_t index = 0; index < sizeof(s_inputs) / sizeof(s_inputs[0]) && count < capacity; ++index) {
        if (s_inputs[index].stable) {
            controls[count++] = s_inputs[index].control;
        }
    }
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
    if (s_joystick_calibration_active) {
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
    const bool radial_valid = board_joystick_radial_angle(
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

esp_err_t board_set_status_rgb(size_t index, uint8_t red, uint8_t green, uint8_t blue)
{
    if (index >= BOARD_REV_A_STATUS_RGB_COUNT) {
        return ESP_ERR_INVALID_ARG;
    }
    ESP_RETURN_ON_ERROR(led_strip_set_pixel(s_status_strip, index, red, green, blue),
                        "board", "set status RGB pixel");
    return led_strip_refresh(s_status_strip);
}

esp_err_t board_set_under_key_rgb(size_t index, uint8_t red, uint8_t green, uint8_t blue)
{
    if (index >= BOARD_REV_A_UNDER_KEY_RGB_COUNT) {
        return ESP_ERR_INVALID_ARG;
    }
    ESP_RETURN_ON_ERROR(led_strip_set_pixel(s_under_key_strip, index, red, green, blue),
                        "board", "set under-key RGB pixel");
    return led_strip_refresh(s_under_key_strip);
}

esp_err_t board_apply_rgb(
    const board_rgb_t status[BOARD_STATUS_RGB_COUNT],
    const board_rgb_t under_key[BOARD_MAX_UNDER_KEY_RGB_COUNT])
{
    if (status == NULL || under_key == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    for (size_t index = 0; index < BOARD_REV_A_STATUS_RGB_COUNT; ++index) {
        ESP_RETURN_ON_ERROR(led_strip_set_pixel(s_status_strip, index, status[index].red,
                                                status[index].green, status[index].blue),
                            "board", "buffer status RGB pixel");
    }
    for (size_t index = 0; index < BOARD_REV_A_UNDER_KEY_RGB_COUNT; ++index) {
        ESP_RETURN_ON_ERROR(led_strip_set_pixel(s_under_key_strip, index, under_key[index].red,
                                                under_key[index].green, under_key[index].blue),
                            "board", "buffer under-key RGB pixel");
    }
    ESP_RETURN_ON_ERROR(led_strip_refresh(s_status_strip), "board", "refresh status RGB chain");
    return led_strip_refresh(s_under_key_strip);
}

esp_err_t board_apply_status_rgb(
    const board_rgb_t status[BOARD_STATUS_RGB_COUNT])
{
    if (status == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    for (size_t index = 0; index < BOARD_REV_A_STATUS_RGB_COUNT; ++index) {
        ESP_RETURN_ON_ERROR(led_strip_set_pixel(s_status_strip, index, status[index].red,
                                                status[index].green, status[index].blue),
                            "board", "buffer USB status RGB pixel");
    }
    return led_strip_refresh(s_status_strip);
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
    return display_fill_bottom_overscan(rgb565);
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
    static const char lines[2][7] = {"BORING", "DESIGH"};
    enum {
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

    const bool landscape = s_display_width >= s_display_height;
    const uint8_t pitch = landscape ? 4 : 3;
    const uint8_t dot_size = landscape ? 3 : 2;
    const uint16_t logo_width =
        ((CHARACTER_COUNT - 1) * CHARACTER_ADVANCE + 4) * pitch + dot_size;
    const uint16_t logo_height = (LINE_ADVANCE + 6) * pitch + dot_size;
    if (logo_width > s_display_width || logo_height > s_display_height) {
        return ESP_ERR_INVALID_STATE;
    }
    const int origin_x = (s_display_width - logo_width) / 2;
    const int origin_y = (s_display_height - logo_height) / 2;

    s_status_page_active = false;
    size_t strip_index = 0;
    for (int logo_y = 0; logo_y < logo_height;
         logo_y += DISPLAY_TRANSFER_LINES, ++strip_index) {
        const int strip_y = origin_y + logo_y;
        const int strip_lines =
            logo_y + DISPLAY_TRANSFER_LINES <= logo_height
                ? DISPLAY_TRANSFER_LINES : logo_height - logo_y;
        uint16_t *strip = s_text_band_buffers[strip_index % 2];
        for (size_t index = 0;
             index < (size_t)logo_width * strip_lines; ++index) {
            strip[index] = 0x0000;
        }

        for (size_t line = 0; line < 2; ++line) {
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
                                if (boot_dot_pixel_visible(
                                        dx, dy, dot_size)) {
                                    strip[(pixel_y - strip_y) *
                                              logo_width +
                                          (pixel_x - origin_x)] = 0xFFFF;
                                }
                            }
                        }
                    }
                }
            }
        }
        ESP_RETURN_ON_ERROR(
            esp_lcd_panel_draw_bitmap(s_panel, origin_x, strip_y,
                                      origin_x + logo_width,
                                      strip_y + strip_lines, strip),
            "board", "draw module-dot boot logo");
    }
    return display_fill_bottom_overscan(0x0000);
}

static esp_err_t display_fill_status_bar(uint16_t rgb565)
{
    if (s_panel == NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    for (size_t index = 0; index < sizeof(s_status_bar_buffer) / sizeof(s_status_bar_buffer[0]); ++index) {
        s_status_bar_buffer[index] = rgb565;
    }
    return esp_lcd_panel_draw_bitmap(s_panel, 0, 0, s_display_width,
                                     DISPLAY_TRANSFER_LINES, s_status_bar_buffer);
}

esp_err_t board_set_display_config(uint8_t brightness_percent, uint16_t rotation)
{
    if (brightness_percent > 100 ||
        !(rotation == 0 || rotation == 90 || rotation == 180 || rotation == 270)) {
        return ESP_ERR_INVALID_ARG;
    }
    const board_display_geometry_t geometry = board_display_geometry(rotation);
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
    const uint32_t duty = (1023U * brightness_percent) / 100U;
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
    for (size_t index = 0; index < s_display_width * band_height; ++index) {
        text_band[index] = background;
    }

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
    /* Scaled line bands keep text readable without a full-frame framebuffer. */
    if (profile == NULL || output == NULL || mode == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!s_status_page_active) {
        ESP_RETURN_ON_ERROR(board_display_fill(0x0000), "board", "clear status page");
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
    return draw_text_slot(4, 5, "HOLD KEY7 FUNCTION", 0xFFFF, 2);
}

esp_err_t board_display_show_quick_config(const char *previous_item,
                                          const char *current_item,
                                          const char *next_item,
                                          const char *candidate_value,
                                          const char *saved_value,
                                          bool candidate_saved,
                                          const char *footer)
{
    if (previous_item == NULL || current_item == NULL || next_item == NULL ||
        candidate_value == NULL || footer == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!s_status_page_active) {
        ESP_RETURN_ON_ERROR(board_display_fill(0x0000), "board", "clear quick config page");
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
    if (selected_index > 1 || footer == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!s_status_page_active) {
        ESP_RETURN_ON_ERROR(board_display_fill(0x0000), "board", "clear system select page");
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
    if (title == NULL || first_option == NULL || second_option == NULL ||
        selected_index > 1 || footer == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!s_status_page_active) {
        ESP_RETURN_ON_ERROR(board_display_fill(0x0000), "board",
                            "clear two-choice page");
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
    if (title == NULL || primary == NULL || secondary == NULL ||
        footer == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!s_status_page_active) {
        ESP_RETURN_ON_ERROR(board_display_fill(0x0000), "board",
                            "clear local page");
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
