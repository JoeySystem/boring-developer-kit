#pragma once

/**
 * @file board_rev_a.h
 * @brief Frozen MCU mapping for WMP-S3-REV-A / PCB2_2026-07-18.
 *
 * These values were extracted from the released EasyEDA Pro project whose
 * digest is locked by tools/tests/test_board_contract.py. Do not infer pin
 * changes from an early sketch or edit this table without publishing a new
 * hardware release and updating docs/pcb2-pin-map.md.
 *
 * GPIO ownership is source-reviewed but electrical polarity, display geometry,
 * RGB order, and motor limits remain subject to manufactured-board bring-up.
 */

#include "driver/gpio.h"
#include "hal/adc_types.h"

#define BOARD_REV_A_KEY_COUNT 7
#define BOARD_REV_A_STATUS_RGB_COUNT 8
#define BOARD_REV_A_UNDER_KEY_RGB_COUNT 7

/* Seven independent active-low key inputs with internal pull-ups. */
#define BOARD_REV_A_KEY1_GPIO GPIO_NUM_4
#define BOARD_REV_A_KEY2_GPIO GPIO_NUM_5
#define BOARD_REV_A_KEY3_GPIO GPIO_NUM_6
#define BOARD_REV_A_KEY4_GPIO GPIO_NUM_7
#define BOARD_REV_A_KEY5_GPIO GPIO_NUM_15
#define BOARD_REV_A_KEY6_GPIO GPIO_NUM_16
#define BOARD_REV_A_KEY7_GPIO GPIO_NUM_17

/* Output peripherals. GPIO8 drives the motor transistor, not the motor directly. */
#define BOARD_REV_A_MOTOR_GPIO GPIO_NUM_8
#define BOARD_REV_A_DISPLAY_BACKLIGHT_GPIO GPIO_NUM_9
#define BOARD_REV_A_DISPLAY_CS_GPIO GPIO_NUM_10
#define BOARD_REV_A_DISPLAY_DC_GPIO GPIO_NUM_11
#define BOARD_REV_A_DISPLAY_RESET_GPIO GPIO_NUM_12
#define BOARD_REV_A_DISPLAY_SCLK_GPIO GPIO_NUM_13
#define BOARD_REV_A_DISPLAY_MOSI_GPIO GPIO_NUM_14
#define BOARD_REV_A_UNDER_KEY_RGB_GPIO GPIO_NUM_18

/* Native ESP32-S3 USB-OTG data pair; unavailable for general board I/O. */
#define BOARD_REV_A_USB_DM_GPIO GPIO_NUM_19
#define BOARD_REV_A_USB_DP_GPIO GPIO_NUM_20

/* Recovery, status chain, EC11, joystick switch, and engineering UART. */
#define BOARD_REV_A_BOOT_GPIO GPIO_NUM_0
#define BOARD_REV_A_STATUS_RGB_GPIO GPIO_NUM_38
#define BOARD_REV_A_ENCODER_B_GPIO GPIO_NUM_39
#define BOARD_REV_A_ENCODER_A_GPIO GPIO_NUM_40
#define BOARD_REV_A_ENCODER_KEY_GPIO GPIO_NUM_41
#define BOARD_REV_A_JOYSTICK_KEY_GPIO GPIO_NUM_42
#define BOARD_REV_A_UART_TX_GPIO GPIO_NUM_43
#define BOARD_REV_A_UART_RX_GPIO GPIO_NUM_44

/* Joystick analog axes are ADC1 channels so they remain independent of USB. */
#define BOARD_REV_A_JOYSTICK_Y_GPIO GPIO_NUM_2
#define BOARD_REV_A_JOYSTICK_X_GPIO GPIO_NUM_1

#define BOARD_REV_A_JOYSTICK_X_ADC_CHANNEL ADC_CHANNEL_0
#define BOARD_REV_A_JOYSTICK_Y_ADC_CHANNEL ADC_CHANNEL_1

/* Native ST7789 memory window; the PCB mount correction lives in display logic. */
#define BOARD_REV_A_DISPLAY_WIDTH 135
#define BOARD_REV_A_DISPLAY_HEIGHT 240
#define BOARD_REV_A_DISPLAY_X_GAP 52
#define BOARD_REV_A_DISPLAY_Y_GAP 40
