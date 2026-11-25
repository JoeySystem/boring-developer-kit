"""Pairing window: an empty BLE slot selected while a host is remembered.

The device remembers three hosts. Selecting an empty slot to add a host must
not strand the device there: the choice stays in RAM (NVS "active" keeps
naming a remembered host) until a host pairs, and when nobody pairs within
the pairing fast-advertising window the device goes back to the host it came
from. Nothing on these paths deletes a pairing, and which host may connect is
decided exactly as before.

The production functions are extracted from codex_micro_ble.c and run against
a fake NVS and fake Bluedroid events.
"""

import ctypes
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_lighting_preview_protocol import c_function


ROOT = Path(__file__).resolve().parents[2]
CODEX = ROOT / "components/codex_micro"
BLE_SOURCE = CODEX / "codex_micro_ble.c"
HEADER = CODEX / "include/codex_micro.h"
STUB = CODEX / "codex_micro_stub.c"
POLICY_SOURCE = CODEX / "ble_host_policy_model.c"
PROTOCOL = ROOT / "components/config_protocol/config_protocol.c"

PAIRING_MS = 180000
OP_PAIR_SAVE, OP_SELECT, OP_AUTO_RETURN = 2, 3, 4
SOURCE_AUTO = 4
STRANGER = 0x50


def typedef_block(source, name):
    end = source.index("} " + name + ";") + len("} " + name + ";")
    return source[source.rindex("typedef", 0, end):end]


def atomic_statics(source):
    """Every file-scope atomic of the BLE transport, zero-initialised."""
    return "\n".join(re.findall(
        r"^static atomic_\w+\s+s_\w+(?:\[[^\]]*\])?;", source, re.M))


PRELUDE = r'''
#include <stdatomic.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include "ble_host_policy_model.h"

typedef int esp_err_t;
#define ESP_OK 0
#define ESP_FAIL -1
#define ESP_ERR_NO_MEM 0x101
#define ESP_ERR_INVALID_ARG 0x102
#define ESP_ERR_INVALID_STATE 0x103
#define ESP_ERR_TIMEOUT 0x107
#define ESP_ERR_NVS_NOT_FOUND 0x1102
#define ESP_LOGI(tag, ...) ((void)(tag))
#define ESP_LOGW(tag, ...) ((void)(tag))
#define ESP_LOGE(tag, ...) ((void)(tag))
static const char *TAG = "test";
static const char *esp_err_to_name(esp_err_t e) { (void)e; return "e"; }
#define portENTER_CRITICAL(lock) ((void)(lock))
#define portEXIT_CRITICAL(lock) ((void)(lock))
static int s_state_lock;

#define CODEX_MICRO_BLE_SLOT_COUNT 3u
#define CODEX_MICRO_BLE_NVS_NAMESPACE "codex_ble"
#define CODEX_MICRO_REPORT_ID 6
#define ESP_BD_ADDR_LEN 6
typedef uint8_t esp_bd_addr_t[ESP_BD_ADDR_LEN];
#define BLE_FAST_ADVERTISING_DURATION_MS 30000U
#define BLE_PAIRING_FAST_ADVERTISING_DURATION_MS 180000U
#define BLE_SLOT_CLEAR_TIMEOUT_MS 4000U
enum { BLE_PEER_FILTER_IDLE = 0, BLE_PEER_FILTER_STOPPING };
typedef enum {
    CODEX_TRANSPORT_AUTO = 0, CODEX_TRANSPORT_USB, CODEX_TRANSPORT_BLE,
} codex_transport_t;

/* Bluedroid: only what the handlers read. */
typedef enum {
    ESP_GAP_BLE_GET_DEV_NAME_COMPLETE_EVT, ESP_GAP_BLE_ADV_DATA_SET_COMPLETE_EVT,
    ESP_GAP_BLE_SCAN_RSP_DATA_SET_COMPLETE_EVT,
    ESP_GAP_BLE_ADV_START_COMPLETE_EVT, ESP_GAP_BLE_ADV_STOP_COMPLETE_EVT,
    ESP_GAP_BLE_SET_STATIC_RAND_ADDR_EVT,
    ESP_GAP_BLE_KEY_EVT, ESP_GAP_BLE_SEC_REQ_EVT, ESP_GAP_BLE_AUTH_CMPL_EVT,
    ESP_GAP_BLE_REMOVE_BOND_DEV_COMPLETE_EVT, ESP_GAP_BLE_OTHER_EVT,
} esp_gap_ble_cb_event_t;
#define ESP_BT_STATUS_SUCCESS 0
#define ESP_LE_KEY_PID 2
typedef struct { uint8_t addr_type; esp_bd_addr_t static_addr; } esp_ble_pid_keys_t;
typedef struct {
    struct { int status; } adv_data_cmpl, scan_rsp_data_cmpl, adv_start_cmpl,
        adv_stop_cmpl, set_rand_addr_cmpl;
    struct {
        struct {
            int key_type;
            esp_bd_addr_t bd_addr;
            struct { esp_ble_pid_keys_t pid_key; } p_key_value;
        } ble_key;
        struct { esp_bd_addr_t bd_addr; } ble_req;
        struct {
            bool success;
            esp_bd_addr_t bd_addr;
            uint8_t addr_type;
            uint8_t fail_reason;
        } auth_cmpl;
    } ble_security;
    struct { esp_bd_addr_t bd_addr; int status; } remove_bond_dev_cmpl;
} esp_ble_gap_cb_param_t;
typedef enum {
    ESP_GATTS_CONNECT_EVT, ESP_GATTS_DISCONNECT_EVT, ESP_GATTS_WRITE_EVT,
    ESP_GATTS_MTU_EVT, ESP_GATTS_CONF_EVT,
} esp_gatts_cb_event_t;
typedef uint8_t esp_gatt_if_t;
#define ESP_GATT_OK 0
typedef struct {
    struct { esp_bd_addr_t remote_bda; uint16_t conn_id; } connect;
    struct { uint8_t reason; uint16_t conn_id; } disconnect;
    struct { uint16_t handle; } write;
    struct { uint16_t mtu; } mtu;
    struct { int status; uint16_t handle; } conf;
} esp_ble_gatts_cb_param_t;
typedef const char *esp_event_base_t;
typedef enum {
    ESP_HIDD_START_EVENT, ESP_HIDD_CONNECT_EVENT, ESP_HIDD_OUTPUT_EVENT,
    ESP_HIDD_DISCONNECT_EVENT, ESP_HIDD_OTHER_EVENT,
} esp_hidd_event_t;
typedef struct {
    struct { uint8_t report_id; const uint8_t *data; size_t length; } output;
} esp_hidd_event_data_t;
'''

