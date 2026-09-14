#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define CODEX_MICRO_TASK_COUNT 6u
#define CODEX_ATTENTION_SYNC_MS 3000u

/* Bit values allow concurrent task notifications to be coalesced. */
typedef enum {
    CODEX_ATTENTION_NONE = 0,
    CODEX_ATTENTION_UNREAD = 1u << 0,
    CODEX_ATTENTION_ACTION_REQUIRED = 1u << 1,
    CODEX_ATTENTION_ERROR = 1u << 2,
} codex_micro_attention_t;

typedef struct {
    uint32_t sync_started_ms;
    uint8_t initialized_mask;
    uint8_t last_attention[CODEX_MICRO_TASK_COUNT];
} codex_micro_attention_model_t;

#ifdef __cplusplus
extern "C" {
#endif

void codex_micro_attention_model_init(codex_micro_attention_model_t *model);

/**
 * Observe one raw host task color.
 *
 * The first value for each slot and the first 3 seconds of a source session
 * establish state silently, including off/working followed by old alerts.
 * Repeated attention states are silent. A different attention reason notifies
 * again; working/off rearms the same reason. Unknown colors do not rearm.
 */
codex_micro_attention_t codex_micro_attention_model_observe(
    codex_micro_attention_model_t *model, size_t slot, uint32_t rgb,
    uint32_t now_ms);

#ifdef __cplusplus
}
#endif
