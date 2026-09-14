#include <stdio.h>

#include "board.h"
#include "action_engine.h"
#include "codex_micro.h"
#include "config_protocol.h"
#include "config_store.h"
#include "device_identity.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "firmware_update.h"
#include "prompt_store.h"
#include "screen_icon_store.h"
#include "screen_glyph_store.h"
#include "cw2015.h"
#include "battery_service.h"
#include "ble_config_service.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "usb_service.h"

static const char *TAG = "wired_macro_pad";

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

void app_main(void)
{
    firmware_update_boot_stage(FIRMWARE_BOOT_STAGE_APP_ENTRY);
    char serial[18];
    make_serial(serial);

    ESP_ERROR_CHECK(board_init());
    firmware_update_boot_stage(FIRMWARE_BOOT_STAGE_BOARD_READY);
    const esp_err_t fuel_gauge_result = fuel_gauge_init();
    if (fuel_gauge_result != ESP_OK &&
        fuel_gauge_result != ESP_ERR_NOT_SUPPORTED) {
        ESP_LOGW(TAG, "fuel gauge unavailable at boot: %s",
                 esp_err_to_name(fuel_gauge_result));
    }
    ESP_ERROR_CHECK(config_store_init());
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
    ESP_ERROR_CHECK(action_engine_init());
    firmware_update_boot_stage(FIRMWARE_BOOT_STAGE_ACTION_READY);
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
    const esp_err_t codex_micro_result = codex_micro_init(serial);
    if (codex_micro_result != ESP_OK) {
        ESP_LOGW(TAG, "Codex Micro compatibility unavailable: %s",
                 esp_err_to_name(codex_micro_result));
    }
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
     * All required board, configuration, action and USB services are ready.
     * Codex compatibility is optional and already degrades safely above, so it
     * must not reject an otherwise operable OTA image.
     */
    ESP_ERROR_CHECK(firmware_update_confirm_running());
    firmware_update_boot_stage(FIRMWARE_BOOT_STAGE_MAIN_LOOP);

    for (;;) {
        fuel_gauge_poll();
        fuel_gauge_sample_t battery;
        if (fuel_gauge_latest(&battery)) {
            battery_service_update((uint8_t)(battery.soc_percent + 0.5f));
        }
        codex_micro_poll();
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
        usb_service_poll();
        firmware_update_poll();
        /* One scheduler tick is the minimum non-zero delay at any tick rate. */
        vTaskDelay(1);
    }
}
