#pragma once

/*
 * Key combination with a screen prompt and release to confirm (pure logic).
 *
 * Key 1 (row 0, column 1) and Key 12 (row 3, column 2) share no matrix row
 * or column and involve no knob, so they cannot ghost and do not overlap the
 * power gestures (battery power-on itself holds the knob).
 *
 * - Boot (stability item 6b): both keys already held at the first scan,
 *   before board_init, and kept for BOOT_COMBO_HOLD_MS -> ARMED; the device
 *   then shows a prompt and the combination takes effect on release (safe
 *   mode for that boot). Releasing early, or holding past the release
 *   timeout, boots normally.
 * - In safe mode (stability item 9): the same combination, pressed any time,
 *   prompts for the ROM download mode and enters it on release.
 */

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define BOOT_COMBO_HOLD_MS 3000u
#define BOOT_COMBO_RELEASE_TIMEOUT_MS 30000u

typedef enum {
    BOOT_COMBO_IDLE = 0,
    BOOT_COMBO_HOLDING,
    BOOT_COMBO_ARMED,      /* show the prompt; waiting for release */
    BOOT_COMBO_CONFIRMED,  /* released after the prompt: act */
    BOOT_COMBO_CANCELLED,
} boot_combo_state_t;

typedef struct {
    boot_combo_state_t state;
    uint32_t since_ms;
} boot_combo_t;

/*
 * at_boot: the combination counts only if held at the first sample, and
 * every outcome is final. Otherwise (safe mode) it may start any time and a
 * cancelled attempt resets once both keys are up.
 */
boot_combo_state_t boot_combo_update(boot_combo_t *combo, bool key_1, bool key_12,
                                     uint32_t now_ms, bool at_boot);

#ifdef __cplusplus
}
#endif
