idf_component_register(
    SRCS "config_protocol.c" "claude_status_codec.c" "device_auth_codec.c" "firmware_update_protocol.c"
         "joystick_calibration_model.c" "screen_icon_protocol.c" "screen_glyph_protocol.c"
    INCLUDE_DIRS "include" "../../../protocol/generated"
    REQUIRES app_update board codex_micro config_store device_identity prompt_store screen_icon_store firmware_update fuel_gauge esp_app_format esp_timer cjson mbedtls freertos heap
)
