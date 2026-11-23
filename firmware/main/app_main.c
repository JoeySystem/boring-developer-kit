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
#include "heap_checkpoint.h"
#include "liveness.h"
#include "boot_guard.h"
#include "boot_combo_model.h"
#include "rom_download.h"
#include "esp_timer.h"
#include "esp_heap_caps.h"
#include "prompt_store.h"
#include "host_action_events.h"
#include "screen_icon_store.h"
#include "screen_glyph_store.h"
#include "battery_display.h"
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


#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
#define BOOT_COMBO_ACTIVE 1
static uint32_t now_ms(void)
{
    return (uint32_t)(esp_timer_get_time() / 1000);
}

static boot_combo_state_t sample_combo(boot_combo_t *combo, bool at_boot)
{
    return boot_combo_update(combo, board_boot_key_held(BOARD_CONTROL_KEY_1),
                             board_boot_key_held(BOARD_CONTROL_KEY_12), now_ms(),
                             at_boot);
}

/*
 * Stability item 6b, part 1, before board_init (boot_combo_model.h): Key 1 +
 * Key 12 already held at the first scan and kept for 3 s arm safe mode. Not
 * held costs one scan.
 */
static bool boot_combo_armed_before_board(boot_combo_t *combo)
{
    if (board_boot_keys_prepare() != ESP_OK) {
        return false;
    }
    boot_combo_state_t state;
    while ((state = sample_combo(combo, true)) == BOOT_COMBO_HOLDING) {
        vTaskDelay(pdMS_TO_TICKS(10));
    }
    return state == BOOT_COMBO_ARMED;
}

/* Part 2, after board_init: prompt, then safe mode once both keys are up.
 * board_init leaves the backlight off until the config sets it (found on
 * 68DE8: the prompt was drawn but dark), so light it for the prompt. */
#define BOOT_PROMPT_BRIGHTNESS_PERCENT 60u
static void boot_combo_confirm(boot_combo_t *combo)
{
    (void)board_set_display_config(BOOT_PROMPT_BRIGHTNESS_PERCENT, 0);
    /* Interim text: after-sales gesture, no "safe mode" wording. */
    (void)board_display_show_local("1+12", "REPAIR", "RELEASE", "KEYS", 0xFD20);
    boot_combo_state_t state;
    while ((state = sample_combo(combo, true)) == BOOT_COMBO_ARMED) {
        vTaskDelay(pdMS_TO_TICKS(10));
    }
    if (state == BOOT_COMBO_CONFIRMED) {
        boot_guard_request_safe_mode();
        return;
    }
    /* Cancelled: back to the dark screen a normal boot starts from. */
    (void)board_display_fill(0x0000);
    (void)board_set_display_config(0, 0);
}

/*
 * Stability item 9, in safe mode only: Key 1 + Key 12 held 3 s prompt for the
 * ROM download mode, entered on release. Returns true while the combination
 * owns the keys (their events must not reach the action engine).
 */
static bool poll_safe_mode_download(boot_combo_t *combo)
{
    const boot_combo_state_t previous = combo->state;
    const boot_combo_state_t state = sample_combo(combo, false);
    if (state == BOOT_COMBO_HOLDING && previous == BOOT_COMBO_IDLE) {
        usb_service_release_all(); /* the first key press may have gone out */
    }
    if (state == BOOT_COMBO_ARMED && previous != BOOT_COMBO_ARMED) {
        (void)board_display_show_local("1+12", "DOWNLOAD", "RELEASE", "KEYS", 0xFD20);
    }
    if (state == BOOT_COMBO_CONFIRMED) {
        rom_download_enter();
    }
    return state != BOOT_COMBO_IDLE;
}
#else
#define BOOT_COMBO_ACTIVE 0
#endif

