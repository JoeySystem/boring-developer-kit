#include "claude_status_model.h"
#include <string.h>

static codex_micro_attention_t attention(claude_status_state_t state)
{
    return state == CLAUDE_STATUS_COMPLETED ? CODEX_ATTENTION_UNREAD :
        state == CLAUDE_STATUS_APPROVAL || state == CLAUDE_STATUS_REPLY
            ? CODEX_ATTENTION_ACTION_REQUIRED :
        state == CLAUDE_STATUS_ERROR ? CODEX_ATTENTION_ERROR :
        CODEX_ATTENTION_NONE;
}

const char *claude_status_state_name(claude_status_state_t state)
{
    static const char *const names[] = {
        "idle", "working", "completed", "approval", "reply", "error",
    };
    return state >= 0 && state < CLAUDE_STATUS_COUNT ? names[state] : "idle";
}

uint32_t claude_status_state_rgb(claude_status_state_t state)
{
    switch (state) {
    case CLAUDE_STATUS_WORKING: return 0x304FFEu;
    case CLAUDE_STATUS_COMPLETED: return 0x00FF4Cu;
    case CLAUDE_STATUS_APPROVAL:
    case CLAUDE_STATUS_REPLY: return 0xFF6D00u;
    case CLAUDE_STATUS_ERROR: return 0xFF0033u;
    default: return 0u;
    }
}

void claude_status_model_clear(claude_status_model_t *model)
{
    memset(model, 0, sizeof(*model));
}

void claude_status_model_expire(claude_status_model_t *model, uint32_t now_ms)
{
    if (model->active && (uint32_t)(now_ms - model->updated_ms) >=
            CLAUDE_STATUS_LEASE_MS) {
        claude_status_model_clear(model);
    }
}

static void select_source(claude_status_model_t *model, bool selected)
{
    if (!selected || selected != model->selected) {
        model->pending = CODEX_ATTENTION_NONE;
    }
    model->selected = selected;
}

void claude_status_model_update(claude_status_model_t *model, uint32_t now_ms,
    const claude_status_state_t slots[CLAUDE_STATUS_SLOT_COUNT], bool selected)
{
    claude_status_model_expire(model, now_ms);
    select_source(model, selected);
    for (unsigned i = 0; i < CLAUDE_STATUS_SLOT_COUNT; ++i) {
        /* Initial/reconnected snapshots display current state silently. */
        if (model->active && selected && slots[i] != model->slots[i]) {
            model->pending |= attention(slots[i]);
        }
        model->slots[i] = slots[i];
    }
    model->updated_ms = now_ms;
    model->active = true;
}

codex_micro_attention_t claude_status_model_take(
    claude_status_model_t *model, uint32_t now_ms, bool selected)
{
    claude_status_model_expire(model, now_ms);
    select_source(model, selected);
    const codex_micro_attention_t pending = model->pending;
    model->pending = CODEX_ATTENTION_NONE;
    return pending;
}
