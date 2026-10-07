if(CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2)
    set(DEFAULT_CONFIG_FILE "default_config_matrix12_power_v2.json")
elseif(CONFIG_MACROPAD_BOARD_MATRIX12_V1)
    set(DEFAULT_CONFIG_FILE "default_config_matrix12_v1.json")
elseif(CONFIG_MACROPAD_BOARD_DEVKIT_BOOT_BUTTON)
    set(DEFAULT_CONFIG_FILE "default_config_devkit.json")
else()
    set(DEFAULT_CONFIG_FILE "default_config.json")
endif()

idf_component_register(
    SRCS "config_store.c" "ble_name.c"
    INCLUDE_DIRS "include" "../../../protocol/generated"
    EMBED_TXTFILES "${DEFAULT_CONFIG_FILE}"
    REQUIRES board cjson nvs_flash mbedtls freertos
)
