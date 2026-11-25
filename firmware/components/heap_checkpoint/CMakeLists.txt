idf_component_register(
    SRCS "heap_checkpoint.c"
    INCLUDE_DIRS "include"
    REQUIRES heap freertos esp_timer
)
