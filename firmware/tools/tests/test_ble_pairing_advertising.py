"""Pairing advertising: an empty slot keeps fast advertising for 3 minutes.

Windows scans sparsely and can miss the 200-250 ms slow interval for minutes,
while a phone scanner still sees it. An empty active slot (waiting to pair)
therefore advertises fast for 3 minutes instead of 30 s, and a local key or
knob input restarts that window. Paired slots keep the 30 s window.
"""

import ctypes
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_lighting_preview_protocol import c_function


ROOT = Path(__file__).resolve().parents[2]
BLE_SOURCE = ROOT / "components/codex_micro/codex_micro_ble.c"
ACTION_SOURCE = ROOT / "components/action_engine/action_engine.c"
PROTOCOL_SOURCE = ROOT / "components/config_protocol/config_protocol.c"
HEADER = ROOT / "components/codex_micro/include/codex_micro.h"
STUB = ROOT / "components/codex_micro/codex_micro_stub.c"

FAST_MS = 30000
PAIRING_MS = 180000

HARNESS = r'''
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
typedef int esp_err_t;
#define ESP_OK 0
#define ESP_FAIL -1
#define ESP_LOGW(tag, ...) ((void)(tag))
static const char *TAG = "test";
static const char *esp_err_to_name(esp_err_t e) { (void)e; return "e"; }
typedef struct { uint8_t valid; } codex_micro_ble_peer_t;
static codex_micro_ble_peer_t peers[3];
static codex_micro_ble_peer_t peer_for_slot(uint8_t slot)
{
    codex_micro_ble_peer_t none = {0};
    return slot >= 1 && slot <= 3 ? peers[slot - 1] : none;
}
#define BLE_FAST_ADVERTISING_DURATION_MS 30000U
#define BLE_PAIRING_FAST_ADVERTISING_DURATION_MS 180000U
static atomic_uchar s_active_peer_slot;
static atomic_bool s_ble_suspended, s_connected, s_link_connected,
    s_advertising_active, s_advertising_starting, s_slow_advertising,
    s_advertising_interval_restart_pending, s_local_input_pending,
    s_advertising_input_pending, s_advertising_episode_valid;
static atomic_uint_least32_t s_fast_advertising_until_ms,
    s_advertising_pairing_boosts;
static uint32_t now_ms;
static unsigned stops;
static esp_err_t stop_result;
static uint32_t advertising_now_ms(void) { return now_ms; }
/* The pairing window itself is covered by test_ble_pairing_window.py. */
static unsigned window_polls;
static void poll_pairing_window(void) { window_polls++; }
static esp_err_t esp_ble_gap_stop_advertising(void)
{
    if (stop_result == ESP_OK) stops++;
    return stop_result;
}
'''


def build(directory, code):
    path = Path(directory) / "pairing_advertising.c"
    path.write_text(code)
    library = path.with_suffix(".so")
    subprocess.run(["cc", "-std=c11", "-shared", "-fPIC", "-Wall", "-Wextra",
                    "-Werror", "-Wno-unused-function", str(path), "-o",
                    str(library)], check=True, capture_output=True)
    return ctypes.CDLL(str(library))


class PairingAdvertisingModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        source = BLE_SOURCE.read_text()
        code = HARNESS
        for signature in (
            "static bool active_slot_waits_to_pair(void)",
            "static void reset_advertising_speed(void)",
            "static void poll_advertising_speed(void)",
            "static void poll_pairing_input_boost(void)",
            "void codex_micro_note_local_input(void)",
        ):
            code += c_function(source, signature) + "\n"
        code += r'''
void reset(void) {
    memset(peers, 0, sizeof(peers));
    atomic_store(&s_active_peer_slot, 1);
    atomic_store(&s_ble_suspended, false); atomic_store(&s_connected, false);
    atomic_store(&s_link_connected, false);
    atomic_store(&s_advertising_active, false);
    atomic_store(&s_advertising_starting, false);
    atomic_store(&s_local_input_pending, false);
    atomic_store(&s_advertising_pairing_boosts, 0);
    now_ms = 1000; stops = 0; stop_result = ESP_OK;
    reset_advertising_speed();
}
void pair(uint8_t slot) { peers[slot - 1].valid = 1; }
void start(void) { atomic_store(&s_advertising_active, true); }
/* The stop completion restarts advertising with the current speed. */
void stop_completes(void) {
    atomic_store(&s_advertising_interval_restart_pending, false);
    atomic_store(&s_advertising_active, true);
}
void at(uint32_t ms) { now_ms = ms; poll_pairing_input_boost(); poll_advertising_speed(); }
void input(void) { codex_micro_note_local_input(); }
void set(int which, bool value) {
    atomic_bool *flags[] = {&s_ble_suspended, &s_link_connected,
        &s_advertising_starting, &s_advertising_active};
    atomic_store(flags[which], value);
}
void fail_stops(void) { stop_result = ESP_FAIL; }
void restart_speed(void) { reset_advertising_speed(); }
bool slow(void) { return atomic_load(&s_slow_advertising); }
bool restart_pending(void) { return atomic_load(&s_advertising_interval_restart_pending); }
bool input_pending(void) { return atomic_load(&s_local_input_pending); }
uint32_t fast_until(void) { return atomic_load(&s_fast_advertising_until_ms); }
unsigned stop_count(void) { return stops; }
unsigned boosts(void) { return atomic_load(&s_advertising_pairing_boosts); }
unsigned window_poll_count(void) { return window_polls; }
'''
        lib = cls.lib = build(cls.temp.name, code)
        lib.at.argtypes = [ctypes.c_uint32]
        lib.pair.argtypes = [ctypes.c_uint8]
        lib.set.argtypes = [ctypes.c_int, ctypes.c_bool]
        for name in ("slow", "restart_pending", "input_pending"):
            getattr(lib, name).restype = ctypes.c_bool
        lib.fast_until.restype = ctypes.c_uint32

    def setUp(self):
        self.lib.reset()

    def go_slow(self, at_ms):
        """Advertise until the fast window ends and the slow restart completes."""
        self.lib.start()
        self.lib.at(at_ms)
        self.assertTrue(self.lib.slow())
        self.lib.stop_completes()

    def test_empty_slot_advertises_fast_for_three_minutes(self):
        self.assertEqual(self.lib.fast_until(), 1000 + PAIRING_MS)
        self.lib.start()
        self.lib.at(1000 + PAIRING_MS - 1)
        self.assertFalse(self.lib.slow())
        self.lib.at(1000 + PAIRING_MS)
        self.assertTrue(self.lib.slow())

    def test_paired_slot_keeps_thirty_seconds(self):
        self.lib.pair(1)
        self.lib.restart_speed()
        self.assertEqual(self.lib.fast_until(), 1000 + FAST_MS)

    def test_window_follows_the_active_slot(self):
        self.lib.pair(2)
        self.lib.restart_speed()           # slot 1 still empty
        self.assertEqual(self.lib.fast_until(), 1000 + PAIRING_MS)

    def test_input_switches_slow_pairing_advertising_back_to_fast(self):
        self.go_slow(1000 + PAIRING_MS)
        stops_before = self.lib.stop_count()
        self.lib.input()
        self.lib.at(500000)
        self.assertFalse(self.lib.slow())
        self.assertTrue(self.lib.restart_pending())
        self.assertEqual(self.lib.stop_count(), stops_before + 1)
        self.assertEqual(self.lib.fast_until(), 500000 + PAIRING_MS)
        self.assertEqual(self.lib.boosts(), 1)
        self.lib.stop_completes()
        self.lib.at(500000 + PAIRING_MS - 1)
        self.assertFalse(self.lib.slow())

    def test_input_while_fast_only_extends_the_window(self):
        self.lib.start()
        self.lib.input()
        self.lib.at(100000)
        self.assertEqual(self.lib.fast_until(), 100000 + PAIRING_MS)
        self.assertEqual(self.lib.stop_count(), 0)
        self.assertEqual(self.lib.boosts(), 0)

    def test_input_is_ignored_when_paired_linked_or_suspended(self):
        for case in ("paired", "linked", "suspended"):
            with self.subTest(case=case):
                self.lib.reset()
                if case == "paired":
                    self.lib.pair(1)
                    self.lib.restart_speed()
                    self.go_slow(1000 + FAST_MS)
                else:
                    self.go_slow(1000 + PAIRING_MS)
                    self.lib.set(1 if case == "linked" else 0, True)
                stops_before = self.lib.stop_count()
                self.lib.input()
                self.lib.at(500000)
                self.assertTrue(self.lib.slow())
                self.assertEqual(self.lib.stop_count(), stops_before)
                self.assertEqual(self.lib.boosts(), 0)
                self.assertFalse(self.lib.input_pending())

    def test_input_during_a_start_retries_on_the_next_poll(self):
        self.go_slow(1000 + PAIRING_MS)
        self.lib.set(2, True)              # a start is in flight
        self.lib.input()
        self.lib.at(500000)
        self.assertTrue(self.lib.slow())
        self.assertTrue(self.lib.input_pending())
        self.lib.set(2, False)
        self.lib.at(500010)
        self.assertFalse(self.lib.slow())
        self.assertEqual(self.lib.boosts(), 1)

    def test_input_while_not_advertising_makes_the_next_start_fast(self):
        self.go_slow(1000 + PAIRING_MS)
        self.lib.set(3, False)             # stopped, e.g. retry pending
        stops_before = self.lib.stop_count()
        self.lib.input()
        self.lib.at(500000)
        self.assertFalse(self.lib.slow())
        self.assertEqual(self.lib.stop_count(), stops_before)
        self.assertEqual(self.lib.boosts(), 1)

    def test_failed_stop_stays_slow_and_is_not_counted(self):
        self.go_slow(1000 + PAIRING_MS)
        self.lib.fail_stops()
        self.lib.input()
        self.lib.at(500000)
        self.assertTrue(self.lib.slow())
        self.assertFalse(self.lib.restart_pending())
        self.assertEqual(self.lib.boosts(), 0)

    def test_pairing_window_is_checked_only_on_polls_without_input(self):
        before = self.lib.window_poll_count()
        self.lib.at(2000)
        self.assertEqual(self.lib.window_poll_count(), before + 1)
        self.lib.input()
        self.lib.at(3000)
        self.assertEqual(self.lib.window_poll_count(), before + 1)


