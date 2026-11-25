"""Battery percentage shown to the user stretches the top of the raw SOC.

The CW2015 runs on its factory model, so a full cell at rest reads about 97
percent. battery_display_percent() maps 80..96 raw to 80..100 for the four
places a user sees the number; the low-battery guard keeps the raw value.
"""
import ctypes
import math
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

FIRMWARE = Path(__file__).resolve().parents[2]
COMPONENTS = FIRMWARE / "components"
FUEL = COMPONENTS / "fuel_gauge"


def function(source, name):
    match = re.search(rf"(?m)^[\w ]*\b{name}\([^;{{]*\)\s*\{{", source)
    assert match, name
    depth, index = 0, match.end() - 1
    while True:
        depth += {"{": 1, "}": -1}.get(source[index], 0)
        if depth == 0:
            return source[match.start(): index + 1]
        index += 1


class BatteryDisplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        lib = Path(cls.tmp.name) / "battery_display.so"
        subprocess.run(["cc", "-std=c11", "-shared", "-fPIC", "-Wall", "-Wextra", "-Werror",
                        "-I", str(FUEL / "include"), str(FUEL / "battery_display.c"),
                        "-o", str(lib)], check=True)
        cls.lib = ctypes.CDLL(str(lib))
        cls.lib.battery_display_percent.argtypes = [ctypes.c_float]
        cls.lib.battery_display_percent.restype = ctypes.c_uint8

    def show(self, raw):
        return self.lib.battery_display_percent(raw)

    def test_the_knee_and_the_full_point_are_named_constants(self):
        header = (FUEL / "include/battery_display.h").read_text()
        self.assertIn("#define BATTERY_DISPLAY_KNEE_PERCENT 80.0f", header)
        self.assertIn("#define BATTERY_DISPLAY_FULL_PERCENT 96.0f", header)

    def test_boundary_values(self):
        expected = {0: 0, 1: 1, 42.4: 42, 42.5: 43, 79: 79, 79.5: 80, 80: 80,
                    81: 81, 88: 90, 90: 93, 95: 99, 95.9: 100, 96: 100, 97: 100,
                    100: 100}
        for raw, shown in expected.items():
            with self.subTest(raw=raw):
                self.assertEqual(self.show(raw), shown)

    def test_below_the_knee_is_the_old_rounding(self):
        for raw in [i / 10 for i in range(0, 800)]:
            self.assertEqual(self.show(raw), int(raw + 0.5), raw)

    def test_out_of_range_and_nan_are_clamped(self):
        for raw in (-1000.0, -0.4, -0.0):
            self.assertEqual(self.show(raw), 0, raw)
        self.assertEqual(self.show(float("nan")), 0)
        for raw in (100.5, 150.0, 1e9, float("inf")):
            self.assertEqual(self.show(raw), 100, raw)

    def test_display_never_decreases_as_the_raw_value_rises(self):
        previous = 0
        for step in range(0, 10501):
            shown = self.show(step / 100)
            self.assertGreaterEqual(shown, previous, step / 100)
            previous = shown
        self.assertEqual(previous, 100)

    def test_every_display_value_up_to_100_can_be_reached(self):
        reached = {self.show(step / 100) for step in range(0, 10001)}
        self.assertEqual(reached, set(range(0, 101)))

    # --- who uses it ---
    def source(self, relative):
        return (FIRMWARE / relative).read_text()

    def test_the_four_displays_use_the_display_percentage(self):
        screen = function(self.source("components/action_engine/action_engine.c"),
                          "current_battery_percent")
        self.assertIn("battery_display_percent(battery.soc_percent)", screen)
        self.assertNotIn("+ 0.5f", screen)
        service = self.source("main/app_main.c")
        self.assertIn("battery_service_update(battery_display_percent(battery.soc_percent));", service)
        console = function(self.source("components/config_protocol/config_protocol.c"),
                           "add_battery_status")
        self.assertIn("battery_display_percent(sample.soc_percent)", console)
        codex = self.source("components/codex_micro/codex_micro_ble.c")
        self.assertIn("battery_display_percent(battery.soc_percent)", codex)
        # No display path keeps the old per-place rounding of the raw SOC.
        for path in ("main/app_main.c", "components/config_protocol/config_protocol.c",
                     "components/codex_micro/codex_micro_ble.c"):
            self.assertNotIn("soc_percent + 0.5f", self.source(path), path)

    def test_the_low_battery_guard_keeps_the_raw_value(self):
        source = self.source("components/action_engine/action_engine.c")
        guard = function(source, "guard_sample_from_gauge")
        self.assertIn("raw.soc_percent", guard)
        self.assertNotIn("battery_display_percent", guard)
        model = (COMPONENTS / "action_engine/battery_guard_model.c").read_text()
        self.assertNotIn("battery_display", model)

    def test_console_voltage_stays_raw(self):
        source = self.source("components/config_protocol/config_protocol.c")
        body = function(source, "add_power_guard_status")
        self.assertIn("sample.voltage_v * 1000.0f", body)
        self.assertNotIn("battery_display_percent", body)

    def test_fuel_gauge_component_builds_the_new_file(self):
        self.assertIn('"battery_display.c"', (FUEL / "CMakeLists.txt").read_text())


if __name__ == "__main__":
    unittest.main()