STATE = r'''
typedef uint32_t nvs_handle_t;
#define NVS_READWRITE 1
static nvs_handle_t s_ble_peer_nvs;
static codex_micro_ble_peer_t s_ble_peers[CODEX_MICRO_BLE_SLOT_COUNT];
static codex_micro_ble_identity_t
    s_ble_peer_identities[CODEX_MICRO_BLE_SLOT_COUNT];
static codex_micro_ble_identity_t s_pairing_identity;
static esp_bd_addr_t s_pairing_identity_source;
static uint8_t s_ble_slot_clear_slot;
static codex_micro_ble_peer_t s_ble_slot_clear_peer;
static esp_bd_addr_t s_connected_peer_address;
static esp_bd_addr_t s_auth_bond_removal_address;
static uint8_t s_auth_bond_removal_attempts;
static const char *const BLE_PEER_NVS_KEYS[] = {"peer1", "peer2", "peer3"};
static const char *const BLE_PEER_IDENTITY_NVS_KEYS[] = {
    "peer1_id", "peer2_id", "peer3_id",
};
typedef bool (*codex_micro_ble_gatts_observer_fn)(int, uint16_t, void *, void *);
static codex_micro_ble_gatts_observer_fn s_gatts_observer;
static void *s_gatts_observer_context;

static uint32_t test_now_ms = 1000;
static uint32_t advertising_now_ms(void) { return test_now_ms; }
static uint32_t monotonic_ms(void) { return test_now_ms; }

/* Fake NVS namespace. */
typedef struct { bool used; char key[16]; uint8_t data[16]; size_t length; } nvs_entry_t;
static nvs_entry_t nvs_entries[24];
static unsigned nvs_active_writes, nvs_record_writes, nvs_record_erases;
static esp_err_t nvs_active_write_error;
static bool is_record_key(const char *key)
{
    return strncmp(key, "peer", 4) == 0;
}
static nvs_entry_t *nvs_find(const char *key)
{
    for (size_t i = 0; i < sizeof(nvs_entries) / sizeof(nvs_entries[0]); ++i) {
        if (nvs_entries[i].used && strcmp(nvs_entries[i].key, key) == 0) {
            return &nvs_entries[i];
        }
    }
    return NULL;
}
static esp_err_t nvs_put(const char *key, const void *data, size_t length)
{
    nvs_entry_t *entry = nvs_find(key);
    for (size_t i = 0; entry == NULL &&
         i < sizeof(nvs_entries) / sizeof(nvs_entries[0]); ++i) {
        if (!nvs_entries[i].used) {
            entry = &nvs_entries[i];
        }
    }
    if (entry == NULL || length > sizeof(entry->data)) {
        return ESP_FAIL;
    }
    entry->used = true;
    strncpy(entry->key, key, sizeof(entry->key) - 1);
    memcpy(entry->data, data, length);
    entry->length = length;
    return ESP_OK;
}
static esp_err_t nvs_open(const char *name, int mode, nvs_handle_t *handle)
{
    (void)name; (void)mode;
    *handle = 1;
    return ESP_OK;
}
static esp_err_t nvs_set_u8(nvs_handle_t handle, const char *key, uint8_t value)
{
    (void)handle;
    if (strcmp(key, "active") == 0) {
        if (nvs_active_write_error != ESP_OK) {
            return nvs_active_write_error;
        }
        nvs_active_writes++;
    }
    return nvs_put(key, &value, 1);
}
static esp_err_t nvs_get_u8(nvs_handle_t handle, const char *key, uint8_t *value)
{
    (void)handle;
    const nvs_entry_t *entry = nvs_find(key);
    if (entry == NULL) {
        return ESP_ERR_NVS_NOT_FOUND;
    }
    *value = entry->data[0];
    return ESP_OK;
}
static esp_err_t nvs_set_blob(nvs_handle_t handle, const char *key,
                              const void *value, size_t length)
{
    (void)handle;
    if (is_record_key(key)) {
        nvs_record_writes++;
    }
    return nvs_put(key, value, length);
}
static esp_err_t nvs_get_blob(nvs_handle_t handle, const char *key,
                              void *value, size_t *length)
{
    (void)handle;
    const nvs_entry_t *entry = nvs_find(key);
    if (entry == NULL) {
        return ESP_ERR_NVS_NOT_FOUND;
    }
    if (entry->length > *length) {
        return ESP_FAIL;
    }
    memcpy(value, entry->data, entry->length);
    *length = entry->length;
    return ESP_OK;
}
static esp_err_t nvs_erase_key(nvs_handle_t handle, const char *key)
{
    (void)handle;
    nvs_entry_t *entry = nvs_find(key);
    if (entry == NULL) {
        return ESP_ERR_NVS_NOT_FOUND;
    }
    if (is_record_key(key)) {
        nvs_record_erases++;
    }
    entry->used = false;
    return ESP_OK;
}
static esp_err_t nvs_commit(nvs_handle_t handle) { (void)handle; return ESP_OK; }

/* Collaborators outside this test. */
static void load_ble_slot_history(void) {}
static void load_peer_subscriptions(uint8_t slot) { (void)slot; }
static void load_peer_gatt_layout(uint8_t slot) { (void)slot; }
static void load_peer_local_address(uint8_t slot) { (void)slot; }
static void reload_ble_peer_slot(uint8_t slot) { (void)slot; }
static esp_err_t forget_peer_subscriptions(uint8_t slot) { (void)slot; return ESP_OK; }
static esp_err_t forget_peer_gatt_layout(uint8_t slot) { (void)slot; return ESP_OK; }
static unsigned adopted_slot;
static esp_err_t forget_peer_local_address(uint8_t slot) { (void)slot; return ESP_OK; }
static void adopt_link_local_address(uint8_t slot) { adopted_slot = slot; }
/* The address logic has its own test (test_ble_local_address.py). */
static void verify_advertising_address(void) {}
static atomic_uint_least32_t s_local_address_set_rejections;
/* Not atomic in the firmware, so not copied with the atomic statics. */
static uint16_t s_link_conn_id;
static void release_active_controls(void) {}
static void restore_peer_subscriptions(uint8_t slot, bool fresh) { (void)slot; (void)fresh; }
static void sync_peer_gatt_layout(uint8_t slot, bool fresh, const uint8_t *address)
{
    (void)slot; (void)fresh; (void)address;
}
static void publish_latest_battery_level(void) {}
static void clear_transport_runtime_state(codex_transport_t transport) { (void)transport; }
static void handle_output(const uint8_t *data, size_t length, codex_transport_t transport)
{
    (void)data; (void)length; (void)transport;
}
static void handle_auth_failure(const uint8_t *address, uint8_t reason)
{
    (void)address; (void)reason;
}
static void heap_checkpoint(const char *name) { (void)name; }
#define LIVENESS_BLE_HOST 0
#define LIVENESS_BLE_HOST_TIMEOUT_MS 0
static bool ble_host_liveness_probe(void) { return true; }
static void liveness_set_probe(int id, bool (*probe)(void), int timeout)
{
    (void)id; (void)probe; (void)timeout;
}
static void liveness_beat(int id) { (void)id; }
static void ble_fault_panic_at(uint32_t step) { (void)step; }
static void ble_start_ended(void) {}
static bool hid_subscriptions(uint32_t *mask) { (void)mask; return false; }
static void esp_hidd_gatts_event_handler(esp_gatts_cb_event_t event,
                                         esp_gatt_if_t gatts_if,
                                         esp_ble_gatts_cb_param_t *parameter)
{
    (void)event; (void)gatts_if; (void)parameter;
}
static void note_subscription_change(uint32_t before, uint32_t after, uint16_t handle)
{
    (void)before; (void)after; (void)handle;
}
static void note_ble_disconnect_event(uint8_t reason) { (void)reason; }

static unsigned slot_op_count, auto_return_count;
static unsigned last_slot_op, last_slot_op_slot, last_slot_op_source;
static void note_ble_slot_op(codex_micro_ble_slot_op_t op, uint8_t slot,
                             esp_err_t error, bool timeout,
                             codex_micro_ble_slot_source_t source)
{
    (void)error; (void)timeout;
    slot_op_count++;
    if (op == CODEX_MICRO_BLE_SLOT_OP_AUTO_RETURN) {
        auto_return_count++;
    }
    last_slot_op = op;
    last_slot_op_slot = slot;
    last_slot_op_source = source;
}

static bool test_bonded;
static esp_err_t ble_bond_exists(const uint8_t *address, bool *exists)
{
    (void)address;
    *exists = test_bonded;
    return ESP_OK;
}
static unsigned bond_removals, rejected_bond_removals, disconnect_requests;
static int last_security_accept = -1;
static esp_err_t esp_ble_remove_bond_device(uint8_t *address)
{
    (void)address;
    bond_removals++;
    return ESP_OK;
}
static void remove_failed_auth_bond(const uint8_t *address)
{
    (void)address;
    rejected_bond_removals++;
}
static esp_err_t esp_ble_gap_disconnect(uint8_t *address)
{
    (void)address;
    disconnect_requests++;
    return ESP_OK;
}
static esp_err_t esp_ble_gap_security_rsp(uint8_t *address, bool accept)
{
    (void)address;
    last_security_accept = accept;
    return ESP_OK;
}

/* Advertising: requests complete when the test pumps the stack. */
static unsigned advertising_stop_requests;
static bool pending_stop, pending_start;
static esp_err_t esp_ble_gap_stop_advertising(void)
{
    advertising_stop_requests++;
    pending_stop = true;
    return ESP_OK;
}
static void ensure_advertising(void)
{
    if (atomic_load(&s_ble_suspended) || atomic_load(&s_connected) ||
        atomic_load(&s_link_connected) ||
        atomic_load(&s_advertising_active) ||
        atomic_load(&s_advertising_starting) ||
        !atomic_load(&s_peer_filter_ready)) {
        return;
    }
    atomic_store(&s_advertising_starting, true);
    pending_start = true;
}

static esp_err_t refresh_peer_filter(void);
static void reset_advertising_speed(void);
static void restart_pairing_window_timer(void);
static void poll_pairing_window(void);
static esp_err_t finish_ble_slot_clear(uint8_t slot,
                                       const codex_micro_ble_peer_t *peer);
'''

