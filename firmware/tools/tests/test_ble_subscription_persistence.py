"""BLE HID subscriptions (CCCD) survive restarts for every bonded host.

ESP-IDF keeps CCCD values only in RAM. Windows does not re-subscribe on a
bonded reconnection, so without a per-bond restore it shows the keyboard as
connected but receives no keys after any device restart.
"""

import ctypes
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_lighting_preview_protocol import c_function


ROOT = Path(__file__).resolve().parents[2]
BLE_SOURCE = ROOT / "components/codex_micro/codex_micro_ble.c"
HIDD_SOURCE = ROOT / "components/esp_hid/src/ble_hidd.c"
PROTOCOL_SOURCE = ROOT / "components/config_protocol/config_protocol.c"
HEADER = ROOT / "components/codex_micro/include/codex_micro.h"

ALL = 0x80000007  # three input reports + battery in the fake device
ESP_OK = 0
ESP_FAIL = -1
ESP_ERR_INVALID_STATE = 0x103

COMMON = r'''
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
typedef int esp_err_t;
#define ESP_OK 0
#define ESP_FAIL -1
#define ESP_ERR_INVALID_ARG 0x102
#define ESP_ERR_INVALID_STATE 0x103
#define ESP_ERR_NVS_NOT_FOUND 0x1102
#define UINT32_C(x) ((uint32_t)(x##u))
'''


def build(directory, name, code):
    path = Path(directory) / f"{name}.c"
    path.write_text(code)
    library = path.with_suffix(".so")
    subprocess.run(["cc", "-std=c11", "-shared", "-fPIC", str(path),
                    "-o", str(library)], check=True, capture_output=True)
    return ctypes.CDLL(str(library))


class AppSubscriptionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        source = BLE_SOURCE.read_text()
        keys = re.search(
            r"BLE_PEER_SUBSCRIPTION_NVS_KEYS\[\s*CODEX_MICRO_BLE_SLOT_COUNT\] = "
            r"\{[^}]*\};", source).group(0)
        code = COMMON + r'''
#define CODEX_MICRO_BLE_SLOT_COUNT 3u
#define CODEX_MICRO_REPORT_ID 6
#define ESP_BD_ADDR_LEN 6
#define portENTER_CRITICAL(lock) ((void)(lock))
#define portEXIT_CRITICAL(lock) ((void)(lock))
typedef int portMUX_TYPE;
typedef unsigned nvs_handle_t;
typedef struct esp_hidd_dev_s { int unused; } esp_hidd_dev_t;
typedef struct { int unused; } codex_micro_ble_identity_t;
static portMUX_TYPE s_state_lock;
static nvs_handle_t s_ble_peer_nvs = 1;
static esp_hidd_dev_t s_fake_hid;
static esp_hidd_dev_t *s_hid = &s_fake_hid;
static atomic_bool s_connected, s_connected_peer_valid, s_last_subscribe_notify;
static atomic_uchar s_active_peer_slot;
static uint8_t s_connected_peer_address[ESP_BD_ADDR_LEN];
static atomic_uint_least32_t s_notify_subscribe_events,
    s_notify_unsubscribe_events, s_subscription_restores,
    s_subscription_default_restores, s_subscription_save_failures,
    s_hid_reports_dropped_unsubscribed, s_hid_report_failures;
static atomic_uint_least16_t s_last_subscribe_attr;
static const char *const ''' + keys.replace("static const char *const ", "") + r'''

/* NVS fake: one u32 per key. */
static bool nvs_present[3];
static uint32_t nvs_value[3];
static unsigned nvs_commits, nvs_writes;
static esp_err_t nvs_fail;
static int key_index(const char *key) {
    for (int i = 0; i < 3; ++i) {
        if (strcmp(key, BLE_PEER_SUBSCRIPTION_NVS_KEYS[i]) == 0) return i;
    }
    return -1;
}
static esp_err_t nvs_get_u32(nvs_handle_t h, const char *key, uint32_t *out) {
    (void)h; int i = key_index(key);
    if (i < 0 || !nvs_present[i]) return ESP_ERR_NVS_NOT_FOUND;
    *out = nvs_value[i]; return ESP_OK;
}
static esp_err_t nvs_set_u32(nvs_handle_t h, const char *key, uint32_t v) {
    (void)h; if (nvs_fail) return nvs_fail;
    int i = key_index(key); nvs_present[i] = true; nvs_value[i] = v;
    nvs_writes++; return ESP_OK;
}
static esp_err_t nvs_erase_key(nvs_handle_t h, const char *key) {
    (void)h; int i = key_index(key);
    if (!nvs_present[i]) return ESP_ERR_NVS_NOT_FOUND;
    nvs_present[i] = false; return ESP_OK;
}
static esp_err_t nvs_commit(nvs_handle_t h) { (void)h; nvs_commits++; return ESP_OK; }

/* esp_hid fake: RAM CCCD state. */
static uint32_t hid_mask;
static esp_err_t esp_hidd_ble_get_subscriptions(esp_hidd_dev_t *d, uint32_t *m) {
    (void)d; *m = hid_mask; return ESP_OK;
}
static esp_err_t esp_hidd_ble_get_subscription_mask_all(esp_hidd_dev_t *d, uint32_t *m) {
    (void)d; *m = 0x80000007u; return ESP_OK;
}
static esp_err_t esp_hidd_ble_set_subscriptions(esp_hidd_dev_t *d, uint32_t m) {
    (void)d; hid_mask = m; return ESP_OK;
}
static bool esp_hidd_ble_input_subscribed(esp_hidd_dev_t *d, size_t id) {
    (void)d; return id >= 1 && id <= 3 && (hid_mask & (1u << (id - 1))) != 0;
}
static unsigned input_sets;
static esp_err_t input_result;
static esp_err_t esp_hidd_dev_input_set(esp_hidd_dev_t *d, size_t map, size_t id,
                                        uint8_t *data, size_t len) {
    (void)d; (void)map; (void)id; (void)data; (void)len;
    input_sets++; return input_result;
}
static bool identity_matches = true;
static bool peer_identity_matches(uint8_t slot, const uint8_t a[6],
                                  const codex_micro_ble_identity_t *p) {
    (void)slot; (void)a; (void)p; return identity_matches;
}
static uint32_t s_peer_subscriptions[CODEX_MICRO_BLE_SLOT_COUNT];
static bool s_peer_subscriptions_valid[CODEX_MICRO_BLE_SLOT_COUNT];
'''
        for signature in (
            "static bool valid_ble_slot(uint8_t slot)",
            "static void load_peer_subscriptions(uint8_t slot)",
            "static esp_err_t forget_peer_subscriptions(uint8_t slot)",
            "static esp_err_t save_peer_subscriptions(uint8_t slot, uint32_t mask)",
            "static void restore_peer_subscriptions(uint8_t slot, bool new_pairing)",
            "static bool hid_subscriptions(uint32_t *mask)",
            "static void note_subscription_change(",
            "esp_err_t codex_micro_send_standard_report(",
        ):
            code += c_function(source, signature) + "\n"
        code += r'''
void reset_test(void) {
    memset(nvs_present, 0, sizeof(nvs_present));
    nvs_commits = nvs_writes = 0; nvs_fail = ESP_OK;
    hid_mask = 0; input_sets = 0; input_result = ESP_OK;
    identity_matches = true;
    memset(s_peer_subscriptions, 0, sizeof(s_peer_subscriptions));
    memset(s_peer_subscriptions_valid, 0, sizeof(s_peer_subscriptions_valid));
    atomic_store(&s_connected, false);
    atomic_store(&s_connected_peer_valid, false);
    atomic_store(&s_active_peer_slot, 1);
    atomic_store(&s_subscription_restores, 0);
    atomic_store(&s_subscription_default_restores, 0);
    atomic_store(&s_subscription_save_failures, 0);
    atomic_store(&s_hid_reports_dropped_unsubscribed, 0);
    atomic_store(&s_hid_report_failures, 0);
    atomic_store(&s_notify_subscribe_events, 0);
    atomic_store(&s_notify_unsubscribe_events, 0);
}
void store(int slot, uint32_t v) { nvs_present[slot - 1] = true; nvs_value[slot - 1] = v; }
bool stored(int slot) { return nvs_present[slot - 1]; }
uint32_t stored_value(int slot) { return nvs_value[slot - 1]; }
void boot(void) { for (uint8_t s = 1; s <= 3; ++s) load_peer_subscriptions(s); }
void encrypted(uint8_t slot, bool new_pairing) {
    /* Mirrors the AUTH_CMPL success path ordering. */
    atomic_store(&s_active_peer_slot, slot);
    atomic_store(&s_connected_peer_valid, true);
    restore_peer_subscriptions(slot, new_pairing);
    atomic_store(&s_connected, true);
}
void host_writes(uint32_t mask, uint16_t handle) {
    uint32_t before = 0, after = 0;
    hid_subscriptions(&before);
    hid_mask = mask;
    hid_subscriptions(&after);
    note_subscription_change(before, after, handle);
}
void device_restarts(void) {
    hid_mask = 0; /* esp_hid starts every boot with CCCD = 0 */
    atomic_store(&s_connected, false);
    atomic_store(&s_connected_peer_valid, false);
    memset(s_peer_subscriptions, 0, sizeof(s_peer_subscriptions));
    memset(s_peer_subscriptions_valid, 0, sizeof(s_peer_subscriptions_valid));
    boot();
}
void forget(uint8_t slot) { (void)forget_peer_subscriptions(slot); }
void set_nvs_fail(esp_err_t e) { nvs_fail = e; }
void set_identity(bool v) { identity_matches = v; }
void set_input_result(esp_err_t e) { input_result = e; }
uint32_t mask(void) { return hid_mask; }
unsigned writes(void) { return nvs_writes; }
unsigned sets(void) { return input_sets; }
esp_err_t send(uint8_t id) { uint8_t b[8] = {0}; return codex_micro_send_standard_report(id, b, 8); }
uint32_t counter(int i) {
    switch (i) {
    case 0: return atomic_load(&s_subscription_restores);
    case 1: return atomic_load(&s_subscription_default_restores);
    case 2: return atomic_load(&s_subscription_save_failures);
    case 3: return atomic_load(&s_hid_reports_dropped_unsubscribed);
    case 4: return atomic_load(&s_hid_report_failures);
    case 5: return atomic_load(&s_notify_subscribe_events);
    case 6: return atomic_load(&s_notify_unsubscribe_events);
    default: return atomic_load(&s_last_subscribe_attr);
    }
}
'''
        lib = build(cls.temp.name, "subscriptions", code)
        for name in ("mask", "stored_value", "counter"):
            getattr(lib, name).restype = ctypes.c_uint32
        lib.store.argtypes = [ctypes.c_int, ctypes.c_uint32]
        lib.host_writes.argtypes = [ctypes.c_uint32, ctypes.c_uint16]
        lib.encrypted.argtypes = [ctypes.c_uint8, ctypes.c_bool]
        lib.stored.restype = ctypes.c_bool
        lib.send.argtypes = [ctypes.c_uint8]
        cls.lib = lib

    def setUp(self):
        self.lib.reset_test()

    def test_windows_keys_survive_device_restart_without_resubscribing(self):
        lib = self.lib
        lib.boot()
        lib.encrypted(1, True)
        lib.host_writes(0x00000003, 0x2a)  # Windows: keyboard + consumer only
        self.assertEqual(lib.stored_value(1), 0x00000003)
        lib.device_restarts()
        lib.encrypted(1, False)            # Windows writes nothing this time
        self.assertEqual(lib.mask(), 0x00000003)
        self.assertEqual(lib.send(1), ESP_OK)
        self.assertEqual(lib.sets(), 1)

    def test_legacy_bond_without_saved_mask_gets_every_input(self):
        lib = self.lib
        lib.boot()
        lib.encrypted(2, False)
        self.assertEqual(lib.mask(), ALL)
        self.assertEqual(lib.counter(1), 1)
        self.assertFalse(lib.stored(2))  # nothing written until the host acts

    def test_new_pairing_discards_previous_hosts_mask(self):
        lib = self.lib
        lib.store(1, 0x00000001)
        lib.boot()
        lib.encrypted(1, True)
        self.assertFalse(lib.stored(1))
        self.assertEqual(lib.mask(), ALL)

    def test_each_slot_restores_its_own_mask(self):
        lib = self.lib
        lib.store(1, 0x00000001)
        lib.store(3, 0x80000004)
        lib.boot()
        lib.encrypted(3, False)
        self.assertEqual(lib.mask(), 0x80000004)
        lib.encrypted(1, False)
        self.assertEqual(lib.mask(), 0x00000001)

    def test_unchanged_write_does_not_touch_flash(self):
        lib = self.lib
        lib.store(1, 0x00000003)
        lib.boot()
        lib.encrypted(1, False)
        lib.host_writes(0x00000003, 0x2a)
        self.assertEqual(lib.writes(), 0)

    def test_write_before_encryption_or_from_other_identity_is_not_saved(self):
        lib = self.lib
        lib.boot()
        lib.host_writes(0x00000001, 0x2a)  # not connected yet
        self.assertFalse(lib.stored(1))
        lib.encrypted(1, False)
        lib.set_identity(False)
        lib.host_writes(0x00000002, 0x2a)
        self.assertFalse(lib.stored(1))

    def test_subscription_diagnostics_count_changes(self):
        lib = self.lib
        lib.boot()
        lib.encrypted(1, False)
        lib.host_writes(0x00000000, 0x2e)
        lib.host_writes(0x00000004, 0x31)
        self.assertEqual(lib.counter(6), 1)   # unsubscribe
        self.assertEqual(lib.counter(5), 1)   # subscribe
        self.assertEqual(lib.counter(7), 0x31)

    def test_save_failure_is_counted_and_retried_next_change(self):
        lib = self.lib
        lib.boot()
        lib.encrypted(1, False)
        lib.set_nvs_fail(ESP_FAIL)
        lib.host_writes(0x00000001, 0x2a)
        self.assertEqual(lib.counter(2), 1)
        lib.set_nvs_fail(ESP_OK)
        lib.host_writes(0x00000003, 0x2a)
        self.assertEqual(lib.stored_value(1), 0x00000003)

    def test_forget_erases_saved_mask(self):
        lib = self.lib
        lib.store(2, 0x1)
        lib.boot()
        lib.forget(2)
        self.assertFalse(lib.stored(2))

    def test_unsubscribed_report_is_dropped_not_queued(self):
        lib = self.lib
        lib.store(1, 0x00000001)
        lib.boot()
        lib.encrypted(1, False)
        # Consumer (id 2) is not subscribed: report delivered-as-dropped so
        # usb_service does not hold the press and replay it later.
        self.assertEqual(lib.send(2), ESP_OK)
        self.assertEqual(lib.sets(), 0)
        self.assertEqual(lib.counter(3), 1)

    def test_send_failure_is_counted_and_returned(self):
        lib = self.lib
        lib.boot()
        lib.encrypted(1, False)
        lib.set_input_result(ESP_FAIL)
        self.assertEqual(lib.send(1), ESP_FAIL)
        self.assertEqual(lib.counter(4), 1)


class EspHidSubscriptionMaskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        source = HIDD_SOURCE.read_text()
        code = COMMON + r'''
#define ESP_HIDD_BLE_SUBSCRIPTION_BATTERY (UINT32_C(1) << 31)
enum { ESP_HID_TRANSPORT_BLE = 1 };
enum { ESP_HID_REPORT_TYPE_INPUT = 1, ESP_HID_REPORT_TYPE_OUTPUT = 2 };
enum { ESP_HID_PROTOCOL_MODE_REPORT = 1 };
typedef struct { uint16_t value; } hidd_le_ccc_value_t;
typedef struct {
    uint8_t report_id, report_type, protocol_mode;
    uint16_t ccc_handle;
    hidd_le_ccc_value_t ccc;
} hidd_le_report_item_t;
typedef struct { uint8_t reports_len; hidd_le_report_item_t *reports; } hidd_dev_map_t;
typedef struct { void *dev; int transport; } esp_hidd_dev_t;
typedef struct {
    hidd_le_ccc_value_t bat_ccc;
    hidd_dev_map_t *devices;
    uint8_t devices_len;
} esp_ble_hidd_dev_t;
static esp_ble_hidd_dev_t ble, *s_dev = &ble;
static hidd_le_report_item_t reports[4] = {
    {1, ESP_HID_REPORT_TYPE_INPUT, ESP_HID_PROTOCOL_MODE_REPORT, 0x2b, {0}},
    {2, ESP_HID_REPORT_TYPE_OUTPUT, ESP_HID_PROTOCOL_MODE_REPORT, 0, {0}},
    {2, ESP_HID_REPORT_TYPE_INPUT, ESP_HID_PROTOCOL_MODE_REPORT, 0x2f, {0}},
    {6, ESP_HID_REPORT_TYPE_INPUT, ESP_HID_PROTOCOL_MODE_REPORT, 0x33, {0}},
};
static hidd_dev_map_t map = {4, reports};
static esp_hidd_dev_t dev = {&ble, ESP_HID_TRANSPORT_BLE};
'''
        code += c_function(source, "static hidd_le_report_item_t *get_report_by_id_and_type(") + "\n"
        start = source.index("#define BLE_HIDD_SUBSCRIPTION_INPUT_BITS")
        code += source[start:source.index("#endif /* CONFIG_GATTS_ENABLE */", start)]
        code += r'''
void setup(void) {
    ble.devices = &map; ble.devices_len = 1; ble.bat_ccc.value = 0;
    for (int i = 0; i < 4; ++i) reports[i].ccc.value = 0;
}
void host_write(int index, uint16_t v) { reports[index].ccc.value = v; }
void host_write_battery(uint16_t v) { ble.bat_ccc.value = v; }
uint16_t ccc(int index) { return reports[index].ccc.value; }
uint16_t battery(void) { return ble.bat_ccc.value; }
uint32_t get(void) { uint32_t m = 0xdead; esp_hidd_ble_get_subscriptions(&dev, &m); return m; }
uint32_t all(void) { uint32_t m = 0; esp_hidd_ble_get_subscription_mask_all(&dev, &m); return m; }
int set(uint32_t m) { return esp_hidd_ble_set_subscriptions(&dev, m); }
bool subscribed(int id) { return esp_hidd_ble_input_subscribed(&dev, (size_t)id); }
int foreign(void) { esp_hidd_dev_t other = {&ble, 2}; uint32_t m; return esp_hidd_ble_get_subscriptions(&other, &m); }
'''
        lib = build(cls.temp.name, "ble_hidd_mask", code)
        for name in ("get", "all"):
            getattr(lib, name).restype = ctypes.c_uint32
        lib.set.argtypes = [ctypes.c_uint32]
        lib.ccc.restype = ctypes.c_uint16
        lib.battery.restype = ctypes.c_uint16
        lib.host_write.argtypes = [ctypes.c_int, ctypes.c_uint16]
        lib.subscribed.restype = ctypes.c_bool
        cls.lib = lib

    def setUp(self):
        self.lib.setup()

    def test_mask_has_one_bit_per_input_report_plus_battery(self):
        self.assertEqual(self.lib.all(), 0x80000007)

    def test_get_reflects_host_writes(self):
        self.lib.host_write(2, 1)        # second input report (id 2)
        self.lib.host_write_battery(1)
        self.assertEqual(self.lib.get(), 0x80000002)

    def test_set_round_trips_and_leaves_output_reports_alone(self):
        self.assertEqual(self.lib.set(0x80000005), ESP_OK)
        self.assertEqual(self.lib.get(), 0x80000005)
        self.assertEqual(self.lib.ccc(0), 1)
        self.assertEqual(self.lib.ccc(1), 0)  # output report has no CCCD
        self.assertEqual(self.lib.ccc(2), 0)
        self.assertEqual(self.lib.ccc(3), 1)
        self.assertEqual(self.lib.battery(), 1)

    def test_input_subscribed_by_report_id(self):
        self.lib.set(0x00000004)
        self.assertTrue(self.lib.subscribed(6))
        self.assertFalse(self.lib.subscribed(1))
        self.assertFalse(self.lib.subscribed(9))

    def test_rejects_non_ble_device(self):
        self.assertNotEqual(self.lib.foreign(), ESP_OK)


