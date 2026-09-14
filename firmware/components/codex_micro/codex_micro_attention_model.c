#include "codex_micro_attention_model.h"

#include <string.h>

/* Colors emitted by the current ChatGPT Codex Micro integration. */
#define CODEX_THREAD_COLOR_OFF 0x000000u
#define CODEX_THREAD_COLOR_WORKING 0x304FFEu
#define CODEX_THREAD_COLOR_UNREAD 0x00FF4Cu
#define CODEX_THREAD_COLOR_ACTION_REQUIRED 0xFF6D00u
#define CODEX_THREAD_COLOR_ERROR 0xFF0033u

void codex_micro_attention_model_init(codex_micro_attention_model_t *model)
{
    if (model != NULL) {
        memset(model, 0, sizeof(*model));
    }
}

codex_micro_attention_t codex_micro_attention_model_observe(
    codex_micro_attention_model_t *model, size_t slot, uint32_t rgb,
    uint32_t now_ms)
{
    if (model == NULL || slot >= CODEX_MICRO_TASK_COUNT) {
        return CODEX_ATTENTION_NONE;
    }

    const uint8_t slot_mask = (uint8_t)(1u << slot);
    const codex_micro_attention_t attention =
        rgb == CODEX_THREAD_COLOR_UNREAD ? CODEX_ATTENTION_UNREAD :
        rgb == CODEX_THREAD_COLOR_ACTION_REQUIRED ? CODEX_ATTENTION_ACTION_REQUIRED :
        rgb == CODEX_THREAD_COLOR_ERROR ? CODEX_ATTENTION_ERROR :
        CODEX_ATTENTION_NONE;

    if (model->initialized_mask == 0u) {
        model->sync_started_ms = now_ms;
    }
    if ((model->initialized_mask & slot_mask) == 0u ||
        (uint32_t)(now_ms - model->sync_started_ms) < CODEX_ATTENTION_SYNC_MS) {
        model->initialized_mask |= slot_mask;
        model->last_attention[slot] = (uint8_t)attention;
        return CODEX_ATTENTION_NONE;
    }

    if (rgb == CODEX_THREAD_COLOR_WORKING ||
        rgb == CODEX_THREAD_COLOR_OFF) {
        model->last_attention[slot] = CODEX_ATTENTION_NONE;
        return CODEX_ATTENTION_NONE;
    }
    if (attention == CODEX_ATTENTION_NONE ||
        model->last_attention[slot] == attention) {
        return CODEX_ATTENTION_NONE;
    }

    model->last_attention[slot] = (uint8_t)attention;
    return attention;
}
