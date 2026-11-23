"""The Bluetooth address each computer knows this device by.

Every newly paired computer gets an address of its own. The firmware asks the
stack for an address and then advertises; the stack accepts a new address only
while it is not advertising and otherwise keeps the one it had, saying so in a
later event. Recording the address that was asked for, instead of the one the
stack was on, left a computer unable to find the device: it had paired on one
address and the device later advertised another (CP01-441BF6FFE080,
2026-11-12: Windows was never seen again after Bluetooth moved to the Mac and
back).

The real functions are compiled against a small model of the stack that keeps
those two rules.
"""

import ctypes
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_lighting_preview_protocol import c_function


ROOT = Path(__file__).resolve().parents[2]
BLE = ROOT / "components/codex_micro/codex_micro_ble.c"

HARNESS = r'''
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

typedef int esp_err_t;
#define ESP_OK 0
#define ESP_FAIL -1
#define ESP_ERR_NVS_NOT_FOUND 0x1102
#define ESP_BD_ADDR_LEN 6
typedef uint8_t esp_bd_addr_t[ESP_BD_ADDR_LEN];
#define BLE_ADDR_TYPE_PUBLIC 0
#define BLE_ADDR_TYPE_RANDOM 1
#define CODEX_MICRO_BLE_SLOT_COUNT 3u
#define TAG "test"
#define ESP_LOGE(...) ((void)0)
#define ESP_LOGW(...) ((void)0)
#define portENTER_CRITICAL(lock) ((void)(lock))
#define portEXIT_CRITICAL(lock) ((void)(lock))
static int s_state_lock;
typedef struct { uint8_t valid; } codex_micro_ble_peer_t;
typedef struct { uint8_t own_addr_type; } esp_ble_adv_params_t;
enum { BLE_PEER_FILTER_IDLE, BLE_PEER_FILTER_STOPPING };

static atomic_uchar s_active_peer_slot = 1;
static codex_micro_ble_peer_t s_ble_peers[CODEX_MICRO_BLE_SLOT_COUNT];
static esp_bd_addr_t s_ble_local_addresses[CODEX_MICRO_BLE_SLOT_COUNT];
static bool s_ble_local_address_valid[CODEX_MICRO_BLE_SLOT_COUNT];
static esp_bd_addr_t s_pairing_local_address;
static bool s_pairing_local_address_valid;
static esp_bd_addr_t s_advertised_local_address;
static bool s_advertised_local_address_random;
static atomic_uint_least32_t s_local_address_apply_failures;
static atomic_uint_least32_t s_local_address_adopt_mismatches;
static atomic_uint_least32_t s_local_address_retries;
#define BLE_LOCAL_ADDRESS_RETRY_LIMIT 3u
static atomic_bool s_peer_filter_ready = true;
static atomic_int s_peer_filter_state;
static esp_ble_adv_params_t s_adv_params;
static int s_ble_peer_nvs = 1;
static const char *const BLE_PEER_LOCAL_ADDRESS_NVS_KEYS[] = {
    "peer1_la", "peer2_la", "peer3_la",
};
static bool valid_ble_slot(uint8_t slot) { return slot >= 1 && slot <= 3; }

/* --- the stack, as far as addresses go --- */
static const esp_bd_addr_t PUBLIC_ADDRESS = {0x10, 0x20, 0x30, 0x40, 0x50, 0x60};
static bool stack_advertising;
static bool stack_refuses_while_advertising = true;
static esp_bd_addr_t stack_random_address;
static uint8_t stack_own_type;         /* as of the last advertising start */
static esp_bd_addr_t air_address;      /* what a computer nearby would see */
static int stop_requests, set_requests, set_refusals;
static uint8_t next_random = 1;

static void esp_fill_random(void *buffer, size_t length)
{
    memset(buffer, next_random++, length);
}
static esp_err_t esp_ble_gap_set_rand_addr(esp_bd_addr_t address)
{
    ++set_requests;
    if (stack_advertising && stack_refuses_while_advertising) {
        ++set_refusals;  /* reported later; the call itself returns OK */
        return ESP_OK;
    }
    memcpy(stack_random_address, address, ESP_BD_ADDR_LEN);
    return ESP_OK;
}
static esp_err_t esp_ble_gap_stop_advertising(void)
{
    ++stop_requests;
    stack_advertising = false;
    return ESP_OK;
}
static esp_err_t esp_ble_gap_get_local_used_addr(esp_bd_addr_t address,
                                                 uint8_t *type)
{
    *type = stack_own_type;
    memcpy(address, stack_own_type == BLE_ADDR_TYPE_RANDOM
                        ? stack_random_address : PUBLIC_ADDRESS,
           ESP_BD_ADDR_LEN);
    return ESP_OK;
}
static struct { char key[12]; uint8_t data[6]; bool present; } nvs[3];
static int nvs_slot(const char *key) { return key[4] - '1'; }
static esp_err_t nvs_set_blob(int handle, const char *key, const void *data,
                              size_t length)
{
    (void)handle;
    memcpy(nvs[nvs_slot(key)].data, data, length);
    nvs[nvs_slot(key)].present = true;
    return ESP_OK;
}
static esp_err_t nvs_erase_key(int handle, const char *key)
{
    (void)handle;
    if (!nvs[nvs_slot(key)].present) return ESP_ERR_NVS_NOT_FOUND;
    nvs[nvs_slot(key)].present = false;
    return ESP_OK;
}
static esp_err_t nvs_commit(int handle) { (void)handle; return ESP_OK; }
'''

