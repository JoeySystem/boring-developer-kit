#pragma once

/*
 * Under-key LED current budget for Power V2 (pure logic, host-testable).
 *
 * A full-white step on all twelve LEDs pulls USB (through the SS54 Schottky)
 * below the TPS2116 switchover point, and a weak cell then browns out. This
 * model caps the summed channel output and ramps rises in total output.
 * Falls apply at once.
 *
 * Units are 8-bit channel values summed over every pixel, measured after the
 * board's per-channel output limit (60 % on Power V2: 12 x 3 x 153 = 5508 at
 * full white). Values are empirical starting points (2026-10-28) pending a
 * bench measurement of USB current and the TPS2116 input dip. Change them
 * here only; every caller and test reads these definitions.
 */

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Summed-output ceiling, about 55 % of full white at the channel limit. */
#define LED_POWER_TOTAL_BUDGET 3000U
/* Burst allowance: a rise this small shows at once (one LED full white at
 * the channel limit). It refills at the ramp rate and never stacks higher. */
#define LED_POWER_INSTANT_HEADROOM 459U
/* Ramp rate: 0 -> budget in about 400 ms. */
#define LED_POWER_RAMP_UNITS_PER_MS 8U

#define LED_POWER_MAX_PIXELS 12U

typedef struct {
    uint8_t red;
    uint8_t green;
    uint8_t blue;
} led_power_rgb_t;

typedef struct {
    uint32_t shown_total;
    uint32_t tokens;
    uint32_t last_render_ms;
    bool rendered;
} led_power_model_t;

void led_power_model_init(led_power_model_t *model);

/*
 * Resolve one frame. target holds the wanted values (already channel-limited);
 * output receives the values to drive. All channels are scaled by one factor,
 * so hues are kept. Returns true while the ramp still holds the output below
 * the target, i.e. the caller must render again on a later poll.
 */
bool led_power_model_render(led_power_model_t *model,
                            const led_power_rgb_t *target,
                            led_power_rgb_t *output, size_t count,
                            uint32_t now_ms);

#ifdef __cplusplus
}
#endif
