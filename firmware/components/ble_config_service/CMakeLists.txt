idf_component_register(
    SRCS "ble_config_service.c"
    INCLUDE_DIRS "include" "../../../protocol/generated"
    REQUIRES bt codex_micro config_protocol
)
