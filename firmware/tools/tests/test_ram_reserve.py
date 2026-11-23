"""Emergency DMA-capable internal RAM for the OTA image check and AUTH signing.

esp_ota_end, esp_ota_set_boot_partition and the DS signature allocate that
pool inside ESP-IDF. A unit that exhausted it could start an update but never
finish one, so the firmware holds a reserve from start-up and hands it back
right before those calls.
"""

import ctypes
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COMPONENT = ROOT / "components/ram_reserve"
FIRMWARE_UPDATE = ROOT / "components/firmware_update/firmware_update.c"
PROTOCOL = ROOT / "components/config_protocol/config_protocol.c"

SHIMS = {
    "esp_err.h": "typedef int esp_err_t;\n#define ESP_OK 0\n#define ESP_ERR_NO_MEM 0x101\n",
    "esp_heap_caps.h": (
        "#include <stddef.h>\n#define MALLOC_CAP_INTERNAL 1\n#define MALLOC_CAP_DMA 2\n"
        "void *heap_caps_malloc(size_t size, unsigned caps);\n"
        "void heap_caps_free(void *block);\n"),
    "freertos/FreeRTOS.h": (
        "typedef int portMUX_TYPE;\n#define portMUX_INITIALIZER_UNLOCKED 0\n"
        "#define portENTER_CRITICAL(m) ((void)(m))\n#define portEXIT_CRITICAL(m) ((void)(m))\n"),
}

HARNESS = r'''
#include <stdlib.h>
static int fail_next, live, mallocs;
void *heap_caps_malloc(size_t size, unsigned caps) {
    (void)caps;
    if (fail_next) { fail_next = 0; return NULL; }
    mallocs++; live++; return malloc(size);
}
void heap_caps_free(void *block) { if (block) { live--; free(block); } }
void fail_next_alloc(void) { fail_next = 1; }
int live_blocks(void) { return live; }
int malloc_count(void) { return mallocs; }
'''


class RamReserveModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        base = Path(cls.temp.name)
        for name, text in SHIMS.items():
            (base / name).parent.mkdir(parents=True, exist_ok=True)
            (base / name).write_text(text)
        (base / "harness.c").write_text(HARNESS)
        library = base / "ram_reserve.so"
        subprocess.run(
            ["cc", "-std=c11", "-shared", "-fPIC", "-Wall", "-Wextra", "-Werror",
             "-I", str(base), "-I", str(COMPONENT / "include"),
             str(COMPONENT / "ram_reserve.c"), str(base / "harness.c"), "-o", str(library)],
            check=True, capture_output=True)
        cls.lib = ctypes.CDLL(str(library))
        cls.lib.ram_reserve_held.restype = ctypes.c_bool

    def setUp(self):
        self.lib.ram_reserve_release()

    def test_init_holds_one_block_and_release_returns_it(self):
        self.assertEqual(0, self.lib.ram_reserve_init())
        self.assertTrue(self.lib.ram_reserve_held())
        self.assertEqual(1, self.lib.live_blocks())
        self.lib.ram_reserve_release()
        self.lib.ram_reserve_release()
        self.assertFalse(self.lib.ram_reserve_held())
        self.assertEqual(0, self.lib.live_blocks())

    def test_restore_is_idempotent(self):
        self.lib.ram_reserve_restore()
        before = self.lib.malloc_count()
        self.lib.ram_reserve_restore()
        self.assertEqual(before, self.lib.malloc_count())
        self.assertEqual(1, self.lib.live_blocks())

    def test_exhausted_heap_leaves_it_released(self):
        self.lib.fail_next_alloc()
        self.assertEqual(0x101, self.lib.ram_reserve_init())
        self.assertFalse(self.lib.ram_reserve_held())
        self.lib.ram_reserve_restore()
        self.assertTrue(self.lib.ram_reserve_held())


class RamReserveCallSiteTests(unittest.TestCase):
    def setUp(self):
        self.update = FIRMWARE_UPDATE.read_text()

    def function(self, source, signature):
        body = source[source.index(signature):]
        return body[:body.index("\n}\n")]

    def test_reserve_is_taken_at_start_up(self):
        self.assertIn("ram_reserve_init()", self.function(self.update, "esp_err_t firmware_update_init("))

    def test_reserve_is_released_right_before_the_image_check(self):
        finish = self.function(self.update, "esp_err_t firmware_update_finish(")
        self.assertLess(finish.index("ram_reserve_release();"), finish.index("esp_ota_end("))

    def test_every_failed_or_abandoned_update_restores_it(self):
        self.assertIn("ram_reserve_restore();",
                      self.function(self.update, "static void clear_transaction("))
        poll = self.function(self.update, "void firmware_update_poll(")
        failure = poll[poll.index("could not select the new boot partition"):]
        self.assertIn("ram_reserve_restore();", failure[:failure.index("return;")])

    def test_auth_signature_runs_with_the_reserve_released(self):
        source = PROTOCOL.read_text()
        start = source.index("ram_reserve_release();")
        sign = source.index("device_identity_sign_digest(", start)
        restore = source.index("ram_reserve_restore();", sign)
        self.assertLess(start, sign)
        self.assertLess(sign, restore)

    def test_status_reports_the_reserve(self):
        body = self.function(PROTOCOL.read_text(), "static void add_heap_status(")
        self.assertIn('"reserve_held"', body)
        self.assertIn('"reserve_bytes"', body)


if __name__ == "__main__":
    unittest.main()
