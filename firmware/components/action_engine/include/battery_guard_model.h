#pragma once

/*
 * Low-battery protection policy for Power V2 (pure logic, host-testable).
 *
 * Thresholds are empirical starting values (2026-10-27) pending a measured
 * discharge curve and the Power V2 power-path / LDO dropout review. Change
 * them here only; every caller and test reads these definitions.
 *
 * SOC comes from a CW2015 without a loaded battery profile, so it is treated
 * as a hint: SOC alone can raise the LOW warning, but a critical shutdown
 * also requires the cell voltage to be in the low region.
 */

#include <stdbool.h>
#include <stdint.h>

/* Plausible VCELL window; anything outside is a bad sample, never an action. */
#define BATTERY_GUARD_VALID_MIN_MV 2500U
#define BATTERY_GUARD_VALID_MAX_MV 4500U

/* LOW warning, on battery only. Exit needs both values above the exit band. */
#define BATTERY_GUARD_LOW_ENTER_PERCENT 10U
#define BATTERY_GUARD_LOW_ENTER_MV 3550U
#define BATTERY_GUARD_LOW_EXIT_PERCENT 13U
#define BATTERY_GUARD_LOW_EXIT_MV 3620U

/* CRITICAL: VCELL < 3.40 V, or SOC <= 3 % while VCELL < 3.60 V, sustained. */
#define BATTERY_GUARD_CRITICAL_MV 3400U
#define BATTERY_GUARD_CRITICAL_PERCENT 3U
#define BATTERY_GUARD_CRITICAL_SOC_GUARD_MV 3600U
#define BATTERY_GUARD_CRITICAL_HOLD_MS 10000U

/*
 * USB_LOW: on USB the cell is still in the circuit. A sagging hub hands the
 * load to the TPS2116 battery input, and a near-empty cell then browns out.
 * Below this VCELL, sustained, the caller parks in dark USB standby. Charge
 * current lifts VCELL, so a reading this low means a genuinely low cell.
 */
#define BATTERY_GUARD_USB_LOW_MV 3550U
#define BATTERY_GUARD_USB_LOW_HOLD_MS 10000U

/* Boot gate. A protective shutdown sets a lock that raises the bar. */
#define BATTERY_GUARD_BOOT_MIN_MV 3450U
#define BATTERY_GUARD_LOCKED_BOOT_MIN_MV 3600U
/* After a brownout reset the unloaded VCELL rebounds; demand a healthy cell. */
#define BATTERY_GUARD_BROWNOUT_BOOT_MIN_MV 3700U

/* Locked USB standby resumes the saved ON session once the cell recovers. */
#define BATTERY_GUARD_RESUME_MV 3700U
#define BATTERY_GUARD_RESUME_HOLD_MS 30000U

typedef enum {
    BATTERY_GUARD_NORMAL = 0,
    BATTERY_GUARD_LOW,
    BATTERY_GUARD_CRITICAL,
    BATTERY_GUARD_USB_LOW,
} battery_guard_level_t;

typedef struct {
    bool valid;
    uint16_t voltage_mv;
    uint8_t soc_percent;
} battery_guard_sample_t;

typedef struct {
    battery_guard_level_t level;
    bool critical_pending;
    uint32_t critical_since_ms;
    bool resume_pending;
    uint32_t resume_since_ms;
    bool usb_low_pending;
    uint32_t usb_low_since_ms;
} battery_guard_model_t;

typedef struct {
    bool block;      /* Do not run a full ON boot. */
    bool clear_lock; /* The cell has recovered above the locked threshold. */
} battery_guard_boot_t;

void battery_guard_model_init(battery_guard_model_t *model);

/* Plausible fuel-gauge sample (valid flag plus VCELL inside the window). */
bool battery_guard_sample_usable(const battery_guard_sample_t *sample);

/*
 * Feed one new fuel-gauge sample. external_power means a USB host is mounted
 * (the PCB has no VBUS sense). Returns true when the level changed.
 * CRITICAL and USB_LOW are latched: the caller shuts down or parks in USB
 * standby, and re-initialises the model when it resumes.
 */
bool battery_guard_model_update(battery_guard_model_t *model, uint32_t now_ms,
                                const battery_guard_sample_t *sample,
                                bool external_power);

battery_guard_boot_t battery_guard_boot_decide(bool brownout_reset,
                                               bool lock_set,
                                               const battery_guard_sample_t *sample);

/*
 * While parked in USB standby after a protective shutdown: true once VCELL
 * has stayed at or above the resume threshold for the hold time.
 */
bool battery_guard_model_resume_ready(battery_guard_model_t *model,
                                      uint32_t now_ms,
                                      const battery_guard_sample_t *sample);

/* Low-brightness red breathing for the under-key LEDs, 0..max_level. */
uint8_t battery_guard_breath_level(uint32_t now_ms, uint8_t max_level);
