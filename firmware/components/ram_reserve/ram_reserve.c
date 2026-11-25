#include "ram_reserve.h"

#include "esp_heap_caps.h"
#include "freertos/FreeRTOS.h"

#define RESERVE_CAPS (MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA)

static void *s_reserve;
static portMUX_TYPE s_lock = portMUX_INITIALIZER_UNLOCKED;

esp_err_t ram_reserve_init(void)
{
    ram_reserve_restore();
    return ram_reserve_held() ? ESP_OK : ESP_ERR_NO_MEM;
}

void ram_reserve_release(void)
{
    portENTER_CRITICAL(&s_lock);
    void *block = s_reserve;
    s_reserve = NULL;
    portEXIT_CRITICAL(&s_lock);
    heap_caps_free(block);
}

void ram_reserve_restore(void)
{
    if (ram_reserve_held()) {
        return;
    }
    void *block = heap_caps_malloc(RAM_RESERVE_BYTES, RESERVE_CAPS);
    if (block == NULL) {
        return;
    }
    portENTER_CRITICAL(&s_lock);
    const bool already_held = s_reserve != NULL;
    if (!already_held) {
        s_reserve = block;
    }
    portEXIT_CRITICAL(&s_lock);
    if (already_held) {
        heap_caps_free(block);
    }
}

bool ram_reserve_held(void)
{
    portENTER_CRITICAL(&s_lock);
    const bool held = s_reserve != NULL;
    portEXIT_CRITICAL(&s_lock);
    return held;
}
