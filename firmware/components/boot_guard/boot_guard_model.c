#include "boot_guard_model.h"

#define BOOT_GUARD_MAGIC 0x42475244u /* "BGRD" */

static uint32_t record_check(uint32_t count)
{
    return ~(BOOT_GUARD_MAGIC ^ (count * 0x9E3779B9u));
}

bool boot_guard_record_load(const boot_guard_record_t *record, uint32_t *count)
{
    const bool valid = record != 0 && record->magic == BOOT_GUARD_MAGIC &&
                       record->count <= BOOT_GUARD_LIMIT &&
                       record->check == record_check(record->count);
    if (count != 0) {
        *count = valid ? record->count : 0;
    }
    return valid;
}

void boot_guard_record_store(boot_guard_record_t *record, uint32_t count)
{
    if (record == 0) {
        return;
    }
    if (count > BOOT_GUARD_LIMIT) {
        count = BOOT_GUARD_LIMIT;
    }
    record->magic = BOOT_GUARD_MAGIC;
    record->count = count;
    record->check = record_check(count);
}

boot_guard_decision_t boot_guard_decide(uint32_t previous_count, bool abnormal_reset)
{
    if (previous_count > BOOT_GUARD_LIMIT) {
        previous_count = BOOT_GUARD_LIMIT;
    }
    boot_guard_decision_t decision;
    decision.count = previous_count;
    if (abnormal_reset && decision.count < BOOT_GUARD_LIMIT) {
        ++decision.count;
    }
    decision.safe_mode = decision.count >= BOOT_GUARD_LIMIT;
    decision.stored_count = decision.safe_mode ? BOOT_GUARD_LIMIT - 1u : decision.count;
    return decision;
}

bool boot_guard_stable(bool safe_mode, uint32_t main_loop_ms)
{
    return !safe_mode && main_loop_ms >= BOOT_GUARD_STABLE_MS;
}

bool boot_guard_low_memory_due(boot_guard_low_memory_t *state, bool memory_low,
                               uint32_t main_loop_ms, bool safe_mode, bool update_active)
{
    if (state == 0) {
        return false;
    }
    if (!memory_low || safe_mode || main_loop_ms >= BOOT_GUARD_STABLE_MS) {
        state->low = false;
        return false;
    }
    if (!state->low) {
        state->low = true;
        state->low_since_ms = main_loop_ms;
    }
    return !update_active &&
           main_loop_ms - state->low_since_ms >= BOOT_GUARD_LOW_MEMORY_MS;
}
