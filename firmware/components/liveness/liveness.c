#include "liveness.h"

#include <string.h>

#include "esp_attr.h"
#include "esp_log.h"
#include "esp_system.h"
#include "esp_task_wdt.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#define LIVENESS_TASK_STACK 3072
/* Above TinyUSB (5), the config parser (4) and app_main (1); below the
 * Bluedroid and esp_timer tasks so it never delays radio work. */
#define LIVENESS_TASK_PRIORITY 15

static const char *TAG = "liveness";

static portMUX_TYPE s_lock = portMUX_INITIALIZER_UNLOCKED;
static liveness_model_t s_model;
static liveness_probe_t s_probes[LIVENESS_CHANNEL_COUNT];
static RTC_NOINIT_ATTR liveness_stall_record_t s_stall_record;
static int s_previous_stall = LIVENESS_NONE;
static uint32_t s_previous_stall_uptime_ms;
static bool s_supervising;
static bool s_task_watchdog;

static uint32_t now_ms(void)
{
    return (uint32_t)(esp_timer_get_time() / 1000);
}

void liveness_arm(liveness_channel_t channel, uint32_t timeout_ms)
{
    portENTER_CRITICAL_SAFE(&s_lock);
    liveness_model_arm(&s_model, channel, timeout_ms, now_ms(), true);
    portEXIT_CRITICAL_SAFE(&s_lock);
}

void liveness_disarm(liveness_channel_t channel)
{
    portENTER_CRITICAL_SAFE(&s_lock);
    liveness_model_disarm(&s_model, channel);
    portEXIT_CRITICAL_SAFE(&s_lock);
}

void liveness_beat(liveness_channel_t channel)
{
    portENTER_CRITICAL_SAFE(&s_lock);
    liveness_model_beat(&s_model, channel, now_ms());
    portEXIT_CRITICAL_SAFE(&s_lock);
}

void liveness_set_probe(liveness_channel_t channel, liveness_probe_t probe,
                        uint32_t timeout_ms)
{
    if (channel < 0 || channel >= LIVENESS_CHANNEL_COUNT) {
        return;
    }
    portENTER_CRITICAL_SAFE(&s_lock);
    s_probes[channel] = probe;
    liveness_model_arm_on_beat(&s_model, channel, timeout_ms, false);
    portEXIT_CRITICAL_SAFE(&s_lock);
}

static void run_probes(void)
{
    for (int channel = 0; channel < LIVENESS_CHANNEL_COUNT; ++channel) {
        portENTER_CRITICAL(&s_lock);
        const liveness_probe_t probe = s_probes[channel];
        const bool armed = s_model.slots[channel].armed;
        portEXIT_CRITICAL(&s_lock);
        /* A probe can block on a full queue of a stuck task; then this task
         * stops feeding the task watchdog, which is the intended reset. */
        if (probe != NULL && !probe() && armed) {
            liveness_beat((liveness_channel_t)channel); /* not running: idle */
        }
    }
}

static void supervisor_task(void *parameter)
{
    (void)parameter;
    s_task_watchdog = esp_task_wdt_add(NULL) == ESP_OK;
    if (!s_task_watchdog) {
        ESP_LOGE(TAG, "task watchdog unavailable; stalls are only logged");
    }
    uint32_t last_probe_ms = 0;
    bool reported = false;
    for (;;) {
        const uint32_t now = now_ms();
        if ((uint32_t)(now - last_probe_ms) >= LIVENESS_PROBE_PERIOD_MS) {
            last_probe_ms = now;
            run_probes();
        }
        portENTER_CRITICAL(&s_lock);
        liveness_model_note_overdue(&s_model, now_ms());
        const int stalled = liveness_model_stalled(&s_model, now_ms());
        portEXIT_CRITICAL(&s_lock);
        if (stalled == LIVENESS_NONE) {
            if (reported) {
                /* Recovered before the watchdog fired. */
                reported = false;
                liveness_stall_record_clear(&s_stall_record);
            }
            if (s_task_watchdog) {
                (void)esp_task_wdt_reset();
            }
        } else if (!reported) {
            reported = true;
            liveness_stall_record_store(&s_stall_record, stalled, now);
            ESP_LOGE(TAG, "%s stalled; letting the task watchdog reset",
                     liveness_channel_name(stalled));
        }
        vTaskDelay(pdMS_TO_TICKS(LIVENESS_SUPERVISOR_PERIOD_MS));
    }
}

void liveness_start(void)
{
    const esp_reset_reason_t reason = esp_reset_reason();
    s_previous_stall = reason == ESP_RST_POWERON
        ? LIVENESS_NONE
        : liveness_stall_record_load(&s_stall_record, &s_previous_stall_uptime_ms);
    liveness_stall_record_clear(&s_stall_record);
    if (s_previous_stall != LIVENESS_NONE) {
        ESP_LOGW(TAG, "previous boot reset after %s stalled at %u ms",
                 liveness_channel_name(s_previous_stall),
                 (unsigned)s_previous_stall_uptime_ms);
    }
    s_supervising = xTaskCreate(supervisor_task, "liveness", LIVENESS_TASK_STACK,
                                NULL, LIVENESS_TASK_PRIORITY, NULL) == pdPASS;
    if (!s_supervising) {
        ESP_LOGE(TAG, "liveness supervisor not started");
    }
}

void liveness_get_status(liveness_status_t *status)
{
    if (status == NULL) {
        return;
    }
    memset(status, 0, sizeof(*status));
    status->supervising = s_supervising;
    status->task_watchdog = s_task_watchdog;
    status->previous_stall = s_previous_stall;
    status->previous_stall_uptime_ms = s_previous_stall_uptime_ms;
    portENTER_CRITICAL_SAFE(&s_lock);
    for (int channel = 0; channel < LIVENESS_CHANNEL_COUNT; ++channel) {
        status->beats[channel] = s_model.slots[channel].beats;
        status->armed[channel] = s_model.slots[channel].armed;
        status->resets[channel] = s_model.slots[channel].resets;
        status->max_gap_ms[channel] = s_model.slots[channel].max_gap_ms;
        status->overdue[channel] = s_model.slots[channel].overdue_count;
    }
    portEXIT_CRITICAL_SAFE(&s_lock);
}
