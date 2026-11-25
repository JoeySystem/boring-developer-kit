"""Stability item 2: a stalled main loop or USB config task resets.

components/liveness: armed channels must beat within their timeout; the
supervisor is the only task-watchdog subscriber and feeds it only while no
resetting channel is stalled. After the reliability review only our own tasks reset (the
main loop once it runs, the USB config parser); TinyUSB and Bluedroid are
probed and late answers are only reported; start-up is not watched. Each
channel reports its longest gap so timeouts can be set from measurements.
The stalled channel survives the reset in RTC memory for GET_STATUS.
"""

import ctypes
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_lighting_preview_protocol import c_function


ROOT = Path(__file__).resolve().parents[2]
COMPONENT = ROOT / "components/liveness"
MODEL = COMPONENT / "liveness_model.c"
LIVENESS = COMPONENT / "liveness.c"
HEADER = COMPONENT / "include/liveness.h"
APP_MAIN = ROOT / "main/app_main.c"
USB = ROOT / "components/usb_device/usb_service.c"
BLE = ROOT / "components/codex_micro/codex_micro_ble.c"
PROTOCOL = ROOT / "components/config_protocol/config_protocol.c"
SDKCONFIG = ROOT / "sdkconfig.defaults"

MAIN, USB_RX, USB_STACK, BLE_HOST = range(4)
NONE = -1


class Slot(ctypes.Structure):
    _fields_ = [("armed", ctypes.c_bool), ("arm_on_beat", ctypes.c_bool),
                ("resets", ctypes.c_bool), ("overdue", ctypes.c_bool),
                ("timeout_ms", ctypes.c_uint32), ("last_beat_ms", ctypes.c_uint32),
                ("beats", ctypes.c_uint32), ("max_gap_ms", ctypes.c_uint32),
                ("overdue_count", ctypes.c_uint32)]


class Model(ctypes.Structure):
    _fields_ = [("slots", Slot * 4)]


class Record(ctypes.Structure):
    _fields_ = [("magic", ctypes.c_uint32), ("channel", ctypes.c_uint32),
                ("uptime_ms", ctypes.c_uint32), ("check", ctypes.c_uint32)]


def header_value(name):
    return int(re.search(rf"#define {name} (\d+)u", HEADER.read_text()).group(1))


class LivenessModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        library = Path(cls.temp.name) / "liveness.so"
        subprocess.run(["cc", "-std=c11", "-shared", "-fPIC", "-Wall", "-Wextra", "-Werror",
                        "-I", str(COMPONENT / "include"), str(MODEL), "-o", str(library)],
                       check=True, capture_output=True)
        cls.lib = ctypes.CDLL(str(library))
        cls.lib.liveness_channel_name.restype = ctypes.c_char_p
        cls.lib.liveness_stall_record_load.argtypes = [ctypes.POINTER(Record),
                                                       ctypes.POINTER(ctypes.c_uint32)]
        cls.lib.liveness_model_arm.argtypes = [ctypes.POINTER(Model), ctypes.c_int,
                                               ctypes.c_uint32, ctypes.c_uint32, ctypes.c_bool]
        cls.lib.liveness_model_arm_on_beat.argtypes = [ctypes.POINTER(Model), ctypes.c_int,
                                                       ctypes.c_uint32, ctypes.c_bool]

    def setUp(self):
        self.model = Model()
        self.ref = ctypes.byref(self.model)

    def stalled(self, now):
        return self.lib.liveness_model_stalled(self.ref, ctypes.c_uint32(now))

    def test_unarmed_channels_never_stall(self):
        self.assertEqual(NONE, self.stalled(10 ** 9))

    def test_armed_channel_stalls_only_after_its_timeout(self):
        self.lib.liveness_model_arm(self.ref, MAIN, 20000, 1000, True)
        self.assertEqual(NONE, self.stalled(21000))
        self.assertEqual(MAIN, self.stalled(21001))

    def test_beats_keep_a_channel_alive(self):
        self.lib.liveness_model_arm(self.ref, USB_RX, 30000, 0, True)
        for now in range(0, 300000, 10000):
            self.lib.liveness_model_beat(self.ref, USB_RX, now)
            self.assertEqual(NONE, self.stalled(now + 29999))
        self.assertEqual(30, self.model.slots[USB_RX].beats)

    def test_disarmed_channel_stops_counting(self):
        self.lib.liveness_model_arm(self.ref, MAIN, 20000, 0, True)
        self.lib.liveness_model_disarm(self.ref, MAIN)
        self.assertEqual(NONE, self.stalled(10 ** 6))

    def test_probed_channel_is_armed_by_its_first_answer_and_never_resets(self):
        self.lib.liveness_model_arm_on_beat(self.ref, BLE_HOST, 30000, False)
        self.lib.liveness_model_note_overdue(self.ref, ctypes.c_uint32(10 ** 6))
        self.assertEqual(0, self.model.slots[BLE_HOST].overdue_count, "never answered: unarmed")
        self.lib.liveness_model_beat(self.ref, BLE_HOST, 10 ** 6)
        self.assertTrue(self.model.slots[BLE_HOST].armed)
        late = 10 ** 6 + 30001
        self.assertEqual(NONE, self.stalled(late), "report-only channel")
        self.lib.liveness_model_note_overdue(self.ref, ctypes.c_uint32(late))
        self.lib.liveness_model_note_overdue(self.ref, ctypes.c_uint32(late + 1000))
        self.assertEqual(1, self.model.slots[BLE_HOST].overdue_count, "one episode")

    def test_longest_gap_is_measured(self):
        self.lib.liveness_model_arm(self.ref, MAIN, 20000, 0, True)
        for now in (10, 30, 1530, 1540):
            self.lib.liveness_model_beat(self.ref, MAIN, now)
        self.assertEqual(1500, self.model.slots[MAIN].max_gap_ms)

    def test_clock_wrap(self):
        self.lib.liveness_model_arm(self.ref, MAIN, 20000, 0xFFFFF000, True)
        self.assertEqual(NONE, self.stalled(0x00001000))
        self.assertEqual(MAIN, self.stalled(0x00005000))

    def test_stall_record_round_trip_and_garbage(self):
        record = Record()
        uptime = ctypes.c_uint32()
        self.assertEqual(NONE, self.lib.liveness_stall_record_load(ctypes.byref(record),
                                                                   ctypes.byref(uptime)))
        self.lib.liveness_stall_record_store(ctypes.byref(record), USB_STACK, 123456)
        self.assertEqual(USB_STACK, self.lib.liveness_stall_record_load(
            ctypes.byref(record), ctypes.byref(uptime)))
        self.assertEqual(123456, uptime.value)
        record.uptime_ms ^= 1
        self.assertEqual(NONE, self.lib.liveness_stall_record_load(ctypes.byref(record),
                                                                   ctypes.byref(uptime)))

    def test_channel_names(self):
        names = [self.lib.liveness_channel_name(c).decode() for c in range(-1, 4)]
        self.assertEqual(["none", "main_loop", "usb_config_rx", "usb_stack", "ble_host"], names)


class LivenessTimeoutTests(unittest.TestCase):
    def test_timeouts_cover_slow_paths_with_margin(self):
        self.assertGreaterEqual(header_value("LIVENESS_MAIN_TIMEOUT_MS"), 15000)
        self.assertGreaterEqual(header_value("LIVENESS_USB_RX_TIMEOUT_MS"), 20000)
        self.assertGreaterEqual(header_value("LIVENESS_USB_STACK_TIMEOUT_MS"),
                                3 * header_value("LIVENESS_PROBE_PERIOD_MS"))
        self.assertGreaterEqual(header_value("LIVENESS_BLE_HOST_TIMEOUT_MS"),
                                3 * header_value("LIVENESS_PROBE_PERIOD_MS"))


