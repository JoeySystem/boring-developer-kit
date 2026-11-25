idf_component_register(
    SRCS "config_protocol.c" "claude_status_codec.c" "desktop_agent_status_codec.c" "device_auth_codec.c" "firmware_update_protocol.c"
         "joystick_calibration_model.c" "screen_icon_protocol.c" "screen_glyph_protocol.c"
         "wmp_frame_scanner.c" "firmware_confirm_model.c" "usb_standby_trace.c" "heap_watch_model.c"
    INCLUDE_DIRS "include" "../../../protocol/generated"
    REQUIRES app_update board codex_micro config_store wmp_sha256 ram_reserve heap_checkpoint liveness boot_guard device_identity prompt_store screen_icon_store firmware_update fuel_gauge esp_app_format esp_timer cjson mbedtls freertos heap nvs_flash storage_recovery
)
