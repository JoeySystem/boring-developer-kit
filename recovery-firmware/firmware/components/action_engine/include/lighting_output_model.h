#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/** Clamp the configured master brightness to a safe percentage. */
uint8_t lighting_status_percent(uint8_t brightness_percent);

/** Resolve the displayed master percentage for the under-key LED chain. */
uint8_t lighting_under_key_percent(uint8_t brightness_percent);

/** Scale one LED channel by a clamped percentage. */
uint8_t lighting_scale_channel(uint8_t value, uint8_t percent);

typedef struct {
    bool active;
    uint8_t candidate;
} lighting_preview_model_t;

/** Update the temporary brightness preview. Returns true when output changed. */
bool lighting_preview_set_candidate(lighting_preview_model_t *model,
                                    uint8_t candidate,
                                    uint8_t saved);

/** Clear a temporary preview. Returns true when saved output must be restored. */
bool lighting_preview_clear(lighting_preview_model_t *model);

/** Resolve the brightness that should currently be rendered. */
uint8_t lighting_preview_effective(const lighting_preview_model_t *model,
                                   uint8_t saved);

#define LIGHTING_HOST_PREVIEW_MAX_UNDER_KEY_RGB 12

typedef struct {
    uint8_t red;
    uint8_t green;
    uint8_t blue;
} lighting_output_rgb_t;

typedef struct {
    bool active;
    bool enabled;
    uint8_t brightness;
    size_t under_key_count;
    lighting_output_rgb_t
        under_key[LIGHTING_HOST_PREVIEW_MAX_UNDER_KEY_RGB];
    uint32_t expires_at_ms;
} lighting_host_preview_model_t;

/** Replace the complete RAM-only host preview and refresh its lease. */
bool lighting_host_preview_set(
    lighting_host_preview_model_t *model, bool enabled, uint8_t brightness,
    const lighting_output_rgb_t *under_key, size_t under_key_count,
    uint32_t now_ms, uint32_t lease_ms);

/** Clear the host preview. Returns true when saved output must be restored. */
bool lighting_host_preview_clear(lighting_host_preview_model_t *model);

/** Expire an abandoned host preview using wrap-safe millisecond arithmetic. */
bool lighting_host_preview_expire(lighting_host_preview_model_t *model,
                                  uint32_t now_ms);

/** Resolve host master brightness over the local/saved fallback. */
uint8_t lighting_host_preview_effective_brightness(
    const lighting_host_preview_model_t *model, uint8_t fallback);

/** Resolve one host preview RGB value over the active-config fallback. */
lighting_output_rgb_t lighting_host_preview_effective_rgb(
    const lighting_host_preview_model_t *model, size_t index,
    lighting_output_rgb_t fallback);

#ifdef __cplusplus
}
#endif
