#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "liveness_model.h"

#ifdef __cplusplus
extern "C" {
#endif

/*
 * Timeouts: the slowest normal path of each channel plus a wide margin, to
 * be confirmed with the measured max_gap_ms (GET_STATUS.liveness) on device.
 * - main loop: a config activation (NVS write of a 32 KB config) or a
 *   restart notice is well under 2 s.
 * - USB config rx: OTA writes erase sector by sector; one FW_DATA or a
 *   config/prompt/icon flash write is well under 3 s.
 * - USB stack / BLE host (report only): queued probe work normally runs
 *   within milliseconds.
 * The task watchdog adds its own 5 s once the supervisor stops feeding it.
 */
#define LIVENESS_MAIN_TIMEOUT_MS 20000u
#define LIVENESS_USB_RX_TIMEOUT_MS 30000u
#define LIVENESS_USB_STACK_TIMEOUT_MS 15000u
#define LIVENESS_BLE_HOST_TIMEOUT_MS 30000u
#define LIVENESS_SUPERVISOR_PERIOD_MS 1000u
#define LIVENESS_PROBE_PERIOD_MS 2000u

/*
 * Queues work that calls liveness_beat(channel) on the probed task. Returns
 * false when the task is not running (driver not installed, BLE off), which
 * leaves the channel unmonitored for this round.
 */
typedef bool (*liveness_probe_t)(void);

typedef struct {
    bool supervising;            /* supervisor task runs */
    bool task_watchdog;          /* and is subscribed to the task watchdog */
    int previous_stall;          /* channel that stalled before the last reset */
    uint32_t previous_stall_uptime_ms;
    uint32_t beats[LIVENESS_CHANNEL_COUNT];
    bool armed[LIVENESS_CHANNEL_COUNT];
    bool resets[LIVENESS_CHANNEL_COUNT];
    uint32_t max_gap_ms[LIVENESS_CHANNEL_COUNT];
    uint32_t overdue[LIVENESS_CHANNEL_COUNT]; /* late episodes this boot */
} liveness_status_t;

/* First thing in app_main: starts the supervisor (nothing armed yet). */
void liveness_start(void);
void liveness_arm(liveness_channel_t channel, uint32_t timeout_ms);
void liveness_disarm(liveness_channel_t channel);
/* Any task. */
void liveness_beat(liveness_channel_t channel);
/* Registers a probe; the channel is armed by the probe's first answer and
 * only reports late answers (never resets). */
void liveness_set_probe(liveness_channel_t channel, liveness_probe_t probe,
                        uint32_t timeout_ms);
void liveness_get_status(liveness_status_t *status);

#ifdef __cplusplus
}
#endif
