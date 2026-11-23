#pragma once

/*
 * When a host may cancel OTA rollback (F-01; pure logic, host-testable).
 *
 * Device authentication is one-way: the device proves itself to the host and
 * keeps no host identity. "Authenticated session" therefore means this
 * session completed HELLO and the device answered an AUTH_CHALLENGE. A unit
 * without provisioned identity cannot sign, so HELLO alone suffices there.
 */

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    bool hello;
    bool authenticated;
} firmware_confirm_session_t;

typedef enum {
    FIRMWARE_CONFIRM_ALLOWED = 0,
    FIRMWARE_CONFIRM_NOT_AUTHENTICATED,
    FIRMWARE_CONFIRM_BUILD_MISMATCH,
    FIRMWARE_CONFIRM_PARTITION_MISMATCH,
} firmware_confirm_check_t;

void firmware_confirm_session_reset(firmware_confirm_session_t *session);

/* A fresh HELLO starts a new logical connection: authentication restarts. */
void firmware_confirm_session_hello(firmware_confirm_session_t *session);

/* The device signed an AUTH_CHALLENGE in this session. Ignored before HELLO. */
void firmware_confirm_session_authenticated(firmware_confirm_session_t *session);

bool firmware_confirm_session_trusted(const firmware_confirm_session_t *session,
                                      bool identity_ready);

/* Compatibility path for hosts without FW_CONFIRM (Console 0.2.x and the
 * Windows updater, which does not authenticate): a USB session that completed
 * HELLO and now asks GET_STATUS proves the configuration channel. No
 * authentication is required here; FW_CONFIRM still requires it. */
bool firmware_confirm_auto_due(const firmware_confirm_session_t *session,
                               bool usb_transport, bool pending);

/* FW_CONFIRM: trust first, then the host's build and partition must match. */
firmware_confirm_check_t firmware_confirm_explicit_check(
    const firmware_confirm_session_t *session, bool identity_ready,
    const char *requested_build_id, const char *running_build_id,
    const char *requested_partition, const char *running_partition);

/*
 * Stability item 4: a host's proof alone no longer confirms a fresh image.
 * The proof (HELLO + GET_STATUS on USB, or an accepted FW_CONFIRM) approves
 * it; the image is confirmed once the device has also been healthy (main
 * loop running, USB mounted, not in safe mode) without a break for
 * FIRMWARE_HEALTH_DWELL_MS. BLE readiness is reported, not required
 * (review A4).
 * Approval is kept for the boot, so a host that asked once is enough; the
 * main loop confirms when the dwell completes. Without a host's proof the
 * image is never confirmed: a broken USB configuration channel must roll
 * back, because users cannot reach the ROM download mode.
 */
#define FIRMWARE_HEALTH_DWELL_MS 10000u
/* Approved but not confirmed this long: FW_STATUS reports it as stuck. */
#define FIRMWARE_HEALTH_STUCK_MS 120000u

typedef enum {
    FIRMWARE_WAIT_NONE = 0,       /* not pending, or confirming now */
    FIRMWARE_WAIT_NOT_APPROVED,   /* no host has proved the USB channel yet */
    FIRMWARE_WAIT_SAFE_MODE,
    FIRMWARE_WAIT_USB_NOT_MOUNTED,
    FIRMWARE_WAIT_DWELL,          /* healthy; counting down */
} firmware_wait_reason_t;

firmware_wait_reason_t firmware_wait_reason(bool pending, bool approved, bool safe_mode,
                                            bool usb_mounted, bool ready);
const char *firmware_wait_reason_name(firmware_wait_reason_t reason);

typedef struct {
    bool approved;
    bool approved_by_host;  /* FW_CONFIRM, otherwise the GET_STATUS session */
    bool healthy;
    uint32_t healthy_since_ms;
    uint32_t approved_at_ms;
} firmware_health_gate_t;

void firmware_health_gate_approve(firmware_health_gate_t *gate, bool by_host,
                                  uint32_t now_ms);
/* Approved, still not confirmed after FIRMWARE_HEALTH_STUCK_MS. */
bool firmware_health_gate_stuck(const firmware_health_gate_t *gate, bool pending,
                                uint32_t now_ms);
void firmware_health_gate_observe(firmware_health_gate_t *gate, bool healthy,
                                  uint32_t now_ms);
bool firmware_health_gate_ready(const firmware_health_gate_t *gate, uint32_t now_ms);
/* Approved and ready: confirm now. */
bool firmware_health_gate_due(const firmware_health_gate_t *gate, uint32_t now_ms);
/* Time until ready if the device stays healthy; UINT32_MAX while unhealthy. */
uint32_t firmware_health_gate_remaining_ms(const firmware_health_gate_t *gate,
                                           uint32_t now_ms);

#ifdef __cplusplus
}
#endif
