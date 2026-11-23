"""USB standby trace: OFF/ON cycles on USB power are visible in GET_STATUS.

On USB power the Power V2 power-off pulse cannot remove MCU power: OFF parks
in USB standby and ON is a fast wake without a reset. The trace counts those
periods since boot with an explicit reason for every entry and wake.
"""
import ctypes
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_firmware_confirm import c_function

FIRMWARE = Path(__file__).resolve().parents[2]
COMPONENT = FIRMWARE / 'components/config_protocol'
ENTRY = {'none': 0, 'power_button': 1, 'low_battery': 2, 'usb_low_battery': 3, 'boot': 4}
WAKE = {'none': 0, 'power_button': 1, 'battery_recovered': 2}


class Trace(ctypes.Structure):
    _fields_ = [('active', ctypes.c_bool), ('entries', ctypes.c_uint32),
                ('wakes', ctypes.c_uint32), ('last_entry_reason', ctypes.c_int),
                ('last_wake_reason', ctypes.c_int), ('last_change_ms', ctypes.c_uint32)]


class UsbStandbyTraceModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        lib = Path(cls.tmp.name) / 'trace.so'
        subprocess.run(['cc', '-std=c11', '-shared', '-fPIC', '-Wall', '-Wextra', '-Werror',
                        '-I', str(COMPONENT / 'include'),
                        str(COMPONENT / 'usb_standby_trace.c'), '-o', str(lib)], check=True)
        L = cls.lib = ctypes.CDLL(str(lib))
        for name in ('usb_standby_trace_enter', 'usb_standby_trace_wake'):
            getattr(L, name).restype = ctypes.c_bool
            getattr(L, name).argtypes = [ctypes.POINTER(Trace), ctypes.c_int, ctypes.c_uint32]
        for name in ('usb_standby_entry_reason_name', 'usb_standby_wake_reason_name'):
            getattr(L, name).restype = ctypes.c_char_p
            getattr(L, name).argtypes = [ctypes.c_int]

    def setUp(self):
        self.t = Trace()
        self.lib.usb_standby_trace_reset(ctypes.byref(self.t))

    def enter(self, reason, now):
        return self.lib.usb_standby_trace_enter(ctypes.byref(self.t), ENTRY[reason], now)

    def wake(self, reason, now):
        return self.lib.usb_standby_trace_wake(ctypes.byref(self.t), WAKE[reason], now)

    def snapshot(self):
        return (self.t.active, self.t.entries, self.t.wakes, self.t.last_entry_reason,
                self.t.last_wake_reason, self.t.last_change_ms)

    def test_off_on_cycle_counts_one_period_with_reasons(self):
        self.assertTrue(self.enter('power_button', 1000))
        self.assertEqual((True, 1, 0, 1, 0, 1000), self.snapshot())
        self.assertTrue(self.wake('power_button', 5000))
        self.assertEqual((False, 1, 1, 1, 1, 5000), self.snapshot())

    def test_splash_while_parked_does_not_start_a_new_period(self):
        self.enter('usb_low_battery', 10)
        self.assertFalse(self.enter('boot', 20))  # splash end re-enters standby
        self.assertEqual((True, 1, 0, 3, 0, 10), self.snapshot())

    def test_wake_without_standby_is_ignored(self):
        self.assertFalse(self.wake('power_button', 30))
        self.assertEqual((False, 0, 0, 0, 0, 0), self.snapshot())

    def test_counters_saturate(self):
        self.t.entries = self.t.wakes = 0xFFFFFFFF
        self.enter('low_battery', 1)
        self.wake('battery_recovered', 2)
        self.assertEqual((0xFFFFFFFF, 0xFFFFFFFF), (self.t.entries, self.t.wakes))

    def test_reason_names_are_the_protocol_strings(self):
        for name, value in ENTRY.items():
            self.assertEqual(name.encode(), self.lib.usb_standby_entry_reason_name(value))
        for name, value in WAKE.items():
            self.assertEqual(name.encode(), self.lib.usb_standby_wake_reason_name(value))
        self.assertEqual(b'unknown', self.lib.usb_standby_entry_reason_name(99))
        self.assertEqual(b'unknown', self.lib.usb_standby_wake_reason_name(99))


class UsbStandbyTraceWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.action = (FIRMWARE / 'components/action_engine/action_engine.c').read_text()
        cls.protocol = (COMPONENT / 'config_protocol.c').read_text()

    def test_every_entry_and_wake_names_its_reason(self):
        a = self.action
        self.assertIn('enter_usb_standby(USB_STANDBY_ENTRY_BOOT);',
                      c_function(a, 'static void finish_battery_splash(void)\n{'))
        self.assertIn('enter_usb_standby(s_shutdown_standby_reason);', a)
        self.assertIn('USB_STANDBY_ENTRY_POWER_BUTTON',
                      c_function(a, 'void action_engine_request_shutdown(void)\n{'))
        self.assertIn('USB_STANDBY_ENTRY_LOW_BATTERY',
                      c_function(a, 'static void start_low_battery_shutdown(bool at_boot)\n{'))
        self.assertIn('USB_STANDBY_ENTRY_USB_LOW_BATTERY',
                      c_function(a, 'static void start_low_battery_usb_standby(void)\n{'))
        self.assertIn('s_shutdown_standby_reason = standby_reason;',
                      c_function(a, 'static void begin_shutdown(const board_mist_view_t *view'))
        self.assertIn('wake_from_usb_standby(USB_STANDBY_WAKE_BATTERY_RECOVERED);',
                      c_function(a, 'static void poll_usb_standby(void)\n{'))
        self.assertIn('wake_from_usb_standby(USB_STANDBY_WAKE_POWER_BUTTON);',
                      c_function(a, 'void action_engine_request_shutdown(void)\n{'))

    def test_trace_is_published_and_a_name_restart_is_not_a_wake(self):
        enter = c_function(self.action,
                           'static void enter_usb_standby(usb_standby_entry_reason_t reason)\n{')
        self.assertIn('config_protocol_set_usb_standby_trace(&s_usb_standby_trace);', enter)
        wake = c_function(self.action,
                          'static void wake_from_usb_standby(usb_standby_wake_reason_t reason)\n{')
        self.assertLess(wake.index('action_engine_request_restart();'),
                        wake.index('usb_standby_trace_wake('))

    def test_get_status_reports_the_trace(self):
        status = c_function(self.protocol, 'static void add_usb_standby_status(cJSON *result)\n{')
        for field in ('"active"', '"entries"', '"wakes"', '"last_entry_reason"',
                      '"last_wake_reason"', '"last_change_ms"', '"uptime_ms"'):
            self.assertIn(field, status)
        self.assertIn('taskENTER_CRITICAL(&s_usb_standby_trace_lock);', status)
        get_status = self.protocol[self.protocol.index('    case WMP_MSG_GET_STATUS: {'):]
        self.assertIn('add_usb_standby_status(result);',
                      get_status[:get_status.index('    case WMP_MSG_SET_PLATFORM:')])
        self.assertIn('"usb_standby_trace.c"', (COMPONENT / 'CMakeLists.txt').read_text())


class BootMigrationOnWakeTests(unittest.TestCase):
    def test_pending_check_is_a_dry_run_over_active_and_pending(self):
        store = (FIRMWARE / 'components/config_store/config_store.c').read_text()
        check = c_function(store, 'bool config_store_boot_migration_pending(void)\n{')
        self.assertIn('{s_active_root, s_pending_root}', check)
        self.assertIn('cJSON_Duplicate(roots[index], true)', check)
        self.assertIn('migrate_matrix12_v1_root(copy, &changed)', check)
        self.assertIn('cJSON_Delete(copy);', check)
        for write in ('nvs_', 'write_slot', 'migrate_matrix12_v1_slot'):
            self.assertNotIn(write, check)
        self.assertIn('ESP_LOGW', check)            # failures are not silent

    def test_wake_restarts_for_a_pending_migration_unless_busy(self):
        action = (FIRMWARE / 'components/action_engine/action_engine.c').read_text()
        wake = c_function(action,
                          'static void wake_from_usb_standby(usb_standby_wake_reason_t reason)\n{')
        self.assertIn('!config_protocol_restart_busy() && config_store_boot_migration_pending()',
                      wake)
        self.assertIn('(restart_for_name || restart_for_migration || restart_for_power_cycle) &&\n'
                      '        intent_saved_or_read_only', wake)
        self.assertLess(wake.index('config_store_set_user_powered_on(true)'),
                        wake.index('action_engine_request_restart();'))


if __name__ == '__main__':
    unittest.main()
