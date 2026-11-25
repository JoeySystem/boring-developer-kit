#pragma once

/*
 * Battery percentage shown to the user (screen, BLE battery service, Console,
 * Codex).
 *
 * The CW2015 runs on its factory battery model because no profile is written
 * to it, so a full cell at rest (about 4.14 V) is reported as only about 97
 * percent and a day on USB or a charger never reaches 100. The display value
 * stretches the top of the raw state of charge:
 *
 *   raw <= BATTERY_DISPLAY_KNEE_PERCENT                      unchanged
 *   KNEE < raw < BATTERY_DISPLAY_FULL_PERCENT                linear KNEE..FULL -> KNEE..100
 *   raw >= BATTERY_DISPLAY_FULL_PERCENT                      100
 *
 * Only what the user sees goes through this. The low-battery guard (warning
 * at 10 percent, shutdown at 3 percent) and the raw battery_voltage_mv keep
 * the chip's own values.
 */

#include <stdint.h>

/* Below and at this raw value the display equals the (rounded) raw value. */
#define BATTERY_DISPLAY_KNEE_PERCENT 80.0f
/* At and above this raw value the display shows 100. Set from the cells that
 * settle lowest at full charge; 96 is the starting value. */
#define BATTERY_DISPLAY_FULL_PERCENT 96.0f

/**
 * Map the CW2015 state of charge (percent, may be fractional) to the number
 * to display: rounded half up, clamped to 0..100, never decreasing as the raw
 * value rises. NaN and negative values give 0.
 */
uint8_t battery_display_percent(float raw_soc_percent);
