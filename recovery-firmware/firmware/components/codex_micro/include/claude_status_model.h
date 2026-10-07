#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "codex_micro_attention_model.h"

#define CLAUDE_STATUS_LEASE_MS 5000u
#define CLAUDE_STATUS_SLOT_COUNT CODEX_MICRO_TASK_COUNT

typedef enum {
    CLAUDE_STATUS_IDLE,
    CLAUDE_STATUS_WORKING,
    CLAUDE_STATUS_COMPLETED,
    CLAUDE_STATUS_APPROVAL,
    CLAUDE_STATUS_REPLY,
    CLAUDE_STATUS_ERROR,
    CLAUDE_STATUS_COUNT,
} claude_status_state_t;

typedef struct {
    bool active;
    bool selected;
    uint32_t updated_ms;
    claude_status_state_t slots[CLAUDE_STATUS_SLOT_COUNT];
    codex_micro_attention_t pending;
} claude_status_model_t;

void claude_status_model_clear(claude_status_model_t *model);
void claude_status_model_expire(claude_status_model_t *model, uint32_t now_ms);
void claude_status_model_update(claude_status_model_t *model, uint32_t now_ms,
    const claude_status_state_t slots[CLAUDE_STATUS_SLOT_COUNT], bool selected);
codex_micro_attention_t claude_status_model_take(
    claude_status_model_t *model, uint32_t now_ms, bool selected);
const char *claude_status_state_name(claude_status_state_t state);
uint32_t claude_status_state_rgb(claude_status_state_t state);
