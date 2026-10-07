#pragma once

/**
 * @file board_matrix12_v1.h
 * @brief MCU mapping for the 12-key matrix board exported on 2026-08-10.
 *
 * Source authority:
 *   ProPrj_ESP32 boring 8-8V1.0_2026-08-10.epro2
 *   SHA-256 65483fac468774a3443c7730283d86e1e765171c6f43a934fa6a20a049076927
 *
 * The project identifies U2 as ESP32-S3-WROOM-1-N16R8. Matrix rows drive
 * the cathodes of D2-D13; columns are pulled-up inputs. Physical polarity,
 * display mounting, joystick orientation, motor strength, and final current
 * limits still require validation on the manufactured board.
 */

#include "driver/gpio.h"
#include "hal/adc_types.h"

#define BOARD_MATRIX12_V1_KEY_COUNT 12
#define BOARD_MATRIX12_V1_STATUS_RGB_COUNT 0
#define BOARD_MATRIX12_V1_UNDER_KEY_RGB_COUNT 12
#define BOARD_MATRIX12_V1_MATRIX_ROW_COUNT 4
#define BOARD_MATRIX12_V1_MATRIX_COLUMN_COUNT 4

/* Matrix row outputs: selected row low, all other rows high-impedance. */
#define BOARD_MATRIX12_V1_ROW0_GPIO GPIO_NUM_4
#define BOARD_MATRIX12_V1_ROW1_GPIO GPIO_NUM_5
#define BOARD_MATRIX12_V1_ROW2_GPIO GPIO_NUM_6
#define BOARD_MATRIX12_V1_ROW3_GPIO GPIO_NUM_7

/* Active-low column inputs with internal pull-ups. */
#define BOARD_MATRIX12_V1_COL0_GPIO GPIO_NUM_15
#define BOARD_MATRIX12_V1_COL1_GPIO GPIO_NUM_16
#define BOARD_MATRIX12_V1_COL2_GPIO GPIO_NUM_17
#define BOARD_MATRIX12_V1_COL3_GPIO GPIO_NUM_21

/* GPIO8 drives the motor transistor; GPIO18 starts KEY1 -> KEY12 RGB data. */
#define BOARD_MATRIX12_V1_MOTOR_GPIO GPIO_NUM_8
#define BOARD_MATRIX12_V1_DISPLAY_BACKLIGHT_GPIO GPIO_NUM_9
#define BOARD_MATRIX12_V1_DISPLAY_CS_GPIO GPIO_NUM_10
#define BOARD_MATRIX12_V1_DISPLAY_DC_GPIO GPIO_NUM_11
#define BOARD_MATRIX12_V1_DISPLAY_RESET_GPIO GPIO_NUM_12
#define BOARD_MATRIX12_V1_DISPLAY_SCLK_GPIO GPIO_NUM_13
#define BOARD_MATRIX12_V1_DISPLAY_MOSI_GPIO GPIO_NUM_14
#define BOARD_MATRIX12_V1_UNDER_KEY_RGB_GPIO GPIO_NUM_18

/* Native ESP32-S3 USB data pair. */
#define BOARD_MATRIX12_V1_USB_DM_GPIO GPIO_NUM_19
#define BOARD_MATRIX12_V1_USB_DP_GPIO GPIO_NUM_20

/* GPIO38 is routed as 2812RGB but has no populated status chain in this EDA. */
#define BOARD_MATRIX12_V1_UNUSED_STATUS_RGB_GPIO GPIO_NUM_38
#define BOARD_MATRIX12_V1_BOOT_GPIO GPIO_NUM_0
#define BOARD_MATRIX12_V1_ENCODER_B_GPIO GPIO_NUM_39
#define BOARD_MATRIX12_V1_ENCODER_A_GPIO GPIO_NUM_40
#define BOARD_MATRIX12_V1_ENCODER_KEY_GPIO GPIO_NUM_41
#define BOARD_MATRIX12_V1_JOYSTICK_KEY_GPIO GPIO_NUM_42
#define BOARD_MATRIX12_V1_UART_TX_GPIO GPIO_NUM_43
#define BOARD_MATRIX12_V1_UART_RX_GPIO GPIO_NUM_44

#define BOARD_MATRIX12_V1_JOYSTICK_Y_GPIO GPIO_NUM_2
#define BOARD_MATRIX12_V1_JOYSTICK_X_GPIO GPIO_NUM_1
#define BOARD_MATRIX12_V1_JOYSTICK_X_ADC_CHANNEL ADC_CHANNEL_0
#define BOARD_MATRIX12_V1_JOYSTICK_Y_ADC_CHANNEL ADC_CHANNEL_1

/*
 * T085X6-C08-25-V1 is a 0.85-inch 128x128 ST7735 SPI panel. The supplier
 * specification does not publish the controller-RAM visible-window offset.
 * Physical bring-up calibrated the native window to 2/2; the first assembled
 * unit also confirms that the panel is mounted 180 degrees relative to the
 * controller's native orientation.
 */
#define BOARD_MATRIX12_V1_DISPLAY_WIDTH 128
#define BOARD_MATRIX12_V1_DISPLAY_HEIGHT 128
#define BOARD_MATRIX12_V1_DISPLAY_RAM_WIDTH 132
#define BOARD_MATRIX12_V1_DISPLAY_RAM_HEIGHT 162
#define BOARD_MATRIX12_V1_DISPLAY_X_GAP 2
#define BOARD_MATRIX12_V1_DISPLAY_Y_GAP 2
#define BOARD_MATRIX12_V1_DISPLAY_MOUNT_ROTATION 180

/* Board-level ceilings: neither actuator can be driven at full output. */
#define BOARD_MATRIX12_V1_MOTOR_MAX_STRENGTH_PERCENT 70
#define BOARD_MATRIX12_V1_UNDER_KEY_OUTPUT_PERCENT 60
