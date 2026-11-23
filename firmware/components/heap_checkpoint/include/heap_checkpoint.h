#pragma once

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/*
 * Internal heap readings at named points: after each start-up step and at
 * the first USB mount, BLE connection and BLE authentication. Consecutive
 * entries show how much DMA-capable internal RAM each step takes, without a
 * UART console. A label is recorded once; later calls with it are ignored.
 * Labels must be string literals (the pointer is kept).
 */
#define HEAP_CHECKPOINT_MAX 20u

typedef struct {
    const char *label;
    uint32_t uptime_ms;
    uint32_t internal_dma_free;
    uint32_t internal_dma_largest;
    uint32_t internal_free;
} heap_checkpoint_t;

void heap_checkpoint(const char *label);
/* Copies up to capacity entries in recording order; returns the count. */
size_t heap_checkpoint_list(heap_checkpoint_t *entries, size_t capacity);

#ifdef __cplusplus
}
#endif
