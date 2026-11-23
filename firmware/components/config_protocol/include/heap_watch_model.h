#pragma once

#include <stdbool.h>
#include <stdint.h>

/*
 * Low-memory watch for the DMA-capable internal heap. A record of the last
 * low-memory episode survives software resets (the caller keeps it in RTC
 * memory) so a unit that recovered by restarting can still report it.
 * Hysteresis: an episode starts when the largest free block drops below
 * HEAP_WATCH_LOW_ENTER_BYTES and ends once it is back above
 * HEAP_WATCH_LOW_EXIT_BYTES.
 */
#define HEAP_WATCH_LOW_ENTER_BYTES 4096u
#define HEAP_WATCH_LOW_EXIT_BYTES 8192u

typedef struct {
    uint32_t magic;
    uint32_t boot_count;
    uint32_t events;
    uint32_t last_boot;
    uint32_t last_uptime_ms;
    uint32_t last_free;
    uint32_t last_largest;
    uint32_t check;
} heap_watch_record_t;

/* Validates or resets the record; a power-on reset always starts fresh. */
void heap_watch_record_boot(heap_watch_record_t *record, bool power_on);

/* Feeds one sample. Returns true when it starts a new low-memory episode. */
bool heap_watch_sample(heap_watch_record_t *record, bool *low,
                       uint32_t uptime_ms, uint32_t free_bytes,
                       uint32_t largest_block);

/* Boots since the last episode (0 = this boot). Only valid when events > 0. */
uint32_t heap_watch_last_boots_ago(const heap_watch_record_t *record);
