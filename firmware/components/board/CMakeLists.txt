idf_component_register(
    SRCS "board.c" "board_rev_a.c" "board_matrix12_v1.c"
         "board_bmr_icons.c" "board_mist_icons.c"
         "board_mist_ui.c"
         "mist_glyph_assets.c" "mist_glyph_renderer.c" "mist_glyph_pages.c"
         "mist_glyph_motion.c" "mist_display_model.c"
         "boring_5r_ui_core.c"
         "board_control_logic.c" "board_input_logic.c" "board_display_logic.c"
    INCLUDE_DIRS "include"
    REQUIRES
        esp_driver_gpio
        esp_driver_ledc
        esp_driver_spi
        esp_adc
        esp_lcd
        esp_lcd_st7735
        led_strip
        power_control
        screen_icon_store
)
