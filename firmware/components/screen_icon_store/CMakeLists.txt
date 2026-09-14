idf_component_register(
    SRCS "screen_icon_store.c" "screen_glyph_store.c"
    INCLUDE_DIRS "include"
    REQUIRES nvs_flash freertos
)