void app_main(void)
{
    firmware_update_boot_stage(FIRMWARE_BOOT_STAGE_APP_ENTRY);
    /* Stability item 2: a stalled main loop or USB config task resets. */
    liveness_start();
    /* Stability item 5: count abnormal resets; item 6 enters safe mode. */
    boot_guard_start();
    heap_checkpoint("app_entry");
    char serial[18];
    make_serial(serial);

#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    configure_power_management();
#endif

#if BOOT_COMBO_ACTIVE
    boot_combo_t boot_combo = {0};
    const bool combo_armed = boot_combo_armed_before_board(&boot_combo);
#endif
    ESP_ERROR_CHECK(board_init());
#if BOOT_COMBO_ACTIVE
    if (combo_armed) {
        boot_combo_confirm(&boot_combo);
    }
#endif
    /* Automatic (crash loop) or manual (boot key combination). */
    const bool safe_mode = boot_guard_safe_mode();
    firmware_update_boot_stage(FIRMWARE_BOOT_STAGE_BOARD_READY);
    heap_checkpoint("board");
    const esp_err_t fuel_gauge_result = fuel_gauge_init();
    if (fuel_gauge_result != ESP_OK &&
        fuel_gauge_result != ESP_ERR_NOT_SUPPORTED) {
        ESP_LOGW(TAG, "fuel gauge unavailable at boot: %s",
                 esp_err_to_name(fuel_gauge_result));
    }
    /* Safe mode never reads the stored config (it may be what crashes). */
    ESP_ERROR_CHECK(safe_mode ? config_store_init_safe_mode() : config_store_init());
    heap_checkpoint("config_store");
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
    heap_checkpoint("stores");
    ESP_ERROR_CHECK(action_engine_init());
    firmware_update_boot_stage(FIRMWARE_BOOT_STAGE_ACTION_READY);
    heap_checkpoint("action_engine");
    ESP_ERROR_CHECK(firmware_update_init());
    firmware_update_boot_stage(FIRMWARE_BOOT_STAGE_UPDATE_READY);
    heap_checkpoint("firmware_update");
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
    heap_checkpoint("device_identity");
    ESP_ERROR_CHECK(usb_service_init(serial));
    firmware_update_boot_stage(FIRMWARE_BOOT_STAGE_USB_READY);
    heap_checkpoint("usb_service");
    /* Begin from a known neutral HID state before processing physical input. */
    usb_service_release_all();
    ble_config_service_prepare();
    /*
     * USB enumeration has not finished yet, so usb_service_is_mounted() is
     * always false here. Start BLE suspended; the action engine's USB link
     * model releases it once no host claims the cable (about 1.5 s), so a
     * computer on the cable never sees the device advertise and vanish.
     * This also covers a USB charge session (action_engine_ble_start_suspended),
     * which keeps BLE suspended through the battery splash and standby.
     */
    const bool initially_suspended = true;
    const esp_err_t codex_micro_result =
        codex_micro_init(serial, initially_suspended);
    heap_checkpoint("codex_micro_ble");
    if (codex_micro_result != ESP_OK) {
        ESP_LOGW(TAG, "Codex Micro compatibility unavailable: %s",
                 esp_err_to_name(codex_micro_result));
    }
    /* Review A1: BLE start-up out of memory means an unusable boot. */
    if (codex_micro_result == ESP_ERR_NO_MEM && !safe_mode) {
        boot_guard_restart_into_safe_mode(BOOT_GUARD_REASON_BLE_NO_MEM);
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
    heap_checkpoint("ble_config");
    /*
     * All required board, configuration, action and USB services are ready.
     * Codex compatibility is optional and already degrades safely above, so it
     * must not reject an otherwise operable OTA image. A pending OTA image is
     * not confirmed here (F-01): a host must first prove the configuration
     * channel works, otherwise the next reset rolls back.
     */
    ESP_ERROR_CHECK(firmware_update_boot_ready());
    firmware_update_boot_stage(FIRMWARE_BOOT_STAGE_MAIN_LOOP);
    heap_checkpoint("main_loop");
    liveness_arm(LIVENESS_MAIN, LIVENESS_MAIN_TIMEOUT_MS);
    const int64_t main_loop_started_us = esp_timer_get_time();
    uint32_t memory_sampled_ms = 0;
    bool memory_low = false;
#if BOOT_COMBO_ACTIVE
    boot_combo_t download_combo = {0};
#endif
    /* BLE that was skipped or failed cleanly does not hold back health. */
    const bool ble_expected = codex_micro_result == ESP_OK;

    for (;;) {
        TickType_t loop_delay_ticks = 1;
        liveness_beat(LIVENESS_MAIN);
        const uint32_t main_loop_ms =
            (uint32_t)((esp_timer_get_time() - main_loop_started_us) / 1000);
        /* Boot phase only, twice a second: a heap walk is not free. */
        if (main_loop_ms < BOOT_GUARD_STABLE_MS &&
            main_loop_ms - memory_sampled_ms >= 500u) {
            memory_sampled_ms = main_loop_ms;
            memory_low = heap_caps_get_largest_free_block(
                             MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA) <
                         BOOT_GUARD_LOW_MEMORY_BYTES;
        }
        boot_guard_poll(main_loop_ms, memory_low, firmware_update_is_active());
        /* Stability item 4: a fresh image is confirmed only while healthy. */
        config_protocol_note_runtime_health(
            usb_service_is_mounted(), safe_mode,
            !ble_expected || codex_micro_ble_services_ready());
        fuel_gauge_poll();
        fuel_gauge_sample_t battery;
        if (fuel_gauge_latest(&battery)) {
            battery_service_update(battery_display_percent(battery.soc_percent));
        }
        codex_micro_poll();
        board_poll();
        config_protocol_poll();
        if (board_take_shutdown_request()) {
            action_engine_request_shutdown();
        }
        board_event_t event;
#if BOOT_COMBO_ACTIVE
        const bool combo_owns_keys =
            safe_mode && poll_safe_mode_download(&download_combo);
#else
        const bool combo_owns_keys = false;
#endif
        while (board_next_event(&event)) {
            if (!combo_owns_keys) {
                action_engine_handle_event(&event);
            }
        }
        action_engine_poll();
        if (!config_protocol_screen_icon_upload_active() &&
            config_store_has_pending()) {
            const bool activation_ready =
                config_protocol_calibration_ownership_requested()
                    ? config_protocol_calibration_activation_ready()
                    : action_engine_config_activation_ready() ||
                          board_keys_neutral();
            if (config_store_poll(activation_ready)) {
                config_protocol_calibration_activated();
                action_engine_config_activated();
            }
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
        usb_service_poll();
        firmware_update_poll();
        /* Match the 20 ms input debounce while halving standby wakeups. */
        vTaskDelay(loop_delay_ticks);
    }
}
