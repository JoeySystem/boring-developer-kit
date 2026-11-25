"""Every restart must leave the USB bus before the chip resets.

Without an explicit detach the ESP32-S3 keeps its USB pull-up through
esp_restart(); Windows can then keep the stale device instance and fail to
configure the CDC port (error 433) until the cable is replugged.
"""

import ctypes
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_lighting_preview_protocol import c_function


ROOT = Path(__file__).resolve().parents[2]
USB_SOURCE = ROOT / "components/usb_device/usb_service.c"
RESTART_CALLERS = (
    ROOT / "components/firmware_update/firmware_update.c",
    ROOT / "components/action_engine/action_engine.c",
)


class UsbRestartDetachTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        source = USB_SOURCE.read_text()
        delay = re.search(r"#define USB_RESTART_DETACH_US \(([^)]*)\)", source)
        cls.delay_us = eval(delay.group(1).replace("u", ""))  # noqa: S307
        code = r'''
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#define USB_RESTART_DETACH_US (''' + delay.group(1) + r''')
static atomic_bool s_mounted, s_driver_installed;
static unsigned calls[8];
static unsigned order[8];
static unsigned step;
static uint32_t delayed_us;
static bool tud_disconnect(void) { calls[0]++; order[0] = ++step; return true; }
static void esp_rom_delay_us(uint32_t us) {
    calls[1]++; order[1] = ++step; delayed_us += us;
}
'''
        code += c_function(source, "static void detach_before_restart(void)") + "\n"
        code += r'''
void reset_test(bool installed, bool mounted) {
    atomic_store(&s_driver_installed, installed);
    atomic_store(&s_mounted, mounted);
    for (unsigned i = 0; i < 8; ++i) { calls[i] = 0; order[i] = 0; }
    step = 0; delayed_us = 0;
}
void run_shutdown(void) { detach_before_restart(); }
unsigned read_call(unsigned i) { return calls[i]; }
unsigned read_order(unsigned i) { return order[i]; }
uint32_t read_delay(void) { return delayed_us; }
bool read_mounted(void) { return atomic_load(&s_mounted); }
'''
        path = Path(cls.temp.name) / "usb_detach.c"
        path.write_text(code)
        library = path.with_suffix(".dylib")
        subprocess.run(["cc", "-std=c11", "-shared", "-fPIC", str(path),
                        "-o", str(library)], check=True, capture_output=True)
        cls.lib = ctypes.CDLL(str(library))
        cls.lib.reset_test.argtypes = [ctypes.c_bool, ctypes.c_bool]
        cls.lib.read_call.argtypes = [ctypes.c_uint]
        cls.lib.read_order.argtypes = [ctypes.c_uint]
        cls.lib.read_delay.restype = ctypes.c_uint32
        cls.lib.read_mounted.restype = ctypes.c_bool

    def test_shutdown_disconnects_then_waits_for_host(self):
        self.lib.reset_test(True, True)
        self.lib.run_shutdown()
        self.assertEqual(self.lib.read_call(0), 1)
        self.assertEqual(self.lib.read_call(1), 1)
        self.assertLess(self.lib.read_order(0), self.lib.read_order(1))
        self.assertGreaterEqual(self.lib.read_delay(), 200_000)
        self.assertFalse(self.lib.read_mounted())

    def test_detach_window_is_long_enough_but_bounded(self):
        self.assertGreaterEqual(self.delay_us, 200_000)
        self.assertLessEqual(self.delay_us, 1_000_000)

    def test_shutdown_is_a_noop_before_the_driver_exists(self):
        self.lib.reset_test(False, False)
        self.lib.run_shutdown()
        self.assertEqual(self.lib.read_call(0), 0)
        self.assertEqual(self.lib.read_call(1), 0)

    def test_handler_is_registered_only_after_driver_install(self):
        init = c_function(USB_SOURCE.read_text(), "esp_err_t usb_service_init(")
        install = init.index("tinyusb_driver_install(&usb_config)")
        failure_return = init.index("return error;", install)
        installed = init.index("atomic_store(&s_driver_installed, true);")
        register = init.index(
            "esp_register_shutdown_handler(detach_before_restart)")
        self.assertLess(failure_return, installed)
        self.assertLess(installed, register)

    def test_boot_drops_off_the_bus_after_any_non_power_on_reset(self):
        # Covers the first boot after an OTA from older firmware, which
        # restarted without detaching (error 433 on the readback).
        source = USB_SOURCE.read_text()
        decide = c_function(source, "static bool usb_boot_reattach_needed(")
        self.assertIn("reason != ESP_RST_POWERON", decide)
        init = c_function(source, "esp_err_t usb_service_init(")
        tail = init[init.index("tinyusb_cdcacm_init(&cdc_config)"):]
        self.assertLess(tail.index("tud_disconnect()"), tail.index("vTaskDelay("))
        self.assertLess(tail.index("vTaskDelay("), tail.index("tud_connect()"))
        delay = int(re.search(r"#define USB_BOOT_REATTACH_MS (\d+)u", source).group(1))
        self.assertGreaterEqual(delay, 200)
        self.assertLessEqual(delay, 1000)

    def test_all_restart_paths_go_through_esp_restart(self):
        # The shutdown handler covers a path only if it calls esp_restart();
        # a direct chip reset would bypass the USB detach.
        for path in RESTART_CALLERS:
            source = path.read_text()
            self.assertIn("esp_restart()", source, path.name)
            self.assertNotIn("esp_restart_noos", source, path.name)
            self.assertNotIn("software_reset", source, path.name)


if __name__ == "__main__":
    unittest.main()
