#pragma once

/* Locked, clock-driven wrapper around desktop_agent_status_model. */

#include "desktop_agent_status_model.h"

bool desktop_agent_status_supported(void);
void desktop_agent_status_set(
    const desktop_agent_state_t states[DESKTOP_AGENT_SLOT_COUNT],
    const uint32_t completion_seq[DESKTOP_AGENT_SLOT_COUNT],
    codex_agent_press_transport_t transport, bool encoder_navigation);
void desktop_agent_status_clear(void);
/* Expired leases read back as unclaimed with six unknown slots. */
void desktop_agent_status_get(desktop_agent_model_t *snapshot);
codex_micro_attention_t desktop_agent_status_take_attention(bool selected);
bool desktop_agent_status_take_visual_change(bool selected);
bool desktop_agent_status_handle_key(unsigned agent, bool pressed);
bool desktop_agent_status_handle_encoder(int direction, bool pressed);
void desktop_agent_status_release_keys(void);
codex_agent_press_snapshot_t desktop_agent_status_get_press(void);
