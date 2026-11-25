idf_component_register(
    SRCS "firmware_update.c" "firmware_image_descriptor.c"
    INCLUDE_DIRS "include" "../../../protocol/generated"
    REQUIRES app_update board esp_app_format esp_timer wmp_sha256 ram_reserve
)

# Keep the fixed-offset hardware descriptor even though application code never
# directly references its symbol.
target_link_libraries(${COMPONENT_LIB} INTERFACE "-u custom_app_desc")
