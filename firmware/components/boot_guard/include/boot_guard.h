#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "boot_guard_model.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    BOOT_GUARD_REASON_NONE = 0,
    BOOT_GUARD_REASON_CRASH_LOOP,  /* BOOT_GUARD_LIMIT failed boots in a row */
    BOOT_GUARD_REASON_MANUAL,      /* boot key combination (Key 1 + Key 12) */
    BOOT_GUARD_REASON_LOW_MEMORY,  /* the previous boot ran out of internal DMA memory */
    BOOT_GUARD_REASON_BLE_NO_MEM,  /* the previous boot's BLE start-up ran out of memory */
} boot_guard_reason_t;

typedef struct {
    bool safe_mode;
    boot_guard_reason_t reason;
    uint32_t abnormal_resets;  /* in a row, including the reset before this boot */
    int32_t reset_reason;      /* esp_reset_reason() of this boot */
    bool nvs_mirror;           /* the NVS mirror could be read and written */
    bool stable_cleared;       /* this boot ran long enough to clear the count */
} boot_guard_status_t;

/* app_main, before any store is loaded. */
void boot_guard_start(void);
bool boot_guard_safe_mode(void);
/* Boot key combination, before any store is loaded: safe mode for this boot
 * only; the count is unchanged, so the next restart tries a normal boot. */
void boot_guard_request_safe_mode(void);
/*
 * Main loop: clears the count once a normal boot has run long enough; during
 * the boot phase a lasting memory exhaustion restarts into safe mode
 * (memory_low: largest free internal DMA block below
 * BOOT_GUARD_LOW_MEMORY_BYTES).
 */
void boot_guard_poll(uint32_t main_loop_ms, bool memory_low, bool update_active);
/* Restart once into safe mode (not from safe mode). Returns only in safe mode. */
void boot_guard_restart_into_safe_mode(boot_guard_reason_t reason);
void boot_guard_get_status(boot_guard_status_t *status);
const char *boot_guard_reason_name(boot_guard_reason_t reason);
const char *boot_guard_reset_reason_name(int32_t reason);

#ifdef __cplusplus
}
#endif
