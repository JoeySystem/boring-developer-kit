#pragma once

#include <stdbool.h>
#include <stddef.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/*
 * Emergency DMA-capable internal RAM. Held from start-up and handed back
 * right before ESP-IDF work that must allocate from that pool and that the
 * device cannot do without: the OTA image check (esp_ota_end and
 * esp_ota_set_boot_partition hash through the PSA driver) and the device
 * identity signature. Without it a unit with an exhausted heap could still
 * reach FW_BEGIN but never finish an update. Release and restore are
 * idempotent and safe from any task.
 */
#define RAM_RESERVE_BYTES 8192u

esp_err_t ram_reserve_init(void);
void ram_reserve_release(void);
/* Best effort: re-acquires the reserve if the heap allows it. */
void ram_reserve_restore(void);
bool ram_reserve_held(void);

#ifdef __cplusplus
}
#endif
