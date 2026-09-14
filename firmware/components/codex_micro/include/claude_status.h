#pragma once

#include "claude_status_model.h"

/* Volatile USB CDC state, independent of Codex Vendor HID task colors.
 * Callbacks only update RAM; the Action Engine remains the hardware owner. */
bool claude_status_supported(void);
void claude_status_set(const claude_status_state_t slots[CLAUDE_STATUS_SLOT_COUNT]);
void claude_status_clear(void);
void claude_status_get(claude_status_model_t *snapshot);
codex_micro_attention_t claude_status_take_attention(bool selected);