class SubscriptionWiringTests(unittest.TestCase):
    def setUp(self):
        self.source = BLE_SOURCE.read_text()

    def test_restore_runs_before_reports_are_allowed(self):
        auth = self.source[self.source.index("case ESP_GAP_BLE_AUTH_CMPL_EVT:"):]
        restore = auth.index("restore_peer_subscriptions(slot, new_pairing);")
        connected = auth.index("atomic_store(&s_connected, true);")
        self.assertLess(restore, connected)

    def test_gatts_write_is_tracked_around_esp_hid_handler(self):
        handler = c_function(self.source, "static void combined_gatts_event_handler(")
        before = handler.index("hid_subscriptions(&subscriptions_before)")
        dispatch = handler.index("esp_hidd_gatts_event_handler(event, gatts_if, parameter);")
        after = handler.index("note_subscription_change(subscriptions_before")
        self.assertLess(before, dispatch)
        self.assertLess(dispatch, after)
        self.assertIn("ESP_GATTS_MTU_EVT", handler)
        self.assertIn("ESP_GATTS_CONF_EVT", handler)

    def test_saved_masks_are_loaded_and_erased_with_the_bond(self):
        self.assertIn("load_peer_subscriptions(slot);",
                      c_function(self.source, "static void reload_ble_peer_slot("))
        self.assertIn("load_peer_subscriptions(slot);",
                      c_function(self.source, "static esp_err_t load_ble_peer_slots(void)"))
        self.assertIn("forget_peer_subscriptions(slot)",
                      c_function(self.source, "static esp_err_t finish_ble_slot_clear("))
        self.assertIn("forget_peer_subscriptions(",
                      c_function(self.source, "esp_err_t codex_micro_factory_reset("))

    def test_vendor_report_refuses_unsubscribed_host(self):
        vendor = c_function(self.source, "static esp_err_t send_vendor_report(")
        check = vendor.index("esp_hidd_ble_input_subscribed(s_hid, CODEX_MICRO_REPORT_ID)")
        self.assertIn("return ESP_ERR_INVALID_STATE;", vendor[check:check + 200])

    def test_new_diagnostics_reach_get_status(self):
        protocol = PROTOCOL_SOURCE.read_text()
        header = HEADER.read_text()
        for field in ("hid_subscriptions", "hid_subscriptions_all",
                      "subscription_restores", "subscription_default_restores",
                      "subscription_save_failures",
                      "hid_reports_dropped_unsubscribed", "hid_report_failures"):
            with self.subTest(field=field):
                self.assertIn(f"uint32_t {field};", header)
                self.assertIn(f'"{field}"', protocol)
                self.assertRegex(self.source, rf"\.{field} =|->{field} =")


if __name__ == "__main__":
    unittest.main()
