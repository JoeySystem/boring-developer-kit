#pragma once

/*
 * Which remembered computer ("host") the BLE transport should be on.
 *
 * The device remembers up to three hosts, one per slot. The user should not
 * have to think in slots, so the slot bookkeeping follows these rules:
 *
 * - Selecting an empty slot while another slot holds a host opens a "pairing
 *   window": the slot is selected in memory only and the persisted active slot
 *   keeps pointing at a remembered host until a new host has paired.
 * - A pairing window that nobody used returns to the host it came from.
 * - While any host is remembered, both the persisted and the in-memory active
 *   slot at boot point at a remembered host.
 *
 * Pure decisions only: no ESP-IDF, no allocation, no state. Nothing here
 * deletes a bond or clears a record. Slots are numbered 1..3 and 0 means
 * "none". Every function is total: a NULL pointer reads as "nothing known"
 * and a slot outside 1..3 is never treated as holding a host; the result for
 * each such case is stated on the function.
 */

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define BLE_HOST_POLICY_SLOT_COUNT 3u

typedef struct {
    bool paired[BLE_HOST_POLICY_SLOT_COUNT]; /* index = slot - 1 */
} ble_host_records_t;

/*
 * Lowest empty slot (1..3), or 0 when all three hold a host.
 * NULL records: 0 (no slot is offered).
 */
uint8_t ble_host_policy_free_slot(const ble_host_records_t *records);

/*
 * The host the single switch key moves to from current: the next slot after
 * current, in the order 1, 2, 3, 1, ..., that holds a host and is not current
 * itself. 0 when no other slot holds a host.
 * A current outside 1..3 reads as 1. NULL records: 0.
 */
uint8_t ble_host_policy_next_host(const ble_host_records_t *records,
                                  uint8_t current);

/*
 * Slot to use in memory at boot. persisted_slot is the active slot read from
 * NVS and may be any byte: outside 1..3 it reads as 1. When it holds a host
 * it is used; when it is empty and another slot holds a host, the lowest slot
 * holding a host is used; when all are empty it is used as is.
 * NULL records: the normalized persisted_slot. The result is always 1..3.
 */
uint8_t ble_host_policy_boot_slot(const ble_host_records_t *records,
                                  uint8_t persisted_slot);

/*
 * Whether selecting slot is written to NVS right away: true when slot holds a
 * host or all three slots are empty; false for an empty slot while another
 * slot holds a host (pairing window, memory only).
 * NULL records or slot outside 1..3: false (nothing is written).
 */
bool ble_host_policy_select_persists(const ble_host_records_t *records,
                                     uint8_t slot);

/*
 * Where a pairing window returns to when the empty slot is selected while
 * current is the active slot: current when it holds a host; otherwise the
 * lowest slot other than slot that holds a host; 0 when there is none.
 * A current outside 1..3 is treated as empty.
 * NULL records, slot outside 1..3, or slot holding a host (no pairing
 * window): 0.
 */
uint8_t ble_host_policy_return_slot(const ble_host_records_t *records,
                                    uint8_t current, uint8_t slot);

/*
 * Persisted active slot after cleared_slot was cleared (records already show
 * it empty): the lowest slot holding a host, or cleared_slot when all are
 * empty.
 * A cleared_slot outside 1..3 reads as 1 in the all-empty case; NULL records
 * read as all empty. The result is always 1..3.
 */
uint8_t ble_host_policy_active_after_clear(const ble_host_records_t *records,
                                           uint8_t cleared_slot);

typedef struct {
    uint8_t active_slot;      /* slot selected in memory */
    uint8_t return_slot;      /* 0 = no pairing window or nowhere to return */
    bool fast_window_elapsed; /* the pairing fast-advertising window is over */
    bool link_connected;      /* a BLE link is up, encrypted or not */
    bool suspended;           /* BLE is suspended (USB in use, standby) */
    bool busy;                /* a slot clear or bond removal is in progress */
} ble_host_window_t;

/*
 * Whether the pairing window should end and return to return_slot. All six
 * must hold: active_slot is empty, return_slot holds a host, the fast window
 * has elapsed, no link is up, BLE is not suspended, and nothing is busy.
 * NULL records or window, or active_slot outside 1..3: false.
 */
bool ble_host_policy_window_expired(const ble_host_records_t *records,
                                    const ble_host_window_t *window);

/*
 * Whether a connecting host is accepted, written down as the transport
 * decides it today at its three decision points. Nothing calls this yet; it
 * is the baseline that the next step of the simplification changes, so the
 * stages are deliberately not unified here:
 *
 * - At CONNECT and SECURITY_REQUEST an empty active slot accepts any host,
 *   and a host matching the active slot is accepted without looking at the
 *   other slots.
 * - At AUTH_COMPLETE a host that belongs to another slot is rejected even
 *   when the active slot is empty or also matches it.
 *
 * The caller works out the inputs. At CONNECT and SECURITY_REQUEST only the
 * on-air address is known; at AUTH_COMPLETE the identity presented during
 * pairing is known as well, so the same host may match differently there.
 */
typedef enum {
    BLE_HOST_STAGE_CONNECT,          /* ESP_GATTS_CONNECT_EVT */
    BLE_HOST_STAGE_SECURITY_REQUEST, /* ESP_GAP_BLE_SEC_REQ_EVT */
    BLE_HOST_STAGE_AUTH_COMPLETE,    /* ESP_GAP_BLE_AUTH_CMPL_EVT, success */
} ble_host_stage_t;

typedef enum {
    BLE_HOST_ACCEPT = 0,
    BLE_HOST_REJECT_SUSPENDED,
    BLE_HOST_REJECT_OTHER_SLOT,
    BLE_HOST_REJECT_UNKNOWN_HOST,
} ble_host_decision_t;

typedef struct {
    ble_host_stage_t stage;
    bool suspended;      /* BLE is suspended (USB in use, standby) */
    bool active_paired;  /* the active slot holds a host */
    bool matches_active; /* the host matches the active slot's record */
    uint8_t other_slot;  /* 0 = the host belongs to no other slot */
} ble_host_peer_t;

/*
 * Suspended rejects first at every stage. matches_active is ignored while
 * the active slot is empty, and any non-zero other_slot counts as "belongs
 * to another slot".
 * NULL peer or a stage outside the enum: BLE_HOST_REJECT_SUSPENDED, the one
 * rejection that is handled by disconnecting only (no screen prompt, no bond
 * removal). Invalid input is never accepted.
 */
ble_host_decision_t ble_host_policy_decide(const ble_host_peer_t *peer);

#ifdef __cplusplus
}
#endif
