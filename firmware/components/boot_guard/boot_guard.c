#include "boot_guard.h"

#include <string.h>

#include "esp_app_desc.h"
#include "esp_attr.h"
#include "esp_log.h"
#include "esp_system.h"
#include "nvs.h"
#include "nvs_flash.h"
#include "sdkconfig.h"

#define BOOT_GUARD_NAMESPACE "wmp_boot"
#define BOOT_GUARD_KEY "crashes"
#define BOOT_GUARD_IMAGE_KEY "image"

static const char *TAG = "boot_guard";

#define SAFE_NEXT_MAGIC 0x53414645u /* "SAFE" */

typedef struct {
    uint32_t magic;
    uint32_t reason;
    uint32_t check;
} safe_next_t;

static RTC_NOINIT_ATTR boot_guard_record_t s_record;
/* The count belongs to one firmware image: a different image
 * starts from zero. */
static RTC_NOINIT_ATTR uint32_t s_record_image;
static RTC_NOINIT_ATTR safe_next_t s_safe_next;
static boot_guard_status_t s_status;
static boot_guard_low_memory_t s_low_memory;

static bool abnormal_reset(esp_reset_reason_t reason)
{
    switch (reason) {
    case ESP_RST_PANIC:
    case ESP_RST_INT_WDT:
    case ESP_RST_TASK_WDT:
    case ESP_RST_WDT:
    case ESP_RST_CPU_LOCKUP:
        return true;
    default:
        return false;
    }
}

static bool rtc_survives(esp_reset_reason_t reason)
{
    return reason != ESP_RST_POWERON && reason != ESP_RST_BROWNOUT &&
           reason != ESP_RST_UNKNOWN;
}

/* The default "nvs" partition (BLE bonds); recovery stays with codex_micro. */
static uint32_t running_image_id(void)
{
    const esp_app_desc_t *app = esp_app_get_description();
    if (app == NULL) {
        return 0;
    }
    return (uint32_t)app->app_elf_sha256[0] << 24 | (uint32_t)app->app_elf_sha256[1] << 16 |
           (uint32_t)app->app_elf_sha256[2] << 8 | app->app_elf_sha256[3];
}

static bool nvs_mirror_read(uint32_t *count, uint32_t *image)
{
    *count = 0;
    *image = 0;
    if (nvs_flash_init_partition(NVS_DEFAULT_PART_NAME) != ESP_OK) {
        return false;
    }
    nvs_handle_t handle = 0;
    const esp_err_t error = nvs_open(BOOT_GUARD_NAMESPACE, NVS_READONLY, &handle);
    if (error == ESP_ERR_NVS_NOT_FOUND) {
        return true;
    }
    if (error != ESP_OK) {
        return false;
    }
    uint8_t value = 0;
    const esp_err_t get_error = nvs_get_u8(handle, BOOT_GUARD_KEY, &value);
    (void)nvs_get_u32(handle, BOOT_GUARD_IMAGE_KEY, image);
    nvs_close(handle);
    if (get_error == ESP_OK) {
        *count = value <= BOOT_GUARD_LIMIT ? value : BOOT_GUARD_LIMIT;
        return true;
    }
    return get_error == ESP_ERR_NVS_NOT_FOUND;
}

static bool nvs_mirror_write(uint32_t count)
{
    nvs_handle_t handle = 0;
    if (nvs_open(BOOT_GUARD_NAMESPACE, NVS_READWRITE, &handle) != ESP_OK) {
        return false;
    }
    esp_err_t error = nvs_set_u8(handle, BOOT_GUARD_KEY, (uint8_t)count);
    if (error == ESP_OK) {
        error = nvs_set_u32(handle, BOOT_GUARD_IMAGE_KEY, running_image_id());
    }
    if (error == ESP_OK) {
        error = nvs_commit(handle);
    }
    nvs_close(handle);
    return error == ESP_OK;
}