PRODUCTION = (
    "static bool valid_ble_slot(uint8_t slot)",
    "static codex_micro_ble_peer_t peer_for_slot(uint8_t slot)",
    "static codex_micro_ble_identity_t peer_identity_for_slot(uint8_t slot)",
    "static ble_host_records_t host_records(void)",
    "static esp_err_t persist_active_peer_slot(uint8_t slot)",
    "static esp_err_t persist_peer_identity(",
    "static void close_pairing_window_after_pair_save(uint8_t slot)",
    "static esp_err_t load_ble_peer_slots(void)",
    "static bool peer_address_matches(",
    "static bool identity_address_matches(",
    "static bool peer_identity_matches(",
    "static uint8_t peer_slot_for_identity(",
    "static codex_micro_ble_identity_t take_pairing_identity(",
    "static void note_ble_reject(",
    "static esp_err_t request_ble_disconnect(",
    "static void publish_ble_slot_clear_result(uint8_t slot, esp_err_t error)",
    "static bool claim_pending_ble_slot_clear(",
    "static void complete_pending_ble_slot_clear(",
    "static esp_err_t finish_ble_slot_clear(",
    "static esp_err_t refresh_peer_filter(void)",
    "static bool active_slot_waits_to_pair(void)",
    "static void reset_advertising_speed(void)",
    "static void reset_advertising_speed_after_link(void)",
    "static void poll_advertising_speed(void)",
    "static void poll_pairing_input_boost(void)",
    "static void restart_pairing_window_timer(void)",
    "static void poll_pairing_window(void)",
    "static void note_ble_connect_time(void)",
    "static void hid_event(",
    "static void note_link_up(uint16_t conn_id)",
    "static void note_link_down(uint16_t conn_id)",
    "static void combined_gatts_event_handler(",
    "static void gap_event(",
    "void codex_micro_note_local_input(void)",
    "void codex_micro_set_ble_suspended_for_source(",
    "esp_err_t codex_micro_select_ble_slot_from(",
    "esp_err_t codex_micro_clear_ble_slot_from(",
    "uint8_t codex_micro_active_ble_slot(void)",
    "bool codex_micro_ble_pairing_window(",
    "bool codex_micro_take_ble_pairing_return(",
    "uint8_t codex_micro_ble_free_slot(void)",
    "bool codex_micro_take_ble_slot_redirect(",
    "bool codex_micro_take_ble_peer_rejection(",
)

DRIVER = r'''
static void fill(uint8_t *address, uint8_t seed)
{
    for (uint8_t index = 0; index < ESP_BD_ADDR_LEN; ++index) {
        address[index] = (uint8_t)(seed + index);
    }
}

/* Deliver the completions of advertising requests, as the BT task would. */
void t_pump(void)
{
    esp_ble_gap_cb_param_t parameter;
    memset(&parameter, 0, sizeof(parameter));
    for (int round = 0; round < 4; ++round) {
        if (pending_stop) {
            pending_stop = false;
            gap_event(ESP_GAP_BLE_ADV_STOP_COMPLETE_EVT, &parameter);
        }
        if (pending_start) {
            pending_start = false;
            gap_event(ESP_GAP_BLE_ADV_START_COMPLETE_EVT, &parameter);
        }
    }
}

/* Flash content before boot. */
void t_store_host(uint8_t slot, uint8_t seed)
{
    codex_micro_ble_peer_t peer = {.valid = 1u, .address_type = 1u};
    fill(peer.address, seed);
    (void)nvs_put(BLE_PEER_NVS_KEYS[slot - 1u], &peer, sizeof(peer));
}
void t_store_active(uint8_t slot) { (void)nvs_put("active", &slot, 1); }

int t_boot(void)
{
    const esp_err_t result = load_ble_peer_slots();
    atomic_store(&s_advertising_configured, true);
    atomic_store(&s_scan_response_configured, true);
    atomic_store(&s_peer_filter_ready, true);
    reset_advertising_speed();
    ensure_advertising();
    t_pump();
    return result;
}

/* One main-loop poll, in codex_micro_poll order. */
void t_poll(void)
{
    poll_pairing_input_boost();
    poll_advertising_speed();
    t_pump();
}
void t_run(uint32_t duration_ms)
{
    for (uint32_t elapsed = 0; elapsed < duration_ms; elapsed += 500u) {
        test_now_ms += 500u;
        t_poll();
    }
}
void t_advance(uint32_t duration_ms) { test_now_ms += duration_ms; }

/* The stack numbers each physical link; every service of the link gets the
 * same number in its own connect and disconnect event. */
static uint16_t test_conn_id = 1;
void t_next_link(void) { ++test_conn_id; }
void t_gatts_connect(uint8_t seed)
{
    esp_ble_gatts_cb_param_t parameter;
    memset(&parameter, 0, sizeof(parameter));
    fill(parameter.connect.remote_bda, seed);
    parameter.connect.conn_id = test_conn_id;
    combined_gatts_event_handler(ESP_GATTS_CONNECT_EVT, 0, &parameter);
}
/* The HID library's own notice of the same connection, from its event task. */
void t_hid_connect(void)
{
    hid_event(NULL, NULL, ESP_HIDD_CONNECT_EVENT, NULL);
}
void t_connect(uint8_t seed)
{
    t_gatts_connect(seed);
    t_hid_connect();
}
void t_security_request(uint8_t seed)
{
    esp_ble_gap_cb_param_t parameter;
    memset(&parameter, 0, sizeof(parameter));
    fill(parameter.ble_security.ble_req.bd_addr, seed);
    gap_event(ESP_GAP_BLE_SEC_REQ_EVT, &parameter);
}
void t_auth_success(uint8_t seed)
{
    esp_ble_gap_cb_param_t parameter;
    memset(&parameter, 0, sizeof(parameter));
    parameter.ble_security.auth_cmpl.success = true;
    parameter.ble_security.auth_cmpl.addr_type = 1u;
    fill(parameter.ble_security.auth_cmpl.bd_addr, seed);
    gap_event(ESP_GAP_BLE_AUTH_CMPL_EVT, &parameter);
    t_pump();
}
void t_gatts_disconnect_id(uint16_t conn_id)
{
    esp_ble_gatts_cb_param_t parameter;
    memset(&parameter, 0, sizeof(parameter));
    parameter.disconnect.conn_id = conn_id;
    combined_gatts_event_handler(ESP_GATTS_DISCONNECT_EVT, 0, &parameter);
    t_pump();
}
void t_gatts_disconnect(void) { t_gatts_disconnect_id(test_conn_id); }
/* The HID library's own notice of the same disconnect, from its event task. */
void t_hid_disconnect(void)
{
    hid_event(NULL, NULL, ESP_HIDD_DISCONNECT_EVENT, NULL);
    t_pump();
}
void t_disconnect(void)
{
    t_gatts_disconnect();
    t_hid_disconnect();
    t_next_link();
}
void t_bond_removed(uint8_t seed)
{
    esp_ble_gap_cb_param_t parameter;
    memset(&parameter, 0, sizeof(parameter));
    fill(parameter.remove_bond_dev_cmpl.bd_addr, seed);
    gap_event(ESP_GAP_BLE_REMOVE_BOND_DEV_COMPLETE_EVT, &parameter);
    t_pump();
}
void t_key(void) { codex_micro_note_local_input(); }
void t_suspend(bool suspended)
{
    codex_micro_set_ble_suspended_for_source(
        suspended, CODEX_MICRO_BLE_DISCONNECT_USB);
    t_pump();
}
int t_select(uint8_t slot)
{
    const esp_err_t result = codex_micro_select_ble_slot_from(
        slot, CODEX_MICRO_BLE_SLOT_SOURCE_KEY);
    t_pump();
    return result;
}
int t_clear(uint8_t slot)
{
    const esp_err_t result = codex_micro_clear_ble_slot_from(
        slot, CODEX_MICRO_BLE_SLOT_SOURCE_PROTOCOL);
    t_pump();
    return result;
}
void t_set_bonded(bool bonded) { test_bonded = bonded; }
void t_fail_active_writes(int error) { nvs_active_write_error = error; }

static unsigned paired_mask(bool stored)
{
    unsigned mask = 0;
    for (uint8_t slot = 1u; slot <= CODEX_MICRO_BLE_SLOT_COUNT; ++slot) {
        const bool paired = stored
            ? nvs_find(BLE_PEER_NVS_KEYS[slot - 1u]) != NULL
            : s_ble_peers[slot - 1u].valid == 1u;
        mask |= paired ? 1u << slot : 0u;
    }
    return mask;
}

unsigned t_adopted_slot(void) { return adopted_slot; }
unsigned t_read(int item)
{
    uint8_t slot = 0, other = 0;
    switch (item) {
    case 0: return codex_micro_active_ble_slot();
    case 1: return nvs_find("active") != NULL ? nvs_find("active")->data[0] : 255u;
    case 2: return nvs_active_writes;
    case 3: return codex_micro_ble_pairing_window(NULL, NULL);
    case 4: (void)codex_micro_ble_pairing_window(&slot, &other); return slot;
    case 5: (void)codex_micro_ble_pairing_window(&slot, &other); return other;
    case 6: return codex_micro_take_ble_pairing_return(&slot) ? slot : 0u;
    case 7: return codex_micro_take_ble_slot_redirect(&slot) ? slot : 0u;
    case 8: return codex_micro_take_ble_peer_rejection(&slot) ? slot : 0u;
    case 9: return atomic_load(&s_slow_advertising);
    case 10: return last_slot_op;
    case 11: return last_slot_op_slot;
    case 12: return last_slot_op_source;
    case 13: return auto_return_count;
    case 14: return nvs_record_erases;
    case 15: return bond_removals + rejected_bond_removals;
    case 16: return disconnect_requests;
    case 17: return (unsigned)last_security_accept;
    case 18: return paired_mask(false);
    case 19: return paired_mask(true);
    case 20: return atomic_load(&s_connected);
    case 21: return atomic_load(&s_ble_reject_count);
    case 22: return atomic_load(
        &s_ble_reject_reason_counts[CODEX_MICRO_BLE_REJECT_OTHER_SLOT]);
    case 23: return codex_micro_ble_free_slot();
    case 24: return atomic_load(&s_ble_connect_last_ms);
    case 25: return atomic_load(&s_ble_connect_max_ms);
    case 26: return atomic_load(&s_active_slot_persist_failures);
    case 27: return atomic_load(&s_fast_advertising_until_ms) - test_now_ms;
    case 28: return atomic_load(&s_ble_slot_clear_pending);
    case 29: return atomic_load(&s_advertising_active);
    case 30: return atomic_load(&s_pairing_return_slot);
    case 31: return nvs_record_writes;
    case 32: return atomic_load(
        &s_ble_reject_reason_counts[CODEX_MICRO_BLE_REJECT_UNKNOWN_HOST]);
    case 33: return advertising_stop_requests;
    case 34: return slot_op_count;
    case 35: return atomic_load(&s_link_connected);
    }
    return 0;
}
'''

