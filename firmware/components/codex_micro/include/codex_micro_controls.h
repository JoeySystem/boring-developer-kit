#pragma once

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    CODEX_MICRO_KEY_NONE = 0,
    CODEX_MICRO_KEY_VENDOR,
    CODEX_MICRO_KEY_STANDARD_HID,
} codex_micro_key_kind_t;

typedef enum {
    CODEX_MICRO_KEY_LAYOUT_REV_A = 0,
    CODEX_MICRO_KEY_LAYOUT_MATRIX12,
} codex_micro_key_layout_t;

#define CODEX_MICRO_AGENT_NONE (-1)

typedef struct {
    codex_micro_key_kind_t kind;
    uint16_t hid_usage;
    const char *vendor_key;
    int8_t agent;
    const char *short_name;
} codex_micro_key_mapping_t;

/**
 * Resolve the fixed Codex-mode action for a zero-based physical key index.
 *
 * The seven-key Rev A and twelve-key Matrix12 targets deliberately have
 * different Codex layouts. Normal keyboard mode never calls this mapping and
 * remains Profile-driven.
 */
codex_micro_key_mapping_t codex_micro_map_key_index(
    codex_micro_key_layout_t layout, size_t key_index);

/** Return the fixed Codex key layout selected by the firmware build target. */
codex_micro_key_layout_t codex_micro_active_key_layout(void);

#ifdef __cplusplus
}
#endif
