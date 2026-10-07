#include "under_key_self_test.h"

#include <string.h>

#define SELF_TEST_LEVEL 24

bool under_key_self_test_render(
    uint32_t elapsed_ms, size_t led_count,
    under_key_self_test_rgb_t output[UNDER_KEY_SELF_TEST_LED_COUNT])
{
    return under_key_self_test_render_timed(
        elapsed_ms, led_count, UNDER_KEY_SELF_TEST_STEP_MS, output);
}

bool under_key_self_test_render_timed(
    uint32_t elapsed_ms, size_t led_count, uint32_t step_ms,
    under_key_self_test_rgb_t output[UNDER_KEY_SELF_TEST_LED_COUNT])
{
    if (output == NULL) {
        return false;
    }
    memset(output, 0,
           sizeof(under_key_self_test_rgb_t) * UNDER_KEY_SELF_TEST_LED_COUNT);
    if (led_count == 0 || step_ms == 0 ||
        led_count > UNDER_KEY_SELF_TEST_LED_COUNT) {
        return false;
    }
    const size_t active_index = elapsed_ms / step_ms;
    if (active_index >= led_count) {
        return false;
    }
    output[active_index] = (under_key_self_test_rgb_t) {
        .red = SELF_TEST_LEVEL,
        .green = SELF_TEST_LEVEL,
        .blue = SELF_TEST_LEVEL,
    };
    return true;
}
