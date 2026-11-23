#pragma once

/*
 * Stability item 3: BLE advertising never stops being retried.
 *
 * The main-loop advertising watchdog restarts advertising whenever nothing
 * holds BLE back and the device is neither connected nor advertising. It used
 * to do so every 3 s forever, each time creating a retry task (3 KB of
 * internal RAM) even when the controller kept failing, e.g. with the heap
 * exhausted. Consecutive restarts that do not lead to advertising now back
 * off (3 s three times, then 10 s, 30 s, and 60 s from then on) and a
 * deliberate key or knob input retries at once (at most once a second). Any
 * check that finds the device advertising or connected resets the backoff.
 */

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define BLE_ADV_BACKOFF_FAST_MS 3000u
#define BLE_ADV_BACKOFF_FAST_ATTEMPTS 3u
#define BLE_ADV_BACKOFF_MAX_MS 60000u
#define BLE_ADV_BACKOFF_INPUT_MIN_MS 1000u

typedef struct {
    uint32_t consecutive;   /* restarts since advertising last worked */
    uint32_t last_attempt_ms;
    uint32_t restarts;      /* every restart this boot */
    uint32_t input_restarts;
} ble_adv_backoff_t;

/* Delay before the next restart after `consecutive` failed ones. */
uint32_t ble_adv_backoff_delay_ms(uint32_t consecutive);

/* Advertising or connected: the next failure starts from the fast delay. */
void ble_adv_backoff_reset(ble_adv_backoff_t *backoff);

/*
 * True when a restart is due now; records it. `input` is a deliberate local
 * input since the last check.
 */
bool ble_adv_backoff_take(ble_adv_backoff_t *backoff, uint32_t now_ms, bool input);

#ifdef __cplusplus
}
#endif
