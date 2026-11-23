#include "desktop_agent_status_model.h"

#include <stddef.h>
#include <string.h>

static const char *const STATE_NAMES[DESKTOP_AGENT_STATE_COUNT] = {
    "unknown", "idle", "standby", "working", "completed",
    "approval", "reply", "error", "codex",
};

/* Same palette as the Codex task colors; standby matches Codex's white. */
static const uint32_t STATE_RGB[DESKTOP_AGENT_STATE_COUNT] = {
    0x303030u, 0x000000u, 0xffffffu, 0x304ffeu, 0x00ff4cu,
    0xff6d00u, 0xff6d00u, 0xff0033u, 0x000000u,
};

const char *desktop_agent_state_name(desktop_agent_state_t state)
{
    return (unsigned)state < DESKTOP_AGENT_STATE_COUNT
        ? STATE_NAMES[state] : "unknown";
}

bool desktop_agent_state_from_name(const char *name, desktop_agent_state_t *state)
{
    for (unsigned i = 0; name != NULL && i < DESKTOP_AGENT_STATE_COUNT; ++i) {
        if (strcmp(name, STATE_NAMES[i]) == 0) {
            *state = (desktop_agent_state_t)i;
            return true;
        }
    }
    return false;
}

uint32_t desktop_agent_state_rgb(desktop_agent_state_t state)
{
    return (unsigned)state < DESKTOP_AGENT_STATE_COUNT ? STATE_RGB[state] : 0u;
}

void desktop_agent_model_release_keys(desktop_agent_model_t *model)
{
    memset(model->held, 0, sizeof(model->held));
    codex_agent_press_model_clear(&model->press);
    model->navigation.anchor_sequence = model->navigation.sequence;
    model->navigation.agent = -1;
    model->navigation.offset = 0;
    model->navigation.valid = false;
    model->navigation.updated_ms = 0;
}

static void reset_slots(desktop_agent_model_t *model)
{
    for (unsigned i = 0; i < DESKTOP_AGENT_SLOT_COUNT; ++i) {
        model->states[i] = DESKTOP_AGENT_UNKNOWN;
        model->alerted[i] = CODEX_ATTENTION_NONE;
    }
}

void desktop_agent_model_clear(desktop_agent_model_t *model)
{
    /* A new owner must never reuse a boot-scoped key sequence. */
    const uint32_t sequence = model->press.latest.sequence;
    const uint32_t navigation_sequence = model->navigation.sequence;
    const uint32_t reset_sequence = model->reset_sequence + 1u;
    memset(model, 0, sizeof(*model));
    model->press.latest.sequence = sequence;
    model->navigation.sequence = navigation_sequence;
    model->reset_sequence = reset_sequence;
    desktop_agent_model_release_keys(model);
    reset_slots(model);
}

void desktop_agent_model_expire(desktop_agent_model_t *model, uint32_t now)
{
    if (model->active &&
        (uint32_t)(now - model->updated_ms) >= DESKTOP_AGENT_LEASE_MS) {
        model->active = false;
        model->claimed = false;
        ++model->reset_sequence;
        model->pending = CODEX_ATTENTION_NONE;
        model->visual_pending = false;
        desktop_agent_model_release_keys(model);
        reset_slots(model);
    }
}

static bool rearms(desktop_agent_state_t state)
{
    return state == DESKTOP_AGENT_WORKING || state == DESKTOP_AGENT_IDLE ||
           state == DESKTOP_AGENT_STANDBY || state == DESKTOP_AGENT_UNKNOWN;
}

static codex_micro_attention_t alert_class(desktop_agent_state_t state)
{
    return state == DESKTOP_AGENT_APPROVAL || state == DESKTOP_AGENT_REPLY
        ? CODEX_ATTENTION_ACTION_REQUIRED
        : state == DESKTOP_AGENT_ERROR ? CODEX_ATTENTION_ERROR
        : CODEX_ATTENTION_NONE;
}