class PairingAdvertisingWiringTests(unittest.TestCase):
    def test_poll_handles_input_before_the_speed_transition(self):
        poll = c_function(BLE_SOURCE.read_text(), "void codex_micro_poll(void)")
        self.assertLess(poll.index("poll_pairing_input_boost();"),
                        poll.index("poll_advertising_speed();"))

    def test_only_deliberate_inputs_are_forwarded(self):
        handler = c_function(ACTION_SOURCE.read_text(),
                             "void action_engine_handle_event(const board_event_t *event)")
        self.assertIn("if (event->pressed && !joystick_direction_control(event->control)) {\n"
                      "        codex_micro_note_local_input();", handler)
        # Inputs that only wake idle standby already restart the fast window.
        self.assertLess(handler.index("wake_from_idle_standby();"),
                        handler.index("codex_micro_note_local_input();"))

    def test_status_reports_boosts_and_the_stub_links(self):
        self.assertIn('"advertising_pairing_boosts"', PROTOCOL_SOURCE.read_text())
        self.assertIn("uint32_t advertising_pairing_boosts;", HEADER.read_text())
        self.assertIn("void codex_micro_note_local_input(void);", HEADER.read_text())
        self.assertIn("void codex_micro_note_local_input(void)\n{\n}", STUB.read_text())
        init = c_function(BLE_SOURCE.read_text(), "esp_err_t codex_micro_init(")
        self.assertIn("atomic_store(&s_advertising_pairing_boosts, 0);", init)


if __name__ == "__main__":
    unittest.main()
