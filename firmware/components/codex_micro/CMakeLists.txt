if(CONFIG_CODEX_MICRO_BLE_ENABLED)
    set(codex_micro_transport_source "codex_micro_ble.c")
else()
    set(codex_micro_transport_source "codex_micro_stub.c")
endif()

# Requirements are collected before sdkconfig values are available, so they
# cannot be declared inside the CONFIG_CODEX_MICRO_BLE_ENABLED branch.
idf_component_register(
    SRCS "codex_micro_attention_model.c" "ble_recovery_model.c" "ble_advertising_backoff_model.c" "ble_slot_history_model.c"
         "ble_host_policy_model.c"
         "claude_status.c" "claude_status_model.c"
         "codex_agent_press_model.c"
         "desktop_agent_status.c" "desktop_agent_status_model.c"
         "codex_micro_controls.c" "codex_micro_mode.c"
         "codex_micro_protocol.c"
         "codex_micro_radial_filter.c"
         "${codex_micro_transport_source}"
    INCLUDE_DIRS "include" "../../../protocol/generated"
    REQUIRES board battery_service fuel_gauge
    PRIV_REQUIRES bt heap_checkpoint liveness boot_guard esp_app_format esp_hid esp_timer espressif__cjson nvs_flash config_store storage_recovery
)
