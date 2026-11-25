idf_component_register(
    SRCS "storage_recovery.c" "storage_recovery_policy.c"
    INCLUDE_DIRS "include"
    REQUIRES nvs_flash
    PRIV_REQUIRES app_update
)
