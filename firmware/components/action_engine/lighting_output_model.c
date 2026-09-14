#include "lighting_output_model.h"

#include <stddef.h>
#include <string.h>

#define LIGHTING_PERCENT_MAX 100U

uint8_t lighting_status_percent(uint8_t brightness_percent)
{
    return brightness_percent > LIGHTING_PERCENT_MAX
               ? LIGHTING_PERCENT_MAX : brightness_percent;
}

uint8_t lighting_under_key_percent(uint8_t brightness_percent)
{
    /*
     * The board driver already applies the product's physical output limit.
     * Applying another comfort factor here made the live LEDs dimmer than the
     * percentage shown on the local setting page.
     */
    return lighting_status_percent(brightness_percent);
}

uint8_t lighting_scale_channel(uint8_t value, uint8_t percent)
{
    return (uint8_t)(((unsigned)value * lighting_status_percent(percent)) /
                     LIGHTING_PERCENT_MAX);
}

bool lighting_preview_set_candidate(lighting_preview_model_t *model,
                                    uint8_t candidate,
                                    uint8_t saved)
{
    if (model == NULL) {
        return false;
    }
    const bool next_active = candidate != saved;
    const bool changed = model->active != next_active ||
                         (next_active && model->candidate != candidate);
    model->active = next_active;
    model->candidate = candidate;
    return changed;
}

bool lighting_preview_clear(lighting_preview_model_t *model)
{
    if (model == NULL || !model->active) {
        return false;
    }
    model->active = false;
    return true;
}

uint8_t lighting_preview_effective(const lighting_preview_model_t *model,
                                   uint8_t saved)
{
    return model != NULL && model->active ? model->candidate : saved;
}

bool lighting_host_preview_set(
    lighting_host_preview_model_t *model, bool enabled, uint8_t brightness,
    const lighting_output_rgb_t *under_key, size_t under_key_count,
    uint32_t now_ms, uint32_t lease_ms)
{
    if (model == NULL || under_key == NULL || under_key_count == 0 ||
        under_key_count > LIGHTING_HOST_PREVIEW_MAX_UNDER_KEY_RGB) {
        return false;
    }
    const uint8_t clamped_brightness =
        lighting_status_percent(brightness);
    const bool changed =
        !model->active || model->enabled != enabled ||
        model->brightness != clamped_brightness ||
        model->under_key_count != under_key_count ||
        memcmp(model->under_key, under_key,
               under_key_count * sizeof(under_key[0])) != 0;
    model->active = true;
    model->enabled = enabled;
    model->brightness = clamped_brightness;
    model->under_key_count = under_key_count;
    memcpy(model->under_key, under_key,
           under_key_count * sizeof(under_key[0]));
    model->expires_at_ms = now_ms + lease_ms;
    return changed;
}

bool lighting_host_preview_clear(lighting_host_preview_model_t *model)
{
    if (model == NULL || !model->active) {
        return false;
    }
    model->active = false;
    return true;
}

bool lighting_host_preview_expire(lighting_host_preview_model_t *model,
                                  uint32_t now_ms)
{
    if (model == NULL || !model->active ||
        (int32_t)(now_ms - model->expires_at_ms) < 0) {
        return false;
    }
    model->active = false;
    return true;
}

uint8_t lighting_host_preview_effective_brightness(
    const lighting_host_preview_model_t *model, uint8_t fallback)
{
    if (model == NULL || !model->active) {
        return fallback;
    }
    return model->enabled ? model->brightness : 0;
}

lighting_output_rgb_t lighting_host_preview_effective_rgb(
    const lighting_host_preview_model_t *model, size_t index,
    lighting_output_rgb_t fallback)
{
    if (model == NULL || !model->active || index >= model->under_key_count) {
        return fallback;
    }
    return model->under_key[index];
}