#if CONFIG_WMP_CRASH_FAULT_INJECTION
/*
 * TEST BUILD ONLY (tools/build.sh --crash-fault-injection). One scripted
 * fault per boot, 3 s into the main loop: 1 main loop stalls (liveness ->
 * task watchdog), 2 and 3 abort (panic), then no fault: boot 4 is the safe
 * mode boot, and the restart after it runs normally. A power-on reset
 * restarts the script; the boot guard count is kept in NVS regardless.
 * Boots of an unconfirmed (pending-verify) image never advance the script:
 * a fault there would only roll the update back.
 */
#include "storage_recovery.h"
#define CRASH_FAULT_MAGIC 0x43524654u /* "CRFT" */
#define CRASH_FAULT_DELAY_MS 3000u
typedef struct {
    uint32_t magic;
    uint32_t step;
} crash_fault_script_t;
static RTC_NOINIT_ATTR crash_fault_script_t s_crash_fault;
static bool s_crash_fault_done;

static void crash_fault_next_boot(esp_reset_reason_t reason)
{
    if (s_crash_fault.magic != CRASH_FAULT_MAGIC || s_crash_fault.step > 100u ||
        reason == ESP_RST_POWERON) {
        s_crash_fault.magic = CRASH_FAULT_MAGIC;
        s_crash_fault.step = 0;
    }
    if (storage_recovery_image_pending_verify()) {
        s_crash_fault_done = true;
        ESP_LOGW(TAG, "CRASH FAULT INJECTION waits for a confirmed image");
        return;
    }
    ++s_crash_fault.step;
    ESP_LOGW(TAG, "CRASH FAULT INJECTION test build: step %u",
             (unsigned)s_crash_fault.step);
}

static void crash_fault_poll(uint32_t main_loop_ms)
{
    if (s_crash_fault_done || s_status.safe_mode ||
        main_loop_ms < CRASH_FAULT_DELAY_MS) {
        return;
    }
    s_crash_fault_done = true;
    if (s_crash_fault.step == 1u) {
        ESP_LOGE(TAG, "CRASH FAULT INJECTION: stalling the main loop");
        for (;;) {
        }
    }
    if (s_crash_fault.step == 2u || s_crash_fault.step == 3u) {
        ESP_LOGE(TAG, "CRASH FAULT INJECTION: abort at step %u",
                 (unsigned)s_crash_fault.step);
        abort();
    }
}
#else
static inline void crash_fault_next_boot(esp_reset_reason_t reason) { (void)reason; }
static inline void crash_fault_poll(uint32_t main_loop_ms) { (void)main_loop_ms; }
#endif

void boot_guard_start(void)
{
    const esp_reset_reason_t reason = esp_reset_reason();
    const uint32_t image = running_image_id();
    uint32_t rtc_count = 0;
    const bool rtc_valid = rtc_survives(reason) &&
                           boot_guard_record_load(&s_record, &rtc_count) &&
                           s_record_image == image;
    uint32_t nvs_count = 0;
    uint32_t nvs_image = 0;
    const bool mirror = nvs_mirror_read(&nvs_count, &nvs_image);
    if (nvs_image != image) {
        nvs_count = 0;
    }
    const uint32_t previous = rtc_valid ? rtc_count : nvs_count;
    const boot_guard_decision_t decision =
        boot_guard_decide(previous, abnormal_reset(reason));

    memset(&s_status, 0, sizeof(s_status));
    s_status.safe_mode = decision.safe_mode;
    s_status.reason = decision.safe_mode ? BOOT_GUARD_REASON_CRASH_LOOP
                                         : BOOT_GUARD_REASON_NONE;
    s_status.abnormal_resets = decision.count;
    s_status.reset_reason = (int32_t)reason;
    const bool safe_next = reason == ESP_RST_SW && s_safe_next.magic == SAFE_NEXT_MAGIC &&
                           s_safe_next.check == ~(SAFE_NEXT_MAGIC ^ s_safe_next.reason) &&
                           (s_safe_next.reason == BOOT_GUARD_REASON_LOW_MEMORY ||
                            s_safe_next.reason == BOOT_GUARD_REASON_BLE_NO_MEM);
    if (safe_next && !decision.safe_mode) {
        s_status.safe_mode = true;
        s_status.reason = (boot_guard_reason_t)s_safe_next.reason;
    }
    memset(&s_safe_next, 0, sizeof(s_safe_next));
    boot_guard_record_store(&s_record, decision.stored_count);
    s_record_image = image;
    s_status.nvs_mirror = mirror &&
        ((decision.stored_count == nvs_count && nvs_image == image) ||
         nvs_mirror_write(decision.stored_count));
    crash_fault_next_boot(reason);
    if (decision.safe_mode) {
        ESP_LOGE(TAG, "%u abnormal resets in a row (last %s); SAFE MODE",
                 (unsigned)decision.count, boot_guard_reset_reason_name(reason));
    } else if (decision.count > 0) {
        ESP_LOGW(TAG, "%u abnormal reset(s) in a row (last %s)",
                 (unsigned)decision.count, boot_guard_reset_reason_name(reason));
    }
}

