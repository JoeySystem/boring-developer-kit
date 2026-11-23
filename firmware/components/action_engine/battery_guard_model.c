#include "battery_guard_model.h"

#include <stddef.h>

#define BATTERY_GUARD_BREATH_PERIOD_MS 4000U

void battery_guard_model_init(battery_guard_model_t *model)
{
    if (model == NULL) return;
    *model = (battery_guard_model_t){ .level = BATTERY_GUARD_NORMAL };
}

bool battery_guard_sample_usable(const battery_guard_sample_t *sample)
{
    return sample != NULL && sample->valid &&
           sample->voltage_mv >= BATTERY_GUARD_VALID_MIN_MV &&
           sample->voltage_mv <= BATTERY_GUARD_VALID_MAX_MV;
}

static bool critical_condition(const battery_guard_sample_t *sample)
{
    return sample->voltage_mv < BATTERY_GUARD_CRITICAL_MV ||
           (sample->soc_percent <= BATTERY_GUARD_CRITICAL_PERCENT &&
            sample->voltage_mv < BATTERY_GUARD_CRITICAL_SOC_GUARD_MV);
}

static bool low_enter_condition(const battery_guard_sample_t *sample)
{
    return sample->soc_percent <= BATTERY_GUARD_LOW_ENTER_PERCENT ||
           sample->voltage_mv < BATTERY_GUARD_LOW_ENTER_MV;
}

static bool low_exit_condition(const battery_guard_sample_t *sample)
{
    return sample->soc_percent >= BATTERY_GUARD_LOW_EXIT_PERCENT &&
           sample->voltage_mv >= BATTERY_GUARD_LOW_EXIT_MV;
}

bool battery_guard_model_update(battery_guard_model_t *model, uint32_t now_ms,
                                const battery_guard_sample_t *sample,
                                bool external_power)
{
    if (model == NULL) return false;
    const battery_guard_level_t previous = model->level;
    if (model->level == BATTERY_GUARD_CRITICAL ||
        model->level == BATTERY_GUARD_USB_LOW) {
        return false; /* Latched until the caller powers off or reboots. */
    }
    if (external_power) {
        model->critical_pending = false;
        model->level = BATTERY_GUARD_NORMAL;
        if (!battery_guard_sample_usable(sample) ||
            sample->voltage_mv >= BATTERY_GUARD_USB_LOW_MV) {
            model->usb_low_pending = false;
        } else if (!model->usb_low_pending) {
            model->usb_low_pending = true;
            model->usb_low_since_ms = now_ms;
        } else if (now_ms - model->usb_low_since_ms >=
                   BATTERY_GUARD_USB_LOW_HOLD_MS) {
            model->level = BATTERY_GUARD_USB_LOW;
        }
        return model->level != previous;
    }
    model->usb_low_pending = false;
    if (!battery_guard_sample_usable(sample)) {
        /* A bad read neither raises nor clears a state, and restarts the
         * critical hold so it always spans consecutive good samples. */
        model->critical_pending = false;
        return false;
    }
    if (critical_condition(sample)) {
        if (!model->critical_pending) {
            model->critical_pending = true;
            model->critical_since_ms = now_ms;
        } else if (now_ms - model->critical_since_ms >=
                   BATTERY_GUARD_CRITICAL_HOLD_MS) {
            model->level = BATTERY_GUARD_CRITICAL;
            return true;
        }
        /* While a critical hold runs the warning is already justified. */
        model->level = BATTERY_GUARD_LOW;
        return model->level != previous;
    }
    model->critical_pending = false;
    if (low_enter_condition(sample)) {
        model->level = BATTERY_GUARD_LOW;
    } else if (model->level == BATTERY_GUARD_LOW && low_exit_condition(sample)) {
        model->level = BATTERY_GUARD_NORMAL;
    }
    return model->level != previous;
}

battery_guard_boot_t battery_guard_boot_decide(bool brownout_reset,
                                               bool lock_set,
                                               const battery_guard_sample_t *sample)
{
    battery_guard_boot_t decision = {0};
    const bool usable = battery_guard_sample_usable(sample);
    if (brownout_reset) {
        /* The supply already collapsed once; a missing sample is not proof
         * of a healthy cell here. */
        decision.block = !usable ||
                         sample->voltage_mv < BATTERY_GUARD_BROWNOUT_BOOT_MIN_MV;
        return decision;
    }
    if (!usable) {
        /* Keep the existing rule: an unavailable first sample is not OFF. */
        return decision;
    }
    const uint16_t minimum = lock_set ? BATTERY_GUARD_LOCKED_BOOT_MIN_MV
                                      : BATTERY_GUARD_BOOT_MIN_MV;
    decision.block = sample->voltage_mv < minimum;
    decision.clear_lock = lock_set && !decision.block;
    return decision;
}

bool battery_guard_model_resume_ready(battery_guard_model_t *model,
                                      uint32_t now_ms,
                                      const battery_guard_sample_t *sample)
{
    if (model == NULL) return false;
    if (!battery_guard_sample_usable(sample) ||
        sample->voltage_mv < BATTERY_GUARD_RESUME_MV) {
        model->resume_pending = false;
        return false;
    }
    if (!model->resume_pending) {
        model->resume_pending = true;
        model->resume_since_ms = now_ms;
        return false;
    }
    return now_ms - model->resume_since_ms >= BATTERY_GUARD_RESUME_HOLD_MS;
}

uint8_t battery_guard_breath_level(uint32_t now_ms, uint8_t max_level)
{
    const uint32_t half = BATTERY_GUARD_BREATH_PERIOD_MS / 2U;
    const uint32_t phase = now_ms % BATTERY_GUARD_BREATH_PERIOD_MS;
    const uint32_t ramp = phase < half ? phase : BATTERY_GUARD_BREATH_PERIOD_MS - phase;
    /* Quadratic ease keeps the dim end long, like a slow breath. Floor 1/8. */
    const uint32_t eased = (ramp * ramp) / half; /* 0..half */
    const uint32_t floor_level = max_level / 8U;
    return (uint8_t)(floor_level +
                     ((uint32_t)(max_level - floor_level) * eased) / half);
}
