#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/*
 * The last BLE slot operations, newest first, persisted as one NVS blob so a
 * returned or field unit shows who changed its pairings and when. 68DE8 lost
 * both pairings while only the last operation was kept, so the first clear
 * could only be inferred.
 */
#define BLE_SLOT_HISTORY_LENGTH 8u
#define BLE_SLOT_HISTORY_MAGIC 0x48534C42u /* "BLSH" */
#define BLE_SLOT_HISTORY_VERSION 1u

typedef struct {
    uint8_t op;      /* codex_micro_ble_slot_op_t */
    uint8_t slot;
    uint8_t source;  /* codex_micro_ble_slot_source_t */
    uint8_t timeout;
    int32_t error;
    uint32_t uptime_ms;
    uint32_t boot;   /* persisted boot counter at the time */
} ble_slot_history_entry_t;

typedef struct {
    uint32_t magic;
    uint32_t version;
    uint32_t count;
    ble_slot_history_entry_t entries[BLE_SLOT_HISTORY_LENGTH];
} ble_slot_history_t;

void ble_slot_history_reset(ble_slot_history_t *history);
/* Accepts a blob read from NVS; anything malformed starts an empty history. */
void ble_slot_history_load(ble_slot_history_t *history, const void *blob,
                           size_t length);
void ble_slot_history_record(ble_slot_history_t *history,
                             const ble_slot_history_entry_t *entry);
