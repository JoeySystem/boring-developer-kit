idf_component_register(
    SRCS "device_identity.c" "device_identity_esp.c"
    INCLUDE_DIRS "include"
    PRIV_INCLUDE_DIRS "."
    REQUIRES cjson esp_partition esp_secure_cert_mgr mbedtls
)
