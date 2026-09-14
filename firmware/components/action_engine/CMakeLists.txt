idf_component_register(
    SRCS "action_engine.c" "claude_code_shortcuts.c"
         "codex_attention_haptic_model.c"
         "diagnostic_capture_model.c"
         "double_click_model.c" "eda_shortcuts.c"
         "encoder_haptic_model.c"
         "home_status_model.c"
         "idle_standby_model.c"
         "light_idle_model.c"
         "joystick_radial_haptic_model.c"
         "lighting_output_model.c"
         "pomodoro_display_model.c" "pomodoro_model.c" "quick_config_model.c"
         "prompt_joystick_model.c"
         "status_indicator.c"
         "under_key_self_test.c"
         "usb_connection_feedback.c"
    INCLUDE_DIRS "include"
    REQUIRES board codex_micro config_protocol config_store prompt_store fuel_gauge usb_device freertos esp_system firmware_update
)
