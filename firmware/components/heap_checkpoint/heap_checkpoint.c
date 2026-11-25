#include "heap_checkpoint.h"

#include <stdbool.h>
#include <string.h>

#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"

static heap_checkpoint_t s_entries[HEAP_CHECKPOINT_MAX];
static size_t s_count;
static portMUX_TYPE s_lock = portMUX_INITIALIZER_UNLOCKED;

void heap_checkpoint(const char *label)
{
    if (label == NULL) {
        return;
    }
    const uint32_t dma = MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA;
    const heap_checkpoint_t entry = {
        .label = label,
        .uptime_ms = (uint32_t)(esp_timer_get_time() / 1000),
        .internal_dma_free = (uint32_t)heap_caps_get_free_size(dma),
        .internal_dma_largest = (uint32_t)heap_caps_get_largest_free_block(dma),
        .internal_free = (uint32_t)heap_caps_get_free_size(
            MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT),
    };
    portENTER_CRITICAL(&s_lock);
    bool known = false;
    for (size_t index = 0; index < s_count; ++index) {
        if (s_entries[index].label == label ||
            strcmp(s_entries[index].label, label) == 0) {
            known = true;
            break;
        }
    }
    if (!known && s_count < HEAP_CHECKPOINT_MAX) {
        s_entries[s_count++] = entry;
    }
    portEXIT_CRITICAL(&s_lock);
}

size_t heap_checkpoint_list(heap_checkpoint_t *entries, size_t capacity)
{
    portENTER_CRITICAL(&s_lock);
    const size_t count = s_count < capacity ? s_count : capacity;
    memcpy(entries, s_entries, count * sizeof(*entries));
    portEXIT_CRITICAL(&s_lock);
    return count;
}
