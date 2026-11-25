#pragma once

/*
 * Desktop Agent status source (`desktop`; pure logic, host-testable).
 *
 * Console aggregates tasks from desktop apps (Doubao, Claude, WorkBuddy...)
 * into six slots for Agent keys 1, 2, 4, 5, 6 and 7 and leases them with
 * SET_AGENT_STATUS. The firmware never knows which app owns a slot. A slot in
 * state `codex` is yielded to the official Codex path: its light, key and
 * reminders stay Codex's. Contract: protocol.md "Desktop Agent status".
 */

#include <stdbool.h>
#include <stdint.h>

#include "codex_agent_press_model.h"
#include "codex_micro_attention_model.h"

#define DESKTOP_AGENT_SLOT_COUNT 6u
#define DESKTOP_AGENT_LEASE_MS 5000u
#define DESKTOP_AGENT_NAVIGATION_TTL_MS 2000u

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    DESKTOP_AGENT_UNKNOWN = 0,
    DESKTOP_AGENT_IDLE,
    DESKTOP_AGENT_STANDBY,
    DESKTOP_AGENT_WORKING,
    DESKTOP_AGENT_COMPLETED,
    DESKTOP_AGENT_APPROVAL,
    DESKTOP_AGENT_REPLY,
    DESKTOP_AGENT_ERROR,
    DESKTOP_AGENT_CODEX,
    DESKTOP_AGENT_STATE_COUNT,
} desktop_agent_state_t;

typedef struct {
    uint32_t sequence;
    uint32_t anchor_sequence;
    uint32_t updated_ms;
    int8_t agent;
    uint8_t offset;
    bool valid;
} desktop_agent_navigation_t;

typedef struct {
    bool claimed;
    bool active;
    bool selected;
    uint32_t updated_ms;
    /* Bumped on every clear/expiry so callers can cancel motor work. */
    uint32_t reset_sequence;
    desktop_agent_state_t states[DESKTOP_AGENT_SLOT_COUNT];
    uint32_t completion_seq[DESKTOP_AGENT_SLOT_COUNT];
    /* Last approval/reply or error reminder per slot; working, idle,
     * standby and unknown rearm it. */
    codex_micro_attention_t alerted[DESKTOP_AGENT_SLOT_COUNT];
    codex_micro_attention_t pending;
    bool visual_pending;
    codex_agent_press_transport_t transport;
    bool held[DESKTOP_AGENT_SLOT_COUNT];
    codex_agent_press_model_t press;
    bool encoder_navigation;
    desktop_agent_navigation_t navigation;
} desktop_agent_model_t;

const char *desktop_agent_state_name(desktop_agent_state_t state);
bool desktop_agent_state_from_name(const char *name, desktop_agent_state_t *state);
/* 0xRRGGBB for a desktop-owned slot; meaningless for DESKTOP_AGENT_CODEX. */
uint32_t desktop_agent_state_rgb(desktop_agent_state_t state);

void desktop_agent_model_clear(desktop_agent_model_t *model);
void desktop_agent_model_expire(desktop_agent_model_t *model, uint32_t now);
void desktop_agent_model_update(desktop_agent_model_t *model, uint32_t now,
    const desktop_agent_state_t states[DESKTOP_AGENT_SLOT_COUNT],
    const uint32_t completion_seq[DESKTOP_AGENT_SLOT_COUNT], bool selected,
    codex_agent_press_transport_t transport, bool encoder_navigation);
codex_micro_attention_t desktop_agent_model_take(desktop_agent_model_t *model,
    uint32_t now, bool selected);
bool desktop_agent_model_take_visual(desktop_agent_model_t *model,
    uint32_t now, bool selected);
/* True when the lease owns this key (slot not yielded to Codex). */
bool desktop_agent_model_key(desktop_agent_model_t *model, uint64_t now,
    unsigned agent, bool pressed, bool selected);
void desktop_agent_model_release_keys(desktop_agent_model_t *model);
bool desktop_agent_model_rotate(desktop_agent_model_t *model, uint32_t now,
    int direction, bool pressed, bool selected);
/* Slots yielded to Codex while the lease is active; all six when inactive. */
uint8_t desktop_agent_model_codex_mask(const desktop_agent_model_t *model);

#ifdef __cplusplus
}
#endif
