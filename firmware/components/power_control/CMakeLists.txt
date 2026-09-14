idf_component_register(
    SRCS "power_button.c" "power_control.c"
    INCLUDE_DIRS "include"
    REQUIRES esp_driver_gpio freertos
)