class LivenessWiringTests(unittest.TestCase):
    def test_task_watchdog_panics_and_watches_only_the_supervisor(self):
        config = SDKCONFIG.read_text()
        self.assertIn("CONFIG_ESP_TASK_WDT_PANIC=y", config)
        self.assertIn("# CONFIG_ESP_TASK_WDT_CHECK_IDLE_TASK_CPU0 is not set", config)
        self.assertIn("# CONFIG_ESP_TASK_WDT_CHECK_IDLE_TASK_CPU1 is not set", config)

    def test_supervisor_feeds_only_while_nothing_is_stalled(self):
        task = c_function(LIVENESS.read_text(), "static void supervisor_task(void *parameter)")
        self.assertIn("esp_task_wdt_add(NULL)", task)
        feed = task.index("(void)esp_task_wdt_reset();")
        self.assertLess(task.index("if (stalled == LIVENESS_NONE) {"), feed)
        self.assertLess(feed, task.index("} else if (!reported) {"))
        self.assertIn("liveness_stall_record_store(&s_stall_record, stalled, now);", task)

    def test_main_loop_is_armed_only_once_it_runs(self):
        source = APP_MAIN.read_text()
        main = c_function(source, "void app_main(void)")
        self.assertLess(main.index("liveness_start();"), main.index("board_init()"))
        loop = main.index("for (;;) {")
        self.assertLess(main.index("config_store_init"),
                        main.index("liveness_arm(LIVENESS_MAIN, LIVENESS_MAIN_TIMEOUT_MS);"))
        self.assertLess(main.index("liveness_arm(LIVENESS_MAIN, LIVENESS_MAIN_TIMEOUT_MS);"), loop)
        self.assertIn("liveness_beat(LIVENESS_MAIN);", main[loop:])
        self.assertNotIn("LIVENESS_BOOT", source)
        start = c_function(LIVENESS.read_text(), "void liveness_start(void)")
        self.assertNotIn("liveness_arm(", start)

    def test_probes_never_reset(self):
        source = LIVENESS.read_text()
        probe = c_function(source, "void liveness_set_probe(liveness_channel_t channel,")
        self.assertIn("liveness_model_arm_on_beat(&s_model, channel, timeout_ms, false);", probe)
        arm = c_function(source, "void liveness_arm(liveness_channel_t channel, uint32_t timeout_ms)")
        self.assertIn("now_ms(), true);", arm)

    def test_usb_rx_beats_inside_the_drain_loop(self):
        task = c_function(USB.read_text(), "static void config_rx_task(void *parameter)")
        self.assertEqual(2, task.count("liveness_beat(LIVENESS_USB_RX);"))
        feed = task.index("config_protocol_feed_transport(")
        self.assertGreater(task.rindex("liveness_beat(LIVENESS_USB_RX);"), feed)

    def test_usb_stack_probe_runs_on_the_tinyusb_task(self):
        source = USB.read_text()
        probe = c_function(source, "static bool usb_stack_liveness_probe(void)")
        self.assertIn("usbd_defer_func(usb_stack_liveness_beat, NULL, false);", probe)
        self.assertIn("liveness_set_probe(LIVENESS_USB_STACK, usb_stack_liveness_probe,",
                      source)

    def test_ble_probe_answer_beats(self):
        source = BLE.read_text()
        probe = c_function(source, "static bool ble_host_liveness_probe(void)")
        self.assertIn("esp_ble_gap_get_device_name()", probe)
        self.assertIn("ESP_BLUEDROID_STATUS_ENABLED", probe)
        gap = c_function(source, "static void gap_event(esp_gap_ble_cb_event_t event,")
        case = gap.index("case ESP_GAP_BLE_GET_DEV_NAME_COMPLETE_EVT:")
        self.assertIn("liveness_beat(LIVENESS_BLE_HOST);", gap[case:case + 200])

    def test_get_status_reports_liveness(self):
        source = PROTOCOL.read_text()
        self.assertIn("add_liveness_status(result);", source)
        for key in ('"liveness"', '"previous_stall"', '"task_watchdog"', '"channels"',
                    '"max_gap_ms"', '"overdue"', '"resets"'):
            self.assertIn(key, source)


if __name__ == "__main__":
    unittest.main()
