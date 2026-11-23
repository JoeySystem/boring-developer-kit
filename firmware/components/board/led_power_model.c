#include "led_power_model.h"

void led_power_model_init(led_power_model_t *model)
{
    if (model == NULL) return;
    model->shown_total = 0;
    model->tokens = LED_POWER_INSTANT_HEADROOM;
    model->last_render_ms = 0;
    model->rendered = false;
}

static uint32_t sum_channels(const led_power_rgb_t *pixels, size_t count)
{
    uint32_t total = 0;
    for (size_t index = 0; index < count; ++index) {
        total += (uint32_t)pixels[index].red + pixels[index].green +
                 pixels[index].blue;
    }
    return total;
}

static uint8_t scale_channel(uint8_t value, uint32_t cap, uint32_t total)
{
    return (uint8_t)(((uint32_t)value * cap) / total);
}

bool led_power_model_render(led_power_model_t *model,
                            const led_power_rgb_t *target,
                            led_power_rgb_t *output, size_t count,
                            uint32_t now_ms)
{
    if (model == NULL || target == NULL || output == NULL) return false;

    /* Token bucket: the allowance refills at the ramp rate and is capped at
     * the burst headroom, so frequent renders never outrun the ramp. */
    const uint32_t elapsed = model->rendered ? now_ms - model->last_render_ms : 0;
    const uint32_t refill = elapsed >= LED_POWER_INSTANT_HEADROOM
                                ? LED_POWER_INSTANT_HEADROOM
                                : elapsed * LED_POWER_RAMP_UNITS_PER_MS;
    model->tokens += refill;
    if (model->tokens > LED_POWER_INSTANT_HEADROOM) {
        model->tokens = LED_POWER_INSTANT_HEADROOM;
    }
    model->last_render_ms = now_ms;
    model->rendered = true;

    uint32_t cap = model->shown_total + model->tokens;
    if (cap > LED_POWER_TOTAL_BUDGET) cap = LED_POWER_TOTAL_BUDGET;

    const uint32_t total = sum_channels(target, count);
    bool ramping = false;
    if (total <= cap) {
        for (size_t index = 0; index < count; ++index) output[index] = target[index];
    } else {
        for (size_t index = 0; index < count; ++index) {
            output[index] = (led_power_rgb_t) {
                .red = scale_channel(target[index].red, cap, total),
                .green = scale_channel(target[index].green, cap, total),
                .blue = scale_channel(target[index].blue, cap, total),
            };
        }
        ramping = cap < LED_POWER_TOTAL_BUDGET;
    }

    const uint32_t shown = sum_channels(output, count);
    if (shown > model->shown_total) {
        const uint32_t rise = shown - model->shown_total;
        model->tokens = rise >= model->tokens ? 0 : model->tokens - rise;
    }
    model->shown_total = shown;
    return ramping;
}
