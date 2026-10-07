#include <stdio.h>

#include "board.h"
#include "action_engine.h"
#include "codex_micro.h"
#include "config_protocol.h"
#include "config_store.h"
#include "device_identity.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_pm.h"
#include "firmware_update.h"
#include "prompt_store.h"
#include "host_action_events.h"
#include "screen_icon_store.h"
#include "screen_glyph_store.h"
#include "under_key_self_test.h"
#include "cw2015.h"
#include "battery_service.h"
#include "ble_config_service.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "usb_service.h"

static const char *TAG = "wired_macro_pad";

#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
static void configure_power_management(void)
{
    const esp_pm_config_t pm_config = {
        .max_freq_mhz = 160,
        .min_freq_mhz = 80,
        /* DFS only. USB/BLE and input wake behavior are unchanged. */
        .light_sleep_enable = false,
    };
    ESP_ERROR_CHECK(esp_pm_configure(&pm_config));
    ESP_LOGI(TAG, "dynamic CPU frequency enabled: 80-160 MHz");
}
#endif

static bool ble_standard_connected(void *context)
{
    (void)context;
    return codex_micro_ble_connected();
}

static esp_err_t ble_standard_send(uint8_t report_id, const uint8_t *data,
                                   size_t length, void *context)
{
    (void)context;
    return codex_micro_send_standard_report(report_id, data, length);
}

static bool codex_usb_connected(void *context)
{
    (void)context;
    return usb_service_is_mounted();
}

static esp_err_t codex_usb_send(const uint8_t *data, size_t length,
                                void *context)
{
    (void)context;
    return usb_service_send_vendor_report(data, length);
}

static void codex_usb_output(uint8_t report_id, const uint8_t *data,
                             size_t length, void *context)
{
    (void)context;
    codex_micro_receive_usb_output(report_id, data, length);
}

