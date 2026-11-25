#include "boot_combo_model.h"

static boot_combo_state_t set(boot_combo_t *combo, boot_combo_state_t state,
                              uint32_t now_ms)
{
    combo->state = state;
    combo->since_ms = now_ms;
    return state;
}

boot_combo_state_t boot_combo_update(boot_combo_t *combo, bool key_1, bool key_12,
                                     uint32_t now_ms, bool at_boot)
{
    if (combo == 0) {
        return BOOT_COMBO_CANCELLED;
    }
    const bool both = key_1 && key_12;
    const bool none = !key_1 && !key_12;
    switch (combo->state) {
    case BOOT_COMBO_IDLE:
        if (both) {
            return set(combo, BOOT_COMBO_HOLDING, now_ms);
        }
        return at_boot ? set(combo, BOOT_COMBO_CANCELLED, now_ms) : BOOT_COMBO_IDLE;
    case BOOT_COMBO_HOLDING:
        if (!both) {
            return set(combo, at_boot ? BOOT_COMBO_CANCELLED : BOOT_COMBO_IDLE, now_ms);
        }
        if (now_ms - combo->since_ms >= BOOT_COMBO_HOLD_MS) {
            return set(combo, BOOT_COMBO_ARMED, now_ms);
        }
        return BOOT_COMBO_HOLDING;
    case BOOT_COMBO_ARMED:
        if (none) {
            return set(combo, BOOT_COMBO_CONFIRMED, now_ms);
        }
        if (now_ms - combo->since_ms >= BOOT_COMBO_RELEASE_TIMEOUT_MS) {
            return set(combo, BOOT_COMBO_CANCELLED, now_ms);
        }
        return BOOT_COMBO_ARMED;
    case BOOT_COMBO_CANCELLED:
        if (!at_boot && none) {
            return set(combo, BOOT_COMBO_IDLE, now_ms);
        }
        return BOOT_COMBO_CANCELLED;
    case BOOT_COMBO_CONFIRMED:
    default:
        return combo->state;
    }
}
