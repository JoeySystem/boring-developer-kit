"""Low-battery protection policy (battery_guard_model.c)."""
import ctypes
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2] / 'components/action_engine'
NORMAL, LOW, CRITICAL, USB_LOW = 0, 1, 2, 3


class Sample(ctypes.Structure):
    _fields_ = [('valid', ctypes.c_bool), ('voltage_mv', ctypes.c_uint16),
                ('soc_percent', ctypes.c_uint8)]


class Model(ctypes.Structure):
    _fields_ = [('level', ctypes.c_int), ('critical_pending', ctypes.c_bool),
                ('critical_since_ms', ctypes.c_uint32), ('resume_pending', ctypes.c_bool),
                ('resume_since_ms', ctypes.c_uint32), ('usb_low_pending', ctypes.c_bool),
                ('usb_low_since_ms', ctypes.c_uint32)]


class Boot(ctypes.Structure):
    _fields_ = [('block', ctypes.c_bool), ('clear_lock', ctypes.c_bool)]


def sample(mv, soc=50, valid=True):
    return Sample(valid, mv, soc)


class BatteryGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        lib = Path(cls.tmp.name) / 'guard.so'
        subprocess.run(['cc', '-std=c11', '-shared', '-fPIC', '-Wall', '-Wextra', '-Werror',
                        '-I', str(ROOT / 'include'), str(ROOT / 'battery_guard_model.c'),
                        '-o', str(lib)], check=True)
        cls.lib = ctypes.CDLL(str(lib))
        L = cls.lib
        L.battery_guard_model_update.argtypes = [ctypes.POINTER(Model), ctypes.c_uint32,
                                                 ctypes.POINTER(Sample), ctypes.c_bool]
        L.battery_guard_model_update.restype = ctypes.c_bool
        L.battery_guard_boot_decide.argtypes = [ctypes.c_bool, ctypes.c_bool,
                                                ctypes.POINTER(Sample)]
        L.battery_guard_boot_decide.restype = Boot
        L.battery_guard_model_resume_ready.argtypes = [ctypes.POINTER(Model), ctypes.c_uint32,
                                                       ctypes.POINTER(Sample)]
        L.battery_guard_model_resume_ready.restype = ctypes.c_bool
        L.battery_guard_breath_level.argtypes = [ctypes.c_uint32, ctypes.c_uint8]
        L.battery_guard_breath_level.restype = ctypes.c_uint8

    def setUp(self):
        self.m = Model()
        self.lib.battery_guard_model_init(ctypes.byref(self.m))

    def feed(self, now, s, external=False):
        return self.lib.battery_guard_model_update(ctypes.byref(self.m), now,
                                                   ctypes.byref(s), external)

    def boot(self, brownout, lock, s):
        r = self.lib.battery_guard_boot_decide(brownout, lock, ctypes.byref(s))
        return r.block, r.clear_lock

    def test_low_enters_on_voltage_or_soc_and_exits_with_hysteresis(self):
        self.assertTrue(self.feed(0, sample(3540, 40)))
        self.assertEqual(LOW, self.m.level)
        self.assertFalse(self.feed(2000, sample(3600, 40)))   # inside the band
        self.assertEqual(LOW, self.m.level)
        self.assertTrue(self.feed(4000, sample(3620, 13)))
        self.assertEqual(NORMAL, self.m.level)
        self.feed(6000, sample(3800, 10))                     # SOC alone
        self.assertEqual(LOW, self.m.level)
        self.feed(8000, sample(3800, 12))                     # SOC below exit
        self.assertEqual(LOW, self.m.level)

    def test_critical_needs_ten_seconds_of_consecutive_low_samples(self):
        for t in range(0, 10000, 2000):
            self.feed(t, sample(3390))
            self.assertEqual(LOW, self.m.level)
        self.assertTrue(self.feed(10000, sample(3390)))
        self.assertEqual(CRITICAL, self.m.level)
        self.assertFalse(self.feed(12000, sample(4100, 90)))  # latched
        self.assertEqual(CRITICAL, self.m.level)

    def test_load_spike_recovery_or_bad_sample_restarts_the_hold(self):
        self.feed(0, sample(3390))
        self.feed(8000, sample(3450))      # recovered between spikes
        self.feed(10000, sample(3390))
        self.feed(18000, sample(3390))
        self.assertEqual(LOW, self.m.level)
        self.feed(19000, sample(0, 0, valid=False))
        self.feed(20000, sample(3390))
        self.feed(29000, sample(3390))
        self.assertEqual(LOW, self.m.level)
        self.feed(30000, sample(3390))
        self.assertEqual(CRITICAL, self.m.level)

    def test_soc_only_critical_requires_low_voltage_region(self):
        self.feed(0, sample(3700, 2))
        self.feed(20000, sample(3700, 2))
        self.assertEqual(LOW, self.m.level)       # bogus SOC never powers off
        self.feed(22000, sample(3590, 2))
        self.feed(32000, sample(3590, 2))
        self.assertEqual(CRITICAL, self.m.level)

    def test_out_of_range_and_external_power(self):
        self.feed(0, sample(2400, 0))
        self.feed(20000, sample(4600, 0))
        self.assertEqual(NORMAL, self.m.level)
        self.feed(30000, sample(3390))
        self.feed(36000, sample(3390), external=True)
        self.assertEqual(NORMAL, self.m.level)
        self.assertFalse(self.m.critical_pending)

    def test_low_cell_on_usb_latches_usb_low_after_the_hold(self):
        for t in range(0, 10000, 2000):
            self.assertFalse(self.feed(t, sample(3540), external=True))
            self.assertEqual(NORMAL, self.m.level)
        self.assertTrue(self.feed(10000, sample(3540), external=True))
        self.assertEqual(USB_LOW, self.m.level)
        # Latched: neither recovery nor unplugging clears it; the caller does.
        self.assertFalse(self.feed(12000, sample(4100, 90), external=True))
        self.assertFalse(self.feed(14000, sample(3390), external=False))
        self.assertEqual(USB_LOW, self.m.level)

    def test_usb_low_hold_restarts_on_recovery_bad_sample_or_unplug(self):
        self.feed(0, sample(3540), external=True)
        self.feed(8000, sample(3560), external=True)        # charge lifted it
        self.feed(9000, sample(3540), external=True)
        self.feed(18000, sample(3540), external=True)
        self.assertEqual(NORMAL, self.m.level)
        self.feed(18500, sample(0, 0, valid=False), external=True)
        self.feed(19000, sample(3540), external=True)
        self.feed(28000, sample(3540), external=True)
        self.assertEqual(NORMAL, self.m.level)
        self.feed(28500, sample(3540, 40), external=False)   # unplugged: LOW
        self.assertEqual(LOW, self.m.level)
        self.feed(29000, sample(3540), external=True)
        self.assertEqual(NORMAL, self.m.level)
        self.feed(38000, sample(3540), external=True)
        self.assertEqual(NORMAL, self.m.level)
        self.feed(39000, sample(3540), external=True)
        self.assertEqual(USB_LOW, self.m.level)

    def test_soc_alone_never_raises_usb_low(self):
        self.feed(0, sample(3800, 1), external=True)
        self.feed(20000, sample(3800, 1), external=True)
        self.assertEqual(NORMAL, self.m.level)

    def test_usb_low_clock_wrap(self):
        start = 0xFFFFF000
        self.feed(start, sample(3540), external=True)
        self.feed((start + 9999) & 0xFFFFFFFF, sample(3540), external=True)
        self.assertEqual(NORMAL, self.m.level)
        self.feed((start + 10000) & 0xFFFFFFFF, sample(3540), external=True)
        self.assertEqual(USB_LOW, self.m.level)

    def test_clock_wrap(self):
        start = 0xFFFFF000
        self.feed(start, sample(3390))
        self.feed((start + 9999) & 0xFFFFFFFF, sample(3390))
        self.assertEqual(LOW, self.m.level)
        self.feed((start + 10000) & 0xFFFFFFFF, sample(3390))
        self.assertEqual(CRITICAL, self.m.level)

    def test_boot_gate(self):
        self.assertEqual((True, False), self.boot(False, False, sample(3440)))
        self.assertEqual((False, False), self.boot(False, False, sample(3450)))
        self.assertEqual((False, False), self.boot(False, False, sample(0, valid=False)))
        self.assertEqual((True, False), self.boot(False, True, sample(3590)))
        self.assertEqual((False, True), self.boot(False, True, sample(3600)))
        self.assertEqual((True, False), self.boot(True, False, sample(3690)))
        self.assertEqual((True, False), self.boot(True, False, sample(0, valid=False)))
        self.assertEqual((False, False), self.boot(True, False, sample(3700)))

    def test_resume_requires_sustained_recovery(self):
        r = lambda t, mv: self.lib.battery_guard_model_resume_ready(
            ctypes.byref(self.m), t, ctypes.byref(sample(mv)))
        self.assertFalse(r(0, 3700))
        self.assertFalse(r(29999, 3700))
        self.assertFalse(r(30000, 3690))   # dip restarts
        self.assertFalse(r(32000, 3710))
        self.assertTrue(r(62000, 3710))

    def test_breath_is_bounded_and_slow(self):
        levels = [self.lib.battery_guard_breath_level(t, 40) for t in range(0, 8000, 50)]
        self.assertEqual(5, min(levels))
        self.assertEqual(40, max(levels))
        self.assertEqual(self.lib.battery_guard_breath_level(0, 40),
                         self.lib.battery_guard_breath_level(4000, 40))


