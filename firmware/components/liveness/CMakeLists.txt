idf_component_register(
    SRCS "liveness_model.c" "liveness.c"
    INCLUDE_DIRS "include"
    PRIV_REQUIRES esp_timer esp_system
)
