idf_component_register(
    SRCS "boot_guard_model.c" "boot_combo_model.c" "boot_guard.c"
    INCLUDE_DIRS "include"
    PRIV_REQUIRES nvs_flash esp_system esp_app_format storage_recovery
)
