#pragma once

/*
 * Stability item 5: a generic boot crash-loop guard (pure logic).
 *
 * Every boot that follows an abnormal reset (panic, interrupt/task/other
 * watchdog, CPU lock-up) counts one more; BOOT_GUARD_LIMIT in a row starts
 * the next boot in safe mode (item 6). A boot that runs normally for
 * BOOT_GUARD_STABLE_MS clears the count. Power-on, software, brownout, USB
 * and external resets neither count nor clear.
 *
 * A safe-mode boot stores BOOT_GUARD_LIMIT - 1, so restarting (power off/on,
 * a host REBOOT) tries a normal boot, and one more abnormal reset of that
 * boot returns to safe mode at once.
 *
 * The count lives in RTC memory (magic and check word) and is mirrored to
 * NVS so a power cut between crashes does not lose it. It belongs to the
 * running image (first ELF SHA-256 bytes): another image starts from zero.
 *
 * A boot that does not crash but is unusable for lack of memory (the
 * returned unit: the DMA-capable internal heap was gone right after
 * start-up and every request was refused) restarts once straight into safe
 * mode (review A1): when the largest free internal DMA block stays below
 * BOOT_GUARD_LOW_MEMORY_BYTES for BOOT_GUARD_LOW_MEMORY_MS during the boot
 * phase, or BLE start-up fails with ESP_ERR_NO_MEM. Never in safe mode or
 * during a firmware update; one restart per boot, and the safe-mode boot
 * never triggers it, so it cannot loop by itself.
 */

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define BOOT_GUARD_LIMIT 3u
#define BOOT_GUARD_STABLE_MS 60000u
/* Same threshold as the GET_STATUS heap watch low-memory episode. */
#define BOOT_GUARD_LOW_MEMORY_BYTES 4096u
#define BOOT_GUARD_LOW_MEMORY_MS 10000u

typedef struct {
    bool low;
    uint32_t low_since_ms;
} boot_guard_low_memory_t;

typedef struct {
    uint32_t magic;
    uint32_t count;
    uint32_t check;
} boot_guard_record_t;

typedef struct {
    uint32_t count;         /* abnormal resets in a row, including this one */
    bool safe_mode;         /* this boot runs in safe mode */
    uint32_t stored_count;  /* what to keep for the next boot */
} boot_guard_decision_t;

/* False (count 0) when the record does not verify. */
bool boot_guard_record_load(const boot_guard_record_t *record, uint32_t *count);
void boot_guard_record_store(boot_guard_record_t *record, uint32_t count);

/*
 * previous_count: the RTC record when it verifies after a reset that keeps
 * RTC memory, otherwise the NVS mirror (0 when absent).
 */
boot_guard_decision_t boot_guard_decide(uint32_t previous_count, bool abnormal_reset);

/* True when this boot should record a low-memory failure and restart. */
bool boot_guard_low_memory_due(boot_guard_low_memory_t *state, bool memory_low,
                               uint32_t main_loop_ms, bool safe_mode, bool update_active);

/* True once a normal boot has run long enough to clear the count. */
bool boot_guard_stable(bool safe_mode, uint32_t main_loop_ms);

#ifdef __cplusplus
}
#endif
