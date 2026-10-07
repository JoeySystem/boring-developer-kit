#pragma once

#include <stdbool.h>
#include <stdint.h>

#define CODEX_AGENT_PRESS_LIFETIME_MS 2000u

typedef enum {
    CODEX_AGENT_PRESS_TRANSPORT_NONE = 0,
    CODEX_AGENT_PRESS_TRANSPORT_USB,
    CODEX_AGENT_PRESS_TRANSPORT_BLE,
} codex_agent_press_transport_t;

typedef struct {
    uint32_t sequence;
    int agent; /* -1 means no live notification; otherwise zero-based 0..5. */
    codex_agent_press_transport_t transport;
} codex_agent_press_snapshot_t;

typedef struct {
    codex_agent_press_snapshot_t latest;
    uint64_t recorded_at_ms;
} codex_agent_press_model_t;

void codex_agent_press_model_init(codex_agent_press_model_t *model);
/** Invalidate an event without reusing its sequence on reconnect/mode change. */
void codex_agent_press_model_clear(codex_agent_press_model_t *model);
/** Call only after a new physical Agent down edge was successfully transmitted. */
void codex_agent_press_model_record(codex_agent_press_model_t *model, int agent,
                                    codex_agent_press_transport_t transport,
                                    uint64_t now_ms);
/** Non-destructive: repeated status reads never consume or extend the event. */
codex_agent_press_snapshot_t codex_agent_press_model_read(
    const codex_agent_press_model_t *model, uint64_t now_ms);
