idf_component_register(
    SRCS "cw2015.c" "cw2015_model.c"
    INCLUDE_DIRS "include"
    REQUIRES board esp_driver_i2c esp_timer
)
