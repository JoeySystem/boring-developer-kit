#pragma once

/*
 * Stability item 2: a stalled main loop, USB or BLE task resets the device.
 *
 * Liveness is tracked per channel: each armed channel must beat within its
 * timeout. Only our own tasks reset the device (main loop after it starts,
 * the USB configuration parser): a single supervisor task is subscribed to
 * the task watchdog and feeds it only while none of them is stalled; a stall
 * lets the task watchdog panic and reset with ESP_RST_TASK_WDT, which the boot
 * crash-loop guard (item 5) counts. The ESP-IDF tasks (TinyUSB, Bluedroid)
 * normally wait without a timeout, so first batch only probes them (queued
 * work that beats when it runs) and reports late answers; it never resets
 * for them. Start-up is not watched.
 *
 * Timeouts cover the slowest normal path of each channel (see liveness.h).
 * The stalled channel is kept in RTC memory for the next boot's GET_STATUS.
 */

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    LIVENESS_MAIN = 0,   /* main loop iteration (armed when the loop starts) */
    LIVENESS_USB_RX,     /* USB configuration parser task (incl. OTA writes) */
    LIVENESS_USB_STACK,  /* TinyUSB task, probed */
    LIVENESS_BLE_HOST,   /* Bluedroid BTC/BTU tasks, probed */
    LIVENESS_CHANNEL_COUNT,
} liveness_channel_t;

#define LIVENESS_NONE (-1)

typedef struct {
    bool armed;
    bool arm_on_beat;    /* probed: armed by the first answer */
    bool resets;         /* a stall resets; otherwise it is only reported */
    bool overdue;
    uint32_t timeout_ms;
    uint32_t last_beat_ms;
    uint32_t beats;
    uint32_t max_gap_ms; /* longest time between beats while armed */
    uint32_t overdue_count;
} liveness_slot_t;

typedef struct {
    liveness_slot_t slots[LIVENESS_CHANNEL_COUNT];
} liveness_model_t;

void liveness_model_arm(liveness_model_t *model, liveness_channel_t channel,
                        uint32_t timeout_ms, uint32_t now_ms, bool resets);
/*
 * For probed channels: armed by the first beat, so a probe whose answer never
 * arrives on some hardware or stack configuration cannot cause resets.
 */
void liveness_model_arm_on_beat(liveness_model_t *model, liveness_channel_t channel,
                                uint32_t timeout_ms, bool resets);
void liveness_model_disarm(liveness_model_t *model, liveness_channel_t channel);
void liveness_model_beat(liveness_model_t *model, liveness_channel_t channel,
                         uint32_t now_ms);
/* The first armed resetting channel that has not beaten within its timeout. */
int liveness_model_stalled(const liveness_model_t *model, uint32_t now_ms);
/* Counts each new overdue episode of every armed channel (for reporting). */
void liveness_model_note_overdue(liveness_model_t *model, uint32_t now_ms);
const char *liveness_channel_name(int channel);

/* Survives the watchdog reset; anything that does not verify reads as none. */
typedef struct {
    uint32_t magic;
    uint32_t channel;
    uint32_t uptime_ms;
    uint32_t check;
} liveness_stall_record_t;

void liveness_stall_record_store(liveness_stall_record_t *record, int channel,
                                 uint32_t uptime_ms);
/* LIVENESS_NONE when the record does not verify. */
int liveness_stall_record_load(const liveness_stall_record_t *record,
                               uint32_t *uptime_ms);
void liveness_stall_record_clear(liveness_stall_record_t *record);

#ifdef __cplusplus
}
#endif
