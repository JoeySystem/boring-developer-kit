#include "ble_recovery_model.h"

#define BLE_CRASH_GUARD_MAGIC 0x424C4552u /* "BLER" */

static uint32_t guard_check(uint32_t count, uint32_t starting)
{
    return ~(BLE_CRASH_GUARD_MAGIC ^ count ^ (starting << 16));
}

bool ble_crash_guard_load(const ble_crash_guard_t *guard,
                          uint32_t *crash_loop_count, bool *ble_starting)
{
    const bool valid = guard != 0 && guard->magic == BLE_CRASH_GUARD_MAGIC &&
                       guard->crash_loop_count <= BLE_RECOVERY_CRASH_LOOP_LIMIT &&
                       guard->ble_starting <= 1u &&
                       guard->check == guard_check(guard->crash_loop_count,
                                                   guard->ble_starting);
    *crash_loop_count = valid ? guard->crash_loop_count : 0;
    *ble_starting = valid && guard->ble_starting == 1u;
    return valid;
}

void ble_crash_guard_store(ble_crash_guard_t *guard, uint32_t crash_loop_count,
                           bool ble_starting)
{
    if (guard == 0) {
        return;
    }
    if (crash_loop_count > BLE_RECOVERY_CRASH_LOOP_LIMIT) {
        crash_loop_count = BLE_RECOVERY_CRASH_LOOP_LIMIT;
    }
    guard->magic = BLE_CRASH_GUARD_MAGIC;
    guard->crash_loop_count = crash_loop_count;
    guard->ble_starting = ble_starting ? 1u : 0u;
    guard->check = guard_check(guard->crash_loop_count, guard->ble_starting);
}

uint32_t ble_crash_guard_next(uint32_t count, bool power_on_reset,
                              bool crash_reset, bool previous_ble_starting)
{
    if (power_on_reset || !crash_reset || !previous_ble_starting) {
        return 0;
    }
    return count < BLE_RECOVERY_CRASH_LOOP_LIMIT ? count + 1u
                                                 : BLE_RECOVERY_CRASH_LOOP_LIMIT;
}

bool ble_crash_guard_skip_ble(uint32_t count)
{
    return count >= BLE_RECOVERY_CRASH_LOOP_LIMIT;
}

const char *ble_recovery_result_name(ble_recovery_result_t result)
{
    switch (result) {
    case BLE_RECOVERY_RESULT_NONE:
        return "none";
    case BLE_RECOVERY_RESULT_RECOVERED_IN_PLACE:
        return "recovered_in_place";
    case BLE_RECOVERY_RESULT_FAILED:
        return "failed";
    case BLE_RECOVERY_RESULT_SKIPPED_AFTER_CRASH_LOOP:
        return "skipped_after_crash_loop";
    case BLE_RECOVERY_RESULT_SKIPPED_SAFE_MODE:
        return "skipped_safe_mode";
    }
    return "unknown";
}