static void make_serial(char serial[18])
{
    /* Stable per-module identity; port names are not stable across reconnects. */
    uint8_t mac[6];
    ESP_ERROR_CHECK(esp_read_mac(mac, ESP_MAC_WIFI_STA));
    snprintf(serial, 18, "CP01-%02X%02X%02X%02X%02X%02X",
             mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
}

#if CONFIG_MACROPAD_ASSEMBLY_ALIGNMENT
#define ASSEMBLY_LED_SELF_TEST_STEP_MS 250
#define ASSEMBLY_INPUT_IDLE_MS 5000
#define ASSEMBLY_RESULT_VISIBLE_MS 3000
#define ASSEMBLY_MOTOR_STRENGTH_PERCENT 60 /* normal firmware haptic level 3 */
#define ASSEMBLY_MOTOR_DURATION_MS 100

static bool s_assembly_led_self_test_active;
static bool s_assembly_led_self_test_step_rendered;
static size_t s_assembly_led_self_test_step;
static TickType_t s_assembly_led_self_test_step_started_at;

static void assembly_led_self_test_start(void)
{
    s_assembly_led_self_test_step_started_at = xTaskGetTickCount();
    s_assembly_led_self_test_step = 0;
    s_assembly_led_self_test_step_rendered = false;
    s_assembly_led_self_test_active = true;
    ESP_LOGI(TAG, "assembly LED inspection started: %u keys, %u ms each",
             (unsigned)board_under_key_rgb_count(),
             (unsigned)ASSEMBLY_LED_SELF_TEST_STEP_MS);
}

static void assembly_poll_led_self_test(void)
{
    if (!s_assembly_led_self_test_active) {
        return;
    }

    const TickType_t now = xTaskGetTickCount();
    if (s_assembly_led_self_test_step_rendered) {
        const uint32_t step_elapsed_ms =
            (uint32_t)(now - s_assembly_led_self_test_step_started_at) *
            portTICK_PERIOD_MS;
        if (step_elapsed_ms < ASSEMBLY_LED_SELF_TEST_STEP_MS) {
            return;
        }
        ++s_assembly_led_self_test_step;
        s_assembly_led_self_test_step_started_at = now;
        s_assembly_led_self_test_step_rendered = false;
    }

    under_key_self_test_rgb_t rendered[UNDER_KEY_SELF_TEST_LED_COUNT];
    const size_t led_count = board_under_key_rgb_count();
    const bool active = under_key_self_test_render_timed(
        s_assembly_led_self_test_step * ASSEMBLY_LED_SELF_TEST_STEP_MS,
        led_count, ASSEMBLY_LED_SELF_TEST_STEP_MS, rendered);

    const board_rgb_t status[BOARD_STATUS_RGB_COUNT] = {0};
    board_rgb_t under_key[BOARD_MAX_UNDER_KEY_RGB_COUNT] = {0};
    for (size_t index = 0; index < led_count; ++index) {
        under_key[index] = (board_rgb_t) {
            .red = rendered[index].red,
            .green = rendered[index].green,
            .blue = rendered[index].blue,
        };
    }
    ESP_ERROR_CHECK(board_apply_rgb(status, under_key));
    s_assembly_led_self_test_active = active;
    s_assembly_led_self_test_step_rendered = true;
    if (!active) {
        ESP_LOGI(TAG, "assembly LED inspection completed; all key LEDs off");
    }
}

static bool assembly_joystick_active(void)
{
    board_control_t controls[BOARD_CONTROL_COUNT];
    const size_t count = board_get_active_controls(
        controls, sizeof(controls) / sizeof(controls[0]));
    for (size_t index = 0; index < count; ++index) {
        if (controls[index] >= BOARD_CONTROL_JOYSTICK_UP &&
            controls[index] <= BOARD_CONTROL_JOYSTICK_PRESS) {
            return true;
        }
    }
    return false;
}

static bool s_assembly_input_test_active;
static bool s_assembly_home_visible;
static TickType_t s_assembly_input_last_at;
static uint16_t s_assembly_keys_seen;
static uint16_t s_assembly_keys_down;
static uint8_t s_assembly_encoder_seen;
static uint8_t s_assembly_joystick_seen;
static uint8_t s_assembly_joystick_directions_down;
static bool s_assembly_encoder_down;
static bool s_assembly_joystick_down;
static int8_t s_assembly_motor_result; /* 0=unconfirmed, 1=felt, -1=not felt */
static char s_assembly_action[20];
static char s_assembly_progress[20];

static void assembly_reset_input_inspection(void)
{
    s_assembly_keys_seen = 0;
    s_assembly_keys_down = 0;
    s_assembly_encoder_seen = 0;
    s_assembly_joystick_seen = 0;
    s_assembly_joystick_directions_down = 0;
    s_assembly_encoder_down = false;
    s_assembly_joystick_down = false;
    s_assembly_motor_result = 0;
    s_assembly_action[0] = '\0';
    s_assembly_progress[0] = '\0';
}

static unsigned assembly_count_bits(unsigned value)
{
    unsigned count = 0;
    while (value != 0) {
        count += value & 1U;
        value >>= 1;
    }
    return count;
}

static void assembly_haptic_feedback(void)
{
    const esp_err_t result = board_haptic_pulse(
        ASSEMBLY_MOTOR_STRENGTH_PERCENT, ASSEMBLY_MOTOR_DURATION_MS);
    if (result != ESP_OK) {
        ESP_LOGW(TAG, "assembly motor command failed: %s",
                 esp_err_to_name(result));
    }
}

static void assembly_input_event(const board_event_t *event)
{
    if (event->feedback_only) {
        return;
    }

    const bool inputs_complete = s_assembly_keys_seen == 0x0fffU &&
        s_assembly_encoder_seen == 7U && s_assembly_joystick_seen == 31U;
    if (inputs_complete && event->pressed &&
        (event->control == BOARD_CONTROL_JOYSTICK_UP ||
         event->control == BOARD_CONTROL_JOYSTICK_DOWN)) {
        s_assembly_motor_result = event->control == BOARD_CONTROL_JOYSTICK_UP
            ? 1 : -1;
        return;
    }

    size_t key_index;
    if (board_control_key_index(event->control, &key_index)) {
        const uint16_t bit = (uint16_t)(1U << key_index);
        if (event->pressed) {
            if (inputs_complete) {
                s_assembly_motor_result = 0; /* repeat physical motor check */
            }
            s_assembly_keys_down |= bit;
            if (!s_assembly_led_self_test_active) {
                ESP_ERROR_CHECK(board_set_under_key_rgb(key_index, 0, 32, 0));
            }
            /* A visible screen event proves the switch path independently of
             * the LED. The operator must feel the motor to judge it. */
            assembly_haptic_feedback();
        } else {
            if (s_assembly_keys_down & bit) {
                s_assembly_keys_seen |= bit;
            }
            s_assembly_keys_down &= (uint16_t)~bit;
            if (!s_assembly_led_self_test_active) {
                ESP_ERROR_CHECK(board_set_under_key_rgb(key_index, 0, 0, 0));
            }
        }
        snprintf(s_assembly_action, sizeof(s_assembly_action), "%02u %c",
                 (unsigned)key_index + 1U, event->pressed ? '+' : '-');
        snprintf(s_assembly_progress, sizeof(s_assembly_progress), "%02u/12",
                 assembly_count_bits(s_assembly_keys_seen));
        return;
    }

    switch (event->control) {
    case BOARD_CONTROL_ENCODER_CW:
    case BOARD_CONTROL_ENCODER_CCW:
        if (event->pressed) {
            assembly_haptic_feedback();
            s_assembly_encoder_seen |= event->control == BOARD_CONTROL_ENCODER_CW
                ? 1U : 2U;
            snprintf(s_assembly_action, sizeof(s_assembly_action), "R %c",
                     event->control == BOARD_CONTROL_ENCODER_CW ? '+' : '-');
        }
        break;
    case BOARD_CONTROL_ENCODER_PRESS:
        if (event->pressed) {
            assembly_haptic_feedback();
            s_assembly_encoder_down = true;
        } else if (s_assembly_encoder_down) {
            s_assembly_encoder_seen |= 4U;
            s_assembly_encoder_down = false;
        }
        snprintf(s_assembly_action, sizeof(s_assembly_action), "R 0%c",
                 event->pressed ? '+' : '-');
        break;
    case BOARD_CONTROL_JOYSTICK_UP:
    case BOARD_CONTROL_JOYSTICK_DOWN:
    case BOARD_CONTROL_JOYSTICK_LEFT:
    case BOARD_CONTROL_JOYSTICK_RIGHT: {
        const uint8_t bit = (uint8_t)(1U <<
            (event->control - BOARD_CONTROL_JOYSTICK_UP));
        if (event->pressed) {
            assembly_haptic_feedback();
            s_assembly_joystick_directions_down |= bit;
        } else {
            if (s_assembly_joystick_directions_down & bit) {
                s_assembly_joystick_seen |= bit;
            }
            s_assembly_joystick_directions_down &= (uint8_t)~bit;
        }
        const char *direction[] = {"U", "D", "L", "R"};
        snprintf(s_assembly_action, sizeof(s_assembly_action), "J %s%c",
                 direction[event->control - BOARD_CONTROL_JOYSTICK_UP],
                 event->pressed ? '+' : '-');
        break;
    }
    case BOARD_CONTROL_JOYSTICK_PRESS:
        if (event->pressed) {
            assembly_haptic_feedback();
            s_assembly_joystick_down = true;
        } else if (s_assembly_joystick_down) {
            s_assembly_joystick_seen |= 16U;
            s_assembly_joystick_down = false;
        }
        snprintf(s_assembly_action, sizeof(s_assembly_action), "J 0%c",
                 event->pressed ? '+' : '-');
        break;
    default:
        return;
    }
    if (event->control >= BOARD_CONTROL_ENCODER_CCW &&
        event->control <= BOARD_CONTROL_ENCODER_PRESS) {
        snprintf(s_assembly_progress, sizeof(s_assembly_progress), "R %u/3",
                 assembly_count_bits(s_assembly_encoder_seen));
    } else {
        snprintf(s_assembly_progress, sizeof(s_assembly_progress), "J %u/5",
                 assembly_count_bits(s_assembly_joystick_seen));
    }
}

static void assembly_poll_display(void)
{
    board_poll();
    bool redraw_test = false;
    board_event_t event;
    while (board_next_event(&event)) {
        /* In the alignment view the joystick still provides the existing
         * held-home comparison. A key or encoder action opens input inspection. */
        if (!s_assembly_input_test_active &&
            (event.feedback_only ||
             (event.control >= BOARD_CONTROL_JOYSTICK_UP &&
              event.control <= BOARD_CONTROL_JOYSTICK_PRESS))) {
            if (!event.feedback_only && event.pressed) {
                assembly_haptic_feedback();
            }
            continue;
        }
        if (!s_assembly_input_test_active) {
            s_assembly_input_test_active = true;
            s_assembly_home_visible = false;
        }
        if (!event.feedback_only) {
            assembly_input_event(&event);
            s_assembly_input_last_at = xTaskGetTickCount();
            redraw_test = true;
        }
    }
    if (s_assembly_input_test_active) {
        const uint32_t idle_ms =
            (uint32_t)(xTaskGetTickCount() - s_assembly_input_last_at) *
            portTICK_PERIOD_MS;
        const bool inspection_complete = s_assembly_keys_seen == 0x0fffU &&
            s_assembly_encoder_seen == 7U && s_assembly_joystick_seen == 31U &&
            s_assembly_motor_result != 0;
        const uint32_t idle_limit_ms = inspection_complete
            ? ASSEMBLY_RESULT_VISIBLE_MS : ASSEMBLY_INPUT_IDLE_MS;
        if (idle_ms >= idle_limit_ms && board_inputs_neutral()) {
            s_assembly_input_test_active = false;
            assembly_reset_input_inspection();
            board_display_finish_mist_motion();
            ESP_ERROR_CHECK(board_display_show_assembly_alignment());
        } else if (redraw_test) {
            const bool inputs_complete = s_assembly_keys_seen == 0x0fffU &&
                s_assembly_encoder_seen == 7U &&
                s_assembly_joystick_seen == 31U;
            const char *action = s_assembly_action;
            const char *progress = s_assembly_progress;
            const char *hint = NULL;
            if (inputs_complete) {
                if (s_assembly_motor_result > 0) {
                    action = "M +";
                    progress = "";
                } else if (s_assembly_motor_result < 0) {
                    action = "M -";
                    progress = "";
                } else {
                    action = "M ?";
                    progress = "J U+ D-";
                }
            }
            board_display_finish_mist_motion();
            ESP_ERROR_CHECK(board_display_show_assembly_input(
                action, progress, hint));
        }
        return;
    }

    const bool next_home_visible = assembly_joystick_active();
    if (next_home_visible != s_assembly_home_visible) {
        s_assembly_home_visible = next_home_visible;
        if (next_home_visible) {
            ESP_ERROR_CHECK(board_display_show_assembly_home());
        } else {
            board_display_finish_mist_motion();
            ESP_ERROR_CHECK(board_display_show_assembly_alignment());
        }
    }
}
#endif

void app_main(void)
{
    firmware_update_boot_stage(FIRMWARE_BOOT_STAGE_APP_ENTRY);
    char serial[18];
    make_serial(serial);

#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    configure_power_management();
#endif

    ESP_ERROR_CHECK(board_init());
    firmware_update_boot_stage(FIRMWARE_BOOT_STAGE_BOARD_READY);
#if CONFIG_MACROPAD_ASSEMBLY_ALIGNMENT
    ESP_ERROR_CHECK(board_display_show_assembly_alignment());
    ESP_LOGW(TAG, "ASSEMBLY ONLY: fixed screen target; flash official firmware before shipment");
#endif
    const esp_err_t fuel_gauge_result = fuel_gauge_init();
    if (fuel_gauge_result != ESP_OK &&
        fuel_gauge_result != ESP_ERR_NOT_SUPPORTED) {
        ESP_LOGW(TAG, "fuel gauge unavailable at boot: %s",
                 esp_err_to_name(fuel_gauge_result));
    }
    ESP_ERROR_CHECK(config_store_init());
    ESP_ERROR_CHECK(host_action_events_init());
    const esp_err_t prompt_store_result = prompt_store_init();
    if (prompt_store_result != ESP_OK) {
        ESP_LOGW(TAG, "prompt storage unavailable: %s",
                 esp_err_to_name(prompt_store_result));
    }
    const esp_err_t screen_icon_result = screen_icon_store_init();
    if (screen_icon_result != ESP_OK) {
        ESP_LOGW(TAG, "custom home icon unavailable; using built-in: %s",
                 esp_err_to_name(screen_icon_result));
    }
    const esp_err_t glyph_result = screen_glyph_store_init();
    if (glyph_result != ESP_OK)
        ESP_LOGW(TAG, "custom glyphs unavailable; using built-in: %s", esp_err_to_name(glyph_result));
    firmware_update_boot_stage(FIRMWARE_BOOT_STAGE_CONFIG_READY);
#if !CONFIG_MACROPAD_ASSEMBLY_ALIGNMENT
    ESP_ERROR_CHECK(action_engine_init());
    firmware_update_boot_stage(FIRMWARE_BOOT_STAGE_ACTION_READY);
#endif
    ESP_ERROR_CHECK(firmware_update_init());
    firmware_update_boot_stage(FIRMWARE_BOOT_STAGE_UPDATE_READY);
    ESP_LOGI(TAG, "board=%s serial=%s", board_target_name(), serial);
    const esp_err_t identity_result =
        device_identity_init(board_hardware_id(), serial);
    if (identity_result != ESP_OK) {
        device_identity_diagnostics_t diagnostics;
        device_identity_get_diagnostics(&diagnostics);
        ESP_LOGW(TAG,
                 "device authentication identity unavailable: stage=%s "
                 "detail=%ld result=%s",
                 device_identity_failure_stage_name(diagnostics.stage),
                 (long)diagnostics.detail, esp_err_to_name(identity_result));
    }
    ESP_ERROR_CHECK(usb_service_init(serial));
    firmware_update_boot_stage(FIRMWARE_BOOT_STAGE_USB_READY);
    /* Begin from a known neutral HID state before processing physical input. */
    usb_service_release_all();
    ble_config_service_prepare();
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2 && !CONFIG_MACROPAD_ASSEMBLY_ALIGNMENT
    const bool initially_suspended =
        action_engine_ble_start_suspended() || usb_service_is_mounted();
#else
    const bool initially_suspended = usb_service_is_mounted();
#endif
    const esp_err_t codex_micro_result =
        codex_micro_init(serial, initially_suspended);
    if (codex_micro_result != ESP_OK) {
        ESP_LOGW(TAG, "Codex Micro compatibility unavailable: %s",
                 esp_err_to_name(codex_micro_result));
    }
#if CONFIG_MACROPAD_ASSEMBLY_ALIGNMENT
    /* The joystick belongs to the local comparison view in this image. Keep
     * Codex mode from turning its radial state into USB or BLE reports. */
    codex_micro_set_input_suppressed(true);
#endif
    /* BLE failure must not disable an otherwise usable native USB transport.
     * Binding rejects missing common locks before enabling USB callbacks. */
    const codex_micro_usb_hid_t usb_codex_hid = {
        .connected = codex_usb_connected,
        .send = codex_usb_send,
        .context = NULL,
    };
    codex_micro_register_usb_hid(&usb_codex_hid);
    const usb_service_vendor_hid_t usb_vendor_hid = {
        .output = codex_usb_output,
        .context = NULL,
    };
    usb_service_register_vendor_hid(&usb_vendor_hid);
    if (codex_micro_result == ESP_OK) {
        const usb_service_ble_hid_t ble_standard_hid = {
            .connected = ble_standard_connected,
            .send = ble_standard_send,
            .context = NULL,
        };
        usb_service_register_ble_hid(&ble_standard_hid);
        const esp_err_t ble_config_result = ble_config_service_start();
        if (ble_config_result != ESP_OK &&
            ble_config_result != ESP_ERR_NOT_SUPPORTED) {
            ESP_LOGW(TAG, "BLE configuration unavailable: %s",
                     esp_err_to_name(ble_config_result));
        }
    }
    firmware_update_boot_stage(FIRMWARE_BOOT_STAGE_CODEX_READY);
    /*
     * All required services for the selected application are ready. The
     * assembly build substitutes its fixed display for the action engine.
     * Codex compatibility is optional and already degrades safely above, so it
     * must not reject an otherwise operable OTA image.
     */
    ESP_ERROR_CHECK(firmware_update_confirm_running());
    firmware_update_boot_stage(FIRMWARE_BOOT_STAGE_MAIN_LOOP);
#if CONFIG_MACROPAD_ASSEMBLY_ALIGNMENT
    /* Start only after slow boot services are ready, otherwise early LEDs can
     * finish before an operator has a chance to observe them. */
    assembly_led_self_test_start();
#endif

    for (;;) {
        TickType_t loop_delay_ticks = 1;
        fuel_gauge_poll();
        fuel_gauge_sample_t battery;
        if (fuel_gauge_latest(&battery)) {
            battery_service_update((uint8_t)(battery.soc_percent + 0.5f));
        }
        codex_micro_poll();
#if CONFIG_MACROPAD_ASSEMBLY_ALIGNMENT
        /* The board is polled only for the local held-joystick comparison.
         * Keep USB maintenance alive so this image can be replaced closed-case. */
        assembly_poll_display();
        config_protocol_poll();
        assembly_poll_led_self_test();
#else
        board_poll();
        config_protocol_poll();
        if (board_take_shutdown_request()) {
            action_engine_request_shutdown();
        }
        board_event_t event;
        while (board_next_event(&event)) {
            action_engine_handle_event(&event);
        }
        action_engine_poll();
        if (!config_protocol_screen_icon_upload_active() &&
            config_store_has_pending() &&
            config_store_poll(action_engine_config_activation_ready() ||
                              board_inputs_neutral())) {
            action_engine_config_activated();
        }
        action_engine_diagnostics_t action_diagnostics;
        action_engine_get_diagnostics(&action_diagnostics);
        config_protocol_set_action_diagnostics(
            action_diagnostics.host_output_state,
            action_diagnostics.local_page,
            action_diagnostics.round_setting_state,
            action_diagnostics.quick_config_active,
            action_diagnostics.local_confirm_selected,
            action_diagnostics.round_feedback_active,
            action_diagnostics.idle_circle_active,
            action_diagnostics.idle_standby_active,
            action_diagnostics.usb_standby_active,
            action_diagnostics.lighting_preview_busy);
        if (action_diagnostics.idle_standby_active) {
            loop_delay_ticks = pdMS_TO_TICKS(20);
            if (loop_delay_ticks == 0) loop_delay_ticks = 1;
        }
#endif
        usb_service_poll();
        firmware_update_poll();
        /* Match the 20 ms input debounce while halving standby wakeups. */
        vTaskDelay(loop_delay_ticks);
    }
}