DRIVER = r'''
/* Start advertising for the active slot, as start_advertising() does. */
void t_start(void)
{
    (void)apply_advertising_address();
    stack_own_type = s_adv_params.own_addr_type;
    stack_advertising = true;
    memcpy(air_address, stack_own_type == BLE_ADDR_TYPE_RANDOM
                            ? stack_random_address : PUBLIC_ADDRESS,
           ESP_BD_ADDR_LEN);
    verify_advertising_address();
}
/* The restart verify_advertising_address() asked for has come through. */
bool t_restart_if_stopped(void)
{
    if (stack_advertising) return false;
    atomic_store(&s_peer_filter_state, BLE_PEER_FILTER_IDLE);
    atomic_store(&s_peer_filter_ready, true);
    t_start();
    return true;
}
void t_stop(void) { stack_advertising = false; }
/* A computer connects: the controller stops advertising by itself. */
void t_host_connects(void) { stack_advertising = false; }
void t_select(uint8_t slot) { atomic_store(&s_active_peer_slot, slot); }
void t_pair(uint8_t slot)
{
    s_ble_peers[slot - 1].valid = 1;
    adopt_link_local_address(slot);
}
void t_legacy_host(uint8_t slot) { s_ble_peers[slot - 1].valid = 1; }
void t_stack_refuses(bool refuses) { stack_refuses_while_advertising = refuses; }
/* The stack is left advertising behind the firmware's back. */
void t_stack_left_advertising(void) { stack_advertising = true; }
bool t_air_is_public(void)
{
    return memcmp(air_address, PUBLIC_ADDRESS, ESP_BD_ADDR_LEN) == 0;
}
bool t_slot_matches_air(uint8_t slot)
{
    if (!s_ble_local_address_valid[slot - 1]) return t_air_is_public();
    return memcmp(s_ble_local_addresses[slot - 1], air_address,
                  ESP_BD_ADDR_LEN) == 0;
}
bool t_slot_has_own_address(uint8_t slot) { return s_ble_local_address_valid[slot - 1]; }
bool t_slots_share_address(uint8_t a, uint8_t b)
{
    return s_ble_local_address_valid[a - 1] && s_ble_local_address_valid[b - 1] &&
           memcmp(s_ble_local_addresses[a - 1], s_ble_local_addresses[b - 1],
                  ESP_BD_ADDR_LEN) == 0;
}
bool t_stored_matches_slot(uint8_t slot)
{
    return nvs[slot - 1].present == s_ble_local_address_valid[slot - 1] &&
           (!nvs[slot - 1].present ||
            memcmp(nvs[slot - 1].data, s_ble_local_addresses[slot - 1], 6) == 0);
}
unsigned t_apply_failures(void) { return atomic_load(&s_local_address_apply_failures); }
unsigned t_adopt_mismatches(void) { return atomic_load(&s_local_address_adopt_mismatches); }
int t_stop_requests(void) { return stop_requests; }
int t_set_refusals(void) { return set_refusals; }
'''


def build(directory):
    source = BLE.read_text()
    program = "".join((
        HARNESS,
        c_function(source, "static bool local_address_in_use("), "\n",
        c_function(source, "static esp_err_t apply_advertising_address(void)"), "\n",
        c_function(source, "static void verify_advertising_address(void)"), "\n",
        c_function(source, "static void adopt_link_local_address(uint8_t slot)"), "\n",
        DRIVER,
    ))
    path = Path(directory) / "local_address.c"
    path.write_text(program)
    library = Path(directory) / "local_address.so"
    subprocess.run(["cc", "-std=c11", "-shared", "-fPIC", "-Wall", "-Wextra",
                    "-Werror", str(path), "-o", str(library)],
                   check=True, capture_output=True)
    lib = ctypes.CDLL(str(library))
    for name in ("t_air_is_public", "t_slot_matches_air", "t_slot_has_own_address",
                 "t_slots_share_address", "t_stored_matches_slot",
                 "t_restart_if_stopped"):
        getattr(lib, name).restype = ctypes.c_bool
    return lib


class LocalAddressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.lib = build(self.temp.name)

    def add_host(self, slot):
        """A new computer is paired through the pairing window on slot."""
        self.lib.t_stop()
        self.lib.t_select(slot)
        self.lib.t_start()
        self.lib.t_host_connects()
        self.lib.t_pair(slot)

    def switch_to(self, slot):
        """Bluetooth moves to slot; its computer looks for the address it knows."""
        self.lib.t_select(slot)
        self.lib.t_start()

    def test_each_new_computer_gets_its_own_address_and_is_found_again(self):
        self.add_host(1)
        self.add_host(2)
        self.assertTrue(self.lib.t_slot_has_own_address(1))
        self.assertTrue(self.lib.t_slot_has_own_address(2))
        self.assertFalse(self.lib.t_slots_share_address(1, 2))
        for slot in (1, 2, 1, 2):
            self.switch_to(slot)
            self.assertTrue(self.lib.t_slot_matches_air(slot))
            self.lib.t_host_connects()
        self.assertEqual(self.lib.t_apply_failures(), 0)
        self.assertEqual(self.lib.t_adopt_mismatches(), 0)
        self.assertTrue(self.lib.t_stored_matches_slot(1))
        self.assertTrue(self.lib.t_stored_matches_slot(2))

    def test_a_computer_paired_by_older_firmware_stays_on_the_public_address(self):
        self.lib.t_legacy_host(1)
        self.switch_to(1)
        self.assertTrue(self.lib.t_air_is_public())
        self.assertFalse(self.lib.t_slot_has_own_address(1))
        self.lib.t_host_connects()
        self.add_host(2)
        self.switch_to(1)
        self.assertTrue(self.lib.t_air_is_public())
        self.assertEqual(self.lib.t_apply_failures(), 0)

    def test_a_refused_address_is_noticed_and_advertising_starts_over(self):
        self.add_host(1)
        self.add_host(2)
        self.lib.t_select(1)
        # The stack still counts itself as advertising, so it keeps the
        # address of slot 2 and goes on air with it.
        self.lib.t_stack_left_advertising()
        self.lib.t_start()
        self.assertEqual(self.lib.t_apply_failures(), 1)
        self.assertEqual(self.lib.t_stop_requests(), 1)
        # Off, then on again: now the stack takes the address of slot 1.
        self.assertTrue(self.lib.t_restart_if_stopped())
        self.assertTrue(self.lib.t_slot_matches_air(1))
        self.assertEqual(self.lib.t_apply_failures(), 1)
        self.assertFalse(self.lib.t_restart_if_stopped())

    def test_a_computer_is_recorded_with_the_address_it_really_paired_on(self):
        # The case from the field: the address asked for the pairing window
        # is refused, the computer pairs on the address that was on air, and
        # the device must look for it there afterwards.
        self.add_host(1)
        self.lib.t_select(2)
        self.lib.t_stack_left_advertising()
        self.lib.t_start()             # refused; a restart is asked for
        self.lib.t_stack_left_advertising()
        self.lib.t_host_connects()     # the computer got in before it
        self.lib.t_pair(2)
        self.assertEqual(self.lib.t_adopt_mismatches(), 1)
        self.assertTrue(self.lib.t_slot_matches_air(2))
        self.assertTrue(self.lib.t_stored_matches_slot(2))
        # Away and back: the computer of slot 2 sees the address it knows.
        self.switch_to(1)
        self.lib.t_host_connects()
        self.switch_to(2)
        self.assertTrue(self.lib.t_slot_matches_air(2))

    def test_the_restart_is_not_repeated_without_end(self):
        self.add_host(1)
        self.add_host(2)
        self.lib.t_select(1)
        for _ in range(10):
            self.lib.t_stack_left_advertising()
            self.lib.t_start()
        # Three restarts in a row, then it is left alone and only counted.
        self.assertEqual(self.lib.t_stop_requests(), 3)
        self.assertEqual(self.lib.t_apply_failures(), 10)

    def test_source_wiring(self):
        source = BLE.read_text()
        started = source.split("case ESP_GAP_BLE_ADV_START_COMPLETE_EVT:", 1)[1]
        started = started.split("case ESP_GAP_BLE_SET_STATIC_RAND_ADDR_EVT:", 1)[0]
        self.assertIn("verify_advertising_address();", started)
        refused = source.split("case ESP_GAP_BLE_SET_STATIC_RAND_ADDR_EVT:", 1)[1]
        refused = refused.split("break;", 1)[0]
        self.assertIn("s_local_address_set_rejections", refused)
        adopt = c_function(source, "static void adopt_link_local_address(uint8_t slot)")
        self.assertIn("local_address_in_use(address, &random)", adopt)


if __name__ == "__main__":
    unittest.main()
