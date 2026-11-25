#include "desktop_agent_status.h"

#include "claude_status.h"
#include "codex_micro.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"

static portMUX_TYPE s_lock = portMUX_INITIALIZER_UNLOCKED;
static desktop_agent_model_t s_model;

/* Same Matrix12 product targets as the Claude Code status source. */
bool desktop_agent_status_supported(void) { return claude_status_supported(); }

static uint64_t now_ms(void) { return (uint64_t)esp_timer_get_time() / 1000u; }

static bool normal_mode(void)
{
    return codex_micro_mode() == CODEX_MICRO_MODE_NORMAL;
}

void desktop_agent_status_set(
    const desktop_agent_state_t states[DESKTOP_AGENT_SLOT_COUNT],
    const uint32_t completion_seq[DESKTOP_AGENT_SLOT_COUNT],
    codex_agent_press_transport_t transport, bool encoder_navigation)
{
    const bool selected = normal_mode();
    const uint32_t now = (uint32_t)now_ms();
    portENTER_CRITICAL(&s_lock);
    desktop_agent_model_update(&s_model, now, states, completion_seq, selected,
                               transport, encoder_navigation);
    portEXIT_CRITICAL(&s_lock);
}

void desktop_agent_status_clear(void)
{
    portENTER_CRITICAL(&s_lock);
    desktop_agent_model_clear(&s_model);
    portEXIT_CRITICAL(&s_lock);
}

void desktop_agent_status_get(desktop_agent_model_t *snapshot)
{
    const uint32_t now = (uint32_t)now_ms();
    const bool selected = normal_mode();
    portENTER_CRITICAL(&s_lock);
    desktop_agent_model_expire(&s_model, now);
    *snapshot = s_model;
    portEXIT_CRITICAL(&s_lock);
    snapshot->navigation.valid = snapshot->navigation.valid &&
        snapshot->active && snapshot->encoder_navigation && selected &&
        snapshot->navigation.sequence != 0 &&
        (uint32_t)(now - snapshot->navigation.updated_ms) <
            DESKTOP_AGENT_NAVIGATION_TTL_MS;
    if (!snapshot->claimed) {
        for (unsigned i = 0; i < DESKTOP_AGENT_SLOT_COUNT; ++i) {
            snapshot->states[i] = DESKTOP_AGENT_UNKNOWN;
        }
    }
}

codex_micro_attention_t desktop_agent_status_take_attention(bool selected)
{
    const uint32_t now = (uint32_t)now_ms();
    portENTER_CRITICAL(&s_lock);
    const codex_micro_attention_t result =
        desktop_agent_model_take(&s_model, now, selected);
    portEXIT_CRITICAL(&s_lock);
    return result;
}

bool desktop_agent_status_take_visual_change(bool selected)
{
    const uint32_t now = (uint32_t)now_ms();
    portENTER_CRITICAL(&s_lock);
    const bool result = desktop_agent_model_take_visual(&s_model, now, selected);
    portEXIT_CRITICAL(&s_lock);
    return result;
}

bool desktop_agent_status_handle_key(unsigned agent, bool pressed)
{
    const bool selected = normal_mode();
    const uint64_t now = now_ms();
    portENTER_CRITICAL(&s_lock);
    const bool consumed =
        desktop_agent_model_key(&s_model, now, agent, pressed, selected);
    portEXIT_CRITICAL(&s_lock);
    return consumed;
}

bool desktop_agent_status_handle_encoder(int direction, bool pressed)
{
    const bool selected = normal_mode();
    const uint32_t now = (uint32_t)now_ms();
    portENTER_CRITICAL(&s_lock);
    const bool consumed = desktop_agent_model_rotate(&s_model, now, direction,
                                                     pressed, selected);
    portEXIT_CRITICAL(&s_lock);
    return consumed;
}

void desktop_agent_status_release_keys(void)
{
    portENTER_CRITICAL(&s_lock);
    desktop_agent_model_release_keys(&s_model);
    portEXIT_CRITICAL(&s_lock);
}

codex_agent_press_snapshot_t desktop_agent_status_get_press(void)
{
    const uint64_t now = now_ms();
    const bool selected = normal_mode();
    portENTER_CRITICAL(&s_lock);
    desktop_agent_model_expire(&s_model, (uint32_t)now);
    codex_agent_press_snapshot_t result =
        codex_agent_press_model_read(&s_model.press, now);
    const bool active = s_model.active;
    portEXIT_CRITICAL(&s_lock);
    if (!selected || !active) {
        result.agent = -1;
        result.transport = CODEX_AGENT_PRESS_TRANSPORT_NONE;
    }
    return result;
}