(ACTIVE, NVS_ACTIVE, ACTIVE_WRITES, WINDOW_OPEN, WINDOW_SLOT, WINDOW_RETURN,
 TAKE_RETURN, TAKE_REDIRECT, TAKE_REJECTION, SLOW, LAST_OP, LAST_OP_SLOT,
 LAST_OP_SOURCE, AUTO_RETURNS, RECORD_ERASES, BOND_REMOVALS, DISCONNECTS,
 SECURITY_ACCEPT, PAIRED, STORED_PAIRED, CONNECTED, REJECTS,
 REJECTS_OTHER_SLOT, FREE_SLOT, CONNECT_LAST_MS, CONNECT_MAX_MS,
 PERSIST_FAILURES, FAST_REMAINING_MS, CLEAR_PENDING, ADVERTISING,
 RETURN_SLOT, RECORD_WRITES, REJECTS_UNKNOWN, STOP_REQUESTS,
 SLOT_OPS, LINK) = range(36)


def hosts(*slots):
    return sum(1 << slot for slot in slots)


class PairingWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        source = BLE_SOURCE.read_text()
        header = HEADER.read_text()
        code = PRELUDE
        for name in ("codex_micro_ble_reject_reason_t",
                     "codex_micro_ble_disconnect_source_t",
                     "codex_micro_ble_slot_op_t",
                     "codex_micro_ble_slot_source_t"):
            code += typedef_block(header, name) + "\n"
        for name in ("codex_micro_ble_peer_t", "codex_micro_ble_identity_t"):
            code += typedef_block(source, name) + "\n"
        code += atomic_statics(source) + "\n" + STATE
        for signature in PRODUCTION:
            code += c_function(source, signature) + "\n"
        code += DRIVER
        path = Path(cls.temp.name) / "pairing_window.c"
        path.write_text(code)
        cls.library_path = path.with_suffix(".so")
        subprocess.run(
            ["cc", "-std=c11", "-shared", "-fPIC", "-Wall", "-Wextra",
             "-Werror", "-Wno-unused-function", "-Wno-unused-variable",
             "-I", str(CODEX / "include"), str(path), str(POLICY_SOURCE),
             "-o", str(cls.library_path)],
            check=True, capture_output=True,
        )
        cls.copies = 0

    def setUp(self):
        # A fresh copy per test: every static starts from zero, as at boot.
        type(self).copies += 1
        copy = Path(self.temp.name) / f"window_{self.copies}.so"
        shutil.copy(self.library_path, copy)
        lib = self.lib = ctypes.CDLL(str(copy))
        for name in ("t_store_host",):
            getattr(lib, name).argtypes = [ctypes.c_uint8, ctypes.c_uint8]
        for name in ("t_store_active", "t_gatts_connect", "t_connect",
                     "t_security_request", "t_auth_success", "t_bond_removed",
                     "t_select", "t_clear"):
            getattr(lib, name).argtypes = [ctypes.c_uint8]
        for name in ("t_run", "t_advance"):
            getattr(lib, name).argtypes = [ctypes.c_uint32]
        for name in ("t_suspend", "t_set_bonded"):
            getattr(lib, name).argtypes = [ctypes.c_bool]
        lib.t_fail_active_writes.argtypes = [ctypes.c_int]
        lib.t_read.argtypes = [ctypes.c_int]
        lib.t_read.restype = ctypes.c_uint

    def read(self, item):
        return self.lib.t_read(item)

    def boot(self, active, *paired):
        """Flash holds these hosts (address 0x10 * slot) and this "active"."""
        for slot in paired:
            self.lib.t_store_host(slot, 0x10 * slot)
        if active is not None:
            self.lib.t_store_active(active)
        self.assertEqual(self.lib.t_boot(), 0)
        self.assertTrue(self.read(ADVERTISING))

    def pair(self, address):
        """A host pairs afresh: it sends a pairing request, then encrypts."""
        self.lib.t_connect(address)
        self.lib.t_security_request(address)
        self.lib.t_auth_success(address)

    def reconnect(self, address):
        """A host reconnects with the keys it holds: no pairing request."""
        self.lib.t_connect(address)
        self.lib.t_auth_success(address)

    # --- the link state follows the stack's own events, in their order ---

    def test_a_late_library_connect_notice_does_not_undo_the_encrypted_link(self):
        # A bonded Windows host reconnects and encrypts within milliseconds.
        # The HID library reports the same connection through its own event
        # task, which has no ordering against the pairing events and can run
        # after them. Resetting there left the link up with no input
        # (CP01-441BF6FFE080, 2026-11-12: Windows showed connected, keys and
        # knob did nothing, every time it was switched to from the Mac).
        self.boot(1, 1)
        self.lib.t_gatts_connect(0x10)
        self.lib.t_auth_success(0x10)
        self.assertTrue(self.read(CONNECTED))
        self.lib.t_hid_connect()
        self.assertTrue(self.read(CONNECTED))
        self.assertTrue(self.read(LINK))
        self.assertFalse(self.read(ADVERTISING))

    def test_a_late_library_disconnect_notice_does_not_undo_the_next_link(self):
        self.boot(1, 1, 2)
        self.reconnect(0x10)
        self.assertEqual(self.lib.t_select(2), 0)
        # The stack reports the old link gone: advertising for the next host
        # starts here, not when the library's notice gets its turn.
        self.lib.t_gatts_disconnect()
        self.assertFalse(self.read(LINK))
        self.assertTrue(self.read(ADVERTISING))
        self.lib.t_next_link()
        self.lib.t_gatts_connect(0x20)
        self.lib.t_auth_success(0x20)
        self.assertTrue(self.read(CONNECTED))
        self.lib.t_hid_disconnect()  # the old link's notice, late
        self.assertTrue(self.read(CONNECTED))
        self.assertTrue(self.read(LINK))
        self.assertFalse(self.read(ADVERTISING))

    def test_a_link_is_reported_once_per_service_and_handled_once(self):
        self.boot(1, 1)
        self.lib.t_gatts_connect(0x10)
        self.lib.t_gatts_connect(0x10)
        self.lib.t_auth_success(0x10)
        # Another service's connect event for the same link changes nothing.
        self.lib.t_gatts_connect(0x10)
        self.assertTrue(self.read(CONNECTED))
        self.lib.t_gatts_disconnect()
        self.assertFalse(self.read(CONNECTED))
        self.assertFalse(self.read(LINK))
        self.assertTrue(self.read(ADVERTISING))
        self.lib.t_gatts_disconnect()
        self.lib.t_hid_disconnect()
        self.assertFalse(self.read(LINK))
        self.assertTrue(self.read(ADVERTISING))
        self.assertEqual(self.read(REJECTS), 0)

    def test_a_disconnect_of_another_link_does_not_end_this_one(self):
        self.boot(1, 1)
        self.reconnect(0x10)
        self.lib.t_gatts_disconnect_id(200)
        self.assertTrue(self.read(CONNECTED))
        self.assertTrue(self.read(LINK))
        self.assertFalse(self.read(ADVERTISING))
        self.lib.t_disconnect()
        self.assertFalse(self.read(CONNECTED))
        self.assertTrue(self.read(ADVERTISING))

    # --- a. selecting a slot ---

    def test_selecting_an_empty_slot_beside_a_host_is_not_written(self):
        self.boot(1, 1)
        self.assertEqual(self.lib.t_select(2), 0)
        self.assertEqual(self.read(ACTIVE), 2)
        self.assertEqual(self.read(NVS_ACTIVE), 1)
        self.assertEqual(self.read(ACTIVE_WRITES), 0)
        self.assertTrue(self.read(WINDOW_OPEN))
        self.assertEqual((self.read(WINDOW_SLOT), self.read(WINDOW_RETURN)),
                         (2, 1))
        self.assertEqual((self.read(LAST_OP), self.read(LAST_OP_SLOT)),
                         (OP_SELECT, 2))

    def test_selecting_a_paired_slot_is_written_as_before(self):
        self.boot(1, 1, 2)
        self.assertEqual(self.lib.t_select(2), 0)
        self.assertEqual((self.read(ACTIVE), self.read(NVS_ACTIVE)), (2, 2))
        self.assertEqual(self.read(ACTIVE_WRITES), 1)
        self.assertFalse(self.read(WINDOW_OPEN))
        self.assertEqual(self.read(RETURN_SLOT), 0)

    def test_selecting_a_slot_with_no_host_anywhere_is_written_as_before(self):
        self.boot(1)
        self.assertEqual(self.lib.t_select(3), 0)
        self.assertEqual((self.read(ACTIVE), self.read(NVS_ACTIVE)), (3, 3))
        self.assertEqual(self.read(ACTIVE_WRITES), 1)
        self.assertFalse(self.read(WINDOW_OPEN))
        self.lib.t_run(PAIRING_MS + 5000)
        self.assertEqual(self.read(ACTIVE), 3)
        self.assertEqual(self.read(AUTO_RETURNS), 0)

    def test_a_paired_slot_chosen_from_the_window_closes_it(self):
        self.boot(1, 1, 3)
        self.lib.t_select(2)
        self.assertEqual(self.lib.t_select(3), 0)
        self.assertEqual((self.read(ACTIVE), self.read(NVS_ACTIVE)), (3, 3))
        self.assertEqual(self.read(RETURN_SLOT), 0)
        self.lib.t_run(PAIRING_MS + 5000)
        self.assertEqual(self.read(ACTIVE), 3)
        self.assertEqual(self.read(AUTO_RETURNS), 0)

    def test_another_empty_slot_keeps_the_host_to_return_to(self):
        self.boot(3, 3)
        self.lib.t_select(1)
        self.lib.t_select(2)
        self.assertEqual((self.read(WINDOW_SLOT), self.read(WINDOW_RETURN)),
                         (2, 3))
        self.assertEqual((self.read(NVS_ACTIVE), self.read(ACTIVE_WRITES)),
                         (3, 0))

    def test_pressing_the_same_empty_slot_again_keeps_the_return_host(self):
        # Hosts on 2 and 3, working on 3. The lowest host is 2, but the
        # window must still go back to 3 after the combo is pressed twice.
        self.boot(3, 2, 3)
        self.lib.t_select(1)
        self.lib.t_select(1)
        self.assertEqual((self.read(WINDOW_SLOT), self.read(WINDOW_RETURN)),
                         (1, 3))
        self.lib.t_run(PAIRING_MS + 3000)
        self.assertEqual((self.read(ACTIVE), self.read(NVS_ACTIVE)), (3, 3))

    # --- pairing in the window ---

    def test_pairing_in_the_window_writes_the_new_slot(self):
        self.boot(1, 1)
        self.lib.t_select(2)
        self.pair(STRANGER)
        self.assertEqual(self.read(SECURITY_ACCEPT), 1)
        self.assertTrue(self.read(CONNECTED))
        self.assertEqual(self.read(PAIRED), hosts(1, 2))
        self.assertEqual(self.read(STORED_PAIRED), hosts(1, 2))
        self.assertEqual((self.read(ACTIVE), self.read(NVS_ACTIVE)), (2, 2))
        self.assertEqual(self.read(ACTIVE_WRITES), 1)
        self.assertFalse(self.read(WINDOW_OPEN))
        self.assertEqual(self.read(RETURN_SLOT), 0)
        self.assertEqual(self.read(LAST_OP), OP_PAIR_SAVE)
        self.assertEqual((self.read(RECORD_ERASES), self.read(BOND_REMOVALS)),
                         (0, 0))
        # The address the host paired on stays with its slot.
        self.assertEqual(self.lib.t_adopted_slot(), 2)
        # No advertising beside the new host's link: a host remembered in
        # another slot must not get in as a second link.
        self.assertFalse(self.read(ADVERTISING))
        # The new host stays the active one; nothing returns later.
        self.lib.t_disconnect()
        self.assertTrue(self.read(ADVERTISING))
        self.lib.t_run(PAIRING_MS + 5000)
        self.assertEqual(self.read(ACTIVE), 2)
        self.assertEqual(self.read(AUTO_RETURNS), 0)

    def test_restart_before_pairing_starts_on_the_previous_host(self):
        self.boot(1, 1)
        self.lib.t_select(2)
        # Only RAM changed, so flash still describes the state before.
        self.assertEqual(self.read(NVS_ACTIVE), 1)
        self.assertEqual(self.read(STORED_PAIRED), hosts(1))

    def test_failed_active_write_after_pairing_keeps_the_pairing(self):
        self.boot(1, 1)
        self.lib.t_select(2)
        self.lib.t_fail_active_writes(-1)
        self.pair(STRANGER)
        self.assertTrue(self.read(CONNECTED))
        self.assertEqual(self.read(STORED_PAIRED), hosts(1, 2))
        self.assertEqual(self.read(NVS_ACTIVE), 1)  # still a remembered host
        self.assertEqual(self.read(PERSIST_FAILURES), 1)
        self.assertEqual((self.read(RECORD_ERASES), self.read(BOND_REMOVALS)),
                         (0, 0))
        self.assertEqual(self.read(DISCONNECTS), 0)

    # --- b. the window ends ---

    def test_window_returns_to_the_previous_host_once(self):
        self.boot(1, 1)
        self.lib.t_select(2)
        self.lib.t_run(PAIRING_MS - 1000)
        self.assertEqual(self.read(ACTIVE), 2)
        self.lib.t_run(3000)
        self.assertEqual(self.read(ACTIVE), 1)
        self.assertFalse(self.read(WINDOW_OPEN))
        self.assertTrue(self.read(ADVERTISING))
        self.assertFalse(self.read(SLOW))  # fast again for the host
        self.assertEqual(
            (self.read(LAST_OP), self.read(LAST_OP_SLOT),
             self.read(LAST_OP_SOURCE)), (OP_AUTO_RETURN, 1, SOURCE_AUTO))
        self.assertEqual(self.read(TAKE_RETURN), 1)
        self.assertEqual(self.read(TAKE_RETURN), 0)
        self.lib.t_run(3 * PAIRING_MS)
        self.assertEqual(self.read(AUTO_RETURNS), 1)
        self.assertEqual(self.read(ACTIVE), 1)
        # Nothing was written or deleted on the way.
        self.assertEqual(self.read(ACTIVE_WRITES), 0)
        self.assertEqual((self.read(RECORD_ERASES), self.read(BOND_REMOVALS),
                          self.read(RECORD_WRITES)), (0, 0, 0))
        self.assertEqual(self.read(STORED_PAIRED), hosts(1))

    def test_return_waits_for_the_slow_advertising_restart(self):
        self.boot(1, 1)
        self.lib.t_select(2)
        stops = self.read(STOP_REQUESTS)
        self.lib.t_run(PAIRING_MS + 3000)
        self.assertEqual(self.read(ACTIVE), 1)
        # One stop for the slow transition, one for the return; never two
        # stops in flight at once (each completed before the next).
        self.assertEqual(self.read(STOP_REQUESTS), stops + 2)

    def test_local_input_restarts_the_window(self):
        self.boot(1, 1)
        self.lib.t_select(2)
        self.lib.t_run(PAIRING_MS - 10000)
        self.lib.t_key()
        self.lib.t_run(PAIRING_MS - 10000)
        self.assertEqual(self.read(ACTIVE), 2)
        self.lib.t_run(13000)
        self.assertEqual(self.read(ACTIVE), 1)

    def test_a_host_reaching_the_empty_slot_restarts_the_window(self):
        self.boot(1, 1)
        self.lib.t_select(2)
        self.lib.t_run(100000)
        self.assertLess(self.read(FAST_REMAINING_MS), PAIRING_MS)
        self.lib.t_gatts_connect(STRANGER)
        self.assertEqual(self.read(FAST_REMAINING_MS), PAIRING_MS)
        self.assertFalse(self.read(SLOW))
        self.lib.t_advance(50000)
        self.lib.t_security_request(STRANGER)
        self.assertEqual(self.read(SECURITY_ACCEPT), 1)
        self.assertEqual(self.read(FAST_REMAINING_MS), PAIRING_MS)

    def test_a_remembered_host_retrying_does_not_hold_the_window_open(self):
        # The computer of slot 1 sits next to the device and reconnects every
        # few seconds while slot 2 waits to pair. It is refused each time and
        # must not restart the window, or the window would never return.
        self.boot(1, 1)
        self.lib.t_select(2)
        self.lib.t_run(100000)
        remaining = self.read(FAST_REMAINING_MS)
        self.assertLess(remaining, PAIRING_MS)
        # One full retry: link, encryption with its old keys, refusal, drop.
        self.reconnect(0x10)
        self.lib.t_disconnect()
        self.assertEqual(self.read(REJECTS_OTHER_SLOT), 1)
        self.assertLessEqual(self.read(FAST_REMAINING_MS), remaining)
        self.assertEqual(self.read(ACTIVE), 2)
        # It keeps doing that every 2 s (as measured on a Mac) for the whole
        # window and beyond; the window still ends and BLE goes back to it.
        for _ in range((PAIRING_MS + 20000) // 2000):
            self.lib.t_run(2000)
            if self.read(ACTIVE) == 1:
                break
            self.reconnect(0x10)
            self.lib.t_disconnect()
        self.assertEqual(self.read(ACTIVE), 1)
        self.assertEqual(self.read(TAKE_RETURN), 1)
        self.assertEqual((self.read(RECORD_ERASES), self.read(BOND_REMOVALS)),
                         (0, 0))

    def test_a_stranger_who_gave_up_does_not_stop_the_return(self):
        # A new host connects, drops, and never comes back: the window gets
        # its full time again from that drop, then returns.
        self.boot(1, 1)
        self.lib.t_select(2)
        self.lib.t_run(100000)
        self.lib.t_connect(STRANGER)
        self.lib.t_disconnect()
        self.lib.t_run(PAIRING_MS - 1000)
        self.assertEqual(self.read(ACTIVE), 2)
        self.lib.t_run(3000)
        self.assertEqual(self.read(ACTIVE), 1)

    def test_connect_to_a_paired_slot_leaves_the_fast_window_alone(self):
        self.boot(1, 1)
        self.lib.t_advance(10000)
        remaining = self.read(FAST_REMAINING_MS)
        self.lib.t_gatts_connect(0x10)
        self.lib.t_security_request(0x10)
        self.assertEqual(self.read(FAST_REMAINING_MS), remaining)

    def test_no_return_while_a_link_is_up_then_a_full_window_again(self):
        self.boot(1, 1)
        self.lib.t_select(2)
        self.lib.t_run(PAIRING_MS - 10000)
        self.lib.t_connect(STRANGER)
        self.lib.t_run(2 * PAIRING_MS)
        self.assertEqual(self.read(ACTIVE), 2)
        self.lib.t_disconnect()
        self.lib.t_run(PAIRING_MS - 1000)
        self.assertEqual(self.read(ACTIVE), 2)
        self.lib.t_run(3000)
        self.assertEqual(self.read(ACTIVE), 1)

    def test_no_return_while_suspended_then_a_full_window_again(self):
        self.boot(1, 1)
        self.lib.t_select(2)
        self.lib.t_run(PAIRING_MS - 10000)
        self.lib.t_suspend(True)
        self.lib.t_run(2 * PAIRING_MS)
        self.assertEqual(self.read(ACTIVE), 2)
        self.lib.t_suspend(False)
        self.lib.t_run(PAIRING_MS - 1000)
        self.assertEqual(self.read(ACTIVE), 2)
        self.lib.t_run(3000)
        self.assertEqual(self.read(ACTIVE), 1)

    def test_no_return_while_a_clear_is_in_progress(self):
        self.boot(1, 1, 3)
        self.lib.t_select(2)
        self.lib.t_set_bonded(True)
        self.assertEqual(self.lib.t_clear(3), 0)  # bond removal in flight
        self.assertTrue(self.read(CLEAR_PENDING))
        self.lib.t_run(PAIRING_MS + 5000)
        self.assertEqual(self.read(ACTIVE), 2)
        self.assertEqual(self.read(AUTO_RETURNS), 0)

    # --- c. boot ---

    def test_boot_on_an_empty_stored_slot_uses_the_lowest_host_in_ram(self):
        self.boot(2, 3)
        self.assertEqual(self.read(ACTIVE), 3)
        self.assertEqual((self.read(NVS_ACTIVE), self.read(ACTIVE_WRITES)),
                         (2, 0))
        self.assertFalse(self.read(WINDOW_OPEN))
        self.lib.t_run(PAIRING_MS + 5000)
        self.assertEqual(self.read(AUTO_RETURNS), 0)

    def test_boot_keeps_a_stored_slot_that_has_a_host(self):
        self.boot(3, 1, 3)
        self.assertEqual(self.read(ACTIVE), 3)
        self.assertEqual(self.read(ACTIVE_WRITES), 0)

    def test_boot_with_no_host_keeps_the_stored_slot(self):
        self.boot(2)
        self.assertEqual(self.read(ACTIVE), 2)
        self.assertEqual(self.read(ACTIVE_WRITES), 0)

    def test_boot_with_an_invalid_stored_slot(self):
        self.boot(9, 2, 3)
        self.assertEqual(self.read(ACTIVE), 2)
        self.assertEqual((self.read(NVS_ACTIVE), self.read(ACTIVE_WRITES)),
                         (9, 0))

    # --- f. after a clear ---

    def test_clear_keeps_ram_on_the_empty_slot_and_flash_on_another_host(self):
        self.boot(2, 1, 2)
        self.assertEqual(self.lib.t_clear(2), 0)
        self.assertEqual(self.read(PAIRED), hosts(1))
        self.assertEqual(self.read(STORED_PAIRED), hosts(1))
        self.assertEqual((self.read(ACTIVE), self.read(NVS_ACTIVE)), (2, 1))
        self.assertTrue(self.read(WINDOW_OPEN))
        self.assertEqual((self.read(WINDOW_SLOT), self.read(WINDOW_RETURN)),
                         (2, 1))
        self.lib.t_run(PAIRING_MS + 3000)
        self.assertEqual(self.read(ACTIVE), 1)
        self.assertEqual(self.read(TAKE_RETURN), 1)

    def test_clear_of_another_slot_keeps_flash_on_the_host_in_use(self):
        # Working on 3, forgetting 2: a restart must stay on 3, not move to
        # the lowest host (1).
        self.boot(3, 1, 2, 3)
        self.assertEqual(self.lib.t_clear(2), 0)
        self.assertEqual(self.read(PAIRED), hosts(1, 3))
        self.assertEqual((self.read(ACTIVE), self.read(NVS_ACTIVE)), (2, 3))
        self.assertEqual((self.read(WINDOW_SLOT), self.read(WINDOW_RETURN)),
                         (2, 3))

    def test_clear_inside_a_window_keeps_the_windows_return_host(self):
        self.boot(3, 2, 3)
        self.lib.t_select(1)
        self.assertEqual(self.lib.t_clear(2), 0)
        self.assertEqual(self.read(PAIRED), hosts(3))
        self.assertEqual((self.read(ACTIVE), self.read(NVS_ACTIVE)), (2, 3))
        self.assertEqual(self.read(WINDOW_RETURN), 3)

    def test_clear_of_the_only_host_points_flash_at_the_cleared_slot(self):
        self.boot(2, 2)
        self.assertEqual(self.lib.t_clear(2), 0)
        self.assertEqual(self.read(PAIRED), 0)
        self.assertEqual((self.read(ACTIVE), self.read(NVS_ACTIVE)), (2, 2))
        self.assertFalse(self.read(WINDOW_OPEN))
        self.assertEqual(self.read(RETURN_SLOT), 0)
        self.lib.t_run(PAIRING_MS + 5000)
        self.assertEqual(self.read(AUTO_RETURNS), 0)

    def test_clear_completed_by_the_stack_behaves_the_same(self):
        self.boot(1, 1, 3)
        self.lib.t_set_bonded(True)
        self.assertEqual(self.lib.t_clear(3), 0)
        self.assertEqual(self.read(PAIRED), hosts(1, 3))  # not yet
        self.lib.t_set_bonded(False)
        self.lib.t_bond_removed(0x30)
        self.assertEqual(self.read(PAIRED), hosts(1))
        self.assertEqual((self.read(ACTIVE), self.read(NVS_ACTIVE)), (3, 1))
        self.assertEqual((self.read(WINDOW_SLOT), self.read(WINDOW_RETURN)),
                         (3, 1))

    # --- h. hosts of other slots during the window ---

    def test_other_slots_host_is_rejected_in_the_window_without_redirect(self):
        # The neighbour: remembered in slot 3, reconnecting with its old keys
        # while slot 2 waits for a new host. It must not take the window.
        self.boot(1, 1, 3)
        self.lib.t_select(2)
        self.reconnect(0x30)
        self.assertFalse(self.read(CONNECTED))
        self.assertEqual(self.read(DISCONNECTS), 1)
        self.assertEqual((self.read(REJECTS), self.read(REJECTS_OTHER_SLOT)),
                         (1, 1))
        self.assertEqual(self.read(TAKE_REDIRECT), 0)
        self.assertEqual(self.read(ACTIVE), 2)
        self.assertTrue(self.read(WINDOW_OPEN))
        self.assertEqual(self.read(PAIRED), hosts(1, 3))
        self.assertEqual(self.read(STORED_PAIRED), hosts(1, 3))
        self.assertEqual((self.read(RECORD_ERASES), self.read(BOND_REMOVALS)),
                         (0, 0))

    def test_remembered_host_pairing_afresh_in_the_window_goes_to_its_slot(self):
        # Windows 11, 2026-11-12: the computer of slot 1 removed this device
        # and added it again after the user pressed the combo of empty slot 2.
        # It paired, was then refused as "another slot's host" 83 times, and
        # showed connected/not connected in turn. It is the same computer:
        # keep it, on its own slot.
        self.boot(1, 1)
        self.lib.t_select(2)
        self.assertEqual((self.read(WINDOW_SLOT), self.read(WINDOW_RETURN)),
                         (2, 1))
        self.pair(0x10)
        self.assertEqual(self.read(SECURITY_ACCEPT), 1)
        self.assertTrue(self.read(CONNECTED))
        self.assertEqual(self.read(DISCONNECTS), 0)
        self.assertEqual(self.read(REJECTS), 0)
        self.assertEqual((self.read(ACTIVE), self.read(NVS_ACTIVE)), (1, 1))
        self.assertFalse(self.read(WINDOW_OPEN))
        self.assertEqual(self.read(RETURN_SLOT), 0)
        self.assertEqual(self.read(PAIRED), hosts(1))        # not stored twice
        self.assertEqual(self.read(STORED_PAIRED), hosts(1))
        self.assertEqual((self.read(LAST_OP), self.read(LAST_OP_SLOT)),
                         (OP_PAIR_SAVE, 1))
        self.assertEqual((self.read(RECORD_ERASES), self.read(BOND_REMOVALS)),
                         (0, 0))
        # The computer now knows the device by the address it just paired
        # on; that address moves to slot 1 with it.
        self.assertEqual(self.lib.t_adopted_slot(), 1)
        self.assertFalse(self.read(ADVERTISING))
        # It stays there: nothing returns or reopens later.
        self.lib.t_disconnect()
        self.lib.t_run(PAIRING_MS + 5000)
        self.assertEqual(self.read(ACTIVE), 1)
        self.assertEqual(self.read(AUTO_RETURNS), 0)
        self.assertEqual(self.read(TAKE_RETURN), 0)

    def test_remembered_host_pairing_afresh_goes_to_its_slot_not_the_return_slot(self):
        # Working on 3, window on 2, and the computer of slot 1 pairs afresh:
        # it belongs to slot 1, wherever the window would have returned.
        self.boot(3, 1, 3)
        self.lib.t_select(2)
        self.assertEqual(self.read(WINDOW_RETURN), 3)
        self.pair(0x10)
        self.assertTrue(self.read(CONNECTED))
        self.assertEqual((self.read(ACTIVE), self.read(NVS_ACTIVE)), (1, 1))
        self.assertEqual(self.read(PAIRED), hosts(1, 3))
        self.assertEqual(self.read(REJECTS), 0)

    def test_a_new_host_is_still_stored_in_the_empty_slot(self):
        self.boot(1, 1)
        self.lib.t_select(2)
        self.pair(STRANGER)
        self.assertTrue(self.read(CONNECTED))
        self.assertEqual((self.read(ACTIVE), self.read(NVS_ACTIVE)), (2, 2))
        self.assertEqual(self.read(PAIRED), hosts(1, 2))
        self.assertEqual((self.read(LAST_OP), self.read(LAST_OP_SLOT)),
                         (OP_PAIR_SAVE, 2))

    def test_selecting_the_active_slot_again_is_not_recorded_again(self):
        # One press of K3+K9 reached the firmware six times within two
        # seconds on a sample and filled the eight-entry history.
        self.boot(1, 1)
        before = self.read(SLOT_OPS)
        self.lib.t_select(2)
        self.assertEqual(self.read(SLOT_OPS), before + 1)
        for _ in range(5):
            self.lib.t_select(2)
        self.assertEqual(self.read(SLOT_OPS), before + 1)
        self.assertEqual((self.read(WINDOW_SLOT), self.read(WINDOW_RETURN)),
                         (2, 1))
        self.lib.t_select(1)
        self.assertEqual(self.read(SLOT_OPS), before + 2)

    def test_other_slots_host_on_a_paired_slot_still_shows_the_redirect(self):
        self.boot(1, 1, 3)
        self.lib.t_connect(0x30)
        self.assertEqual(self.read(DISCONNECTS), 1)
        self.assertEqual(self.read(REJECTS_OTHER_SLOT), 1)
        self.assertEqual(self.read(TAKE_REDIRECT), 3)

    def test_stranger_on_a_paired_slot_is_still_rejected_with_a_notice(self):
        self.boot(1, 1)
        self.lib.t_connect(STRANGER)
        self.assertEqual(self.read(DISCONNECTS), 1)
        self.assertEqual(self.read(REJECTS_UNKNOWN), 1)
        self.assertEqual(self.read(TAKE_REJECTION), 1)
        self.lib.t_security_request(STRANGER)
        self.assertEqual(self.read(SECURITY_ACCEPT), 0)

    def test_own_host_still_connects(self):
        self.boot(1, 1)
        self.pair(0x10)
        self.assertEqual(self.read(SECURITY_ACCEPT), 1)
        self.assertTrue(self.read(CONNECTED))
        self.assertEqual((self.read(REJECTS), self.read(DISCONNECTS)), (0, 0))

    # --- diagnostics and the small interface ---

    def test_free_slot(self):
        self.boot(1, 1, 3)
        self.assertEqual(self.read(FREE_SLOT), 2)
        self.lib.t_select(2)
        self.pair(STRANGER)
        self.assertEqual(self.read(FREE_SLOT), 0)

    def test_connect_time_is_measured_from_the_start_of_advertising(self):
        self.boot(1, 1)
        self.lib.t_advance(5000)
        self.pair(0x10)
        self.assertEqual(
            (self.read(CONNECT_LAST_MS), self.read(CONNECT_MAX_MS)),
            (5000, 5000))
        self.lib.t_disconnect()
        self.lib.t_advance(2000)
        self.pair(0x10)
        self.assertEqual(
            (self.read(CONNECT_LAST_MS), self.read(CONNECT_MAX_MS)),
            (2000, 5000))


class PairingWindowContractTests(unittest.TestCase):
    def setUp(self):
        self.source = BLE_SOURCE.read_text()

    def test_who_may_connect_is_decided_as_before(self):
        gatts = c_function(self.source,
                           "static void combined_gatts_event_handler(")
        self.assertIn(
            "const bool rejected = peer.valid == 1u &&\n"
            "            !peer_identity_matches(slot, "
            "parameter->connect.remote_bda, NULL);", gatts)
        self.assertIn("if (suspended || rejected) {", gatts)
        gap = c_function(self.source, "static void gap_event(")
        self.assertIn(
            "const bool accept = !suspended &&\n"
            "            (peer.valid != 1u || peer_identity_matches(\n"
            "                slot, parameter->ble_security.ble_req.bd_addr, "
            "NULL));", gap)
        auth = gap[gap.index("case ESP_GAP_BLE_AUTH_CMPL_EVT:"):]
        self.assertIn(
            "if (peer.valid == 1u &&\n"
            "                !peer_identity_matches(\n"
            "                    slot, parameter->ble_security.auth_cmpl.bd_addr,\n"
            "                    &presented_identity)) {", auth)
        # Another slot's host is kept only when it paired afresh in the
        # pairing window; otherwise it is refused as before.
        self.assertIn(
            "if (paired_slot != 0u && peer.valid != 1u &&\n"
            "                (atomic_load(&s_link_pairing_requested) ||\n"
            "                 presented_identity.valid == 1u)) {", auth)
        self.assertIn("            } else if (paired_slot != 0u) {\n", auth)

    def test_active_slot_is_written_only_after_the_record(self):
        gap = c_function(self.source, "static void gap_event(")
        saved = gap.index("persist_peer_identity(slot, &peer, &identity);")
        failed = gap.index("if (result != ESP_OK) {", saved)
        closed = gap.index("close_pairing_window_after_pair_save(slot);")
        self.assertLess(saved, failed)
        self.assertLess(failed, closed)
        self.assertEqual(gap.count("close_pairing_window_after_pair_save("), 1)

    def test_automatic_paths_cannot_delete_or_write(self):
        for signature in ("static void poll_pairing_window(void)",
                          "static void restart_pairing_window_timer(void)",
                          "static void close_pairing_window_after_pair_save("):
            body = c_function(self.source, signature)
            self.assertNotIn("esp_ble_remove_bond_device", body)
            self.assertNotIn("nvs_erase_key", body)
            self.assertNotIn("finish_ble_slot_clear", body)
        window = c_function(self.source, "static void poll_pairing_window(void)")
        self.assertNotIn("persist_", window)
        self.assertNotIn("nvs_", window)
        load = c_function(self.source, "static esp_err_t load_ble_peer_slots(void)")
        self.assertNotIn("nvs_set", load)
        self.assertNotIn("persist_", load)
        # Bond removal stays where it was: rejected pairing (and its retry),
        # orphan cleanup, the clear command and factory reset.
        self.assertEqual(self.source.count("esp_ble_remove_bond_device("), 5)

    def test_window_poll_runs_from_the_main_loop(self):
        poll = c_function(self.source, "void codex_micro_poll(void)")
        self.assertIn("poll_pairing_input_boost();", poll)
        boost = c_function(self.source, "static void poll_pairing_input_boost(void)")
        self.assertIn("poll_pairing_window();", boost)

    def test_factory_reset_and_init_clear_the_window(self):
        for signature in ("esp_err_t codex_micro_factory_reset(void)",
                          "esp_err_t codex_micro_init("):
            body = c_function(self.source, signature)
            self.assertIn("atomic_store(&s_pairing_return_slot, 0u);", body)
            self.assertIn("atomic_store(&s_ble_pairing_return_pending, 0u);",
                          body)

    def test_interface_exists_in_header_transport_and_stub(self):
        for text in (HEADER.read_text(), self.source, STUB.read_text()):
            for name in ("codex_micro_ble_pairing_window(",
                         "codex_micro_take_ble_pairing_return(",
                         "codex_micro_ble_free_slot("):
                self.assertIn(name, text)
        header = HEADER.read_text()
        self.assertIn("CODEX_MICRO_BLE_SLOT_OP_AUTO_RETURN", header)
        self.assertIn("CODEX_MICRO_BLE_SLOT_SOURCE_AUTO", header)

    def test_status_adds_fields_without_changing_existing_ones(self):
        protocol = PROTOCOL.read_text()
        for name in ('"auto_return"', '"auto"', '"ble_pairing_window"',
                     '"ble_connect_time"', '"ble_reject_reasons"'):
            self.assertIn(name, protocol)
        slots = c_function(protocol, "static void add_ble_slot_status(")
        for name in ('"active_slot"', '"slots"', '"slot"', '"paired"',
                     '"connected"'):
            self.assertIn(name, slots)
        self.assertEqual(slots.count("cJSON_Add"), 6)
        runtime = c_function(protocol, "static void add_ble_runtime_diagnostics(")
        rejects = runtime[runtime.index('"ble_rejects"'):
                          runtime.index('"ble_last_disconnect"')]
        self.assertEqual(rejects.count("cJSON_Add"), 4)  # object + 3 fields


if __name__ == "__main__":
    unittest.main()
