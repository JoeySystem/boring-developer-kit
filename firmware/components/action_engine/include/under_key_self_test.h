#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define UNDER_KEY_SELF_TEST_LED_COUNT 12
#define UNDER_KEY_SELF_TEST_STEP_MS 120

typedef struct {
    uint8_t red;
    uint8_t green;
    uint8_t blue;
} under_key_self_test_rgb_t;

/*
 * Renders a short, non-blocking chase across the selected board's key LEDs.
 * Returns true while the sequence is active and false after it has finished.
 */
bool under_key_self_test_render(
    uint32_t elapsed_ms, size_t led_count,
    under_key_self_test_rgb_t output[UNDER_KEY_SELF_TEST_LED_COUNT]);

#ifdef __cplusplus
}
#endif
