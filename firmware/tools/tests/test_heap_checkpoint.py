"""Named internal-heap checkpoints across start-up and the first connections.

Power V2 runs within a few KB of an exhausted DMA-capable internal heap
(586EC: 6.5 KB free 17 s after boot, 755 B minimum). The device console is a
UART that field units do not expose, so the readings at each start-up step
are kept in RAM and reported by GET_STATUS.heap.checkpoints.
"""

import ctypes
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COMPONENT = ROOT / "components/heap_checkpoint"
APP_MAIN = ROOT / "main/app_main.c"
PROTOCOL = ROOT / "components/config_protocol/config_protocol.c"
USB = ROOT / "components/usb_device/usb_service.c"
BLE = ROOT / "components/codex_micro/codex_micro_ble.c"

SHIMS = {
    "esp_heap_caps.h": (
        "#include <stddef.h>\n#define MALLOC_CAP_INTERNAL 1\n#define MALLOC_CAP_DMA 2\n"
        "#define MALLOC_CAP_8BIT 4\n"
        "size_t heap_caps_get_free_size(unsigned caps);\n"
        "size_t heap_caps_get_largest_free_block(unsigned caps);\n"),
    "esp_timer.h": "#include <stdint.h>\nint64_t esp_timer_get_time(void);\n",
    "freertos/FreeRTOS.h": (
        "typedef int portMUX_TYPE;\n#define portMUX_INITIALIZER_UNLOCKED 0\n"
        "#define portENTER_CRITICAL(m) ((void)(m))\n#define portEXIT_CRITICAL(m) ((void)(m))\n"),
}

HARNESS = r'''
#include <stddef.h>
#include <stdint.h>
#include "heap_checkpoint.h"
static size_t dma_free = 5000, dma_largest = 4000, internal_free = 9000;
static int64_t now_us = 1000000;
size_t heap_caps_get_free_size(unsigned caps) { return caps == 3 ? dma_free : internal_free; }
size_t heap_caps_get_largest_free_block(unsigned caps) { (void)caps; return dma_largest; }
int64_t esp_timer_get_time(void) { return now_us; }
void set_heap(size_t f, size_t l, size_t i, int64_t t) {
    dma_free = f; dma_largest = l; internal_free = i; now_us = t;
}
'''


class Entry(ctypes.Structure):
    _fields_ = [("label", ctypes.c_char_p), ("uptime_ms", ctypes.c_uint32),
                ("internal_dma_free", ctypes.c_uint32),
                ("internal_dma_largest", ctypes.c_uint32),
                ("internal_free", ctypes.c_uint32)]


class HeapCheckpointModelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        for name, text in SHIMS.items():
            (base / name).parent.mkdir(parents=True, exist_ok=True)
            (base / name).write_text(text)
        (base / "harness.c").write_text(HARNESS)
        library = base / "checkpoint.so"
        subprocess.run(
            ["cc", "-std=c11", "-shared", "-fPIC", "-Wall", "-Wextra", "-Werror",
             "-I", str(base), "-I", str(COMPONENT / "include"),
             str(COMPONENT / "heap_checkpoint.c"), str(base / "harness.c"),
             "-o", str(library)],
            check=True, capture_output=True)
        self.lib = ctypes.CDLL(str(library))
        self.lib.heap_checkpoint.argtypes = [ctypes.c_char_p]
        self.lib.heap_checkpoint_list.restype = ctypes.c_size_t
        self.labels = []

    def mark(self, label):
        value = ctypes.c_char_p(label.encode())
        self.labels.append(value)  # keep the pointer alive, as literals are
        self.lib.heap_checkpoint(value)

    def entries(self):
        buffer = (Entry * 32)()
        count = self.lib.heap_checkpoint_list(buffer, 32)
        return [(e.label.decode(), e.uptime_ms, e.internal_dma_free,
                 e.internal_dma_largest, e.internal_free) for e in buffer[:count]]

    def test_records_readings_in_order(self):
        self.mark("board")
        self.lib.set_heap(3000, 2048, 7000, ctypes.c_int64(2500000))
        self.mark("config_store")
        self.assertEqual([("board", 1000, 5000, 4000, 9000),
                          ("config_store", 2500, 3000, 2048, 7000)], self.entries())

    def test_each_label_is_recorded_once(self):
        self.mark("ble_first_connect")
        self.lib.set_heap(10, 0, 20, ctypes.c_int64(9000000))
        self.mark("ble_first_connect")
        self.assertEqual([("ble_first_connect", 1000, 5000, 4000, 9000)], self.entries())

    def test_capacity_is_bounded(self):
        for index in range(30):
            self.mark(f"step{index}")
        entries = self.entries()
        self.assertEqual(20, len(entries))
        self.assertEqual("step19", entries[-1][0])


class HeapCheckpointCallSiteTests(unittest.TestCase):
    def test_start_up_steps_are_marked_in_order(self):
        source = APP_MAIN.read_text()
        main = source[source.index("void app_main(void)"):]
        order = ["app_entry", "board", "config_store", "stores", "action_engine",
                 "firmware_update", "device_identity", "usb_service",
                 "codex_micro_ble", "ble_config", "main_loop"]
        positions = [main.index(f'heap_checkpoint("{label}");') for label in order]
        self.assertEqual(sorted(positions), positions)
        self.assertLess(main.index("config_store_init()"),
                        main.index('heap_checkpoint("config_store");'))

    def test_first_connections_are_marked(self):
        usb = USB.read_text()
        attached = usb[usb.index("if (event->id == TINYUSB_EVENT_ATTACHED) {"):]
        self.assertIn('heap_checkpoint("usb_mounted");', attached[:200])
        ble = BLE.read_text()
        connect = ble[ble.index("if (event == ESP_GATTS_CONNECT_EVT && parameter != NULL) {"):]
        self.assertIn('heap_checkpoint("ble_first_connect");', connect[:200])
        auth = ble[ble.index("case ESP_GAP_BLE_AUTH_CMPL_EVT:"):]
        self.assertIn('heap_checkpoint("ble_first_auth");', auth[:200])

    def test_status_reports_the_checkpoints(self):
        source = PROTOCOL.read_text()
        body = source[source.index("static void add_heap_status("):]
        body = body[:body.index("\n}\n")]
        for token in ('"checkpoints"', '"label"', '"internal_dma_free"',
                      '"internal_dma_largest"', '"internal_free"'):
            self.assertIn(token, body)


if __name__ == "__main__":
    unittest.main()
