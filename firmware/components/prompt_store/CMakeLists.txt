idf_component_register(
    SRCS "prompt_store.c" "prompt_record.c"
    INCLUDE_DIRS "include"
    REQUIRES nvs_flash freertos
)