void desktop_agent_model_update(desktop_agent_model_t *model, uint32_t now,
    const desktop_agent_state_t states[DESKTOP_AGENT_SLOT_COUNT],
    const uint32_t completion_seq[DESKTOP_AGENT_SLOT_COUNT], bool selected,
    codex_agent_press_transport_t transport, bool encoder_navigation)
{
    if (!model->claimed) desktop_agent_model_clear(model);
    desktop_agent_model_expire(model, now);
    if (model->encoder_navigation != encoder_navigation) {
        desktop_agent_model_release_keys(model);
    }
    model->encoder_navigation = encoder_navigation;
    if (!selected || selected != model->selected) {
        model->pending = CODEX_ATTENTION_NONE;
    }
    /* The first snapshot of a lease, or the first after mode selection,
     * only establishes a silent baseline. */
    const bool notify = model->active && model->selected && selected;
    bool visual_changed = !model->active || selected != model->selected;
    for (unsigned i = 0; i < DESKTOP_AGENT_SLOT_COUNT; ++i) {
        const desktop_agent_state_t state = states[i];
        if (state != model->states[i]) visual_changed = true;
        if (rearms(state)) model->alerted[i] = CODEX_ATTENTION_NONE;
        /* A completion counter is host evidence, never a timer. */
        if (notify && state == DESKTOP_AGENT_COMPLETED &&
            completion_seq[i] > model->completion_seq[i]) {
            model->pending |= CODEX_ATTENTION_UNREAD;
        }
        const codex_micro_attention_t alert = alert_class(state);
        if (alert != CODEX_ATTENTION_NONE && alert != model->alerted[i]) {
            if (notify) model->pending |= alert;
            model->alerted[i] = alert;
        }
        if (!model->active || completion_seq[i] > model->completion_seq[i]) {
            model->completion_seq[i] = completion_seq[i];
        }
        model->states[i] = state;
    }
    model->claimed = true;
    model->active = true;
    model->selected = selected;
    if (!selected) {
        model->visual_pending = false;
    } else if (visual_changed) {
        model->visual_pending = true;
    }
    model->transport = transport;
    model->updated_ms = now;
}

bool desktop_agent_model_take_visual(desktop_agent_model_t *model,
    uint32_t now, bool selected)
{
    desktop_agent_model_expire(model, now);
    const bool result = selected && model->selected && model->visual_pending;
    model->visual_pending = false;
    return result;
}

codex_micro_attention_t desktop_agent_model_take(desktop_agent_model_t *model,
    uint32_t now, bool selected)
{
    desktop_agent_model_expire(model, now);
    if (!selected || selected != model->selected) {
        model->pending = CODEX_ATTENTION_NONE;
    }
    model->selected = selected;
    const codex_micro_attention_t result = model->pending;
    model->pending = CODEX_ATTENTION_NONE;
    return result;
}

bool desktop_agent_model_key(desktop_agent_model_t *model, uint64_t now,
    unsigned agent, bool pressed, bool selected)
{
    desktop_agent_model_expire(model, (uint32_t)now);
    if (agent >= DESKTOP_AGENT_SLOT_COUNT || !selected || !model->claimed ||
        model->states[agent] == DESKTOP_AGENT_CODEX) {
        return false;
    }
    const bool new_press = pressed && !model->held[agent];
    model->held[agent] = pressed;
    if (model->active && new_press) {
        codex_agent_press_model_record(&model->press, (int)agent,
                                       model->transport, now);
        if (model->encoder_navigation) {
            if (++model->navigation.sequence == 0) ++model->navigation.sequence;
            model->navigation.anchor_sequence = model->navigation.sequence;
            model->navigation.agent = (int8_t)agent;
            model->navigation.offset = 0;
            model->navigation.updated_ms = (uint32_t)now;
            model->navigation.valid = true;
        }
    }
    return true;
}

bool desktop_agent_model_rotate(desktop_agent_model_t *model, uint32_t now,
    int direction, bool pressed, bool selected)
{
    desktop_agent_model_expire(model, now);
    if (!selected || !model->active || !model->encoder_navigation ||
        (direction != 1 && direction != -1)) {
        return false;
    }
    if (pressed) {
        if (++model->navigation.sequence == 0) ++model->navigation.sequence;
        model->navigation.offset = (uint8_t)((model->navigation.offset +
            DESKTOP_AGENT_SLOT_COUNT + direction) % DESKTOP_AGENT_SLOT_COUNT);
        model->navigation.updated_ms = now;
        model->navigation.valid = true;
    }
    return true;
}

uint8_t desktop_agent_model_codex_mask(const desktop_agent_model_t *model)
{
    if (!model->claimed) return (uint8_t)((1u << DESKTOP_AGENT_SLOT_COUNT) - 1u);
    uint8_t mask = 0;
    for (unsigned i = 0; i < DESKTOP_AGENT_SLOT_COUNT; ++i) {
        if (model->states[i] == DESKTOP_AGENT_CODEX) mask |= (uint8_t)(1u << i);
    }
    return mask;
}
