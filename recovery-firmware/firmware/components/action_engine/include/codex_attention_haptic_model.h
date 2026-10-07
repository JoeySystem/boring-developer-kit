#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "codex_micro_attention_model.h"

/* Longer than control feedback; within the Power V2 motor pulse limit. */
#define CODEX_ATTENTION_HAPTIC_DURATION_MS 250u
#define CODEX_ATTENTION_HAPTIC_GAP_MS 300u
#define CODEX_ATTENTION_HAPTIC_MERGE_MS 300u
#define CODEX_ATTENTION_HAPTIC_QUIET_MS 3000u

typedef struct {
    uint32_t deadline_ms;
    uint32_t quiet_until_ms;
    uint8_t pending;
    uint8_t remaining;
    bool active;
    bool pulse_on;
    bool collecting;
    bool cooldown;
} codex_attention_haptic_model_t;

typedef enum {
    CODEX_ATTENTION_HAPTIC_IDLE = 0,
    CODEX_ATTENTION_HAPTIC_PULSE,
    CODEX_ATTENTION_HAPTIC_STOP,
} codex_attention_haptic_event_t;

void codex_attention_haptic_model_init(codex_attention_haptic_model_t *model);

/*
 * Non-blocking 1/2/3-pulse sequence. Concurrent reasons coalesce to the most
 * urgent (error > action required > unread) over a 300 ms collection window.
 * Arrivals during playback and the following 3 seconds are dropped, not queued.
 * Disabled output drops both the current sequence and pending notifications.
 */
codex_attention_haptic_event_t codex_attention_haptic_model_poll(
    codex_attention_haptic_model_t *model, uint32_t now_ms,
    codex_micro_attention_t notifications, bool enabled);
