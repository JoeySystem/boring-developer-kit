"""USB link debounce (#3), remote wakeup (#13) and the CDC parser task (#11)."""

import ctypes
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_lighting_preview_protocol import c_function


ROOT = Path(__file__).resolve().parents[2]
ACTION = ROOT / "components/action_engine"
USB_SOURCE = ROOT / "components/usb_device/usb_service.c"
APP_SOURCE = ROOT / "main/app_main.c"

NONE, ENUMERATING, READY, SUSPENDED = range(4)


def build(directory, name, code, *sources, includes=()):
    path = Path(directory) / f"{name}.c"
    path.write_text(code)
    library = path.with_suffix(".so")
    command = ["cc", "-std=c11", "-shared", "-fPIC"]
    for include in includes:
        command += ["-I", str(include)]
    command += [str(path), *map(str, sources), "-o", str(library)]
    subprocess.run(command, check=True, capture_output=True)
    return ctypes.CDLL(str(library))


class UsbLinkModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        code = r'''
#include "usb_link_model.h"
static usb_link_model_t model;
void start(uint32_t now) { usb_link_model_init(&model, now); }
bool step(uint32_t now, int input) {
    return usb_link_model_update(&model, now, (usb_link_input_t)input);
}
'''
        cls.lib = build(cls.temp.name, "usb_link", code,
                        ACTION / "usb_link_model.c",
                        includes=[ACTION / "include"])
        cls.lib.start.argtypes = [ctypes.c_uint32]
        cls.lib.step.argtypes = [ctypes.c_uint32, ctypes.c_int]
        cls.lib.step.restype = ctypes.c_bool

    def run_steps(self, start, steps):
        self.lib.start(start)
        return [self.lib.step(now, state) for now, state in steps]

    def test_boot_without_host_releases_ble_after_wait(self):
        owned = self.run_steps(0, [(0, NONE), (1499, NONE), (1500, NONE)])
        self.assertEqual(owned, [True, True, False])

    def test_boot_on_a_host_never_releases_ble(self):
        owned = self.run_steps(0, [(0, NONE), (400, ENUMERATING),
                                   (900, READY), (10000, READY)])
        self.assertEqual(owned, [True, True, True, True])

    def test_boot_enumeration_gets_a_longer_window(self):
        owned = self.run_steps(0, [(1600, ENUMERATING), (4999, ENUMERATING),
                                   (5000, ENUMERATING)])
        self.assertEqual(owned, [True, True, False])

    def test_brief_bus_suspend_keeps_ble_off(self):
        owned = self.run_steps(0, [(100, READY), (5000, SUSPENDED),
                                   (6999, SUSPENDED), (7000, READY),
                                   (8999, SUSPENDED)])
        self.assertEqual(owned, [True] * 5)

    def test_long_suspend_hands_over_to_ble(self):
        owned = self.run_steps(0, [(100, READY), (5000, SUSPENDED),
                                   (7000, SUSPENDED)])
        self.assertEqual(owned, [True, True, False])

    def test_reenumeration_counts_as_a_brief_loss(self):
        owned = self.run_steps(0, [(100, READY), (5000, ENUMERATING),
                                   (6500, READY)])
        self.assertEqual(owned, [True, True, True])

    def test_detach_releases_quickly(self):
        owned = self.run_steps(0, [(100, READY), (5000, NONE), (5499, NONE),
                                   (5500, NONE)])
        self.assertEqual(owned, [True, True, True, False])

    def test_attach_must_be_stable_before_ble_is_dropped(self):
        owned = self.run_steps(0, [(2000, NONE), (3000, READY), (3500, NONE),
                                   (3600, READY), (4599, READY), (4600, READY)])
        self.assertEqual(owned, [False, False, False, False, False, True])

    def test_timer_wraparound(self):
        start = 0xFFFFFF00
        owned = self.run_steps(start, [(start + 10, READY),
                                       (start + 0x200, SUSPENDED),
                                       ((start + 0x200 + 2000) & 0xFFFFFFFF,
                                        SUSPENDED)])
        self.assertEqual(owned, [True, True, False])

    def test_inputs_mirror_usb_service_link_state(self):
        header = (ROOT / "components/usb_device/include/usb_service.h").read_text()
        names = re.findall(r"USB_SERVICE_LINK_(\w+)\s*=?\s*\d*,", header)
        self.assertEqual(names[:4], ["NONE", "ENUMERATING", "READY", "SUSPENDED"])
        model = (ACTION / "include/usb_link_model.h").read_text()
        self.assertEqual(re.findall(r"USB_LINK_INPUT_(\w+)", model)[:4],
                         ["NONE", "ENUMERATING", "READY", "SUSPENDED"])

    def test_engine_and_boot_use_the_model(self):
        action = (ACTION / "action_engine.c").read_text()
        sync = c_function(action, "static void sync_ble_to_usb_data_link(void)")
        self.assertIn("usb_link_model_update(", sync)
        self.assertIn("usb_service_link_state()", sync)
        self.assertNotIn("usb_service_is_mounted()", sync)
        self.assertIn("s_battery_splash_active", sync)
        self.assertIn("usb_link_model_init(&s_usb_link",
                      c_function(action, "esp_err_t action_engine_init(void)"))
        self.assertIn("const bool initially_suspended = true;", APP_SOURCE.read_text())


class RemoteWakeupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        source = USB_SOURCE.read_text()
        interval = re.search(
            r"#define USB_REMOTE_WAKEUP_MIN_INTERVAL_MS (\w+)", source).group(1)
        code = r'''
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#define USB_REMOTE_WAKEUP_MIN_INTERVAL_MS ''' + interval + r'''
static atomic_bool s_mounted, s_remote_wakeup_enabled, s_wake_requested;
static atomic_bool s_standard_enabled;
static atomic_uint_least32_t s_remote_wakeups;
static uint32_t s_last_remote_wakeup_ms;
static bool suspended, ble;
static int64_t now_us;
static unsigned signals;
static bool tud_suspended(void) { return suspended; }
static bool tud_remote_wakeup(void) { signals++; return true; }
static int64_t esp_timer_get_time(void) { return now_us; }
static bool ble_hid_connected(void) { return ble; }
'''
        code += c_function(source, "static void request_remote_wakeup(void)\n{") + "\n"
        code += c_function(source, "static void poll_remote_wakeup(void)") + "\n"
        code += r'''
void setup(bool mounted, bool enabled, bool is_suspended, bool ble_up) {
    atomic_store(&s_mounted, mounted);
    atomic_store(&s_remote_wakeup_enabled, enabled);
    atomic_store(&s_standard_enabled, true);
    atomic_store(&s_wake_requested, false);
    suspended = is_suspended; ble = ble_up; signals = 0;
    s_last_remote_wakeup_ms = 0; now_us = 1000000;
}
void press_and_poll(uint32_t at_ms) {
    now_us = (int64_t)at_ms * 1000; request_remote_wakeup(); poll_remote_wakeup();
}
void poll_only(void) { poll_remote_wakeup(); }
unsigned sent(void) { return signals; }
void set_standard(bool enabled) { atomic_store(&s_standard_enabled, enabled); }
'''
        cls.lib = build(cls.temp.name, "wakeup", code)
        cls.lib.setup.argtypes = [ctypes.c_bool] * 4
        cls.lib.press_and_poll.argtypes = [ctypes.c_uint32]
        cls.lib.set_standard.argtypes = [ctypes.c_bool]

    def test_press_wakes_a_suspended_host_that_allows_it(self):
        self.lib.setup(True, True, True, False)
        self.lib.press_and_poll(1000)
        self.assertEqual(self.lib.sent(), 1)
        self.lib.poll_only()  # the request is consumed
        self.assertEqual(self.lib.sent(), 1)

    def test_no_wakeup_when_host_awake_disallows_or_ble_has_input(self):
        for mounted, enabled, is_suspended, ble in (
                (True, True, False, False), (True, False, True, False),
                (False, True, True, False), (True, True, True, True)):
            with self.subTest(mounted=mounted, enabled=enabled,
                              suspended=is_suspended, ble=ble):
                self.lib.setup(mounted, enabled, is_suspended, ble)
                self.lib.press_and_poll(1000)
                self.assertEqual(self.lib.sent(), 0)

    def test_standby_never_wakes_the_host(self):
        self.lib.setup(True, True, True, False)
        self.lib.set_standard(False)
        self.lib.press_and_poll(1000)
        self.assertEqual(self.lib.sent(), 0)

    def test_wakeups_are_rate_limited(self):
        self.lib.setup(True, True, True, False)
        self.lib.press_and_poll(1000)
        self.lib.press_and_poll(1200)
        self.lib.press_and_poll(1500)
        self.assertEqual(self.lib.sent(), 2)

    def test_descriptor_and_press_paths(self):
        source = USB_SOURCE.read_text()
        self.assertIn("TUSB_DESC_CONFIG_ATT_REMOTE_WAKEUP", source)
        self.assertIn("void tud_suspend_cb(bool remote_wakeup_en)", source)
        for name in ("void usb_service_set_key(", "void usb_service_set_modifier(",
                     "void usb_service_tap_consumer(", "void usb_service_send_mouse("):
            with self.subTest(name=name):
                self.assertIn("request_remote_wakeup();", c_function(source, name))
        self.assertIn("poll_remote_wakeup();",
                      c_function(source, "void usb_service_poll(void)"))


class ConfigRxTaskTests(unittest.TestCase):
    def setUp(self):
        self.source = USB_SOURCE.read_text()

    def test_usb_callbacks_never_run_the_parser_or_wait_on_its_lock(self):
        rx = c_function(self.source, "static void cdc_rx_callback(")
        self.assertNotIn("config_protocol_feed_transport", rx)
        self.assertNotIn("tinyusb_cdcacm_read", rx)
        self.assertIn("xTaskNotifyGive(s_config_rx_task)", rx)
        event = c_function(self.source, "static void usb_event_callback(")
        self.assertNotIn("config_protocol_release_transport", event)
        self.assertIn("s_rx_release_pending", event)

    def test_parser_task_drains_fifo_and_releases_transport(self):
        task = c_function(self.source, "static void config_rx_task(void *parameter)")
        self.assertIn("config_protocol_feed_transport(CONFIG_PROTOCOL_TRANSPORT_USB", task)
        self.assertIn("tinyusb_cdcacm_read(TINYUSB_CDC_ACM_0", task)
        self.assertIn("config_protocol_release_transport(CONFIG_PROTOCOL_TRANSPORT_USB)",
                      task)
        # A timeout covers a notification that races with a drain.
        self.assertIn("ulTaskNotifyTake(pdTRUE,", task)

    def test_task_exists_before_usb_can_deliver_data(self):
        init = c_function(self.source, "esp_err_t usb_service_init(")
        self.assertLess(init.index("xTaskCreate(config_rx_task"),
                        init.index("tinyusb_driver_install(&usb_config)"))
        priority = int(re.search(r"#define USB_CONFIG_RX_TASK_PRIORITY (\d+)",
                                 self.source).group(1))
        self.assertGreaterEqual(priority, 2)
        self.assertLess(priority, 5)  # below the TinyUSB task


if __name__ == "__main__":
    unittest.main()
