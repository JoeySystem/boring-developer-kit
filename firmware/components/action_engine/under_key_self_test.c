#include "under_key_self_test.h"

#include <string.h>

#define SELF_TEST_LEVEL 24

bool under_key_self_test_render(
    uint32_t elapsed_ms, size_t led_count,
    under_key_self_test_rgb_t output[UNDER_KEY_SELF_TEST_LED_COUNT])
{
    if (output == NULL || led_count == 0 ||
        led_count > UNDER_KEY_SELF_TEST_LED_COUNT) {
        return false;
    }
    memset(output, 0,
           sizeof(under_key_self_test_rgb_t) * UNDER_KEY_SELF_TEST_LED_COUNT);
    const size_t active_index = elapsed_ms / UNDER_KEY_SELF_TEST_STEP_MS;
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
