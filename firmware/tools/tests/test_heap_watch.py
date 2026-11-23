"""Low-memory watch and GET_STATUS heap readout.

A returned unit exhausted its DMA-capable internal heap and nothing in
GET_STATUS showed it. The watch samples the largest free block, records the
last episode in RTC memory (kept across software resets, cleared on power-on)
and GET_STATUS reports the three heap pools plus that record.
"""

import ctypes
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COMPONENT = ROOT / "components/config_protocol"
MODEL = COMPONENT / "heap_watch_model.c"
PROTOCOL = COMPONENT / "config_protocol.c"
ENTER, EXIT = 4096, 8192


class Record(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint32) for name in (
        "magic", "boot_count", "events", "last_boot", "last_uptime_ms",
        "last_free", "last_largest", "check")]


class HeapWatchModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        library = Path(cls.temp.name) / "heap_watch.so"
        subprocess.run(
            ["cc", "-std=c11", "-shared", "-fPIC", "-Wall", "-Wextra", "-Werror",
             "-I", str(COMPONENT / "include"), str(MODEL), "-o", str(library)],
            check=True, capture_output=True)
        cls.lib = ctypes.CDLL(str(library))
        cls.lib.heap_watch_sample.restype = ctypes.c_bool
        cls.lib.heap_watch_sample.argtypes = [
            ctypes.POINTER(Record), ctypes.POINTER(ctypes.c_bool),
            ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32]
        cls.lib.heap_watch_record_boot.argtypes = [ctypes.POINTER(Record), ctypes.c_bool]
        cls.lib.heap_watch_last_boots_ago.restype = ctypes.c_uint32

    def setUp(self):
        self.record = Record()
        self.low = ctypes.c_bool(False)
        self.boot(power_on=True)

    def boot(self, power_on=False):
        self.lib.heap_watch_record_boot(ctypes.byref(self.record), power_on)
        self.low.value = False

    def sample(self, largest, free=20000, uptime=1000):
        return self.lib.heap_watch_sample(ctypes.byref(self.record), ctypes.byref(self.low),
                                          uptime, free, largest)

    def boots_ago(self):
        return self.lib.heap_watch_last_boots_ago(ctypes.byref(self.record))

    def test_healthy_heap_records_nothing(self):
        for largest in (ENTER, 30000, ENTER + 1):
            self.assertFalse(self.sample(largest))
        self.assertEqual(0, self.record.events)
        self.assertFalse(self.low.value)

    def test_episode_records_values_once_until_recovery(self):
        self.assertTrue(self.sample(ENTER - 1, free=5000, uptime=123456))
        self.assertEqual((1, 123456, 5000, ENTER - 1),
                         (self.record.events, self.record.last_uptime_ms,
                          self.record.last_free, self.record.last_largest))
        # Still low or only partly recovered: same episode.
        for largest in (100, ENTER + 100, EXIT):
            self.assertFalse(self.sample(largest))
        self.assertEqual(1, self.record.events)
        self.assertFalse(self.sample(EXIT + 1))
        self.assertFalse(self.low.value)
        self.assertTrue(self.sample(64))
        self.assertEqual(2, self.record.events)

    def test_record_survives_software_resets(self):
        self.sample(100, uptime=777)
        self.boot()
        self.boot()
        self.assertEqual((1, 777, 2), (self.record.events, self.record.last_uptime_ms,
                                       self.boots_ago()))
        self.sample(200, uptime=5)
        self.assertEqual((2, 0), (self.record.events, self.boots_ago()))

    def test_power_on_and_corrupt_records_start_fresh(self):
        self.sample(100)
        self.boot(power_on=True)
        self.assertEqual((0, 0), (self.record.events, self.record.boot_count))
        self.sample(100)
        self.record.last_free ^= 1  # RTC noinit garbage or a torn write
        self.boot()
        self.assertEqual((0, 0), (self.record.events, self.record.boot_count))
        self.record.magic = 0
        self.boot()
        self.assertEqual(0, self.record.events)


class HeapStatusSourceTests(unittest.TestCase):
    def setUp(self):
        self.source = PROTOCOL.read_text()

    def test_status_reports_three_pools_and_the_watch(self):
        body = self.source[self.source.index("static void add_heap_status("):]
        body = body[:body.index("\n}\n")]
        for token in ('"internal_dma"', "MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA",
                      '"internal"', '"spiram"', '"low_memory"', '"threshold_bytes"',
                      '"events"', '"boots_ago"'):
            self.assertIn(token, body)
        self.assertIn("add_heap_status(result);", self.source)

    def test_record_lives_in_rtc_memory_and_resets_on_power_on(self):
        self.assertIn("static RTC_NOINIT_ATTR heap_watch_record_t s_heap_watch_record;",
                      self.source)
        self.assertIn("esp_reset_reason() == ESP_RST_POWERON", self.source)

    def test_watch_starts_with_the_protocol(self):
        init = self.source[self.source.index("void config_protocol_init("):]
        init = init[:init.index("\n}\n")]
        self.assertIn("heap_watch_start();", init)


class RamFaultInjectionBuildTests(unittest.TestCase):
    """The heap-exhaustion fault exists only in the ramfault test build."""

    def test_switch_defaults_off_and_only_the_test_defaults_enable_it(self):
        kconfig = (COMPONENT / "Kconfig").read_text()
        block = kconfig[kconfig.index("config WMP_RAM_FAULT_INJECTION"):]
        self.assertIn("default n", block.split("help")[0])
        enabling = [path.name for path in ROOT.glob("sdkconfig*.defaults")
                    if "CONFIG_WMP_RAM_FAULT_INJECTION=y" in path.read_text()]
        self.assertEqual(["sdkconfig.ram-fault-injection.defaults"], enabling)

    def test_build_script_makes_it_a_fixed_custom_power_v2_build(self):
        build = (ROOT / "tools/build.sh").read_text()
        guard = build[build.index('if [ "$ram_fault_injection" = true ]; then'):]
        guard = guard[:guard.index("\nfi\n")]
        for token in ('[ "$official_build" = false ]', '[ "$custom_selected" = false ]',
                      '[ "$ble_fault_injection" = false ]', "matrix12-power-v2-codex-usb",
                      "custom_name=ramfault"):
            self.assertIn(token, guard)
        self.assertIn('sdkconfig_defaults="$sdkconfig_defaults;sdkconfig.ram-fault-injection.defaults"',
                      build)

    def test_release_builds_compile_the_fault_to_nothing(self):
        source = PROTOCOL.read_text()
        inert = source[source.index("#else\nstatic inline void ram_fault_schedule(void) {}"):]
        self.assertTrue(inert.startswith("#else\nstatic inline void ram_fault_schedule(void) {}\n#endif"))
        self.assertIn("#if CONFIG_WMP_RAM_FAULT_INJECTION", source)


if __name__ == "__main__":
    unittest.main()
