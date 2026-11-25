#include "firmware_confirm_model.h"

#include <stddef.h>
#include <stdint.h>
#include <string.h>

void firmware_confirm_session_reset(firmware_confirm_session_t *session)
{
    if (session == NULL) return;
    session->hello = false;
    session->authenticated = false;
}

void firmware_confirm_session_hello(firmware_confirm_session_t *session)
{
    if (session == NULL) return;
    session->hello = true;
    session->authenticated = false;
}

void firmware_confirm_session_authenticated(firmware_confirm_session_t *session)
{
    if (session == NULL || !session->hello) return;
    session->authenticated = true;
}

bool firmware_confirm_session_trusted(const firmware_confirm_session_t *session,
                                      bool identity_ready)
{
    return session != NULL && session->hello &&
           (session->authenticated || !identity_ready);
}

bool firmware_confirm_auto_due(const firmware_confirm_session_t *session,
                               bool usb_transport, bool pending)
{
    return pending && usb_transport && session != NULL && session->hello;
}

static bool same_text(const char *left, const char *right)
{
    return left != NULL && right != NULL && left[0] != '\0' &&
           strcmp(left, right) == 0;
}

firmware_confirm_check_t firmware_confirm_explicit_check(
    const firmware_confirm_session_t *session, bool identity_ready,
    const char *requested_build_id, const char *running_build_id,
    const char *requested_partition, const char *running_partition)
{
    if (!firmware_confirm_session_trusted(session, identity_ready)) {
        return FIRMWARE_CONFIRM_NOT_AUTHENTICATED;
    }
    if (!same_text(requested_build_id, running_build_id)) {
        return FIRMWARE_CONFIRM_BUILD_MISMATCH;
    }
    if (!same_text(requested_partition, running_partition)) {
        return FIRMWARE_CONFIRM_PARTITION_MISMATCH;
    }
    return FIRMWARE_CONFIRM_ALLOWED;
}

void firmware_health_gate_approve(firmware_health_gate_t *gate, bool by_host,
                                  uint32_t now_ms)
{
    if (gate == NULL) return;
    if (!gate->approved) {
        gate->approved_at_ms = now_ms;
    }
    gate->approved = true;
    gate->approved_by_host = gate->approved_by_host || by_host;
}

void firmware_health_gate_observe(firmware_health_gate_t *gate, bool healthy,
                                  uint32_t now_ms)
{
    if (gate == NULL) return;
    if (healthy && !gate->healthy) {
        gate->healthy_since_ms = now_ms;
    }
    gate->healthy = healthy;
}

bool firmware_health_gate_ready(const firmware_health_gate_t *gate, uint32_t now_ms)
{
    return gate != NULL && gate->healthy &&
           (uint32_t)(now_ms - gate->healthy_since_ms) >= FIRMWARE_HEALTH_DWELL_MS;
}

bool firmware_health_gate_due(const firmware_health_gate_t *gate, uint32_t now_ms)
{
    return gate != NULL && gate->approved && firmware_health_gate_ready(gate, now_ms);
}

uint32_t firmware_health_gate_remaining_ms(const firmware_health_gate_t *gate,
                                           uint32_t now_ms)
{
    if (gate == NULL || !gate->healthy) return UINT32_MAX;
    const uint32_t elapsed = now_ms - gate->healthy_since_ms;
    return elapsed >= FIRMWARE_HEALTH_DWELL_MS ? 0u : FIRMWARE_HEALTH_DWELL_MS - elapsed;
}

bool firmware_health_gate_stuck(const firmware_health_gate_t *gate, bool pending,
                                uint32_t now_ms)
{
    return gate != NULL && pending && gate->approved &&
           (uint32_t)(now_ms - gate->approved_at_ms) >= FIRMWARE_HEALTH_STUCK_MS;
}

firmware_wait_reason_t firmware_wait_reason(bool pending, bool approved, bool safe_mode,
                                            bool usb_mounted, bool ready)
{
    if (!pending) return FIRMWARE_WAIT_NONE;
    if (safe_mode) return FIRMWARE_WAIT_SAFE_MODE;
    if (!usb_mounted) return FIRMWARE_WAIT_USB_NOT_MOUNTED;
    if (!approved) return FIRMWARE_WAIT_NOT_APPROVED;
    return ready ? FIRMWARE_WAIT_NONE : FIRMWARE_WAIT_DWELL;
}

const char *firmware_wait_reason_name(firmware_wait_reason_t reason)
{
    switch (reason) {
    case FIRMWARE_WAIT_NOT_APPROVED: return "not_approved";
    case FIRMWARE_WAIT_SAFE_MODE: return "safe_mode";
    case FIRMWARE_WAIT_USB_NOT_MOUNTED: return "usb_not_mounted";
    case FIRMWARE_WAIT_DWELL: return "health_dwell";
    default: return "none";
    }
}