bool boot_guard_safe_mode(void)
{
    return s_status.safe_mode;
}

void boot_guard_request_safe_mode(void)
{
    if (!s_status.safe_mode) {
        s_status.safe_mode = true;
        s_status.reason = BOOT_GUARD_REASON_MANUAL;
        ESP_LOGW(TAG, "boot key combination; SAFE MODE for this boot");
    }
}

void boot_guard_restart_into_safe_mode(boot_guard_reason_t reason)
{
    if (s_status.safe_mode) {
        return;
    }
    s_safe_next.magic = SAFE_NEXT_MAGIC;
    s_safe_next.reason = (uint32_t)reason;
    s_safe_next.check = ~(SAFE_NEXT_MAGIC ^ s_safe_next.reason);
    ESP_LOGE(TAG, "unusable boot (%s); restarting into SAFE MODE",
             boot_guard_reason_name(reason));
    esp_restart();
}

void boot_guard_poll(uint32_t main_loop_ms, bool memory_low, bool update_active)
{
    crash_fault_poll(main_loop_ms);
    if (boot_guard_low_memory_due(&s_low_memory, memory_low, main_loop_ms,
                                  s_status.safe_mode, update_active)) {
        boot_guard_restart_into_safe_mode(BOOT_GUARD_REASON_LOW_MEMORY);
    }
    if (s_status.stable_cleared || !boot_guard_stable(s_status.safe_mode, main_loop_ms)) {
        return;
    }
    s_status.stable_cleared = true;
    uint32_t stored = 0;
    (void)boot_guard_record_load(&s_record, &stored);
    boot_guard_record_store(&s_record, 0);
    if (stored != 0 || s_status.abnormal_resets != 0) {
        (void)nvs_mirror_write(0);
        ESP_LOGI(TAG, "ran normally; crash-loop count cleared");
    }
}

void boot_guard_get_status(boot_guard_status_t *status)
{
    if (status != NULL) {
        *status = s_status;
    }
}

const char *boot_guard_reason_name(boot_guard_reason_t reason)
{
    switch (reason) {
    case BOOT_GUARD_REASON_CRASH_LOOP: return "crash_loop";
    case BOOT_GUARD_REASON_MANUAL: return "manual";
    case BOOT_GUARD_REASON_LOW_MEMORY: return "low_memory";
    case BOOT_GUARD_REASON_BLE_NO_MEM: return "ble_no_memory";
    default: return "none";
    }
}

const char *boot_guard_reset_reason_name(int32_t reason)
{
    switch ((esp_reset_reason_t)reason) {
    case ESP_RST_POWERON: return "power_on";
    case ESP_RST_EXT: return "external";
    case ESP_RST_SW: return "software";
    case ESP_RST_PANIC: return "panic";
    case ESP_RST_INT_WDT: return "interrupt_watchdog";
    case ESP_RST_TASK_WDT: return "task_watchdog";
    case ESP_RST_WDT: return "watchdog";
    case ESP_RST_DEEPSLEEP: return "deep_sleep";
    case ESP_RST_BROWNOUT: return "brownout";
    case ESP_RST_USB: return "usb";
    case ESP_RST_JTAG: return "jtag";
    case ESP_RST_CPU_LOCKUP: return "cpu_lockup";
    default: return "other";
    }
}
