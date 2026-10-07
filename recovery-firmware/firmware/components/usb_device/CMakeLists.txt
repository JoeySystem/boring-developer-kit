idf_component_register(
    SRCS "usb_service.c"
    INCLUDE_DIRS "include"
    REQUIRES esp_tinyusb config_protocol
)

