"""Stability item 3: BLE advertising is retried forever, with backoff.

The main-loop advertising watchdog already restarted advertising every 3 s
after the retry task gave up. It now backs off while restarts keep failing
(3 s three times, then 10 s, 30 s, 60 s), a key press retries at once (at
most once a second), and any check that finds the device advertising or
connected starts over from the fast delay. GET_STATUS shows the backoff.
"""

import ctypes
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_lighting_preview_protocol import c_function


ROOT = Path(__file__).resolve().parents[2]
COMPONENT = ROOT / "components/codex_micro"
MODEL = COMPONENT / "ble_advertising_backoff_model.c"
BLE = COMPONENT / "codex_micro_ble.c"
PROTOCOL = ROOT / "components/config_protocol/config_protocol.c"


class BackoffState(ctypes.Structure):
    _fields_ = [("consecutive", ctypes.c_uint32), ("last_attempt_ms", ctypes.c_uint32),
                ("restarts", ctypes.c_uint32), ("input_restarts", ctypes.c_uint32)]


class BackoffModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        library = Path(cls.temp.name) / "backoff.so"
        subprocess.run(["cc", "-std=c11", "-shared", "-fPIC", "-Wall", "-Wextra", "-Werror",
                        "-I", str(COMPONENT / "include"), str(MODEL), "-o", str(library)],
                       check=True, capture_output=True)
        cls.lib = ctypes.CDLL(str(library))
        cls.lib.ble_adv_backoff_take.restype = ctypes.c_bool
        cls.lib.ble_adv_backoff_take.argtypes = [ctypes.POINTER(BackoffState),
                                                 ctypes.c_uint32, ctypes.c_bool]
        cls.lib.ble_adv_backoff_delay_ms.restype = ctypes.c_uint32

    def setUp(self):
        self.state = BackoffState()

    def take(self, now, input_=False):
        return self.lib.ble_adv_backoff_take(ctypes.byref(self.state), now, input_)

    def restart_times(self, until_ms, step_ms=1000):
        times = []
        for now in range(0, until_ms, step_ms):
            if self.take(now):
                times.append(now)
        return times

    def test_delays_grow_to_one_minute_and_stay(self):
        delays = [self.lib.ble_adv_backoff_delay_ms(n) for n in range(8)]
        self.assertEqual([3000, 3000, 3000, 10000, 30000, 60000, 60000, 60000], delays)

    def test_first_restart_is_immediate(self):
        self.assertTrue(self.take(123456))

    def test_restarts_never_stop(self):
        times = self.restart_times(10 * 60 * 1000)
        self.assertEqual([0, 3000, 6000, 9000, 19000, 49000, 109000], times[:7])
        gaps = {b - a for a, b in zip(times[6:], times[7:])}
        self.assertEqual({60000}, gaps)
        self.assertGreater(len(times), 14)

    def test_key_press_retries_at_once_but_not_more_than_once_a_second(self):
        self.restart_times(60000)  # deep in backoff, last restart at 49000
        self.assertFalse(self.take(49500, True))
        self.assertTrue(self.take(50000, True))
        self.assertFalse(self.take(50400, True))
        self.assertEqual(1, self.state.input_restarts)

    def test_working_advertising_resets_to_the_fast_delay(self):
        self.restart_times(60000)
        self.lib.ble_adv_backoff_reset(ctypes.byref(self.state))
        self.assertEqual(0, self.state.consecutive)
        self.assertTrue(self.take(60001))
        self.assertFalse(self.take(62000))
        self.assertTrue(self.take(63001))

    def test_clock_wrap(self):
        start = 0xFFFFF000
        self.assertTrue(self.take(start))
        self.assertFalse(self.take((start + 2000) & 0xFFFFFFFF))
        self.assertTrue(self.take((start + 3000) & 0xFFFFFFFF))


class BackoffWiringTests(unittest.TestCase):
    def setUp(self):
        self.source = BLE.read_text()
        self.watchdog = c_function(self.source, "static void poll_advertising_watchdog(void)")

    def test_watchdog_takes_the_backoff_before_restarting(self):
        take = self.watchdog.index("ble_adv_backoff_take(&s_advertising_backoff, now_ms, input)")
        self.assertLess(take, self.watchdog.index("atomic_fetch_add(&s_advertising_recoveries, 1u);"))
        self.assertLess(take, self.watchdog.index("ensure_advertising();"))

    def test_advertising_or_connected_resets_the_backoff(self):
        reset = self.watchdog.index("ble_adv_backoff_reset(&s_advertising_backoff);")
        self.assertLess(self.watchdog.index("atomic_load(&s_advertising_active)"), reset)
        self.assertLess(reset, self.watchdog.index("ble_adv_backoff_take("))

    def test_input_skips_the_three_second_poll_gate(self):
        self.assertIn("atomic_exchange(&s_advertising_input_pending, false)", self.watchdog)
        self.assertIn("if (!input && (uint32_t)(now_ms - s_last_advertising_watchdog_ms)",
                      self.watchdog)
        note = c_function(self.source, "void codex_micro_note_local_input(void)")
        self.assertIn("atomic_store(&s_advertising_input_pending, true);", note)

    def test_watchdog_still_runs_from_the_main_loop_poll(self):
        poll = c_function(self.source, "void codex_micro_poll(void)")
        self.assertIn("poll_advertising_watchdog();", poll)

    def test_get_status_reports_the_backoff(self):
        protocol = PROTOCOL.read_text()
        for key in ('"backoff_level"', '"backoff_delay_ms"', '"input_restarts"'):
            self.assertIn(key, protocol)

    def test_model_is_built(self):
        self.assertIn('"ble_advertising_backoff_model.c"',
                      (COMPONENT / "CMakeLists.txt").read_text())


if __name__ == "__main__":
    unittest.main()
