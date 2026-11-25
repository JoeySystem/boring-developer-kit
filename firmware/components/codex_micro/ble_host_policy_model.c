#include "ble_host_policy_model.h"

#include <stddef.h>

static bool slot_valid(uint8_t slot)
{
    return slot >= 1u && slot <= BLE_HOST_POLICY_SLOT_COUNT;
}

static bool slot_paired(const ble_host_records_t *records, uint8_t slot)
{
    return records != NULL && slot_valid(slot) && records->paired[slot - 1u];
}

/* Lowest slot holding a host; 0 = none. */
static uint8_t lowest_paired(const ble_host_records_t *records)
{
    for (uint8_t slot = 1u; slot <= BLE_HOST_POLICY_SLOT_COUNT; ++slot) {
        if (slot_paired(records, slot)) {
            return slot;
        }
    }
    return 0u;
}

uint8_t ble_host_policy_free_slot(const ble_host_records_t *records)
{
    if (records == NULL) {
        return 0u;
    }
    for (uint8_t slot = 1u; slot <= BLE_HOST_POLICY_SLOT_COUNT; ++slot) {
        if (!records->paired[slot - 1u]) {
            return slot;
        }
    }
    return 0u;
}

uint8_t ble_host_policy_next_host(const ble_host_records_t *records,
                                  uint8_t current)
{
    const uint8_t from = slot_valid(current) ? current : 1u;
    for (uint8_t step = 1u; step < BLE_HOST_POLICY_SLOT_COUNT; ++step) {
        const uint8_t slot = (uint8_t)(
            (from - 1u + step) % BLE_HOST_POLICY_SLOT_COUNT + 1u);
        if (slot_paired(records, slot)) {
            return slot;
        }
    }
    return 0u;
}

uint8_t ble_host_policy_boot_slot(const ble_host_records_t *records,
                                  uint8_t persisted_slot)
{
    const uint8_t persisted = slot_valid(persisted_slot) ? persisted_slot : 1u;
    if (slot_paired(records, persisted)) {
        return persisted;
    }
    const uint8_t remembered = lowest_paired(records);
    return remembered != 0u ? remembered : persisted;
}

bool ble_host_policy_select_persists(const ble_host_records_t *records,
                                     uint8_t slot)
{
    if (records == NULL || !slot_valid(slot)) {
        return false;
    }
    return slot_paired(records, slot) || lowest_paired(records) == 0u;
}

uint8_t ble_host_policy_return_slot(const ble_host_records_t *records,
                                    uint8_t current, uint8_t slot)
{
    if (records == NULL || !slot_valid(slot) || slot_paired(records, slot)) {
        return 0u;
    }
    if (slot_paired(records, current)) {
        return current;
    }
    /* slot is empty here, so the lowest host is never slot itself. */
    return lowest_paired(records);
}

uint8_t ble_host_policy_active_after_clear(const ble_host_records_t *records,
                                           uint8_t cleared_slot)
{
    const uint8_t remembered = lowest_paired(records);
    if (remembered != 0u) {
        return remembered;
    }
    return slot_valid(cleared_slot) ? cleared_slot : 1u;
}

bool ble_host_policy_window_expired(const ble_host_records_t *records,
                                    const ble_host_window_t *window)
{
    if (records == NULL || window == NULL) {
        return false;
    }
    return slot_valid(window->active_slot) &&
           !slot_paired(records, window->active_slot) &&
           slot_paired(records, window->return_slot) &&
           window->fast_window_elapsed && !window->link_connected &&
           !window->suspended && !window->busy;
}

ble_host_decision_t ble_host_policy_decide(const ble_host_peer_t *peer)
{
    if (peer == NULL || peer->suspended) {
        return BLE_HOST_REJECT_SUSPENDED;
    }
    switch (peer->stage) {
    case BLE_HOST_STAGE_CONNECT:
    case BLE_HOST_STAGE_SECURITY_REQUEST:
    case BLE_HOST_STAGE_AUTH_COMPLETE:
        break;
    default:
        return BLE_HOST_REJECT_SUSPENDED;
    }
    const bool other = peer->other_slot != 0u;
    if (peer->active_paired && !peer->matches_active) {
        return other ? BLE_HOST_REJECT_OTHER_SLOT
                     : BLE_HOST_REJECT_UNKNOWN_HOST;
    }
    /* Only the completed pairing looks at the other slots for a host that
     * the active slot would take. */
    if (peer->stage == BLE_HOST_STAGE_AUTH_COMPLETE && other) {
        return BLE_HOST_REJECT_OTHER_SLOT;
    }
    return BLE_HOST_ACCEPT;
}
