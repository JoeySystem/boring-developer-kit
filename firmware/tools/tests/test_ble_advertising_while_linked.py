"""No advertising while a BLE link is up.

Found on a Windows PC, 2026-11-11: right after a new computer paired, the
firmware started advertising again although that computer's link was up (the
pairing is saved, and advertising refreshed, before s_connected is set). A
computer remembered in another slot, retrying about twice a second, connected
as a second link and was refused; the disconnect of that second link then
cleared the state of the first, so input stopped reaching the new computer.

These tests run the production start_advertising(), ensure_advertising() and
refresh_peer_filter().
"""

import ctypes
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BLE = ROOT / "components/codex_micro/codex_micro_ble.c"


def c_function(source, signature):
    start = source.index(signature + "\n{")
    return source[start:source.index("\n}\n", start) + 3]


HARNESS_HEAD = r'''
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>

typedef int esp_err_t;
#define ESP_OK 0
#define ESP_ERR_INVALID_STATE 0x103
#define pdPASS 1
#define ESP_LOGE(...) ((void)0)
static const char *TAG = "test";
#define BLE_FAST_ADVERTISING_MIN 0x0020
#define BLE_FAST_ADVERTISING_MAX 0x0030
#define BLE_SLOW_ADVERTISING_MIN 0x0140
#define BLE_SLOW_ADVERTISING_MAX 0x0190
typedef enum { BLE_PEER_FILTER_IDLE = 0, BLE_PEER_FILTER_STOPPING } ble_peer_filter_state_t;
typedef struct { uint16_t adv_int_min, adv_int_max; } esp_ble_adv_params_t;
static esp_ble_adv_params_t s_adv_params;

static atomic_bool s_ble_suspended, s_advertising_held, s_connected,
    s_link_connected, s_advertising_active, s_advertising_starting,
    s_advertising_configured, s_scan_response_configured, s_peer_filter_ready,
    s_slow_advertising, s_advertising_retry_running;
static atomic_uint_least32_t s_advertising_starting_ms,
    s_advertising_retry_started_ms;
static atomic_int s_peer_filter_state;

static unsigned start_requests, stop_requests, retry_tasks;
static uint32_t advertising_now_ms(void) { return 1000u; }
static void reset_advertising_speed(void) { atomic_store(&s_slow_advertising, false); }
static esp_err_t esp_ble_gap_start_advertising(esp_ble_adv_params_t *params)
{
    (void)params;
    start_requests++;
    return ESP_OK;
}
static esp_err_t esp_ble_gap_stop_advertising(void) { stop_requests++; return ESP_OK; }
static void advertising_retry_task(void *parameter) { (void)parameter; }
static int xTaskCreate(void (*task)(void *), const char *name, int stack,
                       void *parameter, int priority, void *handle)
{
    (void)task; (void)name; (void)stack; (void)parameter; (void)priority;
    (void)handle;
    retry_tasks++;
    return pdPASS;
}
static unsigned address_choices;
static esp_err_t apply_advertising_address(void) { address_choices++; return ESP_OK; }
static void ensure_advertising(void);
'''

HARNESS_TAIL = r'''
void t_reset(void)
{
    atomic_store(&s_ble_suspended, false);
    atomic_store(&s_advertising_held, false);
    atomic_store(&s_connected, false);
    atomic_store(&s_link_connected, false);
    atomic_store(&s_advertising_active, false);
    atomic_store(&s_advertising_starting, false);
    atomic_store(&s_advertising_configured, true);
    atomic_store(&s_scan_response_configured, true);
    atomic_store(&s_peer_filter_ready, true);
    atomic_store(&s_advertising_retry_running, false);
    atomic_store(&s_peer_filter_state, BLE_PEER_FILTER_IDLE);
    start_requests = stop_requests = retry_tasks = 0;
}
void t_link(bool up) { atomic_store(&s_link_connected, up); }
void t_encrypted(bool ready) { atomic_store(&s_connected, ready); }
int t_start(void) { return start_advertising(); }
void t_ensure(void) { ensure_advertising(); }
int t_refresh(void) { return refresh_peer_filter(); }
unsigned t_start_requests(void) { return start_requests; }
unsigned t_retry_tasks(void) { return retry_tasks; }
bool t_starting(void) { return atomic_load(&s_advertising_starting); }
'''


class AdvertisingWhileLinkedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = BLE.read_text()
        program = "".join((
            HARNESS_HEAD,
            c_function(source, "static esp_err_t start_advertising(void)"),
            c_function(source, "static void ensure_advertising(void)"),
            c_function(source, "static esp_err_t refresh_peer_filter(void)"),
            HARNESS_TAIL,
        ))
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        path = Path(cls.temp.name) / "advertising.c"
        path.write_text(program)
        library = Path(cls.temp.name) / "advertising.so"
        subprocess.run(["cc", "-std=c11", "-shared", "-fPIC", "-Wall", "-Wextra",
                        "-Werror", "-Wno-unused-function", "-Wno-unused-variable",
                        str(path), "-o", str(library)], check=True)
        cls.lib = ctypes.CDLL(str(library))
        cls.lib.t_link.argtypes = [ctypes.c_bool]
        cls.lib.t_encrypted.argtypes = [ctypes.c_bool]
        cls.lib.t_starting.restype = ctypes.c_bool

    def setUp(self):
        self.lib.t_reset()

    def test_idle_device_advertises(self):
        self.assertEqual(self.lib.t_start(), 0)
        self.assertEqual(self.lib.t_start_requests(), 1)
        self.assertTrue(self.lib.t_starting())

    def test_link_up_before_encryption_does_not_advertise(self):
        # A host has connected and is still pairing: s_connected is false.
        self.lib.t_link(True)
        self.assertEqual(self.lib.t_start(), 0)
        self.assertEqual(self.lib.t_start_requests(), 0)
        self.assertFalse(self.lib.t_starting())
        self.lib.t_ensure()
        self.assertEqual(self.lib.t_start_requests(), 0)
        self.assertEqual(self.lib.t_retry_tasks(), 0)

    def test_saving_a_new_pairing_does_not_restart_advertising(self):
        # AUTH_CMPL of a new host: the record is saved and the peer filter is
        # refreshed while the link is up and before s_connected is set.
        self.lib.t_link(True)
        self.lib.t_encrypted(False)
        self.assertEqual(self.lib.t_refresh(), 0)
        self.assertEqual(self.lib.t_start_requests(), 0)
        self.assertEqual(self.lib.t_retry_tasks(), 0)
        # Once that link is gone the device advertises again.
        self.lib.t_link(False)
        self.lib.t_ensure()
        self.assertEqual(self.lib.t_start_requests(), 1)

    def test_encrypted_link_does_not_advertise(self):
        self.lib.t_link(True)
        self.lib.t_encrypted(True)
        self.lib.t_ensure()
        self.assertEqual(self.lib.t_start_requests(), 0)

    def test_every_start_goes_through_the_guard(self):
        source = BLE.read_text()
        self.assertEqual(source.count("esp_ble_gap_start_advertising("), 1)
        guard = c_function(source, "static esp_err_t start_advertising(void)")
        self.assertIn("atomic_load(&s_link_connected)", guard)


if __name__ == "__main__":
    unittest.main()
