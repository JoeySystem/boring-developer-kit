#include "ble_slot_history_model.h"

#include <string.h>

void ble_slot_history_reset(ble_slot_history_t *history)
{
    memset(history, 0, sizeof(*history));
    history->magic = BLE_SLOT_HISTORY_MAGIC;
    history->version = BLE_SLOT_HISTORY_VERSION;
}

void ble_slot_history_load(ble_slot_history_t *history, const void *blob,
                           size_t length)
{
    ble_slot_history_reset(history);
    if (blob == NULL || length != sizeof(*history)) {
        return;
    }
    ble_slot_history_t stored;
    memcpy(&stored, blob, sizeof(stored));
    if (stored.magic != BLE_SLOT_HISTORY_MAGIC ||
        stored.version != BLE_SLOT_HISTORY_VERSION ||
        stored.count > BLE_SLOT_HISTORY_LENGTH) {
        return;
    }
    *history = stored;
}

void ble_slot_history_record(ble_slot_history_t *history,
                             const ble_slot_history_entry_t *entry)
{
    memmove(&history->entries[1], &history->entries[0],
            (BLE_SLOT_HISTORY_LENGTH - 1u) * sizeof(history->entries[0]));
    history->entries[0] = *entry;
    if (history->count < BLE_SLOT_HISTORY_LENGTH) {
        ++history->count;
    }
}
