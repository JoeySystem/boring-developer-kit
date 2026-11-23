"""Config data and every cJSON allocation prefer PSRAM.

GET_STATUS.heap checkpoints on 68DE8 showed config_store_init taking 54 KB of
the DMA-capable internal RAM (mostly the resident cJSON tree) while the device
entered its main loop with 18.5 KB left. The allocator prefers PSRAM and falls
back to the default heap on boards without it.
"""

import ctypes
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
STORE = ROOT / "components/config_store/config_store.c"
CMAKE = ROOT / "components/config_store/CMakeLists.txt"


def function(source, signature):
    start = source.index(signature)
    return source[start:source.index("\n}\n", start) + 3]


class AllocatorBehaviourTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        source = STORE.read_text()
        code = r'''
#include <stdarg.h>
#include <stdatomic.h>
#include <stddef.h>
#include <stdlib.h>
static atomic_uint s_config_alloc_failures;
unsigned alloc_failures(void) { return atomic_load(&s_config_alloc_failures); }
#define MALLOC_CAP_SPIRAM 0x400
#define MALLOC_CAP_8BIT 0x4
#define MALLOC_CAP_DEFAULT 0x1000
static unsigned seen[4]; static int spiram_full;
static void *heap_caps_malloc_prefer(size_t size, size_t num, ...) {
    va_list caps; va_start(caps, num);
    for (size_t i = 0; i < num && i < 4; ++i) seen[i] = va_arg(caps, unsigned);
    va_end(caps);
    return spiram_full ? NULL : malloc(size);
}
static void *heap_caps_calloc_prefer(size_t n, size_t size, size_t num, ...) {
    va_list caps; va_start(caps, num);
    for (size_t i = 0; i < num && i < 4; ++i) seen[i] = va_arg(caps, unsigned);
    va_end(caps);
    return calloc(n, size);
}
unsigned caps_at(int i) { return seen[i]; }
void set_spiram_full(int value) { spiram_full = value; }
''' + function(source, "static void *config_malloc(size_t size)") \
    + function(source, "static void *config_calloc(size_t count, size_t size)") + r'''
void *alloc(size_t n) { return config_malloc(n); }
void *alloc_zero(size_t n) { return config_calloc(n, 1); }
'''
        path = Path(cls.temp.name) / "alloc.c"
        path.write_text(code)
        library = path.with_suffix(".so")
        subprocess.run(["cc", "-std=c11", "-shared", "-fPIC", "-Wall", "-Werror",
                        "-Wno-unused-function", str(path), "-o", str(library)],
                       check=True, capture_output=True)
        cls.lib = ctypes.CDLL(str(library))
        cls.lib.alloc.restype = ctypes.c_void_p
        cls.lib.alloc_zero.restype = ctypes.c_void_p

    def test_prefers_psram_then_default_heap(self):
        for allocate in (self.lib.alloc, self.lib.alloc_zero):
            with self.subTest(allocate=allocate.__name__):
                self.assertTrue(allocate(ctypes.c_size_t(64)))
                self.assertEqual(0x400 | 0x4, self.lib.caps_at(0))
                self.assertEqual(0x1000, self.lib.caps_at(1))

    def test_failed_allocation_is_counted(self):
        before = self.lib.alloc_failures()
        self.lib.set_spiram_full(1)
        try:
            self.assertFalse(self.lib.alloc(ctypes.c_size_t(64)))
        finally:
            self.lib.set_spiram_full(0)
        self.assertEqual(before + 1, self.lib.alloc_failures())


class AllocatorCallSiteTests(unittest.TestCase):
    def setUp(self):
        self.source = STORE.read_text()

    def test_cjson_hooks_are_installed_first_in_init(self):
        init = function(self.source, "esp_err_t config_store_init(void)")
        body = init[init.index("{") + 1:].lstrip()
        self.assertTrue(body.startswith("install_config_json_allocator();"))
        hooks = function(self.source, "static void install_config_json_allocator(void)")
        self.assertIn(".malloc_fn = config_malloc", hooks)
        self.assertIn(".free_fn = heap_caps_free", hooks)
        self.assertIn("cJSON_InitHooks(&hooks);", hooks)

    def test_large_config_buffers_use_the_psram_allocator(self):
        for line in ("char *output = config_calloc(WMP_CONFIG_MAX_CANONICAL_BYTES + 1, 1);",
                     "char *stored = config_calloc(json_size, 1);",
                     "char *stored_json = config_malloc(expected_json_size);",
                     "*json = config_malloc(length);"):
            self.assertIn(line, self.source)

    def test_component_requires_heap(self):
        self.assertIn(" heap ", CMAKE.read_text())


if __name__ == "__main__":
    unittest.main()
