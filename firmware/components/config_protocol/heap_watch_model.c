#include "heap_watch_model.h"

#include <string.h>

#define HEAP_WATCH_MAGIC 0x48505743u /* "HPWC" */

static uint32_t record_check(const heap_watch_record_t *record)
{
    return HEAP_WATCH_MAGIC ^ record->boot_count ^ (record->events << 1) ^
           (record->last_boot << 2) ^ record->last_uptime_ms ^
           (record->last_free << 3) ^ (record->last_largest << 4) ^ 0xA5A5A5A5u;
}

static void seal(heap_watch_record_t *record)
{
    record->magic = HEAP_WATCH_MAGIC;
    record->check = record_check(record);
}

void heap_watch_record_boot(heap_watch_record_t *record, bool power_on)
{
    if (power_on || record->magic != HEAP_WATCH_MAGIC ||
        record->check != record_check(record)) {
        memset(record, 0, sizeof(*record));
    } else {
        ++record->boot_count;
    }
    seal(record);
}

bool heap_watch_sample(heap_watch_record_t *record, bool *low,
                       uint32_t uptime_ms, uint32_t free_bytes,
                       uint32_t largest_block)
{
    if (*low) {
        if (largest_block > HEAP_WATCH_LOW_EXIT_BYTES) {
            *low = false;
        }
        return false;
    }
    if (largest_block >= HEAP_WATCH_LOW_ENTER_BYTES) {
        return false;
    }
    *low = true;
    ++record->events;
    record->last_boot = record->boot_count;
    record->last_uptime_ms = uptime_ms;
    record->last_free = free_bytes;
    record->last_largest = largest_block;
    seal(record);
    return true;
}

uint32_t heap_watch_last_boots_ago(const heap_watch_record_t *record)
{
    return record->boot_count - record->last_boot;
}
