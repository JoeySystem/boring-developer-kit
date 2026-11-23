"""BLE reachability (#5), service ordering and Service Changed (#6),
authentication failures (#7) and orphan bonds (#10)."""

import ctypes
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_lighting_preview_protocol import c_function


ROOT = Path(__file__).resolve().parents[2]
BLE_SOURCE = ROOT / "components/codex_micro/codex_micro_ble.c"
BACKOFF = ROOT / "components/codex_micro/ble_advertising_backoff_model.c"
CONFIG_SERVICE = ROOT / "components/ble_config_service/ble_config_service.c"
ACTION_SOURCE = ROOT / "components/action_engine/action_engine.c"
PROTOCOL_SOURCE = ROOT / "components/config_protocol/config_protocol.c"

ESP_OK = 0
AUTH_ENC_FAIL = 97      # ESP_AUTH_SMP_ENC_FAIL
AUTH_DIV_NOT_AVAIL = 100  # ESP_AUTH_SMP_DIV_NOT_AVAIL
AUTH_PAIR_NOT_SUPPORT = 82

COMMON = r'''
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
typedef int esp_err_t;
#define ESP_OK 0
#define ESP_FAIL -1
#define ESP_ERR_INVALID_STATE 0x103
#define ESP_ERR_NVS_NOT_FOUND 0x1102
#define ESP_BD_ADDR_LEN 6
#define CODEX_MICRO_BLE_SLOT_COUNT 3u
#define ESP_LOGW(...) ((void)0)
#define ESP_LOGE(...) ((void)0)
#define portENTER_CRITICAL(lock) ((void)(lock))
#define portEXIT_CRITICAL(lock) ((void)(lock))
typedef int portMUX_TYPE;
static portMUX_TYPE s_state_lock;
typedef struct {
    uint8_t valid;
    uint8_t address_type;
    uint8_t address[ESP_BD_ADDR_LEN];
} codex_micro_ble_peer_t;
typedef struct {
    uint8_t valid;
    uint8_t address_type;
    uint8_t address[ESP_BD_ADDR_LEN];
} codex_micro_ble_identity_t;
static codex_micro_ble_peer_t peers[3];
static codex_micro_ble_identity_t identities[3];
static codex_micro_ble_peer_t peer_for_slot(uint8_t slot) { return peers[slot - 1]; }
static codex_micro_ble_identity_t peer_identity_for_slot(uint8_t slot)
    { return identities[slot - 1]; }
static bool valid_ble_slot(uint8_t slot) { return slot >= 1 && slot <= 3; }
'''


def build(directory, name, code):
    path = Path(directory) / f"{name}.c"
    path.write_text(code)
    library = path.with_suffix(".so")
    subprocess.run(["cc", "-std=c11", "-shared", "-fPIC",
                    "-I", str(ROOT / "components/codex_micro/include"), str(path), "-o",
                    str(library)], check=True, capture_output=True)
    return ctypes.CDLL(str(library))


class AdvertisingRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        source = BLE_SOURCE.read_text()
        code = COMMON + BACKOFF.read_text() + r'''
#define BLE_ADVERTISING_WATCHDOG_MS 3000U
#define BLE_ADVERTISING_HOLD_TIMEOUT_MS 5000U
enum { BLE_PEER_FILTER_IDLE = 0, BLE_PEER_FILTER_STOPPING };
typedef struct { int unused; } esp_hidd_dev_t;
typedef struct { int unused; } esp_ble_adv_data_t;
static esp_hidd_dev_t hid;
static esp_hidd_dev_t *s_hid = &hid;
static esp_ble_adv_data_t s_adv_data, s_scan_response_data;
static atomic_bool s_hid_services_ready, s_ble_suspended, s_advertising_held,
    s_link_connected, s_connected, s_advertising_active, s_advertising_starting,
    s_advertising_retry_running, s_advertising_configured,
    s_scan_response_configured, s_peer_filter_ready, s_advertising_input_pending;
static ble_adv_backoff_t s_advertising_backoff;
static atomic_int s_peer_filter_state;
static atomic_uint_least32_t s_hid_ready_ms, s_advertising_recoveries;
static atomic_uint_least32_t s_advertising_starting_ms, s_advertising_retry_started_ms,
    s_advertising_stale_flags;
#define BLE_ADVERTISING_FLAG_TIMEOUT_MS 10000U
static uint32_t s_last_advertising_watchdog_ms;
static uint32_t now_ms;
static unsigned ensures, configs, speed_resets, starts;
static uint32_t advertising_now_ms(void) { return now_ms; }
static void reset_advertising_speed(void) { speed_resets++; }
static esp_err_t esp_ble_gap_config_adv_data(esp_ble_adv_data_t *d)
    { (void)d; configs++; return ESP_OK; }
static esp_err_t start_advertising(void) {
    if (atomic_load(&s_ble_suspended) || atomic_load(&s_advertising_held))
        return ESP_ERR_INVALID_STATE;
    starts++; atomic_store(&s_advertising_active, true); return ESP_OK;
}
static void ensure_advertising(void) {
    if (atomic_load(&s_ble_suspended) || atomic_load(&s_advertising_held)) return;
    ensures++; (void)start_advertising();
}
void codex_micro_release_advertising_hold(void);
'''
        code += c_function(source, "static void poll_advertising_watchdog(void)") + "\n"
        code += c_function(source, "static void poll_advertising_hold(void)") + "\n"
        code += c_function(source, "void codex_micro_release_advertising_hold(void)") + "\n"
        code += c_function(source, "void codex_micro_hold_advertising_for_services(void)") + "\n"
        code += r'''
void reset(void) {
    atomic_store(&s_hid_services_ready, true);
    atomic_store(&s_ble_suspended, false); atomic_store(&s_advertising_held, false);
    atomic_store(&s_link_connected, false); atomic_store(&s_connected, false);
    atomic_store(&s_advertising_active, false); atomic_store(&s_advertising_starting, false);
    atomic_store(&s_advertising_retry_running, false);
    atomic_store(&s_advertising_configured, true);
    atomic_store(&s_scan_response_configured, true);
    atomic_store(&s_peer_filter_ready, true);
    atomic_store(&s_peer_filter_state, BLE_PEER_FILTER_IDLE);
    atomic_store(&s_advertising_recoveries, 0);
    atomic_store(&s_advertising_stale_flags, 0);
    atomic_store(&s_advertising_input_pending, false);
    memset(&s_advertising_backoff, 0, sizeof(s_advertising_backoff));
    s_last_advertising_watchdog_ms = 0; now_ms = 10000;
    ensures = configs = speed_resets = starts = 0;
}
void at(uint32_t ms) { now_ms = ms; poll_advertising_hold(); poll_advertising_watchdog(); }
void set(int which, bool value) {
    atomic_bool *flags[] = {&s_ble_suspended, &s_link_connected, &s_advertising_active,
        &s_advertising_configured, &s_peer_filter_ready, &s_advertising_held};
    atomic_store(flags[which], value);
}
void stuck_filter(void) {
    atomic_store(&s_peer_filter_ready, false);
    atomic_store(&s_peer_filter_state, BLE_PEER_FILTER_STOPPING);
}
void hid_ready_at(uint32_t ms) { atomic_store(&s_hid_ready_ms, ms); }
void stuck_starting(uint32_t since) {
    atomic_store(&s_advertising_starting, true);
    atomic_store(&s_advertising_starting_ms, since);
}
unsigned stale_flags(void) { return atomic_load(&s_advertising_stale_flags); }
unsigned read(int which) {
    switch (which) {
    case 0: return ensures; case 1: return configs; case 2: return starts;
    case 3: return atomic_load(&s_advertising_recoveries);
    case 4: return atomic_load(&s_peer_filter_ready);
    default: return atomic_load(&s_advertising_held);
    }
}
'''
        cls.lib = build(cls.temp.name, "advertising", code)
        cls.lib.at.argtypes = [ctypes.c_uint32]
        cls.lib.set.argtypes = [ctypes.c_int, ctypes.c_bool]
        cls.lib.hid_ready_at.argtypes = [ctypes.c_uint32]

    def setUp(self):
        self.lib.reset()

    def test_idle_device_that_stopped_advertising_is_restarted(self):
        self.lib.at(13000)
        self.assertEqual(self.lib.read(3), 1)
        self.assertEqual(self.lib.read(2), 1)

    def test_watchdog_runs_at_most_every_three_seconds(self):
        self.lib.at(13000)
        self.lib.set(2, False)  # advertising stopped again
        self.lib.at(14000)
        self.assertEqual(self.lib.read(3), 1)
        self.lib.at(16000)
        self.assertEqual(self.lib.read(3), 2)

    def test_watchdog_leaves_legitimate_states_alone(self):
        for which in (0, 1, 2, 5):  # suspended, linked, advertising, held
            with self.subTest(which=which):
                self.lib.reset()
                self.lib.set(which, True)
                self.lib.at(13000)
                self.assertEqual(self.lib.read(3), 0)

    def test_watchdog_repairs_missing_adv_data_and_stuck_filter(self):
        self.lib.set(3, False)
        self.lib.stuck_filter()
        self.lib.at(13000)
        self.assertEqual(self.lib.read(1), 1)
        self.assertEqual(self.lib.read(4), 1)

    def test_a_start_that_never_completes_is_cleared_after_ten_seconds(self):
        # Review B8: a stuck "starting" flag used to block the watchdog forever.
        self.lib.stuck_starting(10000)
        self.lib.at(13000)
        self.assertEqual(self.lib.read(3), 0)
        self.lib.at(20000)
        self.assertEqual(self.lib.stale_flags(), 1)
        self.assertEqual(self.lib.read(3), 1)

    def test_hold_blocks_advertising_until_released_or_timeout(self):
        self.lib.set(5, True)
        self.lib.hid_ready_at(10000)
        self.lib.at(14999)
        self.assertEqual(self.lib.read(2), 0)
        self.assertEqual(self.lib.read(5), 1)
        self.lib.at(15000)
        self.assertEqual(self.lib.read(5), 0)
        self.assertEqual(self.lib.read(2), 1)

    def test_retry_task_and_hold_contract(self):
        source = BLE_SOURCE.read_text()
        self.assertIn("atomic_load(&s_advertising_held)",
                      c_function(source, "static esp_err_t start_advertising(void)"))
        self.assertIn("atomic_load(&s_advertising_held)",
                      c_function(source, "static void ensure_advertising(void)\n{"))
        poll = c_function(source, "void codex_micro_poll(void)")
        for name in ("poll_advertising_hold();", "poll_orphan_bond_cleanup();",
                     "poll_advertising_watchdog();"):
            self.assertIn(name, poll)
        # init must not clear a hold requested before it.
        init = c_function(source, "esp_err_t codex_micro_init(")
        self.assertNotIn("s_advertising_held", init)


class ServiceChangedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        source = BLE_SOURCE.read_text()
        code = COMMON + r'''
typedef unsigned nvs_handle_t;
static nvs_handle_t s_ble_peer_nvs = 1;
static const char *const BLE_PEER_GATT_LAYOUT_NVS_KEYS[3] = {"a", "b", "c"};
static bool stored[3]; static uint32_t stored_value[3];
static int key(const char *k) { return k[0] - 'a'; }
static esp_err_t nvs_get_u32(nvs_handle_t h, const char *k, uint32_t *v)
    { (void)h; if (!stored[key(k)]) return ESP_ERR_NVS_NOT_FOUND; *v = stored_value[key(k)]; return ESP_OK; }
static esp_err_t nvs_set_u32(nvs_handle_t h, const char *k, uint32_t v)
    { (void)h; stored[key(k)] = true; stored_value[key(k)] = v; return ESP_OK; }
static esp_err_t nvs_erase_key(nvs_handle_t h, const char *k)
    { (void)h; if (!stored[key(k)]) return ESP_ERR_NVS_NOT_FOUND; stored[key(k)] = false; return ESP_OK; }
static esp_err_t nvs_commit(nvs_handle_t h) { (void)h; return ESP_OK; }
static const char *esp_err_to_name(esp_err_t e) { (void)e; return "e"; }
typedef esp_err_t (*codex_micro_service_change_fn)(const uint8_t a[6], void *c);
static uint32_t s_gatt_layout_id = 0xA1B2C3D4u;
static uint32_t s_peer_gatt_layout[3];
static bool s_peer_gatt_layout_valid[3];
static codex_micro_service_change_fn s_service_change_fn;
static void *s_service_change_context;
static atomic_uchar s_service_change_pending_slot;
static atomic_uint_least32_t s_service_change_sent, s_service_change_skipped;
static unsigned indications; static esp_err_t send_result;
static esp_err_t sender(const uint8_t a[6], void *c) { (void)a; (void)c; indications++; return send_result; }
'''
        for signature in (
            "static void load_peer_gatt_layout(uint8_t slot)",
            "static esp_err_t forget_peer_gatt_layout(uint8_t slot)",
            "static void save_peer_gatt_layout(uint8_t slot, uint32_t layout)",
            "static void sync_peer_gatt_layout(",
            "void codex_micro_note_service_change_result(bool delivered)",
        ):
            code += c_function(source, signature) + "\n"
        code += r'''
void reset(void) {
    memset(stored, 0, sizeof(stored)); memset(s_peer_gatt_layout_valid, 0, sizeof(s_peer_gatt_layout_valid));
    s_service_change_fn = sender; indications = 0; send_result = ESP_OK;
    atomic_store(&s_service_change_pending_slot, 0);
    atomic_store(&s_service_change_sent, 0); atomic_store(&s_service_change_skipped, 0);
}
void boot(void) { for (uint8_t s = 1; s <= 3; ++s) load_peer_gatt_layout(s); }
void encrypted(uint8_t slot, bool new_pairing) {
    static const uint8_t address[6] = {1, 2, 3, 4, 5, 6};
    sync_peer_gatt_layout(slot, new_pairing, address);
}
void store(int slot, uint32_t v) { stored[slot - 1] = true; stored_value[slot - 1] = v; }
void firmware_changes(void) { s_gatt_layout_id++; }
void result(bool ok) { codex_micro_note_service_change_result(ok); }
void set_send_result(esp_err_t e) { send_result = e; }
void forget(uint8_t s) { (void)forget_peer_gatt_layout(s); }
bool is_stored(int slot) { return stored[slot - 1]; }
unsigned sent(void) { return indications; }
unsigned skipped(void) { return atomic_load(&s_service_change_skipped); }
'''
        cls.lib = build(cls.temp.name, "service_changed", code)
        cls.lib.store.argtypes = [ctypes.c_int, ctypes.c_uint32]
        cls.lib.encrypted.argtypes = [ctypes.c_uint8, ctypes.c_bool]
        cls.lib.result.argtypes = [ctypes.c_bool]
        cls.lib.is_stored.restype = ctypes.c_bool

    def setUp(self):
        self.lib.reset()

    def test_legacy_bond_gets_exactly_one_indication(self):
        self.lib.boot()
        self.lib.encrypted(1, False)
        self.assertEqual(self.lib.sent(), 1)
        self.lib.result(True)
        self.lib.encrypted(1, False)
        self.lib.encrypted(1, False)
        self.assertEqual(self.lib.sent(), 1)
        self.assertEqual(self.lib.skipped(), 2)

    def test_new_pairing_needs_no_indication(self):
        self.lib.boot()
        self.lib.encrypted(2, True)
        self.assertEqual(self.lib.sent(), 0)
        self.lib.encrypted(2, False)
        self.assertEqual(self.lib.sent(), 0)

    def test_firmware_change_announces_once_per_host(self):
        self.lib.store(1, 0xA1B2C3D4)
        self.lib.store(3, 0xA1B2C3D4)
        self.lib.firmware_changes()
        self.lib.boot()
        for slot in (1, 3):
            self.lib.encrypted(slot, False)
            self.lib.result(True)
        self.lib.encrypted(1, False)
        self.lib.encrypted(3, False)
        self.assertEqual(self.lib.sent(), 2)

    def test_failed_indication_is_retried_next_connection(self):
        self.lib.boot()
        self.lib.encrypted(1, False)
        self.lib.result(False)
        self.lib.encrypted(1, False)
        self.assertEqual(self.lib.sent(), 2)
        self.lib.set_send_result(-1)
        self.lib.encrypted(1, False)
        self.lib.result(True)  # stray completion with nothing pending
        self.assertFalse(self.lib.is_stored(1))

    def test_slot_clear_forgets_announcement(self):
        self.lib.store(2, 0xA1B2C3D4)
        self.lib.boot()
        self.lib.forget(2)
        self.assertFalse(self.lib.is_stored(2))

    def test_wiring(self):
        source = BLE_SOURCE.read_text()
        auth = source[source.index("case ESP_GAP_BLE_AUTH_CMPL_EVT:"):]
        self.assertLess(auth.index("sync_peer_gatt_layout(slot, new_pairing,"),
                        auth.index("atomic_store(&s_connected, true);"))
        self.assertIn("forget_peer_gatt_layout(slot)",
                      c_function(source, "static esp_err_t finish_ble_slot_clear("))
        self.assertIn("forget_peer_gatt_layout(",
                      c_function(source, "esp_err_t codex_micro_factory_reset("))
        config = CONFIG_SERVICE.read_text()
        gatts = c_function(config, "static bool gatts_event(")
        connect = gatts[gatts.index("case ESP_GATTS_CONNECT_EVT:"):
                        gatts.index("case ESP_GATTS_SEND_SERVICE_CHANGE_EVT:")]
        self.assertNotIn("esp_ble_gatts_send_service_change_indication(", connect)
        self.assertIn("codex_micro_note_service_change_result(", gatts)
        prepare = c_function(config, "void ble_config_service_prepare(void)\n{")
        self.assertIn("codex_micro_hold_advertising_for_services();", prepare)
        self.assertIn("codex_micro_register_service_change_sender(", prepare)
        start_event = gatts[gatts.index("case ESP_GATTS_START_EVT:"):
                            gatts.index("case ESP_GATTS_CREAT_ATTR_TAB_EVT:")]
        self.assertIn("codex_micro_release_advertising_hold();", start_event)
        start = c_function(config, "esp_err_t ble_config_service_start(void)\n{")
        self.assertIn("codex_micro_release_advertising_hold();", start)


class AuthFailureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        source = BLE_SOURCE.read_text()
        code = COMMON + r'''
#define ESP_AUTH_SMP_ENC_FAIL 97
#define ESP_AUTH_SMP_DIV_NOT_AVAIL 100
typedef enum { CODEX_MICRO_BLE_DISCONNECT_NONE = 0, CODEX_MICRO_BLE_DISCONNECT_REJECT = 4,
    CODEX_MICRO_BLE_DISCONNECT_AUTH_FAIL = 6 } codex_micro_ble_disconnect_source_t;
static atomic_uchar s_active_peer_slot, s_ble_reauth_pending, s_ble_pending_disconnect_source;
static atomic_uint_least32_t s_reauth_prompts, s_auth_fail_disconnects;
static unsigned disconnects; static int last_source;
static esp_err_t request_ble_disconnect(uint8_t a[6], codex_micro_ble_disconnect_source_t s)
    { (void)a; disconnects++; last_source = s;
      atomic_store(&s_ble_pending_disconnect_source, (uint8_t)s); return ESP_OK; }
static bool peer_identity_matches(uint8_t slot, const uint8_t a[6], const codex_micro_ble_identity_t *p)
    { (void)p; return peers[slot - 1].valid == 1 && memcmp(peers[slot - 1].address, a, 6) == 0; }
'''
        code += c_function(source, "static bool auth_failure_means_key_mismatch(") + "\n"
        code += c_function(source, "static void handle_auth_failure(") + "\n"
        code += c_function(source, "bool codex_micro_take_ble_reauth_needed(uint8_t *slot)") + "\n"
        code += r'''
void reset(void) {
    memset(peers, 0, sizeof(peers)); disconnects = 0; last_source = 0;
    atomic_store(&s_ble_pending_disconnect_source, 0);
    atomic_store(&s_active_peer_slot, 1); atomic_store(&s_ble_reauth_pending, 0);
    peers[0].valid = 1; memcpy(peers[0].address, "\x01\x02\x03\x04\x05\x06", 6);
}
void fail(uint8_t host, uint8_t reason) {
    uint8_t address[6] = {host, 2, 3, 4, 5, 6}; handle_auth_failure(address, reason);
}
unsigned dropped(void) { return disconnects; }
void pending_reject(void) { atomic_store(&s_ble_pending_disconnect_source, 4); }
int source(void) { return last_source; }
int take(void) { uint8_t s = 0; return codex_micro_take_ble_reauth_needed(&s) ? s : 0; }
'''
        cls.lib = build(cls.temp.name, "auth_failure", code)
        cls.lib.fail.argtypes = [ctypes.c_uint8, ctypes.c_uint8]

    def setUp(self):
        self.lib.reset()

    def test_every_failure_drops_the_unencrypted_link(self):
        for reason in (AUTH_ENC_FAIL, AUTH_PAIR_NOT_SUPPORT, 0x50):
            self.lib.reset()
            self.lib.fail(9, reason)
            self.assertEqual(self.lib.dropped(), 1)
            self.assertEqual(self.lib.source(), 6)

    def test_an_already_rejected_link_keeps_its_disconnect_reason(self):
        self.lib.pending_reject()
        self.lib.fail(9, AUTH_PAIR_NOT_SUPPORT)
        self.assertEqual(self.lib.dropped(), 0)

    def test_key_mismatch_from_slot_host_asks_to_repair_once(self):
        for reason in (AUTH_ENC_FAIL, AUTH_DIV_NOT_AVAIL):
            self.lib.reset()
            self.lib.fail(1, reason)
            self.assertEqual(self.lib.take(), 1)
            self.assertEqual(self.lib.take(), 0)

    def test_no_prompt_for_other_hosts_or_other_reasons(self):
        self.lib.fail(9, AUTH_ENC_FAIL)
        self.lib.fail(1, AUTH_PAIR_NOT_SUPPORT)
        self.assertEqual(self.lib.take(), 0)

    def test_bonds_are_not_removed_and_ui_shows_prompt(self):
        source = BLE_SOURCE.read_text()
        handler = c_function(source, "static void handle_auth_failure(")
        self.assertNotIn("remove", handler)
        auth = source[source.index("case ESP_GAP_BLE_AUTH_CMPL_EVT:"):
                      source.index("case ESP_GAP_BLE_REMOVE_BOND_DEV_COMPLETE_EVT:")]
        self.assertIn("handle_auth_failure(", auth)
        action = ACTION_SOURCE.read_text()
        self.assertIn("codex_micro_take_ble_reauth_needed(&slot)",
                      c_function(action, "static void poll_ble_reauth_needed(void)"))
        self.assertIn("poll_ble_reauth_needed();", action)
        self.assertIn('return "auth_fail";', PROTOCOL_SOURCE.read_text())


class OrphanBondTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        source = BLE_SOURCE.read_text()
        code = COMMON + r'''
#define ESP_LE_KEY_PID 0x02
typedef struct { uint8_t irk[16]; int addr_type; uint8_t static_addr[6]; } esp_ble_pid_keys_t;
typedef struct { uint8_t key_mask; esp_ble_pid_keys_t pid_key; } esp_ble_bond_key_info_t;
typedef struct { uint8_t bd_addr[6]; esp_ble_bond_key_info_t bond_key; int bd_addr_type; } esp_ble_bond_dev_t;
static esp_ble_bond_dev_t bonds[8]; static int bond_count;
static unsigned removed_mask;
static int esp_ble_get_bond_device_num(void) { return bond_count; }
static esp_err_t esp_ble_get_bond_device_list(int *n, esp_ble_bond_dev_t *out)
    { memcpy(out, bonds, sizeof(bonds[0]) * (size_t)bond_count); *n = bond_count; return ESP_OK; }
static esp_err_t esp_ble_remove_bond_device(uint8_t *a) {
    for (int i = 0; i < bond_count; ++i) if (memcmp(bonds[i].bd_addr, a, 6) == 0) removed_mask |= 1u << i;
    return ESP_OK;
}
static bool s_orphan_cleanup_done;
static atomic_uint_least32_t s_orphan_bonds_removed, s_orphan_cleanup_skipped;
'''
        code += c_function(source, "static bool same_address(") + "\n"
        code += c_function(source, "static bool bond_matches_slot(") + "\n"
        code += c_function(source, "static void cleanup_orphan_bonds(void)") + "\n"
        code += r'''
void reset(void) {
    memset(peers, 0, sizeof(peers)); memset(identities, 0, sizeof(identities));
    memset(bonds, 0, sizeof(bonds)); bond_count = 0; removed_mask = 0;
    atomic_store(&s_orphan_bonds_removed, 0); atomic_store(&s_orphan_cleanup_skipped, 0);
}
void slot(int s, uint8_t addr, uint8_t identity) {
    peers[s - 1].valid = 1; peers[s - 1].address[0] = addr;
    if (identity) { identities[s - 1].valid = 1; identities[s - 1].address[0] = identity; }
}
void bond(uint8_t addr, uint8_t pid) {
    bonds[bond_count].bd_addr[0] = addr;
    if (pid) { bonds[bond_count].bond_key.key_mask = ESP_LE_KEY_PID;
               bonds[bond_count].bond_key.pid_key.static_addr[0] = pid; }
    bond_count++;
}
void run(void) { cleanup_orphan_bonds(); }
unsigned removed(void) { return removed_mask; }
unsigned skipped(void) { return atomic_load(&s_orphan_cleanup_skipped); }
'''
        cls.lib = build(cls.temp.name, "orphans", code)
        cls.lib.slot.argtypes = [ctypes.c_int, ctypes.c_uint8, ctypes.c_uint8]
        cls.lib.bond.argtypes = [ctypes.c_uint8, ctypes.c_uint8]

    def setUp(self):
        self.lib.reset()

    def test_removes_only_bonds_without_a_slot(self):
        self.lib.slot(1, 0x11, 0)
        self.lib.slot(2, 0x22, 0x2A)      # matched through the identity address
        self.lib.bond(0x11, 0)
        self.lib.bond(0x99, 0)            # orphan
        self.lib.bond(0x77, 0x2A)         # slot 2 via the bond's identity key
        self.lib.bond(0x55, 0x56)         # orphan
        self.lib.run()
        self.assertEqual(self.lib.removed(), 0b1010)

    def test_uncertain_mapping_removes_nothing(self):
        self.lib.slot(1, 0x11, 0)
        self.lib.slot(3, 0x33, 0)         # no bond matches slot 3
        self.lib.bond(0x11, 0)
        self.lib.bond(0x99, 0)
        self.lib.run()
        self.assertEqual(self.lib.removed(), 0)
        self.assertEqual(self.lib.skipped(), 1)

    def test_all_slots_empty_removes_every_stale_bond(self):
        self.lib.bond(0x99, 0)
        self.lib.bond(0x98, 0)
        self.lib.run()
        self.assertEqual(self.lib.removed(), 0b11)

    def test_cleanup_waits_for_a_quiet_stack(self):
        poll = c_function(BLE_SOURCE.read_text(), "static void poll_orphan_bond_cleanup(void)")
        for flag in ("s_link_connected", "s_ble_slot_clear_pending",
                     "s_auth_bond_removal_pending", "s_hid_services_ready",
                     "s_orphan_cleanup_done"):
            self.assertIn(flag, poll)


if __name__ == "__main__":
    unittest.main()
