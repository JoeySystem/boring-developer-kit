#include "status_indicator.h"

#include <stddef.h>

/*
 * The original static colors used a channel value of 48 and were uncomfortably
 * bright on the assembled board. All status effects now stay at or below 16.
 */
#define STATUS_MAX_CHANNEL 16u
#define FLOW_STEP_MS 160u
#define BREATH_PERIOD_MS 1600u
#define ERROR_PERIOD_MS 600u

static void clear_frame(
    status_indicator_rgb_t output[STATUS_INDICATOR_LED_COUNT])
{
    for (size_t index = 0; index < STATUS_INDICATOR_LED_COUNT; ++index) {
        output[index] = (status_indicator_rgb_t){0, 0, 0};
    }
}

static void render_flow(
    uint32_t elapsed_ms, uint8_t red, uint8_t green, uint8_t blue,
    status_indicator_rgb_t output[STATUS_INDICATOR_LED_COUNT])
{
    static const uint8_t tail_percent[] = {100, 44, 18};
    const size_t head =
        (elapsed_ms / FLOW_STEP_MS) % STATUS_INDICATOR_LED_COUNT;
    clear_frame(output);
    for (size_t offset = 0;
         offset < sizeof(tail_percent) / sizeof(tail_percent[0]); ++offset) {
        const size_t index =
            (head + STATUS_INDICATOR_LED_COUNT - offset) %
            STATUS_INDICATOR_LED_COUNT;
        output[index] = (status_indicator_rgb_t){
            .red = (uint8_t)((red * tail_percent[offset]) / 100u),
            .green = (uint8_t)((green * tail_percent[offset]) / 100u),
            .blue = (uint8_t)((blue * tail_percent[offset]) / 100u),
        };
    }
}

status_indicator_state_t status_indicator_select(bool booting, bool usb_mounted,
                                                 bool config_session,
                                                 bool has_error)
{
    if (has_error) {
        return STATUS_INDICATOR_ERROR;
    }
    if (booting) {
        return STATUS_INDICATOR_BOOTING;
    }
    if (!usb_mounted) {
        return STATUS_INDICATOR_USB_OFFLINE;
    }
    if (config_session) {
        return STATUS_INDICATOR_CONFIG_SESSION;
    }
    return STATUS_INDICATOR_USB_READY;
}

void status_indicator_render(
    status_indicator_state_t state, uint32_t elapsed_ms,
    status_indicator_rgb_t output[STATUS_INDICATOR_LED_COUNT])
{
    if (output == NULL) {
        return;
    }
    switch (state) {
    case STATUS_INDICATOR_BOOTING:
        render_flow(elapsed_ms, STATUS_MAX_CHANNEL, 10u, 0u, output);
        return;
    case STATUS_INDICATOR_USB_OFFLINE: {
        const uint32_t phase = elapsed_ms % BREATH_PERIOD_MS;
        const uint32_t triangle =
            phase <= BREATH_PERIOD_MS / 2u ? phase : BREATH_PERIOD_MS - phase;
        const uint8_t red =
            (uint8_t)(3u + (triangle * 10u) / (BREATH_PERIOD_MS / 2u));
        const uint8_t green = (uint8_t)((red + 2u) / 4u);
        for (size_t index = 0; index < STATUS_INDICATOR_LED_COUNT; ++index) {
            output[index] = (status_indicator_rgb_t){red, green, 0};
        }
        return;
    }
    case STATUS_INDICATOR_USB_READY:
        for (size_t index = 0; index < STATUS_INDICATOR_LED_COUNT; ++index) {
            output[index] = (status_indicator_rgb_t){0, 10u, 2u};
        }
        return;
    case STATUS_INDICATOR_CONFIG_SESSION:
        render_flow(elapsed_ms, 0u, 3u, STATUS_MAX_CHANNEL, output);
        return;
    case STATUS_INDICATOR_ERROR:
    default: {
        const bool on = (elapsed_ms % ERROR_PERIOD_MS) < ERROR_PERIOD_MS / 2u;
        const uint8_t red = on ? STATUS_MAX_CHANNEL : 0u;
        for (size_t index = 0; index < STATUS_INDICATOR_LED_COUNT; ++index) {
            output[index] = (status_indicator_rgb_t){red, 0, 0};
        }
        return;
    }
    }
}
