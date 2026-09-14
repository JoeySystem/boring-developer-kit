#include "codex_agent_press_model.h"

void codex_agent_press_model_init(codex_agent_press_model_t *model)
{
    *model = (codex_agent_press_model_t){0};
    codex_agent_press_model_clear(model);
}

void codex_agent_press_model_clear(codex_agent_press_model_t *model)
{
    model->latest.agent = -1;
    model->latest.transport = CODEX_AGENT_PRESS_TRANSPORT_NONE;
}

void codex_agent_press_model_record(codex_agent_press_model_t *model, int agent,
                                    codex_agent_press_transport_t transport,
                                    uint64_t now_ms)
{
    if (agent < 0 || agent >= 6 ||
        (transport != CODEX_AGENT_PRESS_TRANSPORT_USB &&
         transport != CODEX_AGENT_PRESS_TRANSPORT_BLE)) {
        return;
    }
    if (++model->latest.sequence == 0u) {
        ++model->latest.sequence;
    }
    model->latest.agent = agent;
    model->latest.transport = transport;
    model->recorded_at_ms = now_ms;
}

codex_agent_press_snapshot_t codex_agent_press_model_read(
    const codex_agent_press_model_t *model, uint64_t now_ms)
{
    codex_agent_press_snapshot_t snapshot = model->latest;
    if (now_ms - model->recorded_at_ms >= CODEX_AGENT_PRESS_LIFETIME_MS) {
        snapshot.agent = -1;
        snapshot.transport = CODEX_AGENT_PRESS_TRANSPORT_NONE;
    }
    return snapshot;
}