FIRMWARE = Path(__file__).resolve().parents[2]


def c_function(source, signature):
    start = source.index(signature)
    depth = 0
    for index in range(source.index('{', start), len(source)):
        depth += {'{': 1, '}': -1}.get(source[index], 0)
        if depth == 0:
            return source[start:index + 1]
    raise AssertionError(signature)


class BatteryGuardIntegrationTests(unittest.TestCase):
    """Source contracts for the action-engine wiring (no IDF on the host)."""

    @classmethod
    def setUpClass(cls):
        cls.action = (FIRMWARE / 'components/action_engine/action_engine.c').read_text()

    def test_boot_gate_runs_before_any_on_boot_output(self):
        init = c_function(self.action, 'esp_err_t action_engine_init(void)')
        gate = init.index('low_battery_boot_gate(reset_reason)')
        self.assertLess(init.index('should_start_usb_charge_session()'), gate)
        blocked = init.index('if (s_low_battery_boot_blocked) {')
        self.assertLess(blocked, init.index('enter_initial_page();'))
        branch = init[blocked:init.index('enter_initial_page();')]
        self.assertIn('s_under_key_self_test_active = false;', branch)
        self.assertIn('start_low_battery_shutdown(true);', branch)
        ble = c_function(self.action, 'bool action_engine_ble_start_suspended(void)')
        self.assertIn('s_low_battery_boot_blocked', ble)

    def test_protective_shutdown_keeps_on_intent_and_sets_lock(self):
        low = c_function(self.action, 'static void start_low_battery_shutdown(bool at_boot)')
        self.assertNotIn('config_store_set_user_powered_on', low)
        self.assertIn('config_store_note_low_battery_shutdown()', low)
        self.assertIn('BOARD_MIST_BATTERY_EMPTY', low)
        self.assertIn('LOW_BATTERY_OFF_NOTICE_MS', low)
        user = c_function(self.action, 'void action_engine_request_shutdown(void)')
        self.assertIn('config_store_set_user_powered_on(false)', user)
        begin = c_function(self.action, 'static void begin_shutdown(const board_mist_view_t *view')
        self.assertNotIn('config_store_set_user_powered_on', begin)

    def test_guard_runs_in_idle_standby_and_locked_standby_resumes(self):
        poll = c_function(self.action, 'void action_engine_poll(void)')
        self.assertLess(poll.index('poll_battery_guard();'),
                        poll.index('if (s_idle_standby.active) {'))
        standby = c_function(self.action, 'static void poll_usb_standby(void)\n{')
        self.assertIn('battery_guard_model_resume_ready', standby)
        self.assertIn('wake_from_usb_standby(USB_STANDBY_WAKE_BATTERY_RECOVERED);', standby)

    def test_usb_low_parks_in_locked_standby_without_lock_or_haptic(self):
        guard = c_function(self.action, 'static void poll_battery_guard(void)\n{')
        branch = guard[guard.index('BATTERY_GUARD_USB_LOW'):]
        self.assertIn('start_low_battery_usb_standby();', branch)
        usb = c_function(self.action, 'static void start_low_battery_usb_standby(void)')
        self.assertIn('s_low_battery_shutdown = true;', usb)
        self.assertIn('"locked_standby"', usb)
        self.assertIn('BOARD_MIST_BATTERY_EMPTY', usb)
        self.assertIn('begin_shutdown(&view, LOW_BATTERY_OFF_NOTICE_MS, true,\n'
                      '                   USB_STANDBY_ENTRY_USB_LOW_BATTERY);', usb)
        self.assertNotIn('config_store_note_low_battery_shutdown', usb)
        self.assertNotIn('config_store_set_user_powered_on', usb)
        self.assertNotIn('board_haptic_pulse', usb)

    def test_button_in_locked_standby_only_shows_the_icon_until_recovery(self):
        user = c_function(self.action, 'void action_engine_request_shutdown(void)')
        standby = user[user.index('if (s_usb_standby_active || s_battery_splash_active) {'):]
        self.assertLess(standby.index('low_battery_standby_holds()'),
                        standby.index('wake_from_usb_standby(USB_STANDBY_WAKE_POWER_BUTTON);'))
        holds = c_function(self.action, 'static bool low_battery_standby_holds(void)')
        self.assertIn('BATTERY_GUARD_RESUME_MV', holds)
        self.assertIn('battery_guard_sample_usable(&sample) &&', holds)
        render = c_function(self.action, 'static void render_mist_battery(void)\n{')
        self.assertIn('s_low_battery_locked_standby', render)

    def test_breathing_keeps_agent_status_overlay_last(self):
        render = c_function(self.action, 'static void render_under_key_output(board_rgb_t *output)')
        self.assertLess(render.index('low_battery_breath_active()'),
                        render.index('if (!s_codex_key_status_active)'))


if __name__ == '__main__':
    unittest.main()
