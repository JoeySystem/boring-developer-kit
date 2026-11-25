#pragma once

/*
 * BLE controller start-up recovery (CP01-14C19F200A48, 2026-10-30).
 *
 * After a warm reset that board's controller init failed (NO_MEM) or asserted
 * in ROM and the interrupt watchdog reset it again, in a loop. Two bounded
 * responses, no hardware assumptions:
 *
 * - In place: a failed esp_bt_controller_init() already powers the BT domain
 *   down, so init is retried up to BLE_RECOVERY_INIT_RETRIES times.
 * - Crash-loop guard: when BLE_RECOVERY_CRASH_LOOP_LIMIT consecutive boots
 *   ended in a watchdog or panic reset while BLE was starting, the next boot
 *   skips BLE so USB stays usable. "Starting" lasts until the HID services
 *   are ready or start-up fails cleanly, which covers the controller task
 *   asserting after init has returned. The record lives in RTC memory with a
 *   magic and a check word; anything that does not verify (power-on,
 *   brownout garbage) reads as zero and not starting.
 */

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define BLE_RECOVERY_INIT_RETRIES 2u
#define BLE_RECOVERY_RETRY_DELAY_MS 100u
#define BLE_RECOVERY_CRASH_LOOP_LIMIT 2u

typedef enum {
    BLE_RECOVERY_RESULT_NONE = 0,       /* first init succeeded */
    BLE_RECOVERY_RESULT_RECOVERED_IN_PLACE,
    BLE_RECOVERY_RESULT_FAILED,         /* retries exhausted; USB only */
    BLE_RECOVERY_RESULT_SKIPPED_AFTER_CRASH_LOOP,
    BLE_RECOVERY_RESULT_SKIPPED_SAFE_MODE,  /* boot guard safe mode (item 6) */
} ble_recovery_result_t;

typedef struct {
    uint32_t magic;
    uint32_t crash_loop_count;
    uint32_t ble_starting;
    uint32_t check;
} ble_crash_guard_t;

/* False (count 0, not starting) when the record does not verify. */
bool ble_crash_guard_load(const ble_crash_guard_t *guard,
                          uint32_t *crash_loop_count, bool *ble_starting);

void ble_crash_guard_store(ble_crash_guard_t *guard, uint32_t crash_loop_count,
                           bool ble_starting);

/*
 * Count for this boot: zero after power-on or when the previous boot was not
 * starting BLE; one more (saturating at the limit) when the previous boot was
 * reset by a watchdog or panic while BLE was starting.
 */
uint32_t ble_crash_guard_next(uint32_t count, bool power_on_reset,
                              bool crash_reset, bool previous_ble_starting);

bool ble_crash_guard_skip_ble(uint32_t count);

const char *ble_recovery_result_name(ble_recovery_result_t result);

#ifdef __cplusplus
}
#endif
