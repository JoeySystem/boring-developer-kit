"""Under-key LED current budget and ramp (led_power_model.c)."""
import ctypes
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2] / 'components/board'
HEADER = (ROOT / 'include/led_power_model.h').read_text()


def constant(name):
    return int(re.search(rf'#define {name} (\d+)U', HEADER).group(1))


BUDGET = constant('LED_POWER_TOTAL_BUDGET')
HEADROOM = constant('LED_POWER_INSTANT_HEADROOM')
RATE = constant('LED_POWER_RAMP_UNITS_PER_MS')
CHANNEL_LIMIT = 153  # Power V2 per-channel limit: 60 % of 255.


class Rgb(ctypes.Structure):
    _fields_ = [('red', ctypes.c_uint8), ('green', ctypes.c_uint8),
                ('blue', ctypes.c_uint8)]


class Model(ctypes.Structure):
    _fields_ = [('shown_total', ctypes.c_uint32), ('tokens', ctypes.c_uint32),
                ('last_render_ms', ctypes.c_uint32), ('rendered', ctypes.c_bool)]


Pixels = Rgb * 12


def white(level=CHANNEL_LIMIT, count=12):
    pixels = Pixels()
    for index in range(count):
        pixels[index] = Rgb(level, level, level)
    return pixels


def total(pixels):
    return sum(p.red + p.green + p.blue for p in pixels)


class LedPowerModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        lib = Path(cls.tmp.name) / 'led_power.so'
        subprocess.run(['cc', '-std=c11', '-shared', '-fPIC', '-Wall', '-Wextra', '-Werror',
                        '-I', str(ROOT / 'include'), str(ROOT / 'led_power_model.c'),
                        '-o', str(lib)], check=True)
        cls.lib = ctypes.CDLL(str(lib))
        cls.lib.led_power_model_render.argtypes = [
            ctypes.POINTER(Model), ctypes.POINTER(Rgb), ctypes.POINTER(Rgb),
            ctypes.c_size_t, ctypes.c_uint32]
        cls.lib.led_power_model_render.restype = ctypes.c_bool

    def setUp(self):
        self.m = Model()
        self.lib.led_power_model_init(ctypes.byref(self.m))
        self.out = Pixels()

    def render(self, target, now):
        return self.lib.led_power_model_render(ctypes.byref(self.m), target, self.out,
                                               12, now)

    def ramp_until_settled(self, target, start, step_ms):
        now = start
        while self.render(target, now):
            now += step_ms
            self.assertLess(now - start, 5000, 'ramp never settled')
        return now

    def test_constants_cap_full_white_below_the_channel_limited_peak(self):
        self.assertLess(BUDGET, 12 * 3 * CHANNEL_LIMIT)
        self.assertGreaterEqual(HEADROOM, 3 * CHANNEL_LIMIT)

    def test_small_change_shows_at_once(self):
        target = Pixels()
        target[3] = Rgb(CHANNEL_LIMIT, CHANNEL_LIMIT, CHANNEL_LIMIT)
        self.assertFalse(self.render(target, 0))
        self.assertEqual(3 * CHANNEL_LIMIT, total(self.out))

    def test_full_white_ramps_and_settles_at_the_budget(self):
        target = white()
        self.assertTrue(self.render(target, 0))
        self.assertLessEqual(total(self.out), HEADROOM)
        end = self.ramp_until_settled(target, 0, 10)
        self.assertLessEqual(total(self.out), BUDGET)
        self.assertGreater(total(self.out), BUDGET - 36)  # integer floor only
        expected_ms = (BUDGET - HEADROOM) / RATE
        self.assertAlmostEqual(end, expected_ms, delta=30)

    def test_frequent_renders_never_outrun_the_ramp(self):
        target = white()
        shown = []
        for now in range(0, 200):
            self.render(target, now)
            shown.append(total(self.out))
        quantum = 12 * 3  # uniform scaling moves every channel together
        for now in range(1, len(shown)):
            self.assertLessEqual(shown[now] - shown[now - 1], RATE + quantum)
            self.assertLessEqual(shown[now], HEADROOM + now * RATE)

    def test_repeated_renders_at_one_instant_do_not_stack_headroom(self):
        target = white()
        for _ in range(20):
            self.render(target, 100)
        self.assertLessEqual(total(self.out), HEADROOM)

    def test_long_idle_gap_never_skips_the_ramp(self):
        self.render(Pixels(), 0)
        self.assertTrue(self.render(white(), 60000))
        self.assertLessEqual(total(self.out), HEADROOM)

    def test_fall_is_immediate_and_rise_again_ramps(self):
        target = white()
        end = self.ramp_until_settled(target, 0, 10)
        self.assertFalse(self.render(Pixels(), end + 1))
        self.assertEqual(0, total(self.out))
        self.assertTrue(self.render(target, end + 2))
        self.assertLessEqual(total(self.out), HEADROOM + RATE)

    def test_scaling_keeps_hue(self):
        target = Pixels()
        for index in range(12):
            target[index] = Rgb(CHANNEL_LIMIT, CHANNEL_LIMIT // 3, 0)
        self.ramp_until_settled(target, 0, 10)
        for pixel in self.out:
            self.assertEqual(0, pixel.blue)
            self.assertAlmostEqual(pixel.red / 3, pixel.green, delta=1)

    def test_target_within_budget_is_reached_exactly(self):
        target = white(level=60)
        self.ramp_until_settled(target, 0, 10)
        self.assertEqual(total(target), total(self.out))

    def test_clock_wrap(self):
        start = 0xFFFFFF00
        self.render(Pixels(), start)
        self.render(white(), (start + 10) & 0xFFFFFFFF)
        first = total(self.out)
        self.render(white(), (start + 400) & 0xFFFFFFFF)
        self.assertGreater(total(self.out), first)
        self.assertLessEqual(total(self.out), BUDGET)


def c_function(source, signature):
    start = source.index(signature)
    depth = 0
    for index in range(source.index('{', start), len(source)):
        depth += {'{': 1, '}': -1}.get(source[index], 0)
        if depth == 0:
            return source[start:index + 1]
    raise AssertionError(signature)


class LedPowerBoardWiringTests(unittest.TestCase):
    """Power V2 drives every under-key write through the model."""

    @classmethod
    def setUpClass(cls):
        cls.board = (ROOT / 'board_matrix12_v1.c').read_text()

    def power_v2_branch(self, function):
        start = function.index('#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2')
        return function[start:function.index('#else', start)]

    def test_both_write_paths_render_through_the_model(self):
        for signature in ('esp_err_t board_set_under_key_rgb(',
                          'esp_err_t board_apply_rgb('):
            branch = self.power_v2_branch(c_function(self.board, signature))
            self.assertIn('set_under_key_target(', branch)
            self.assertIn('return render_under_key_target();', branch)
            self.assertNotIn('led_strip_set_pixel', branch)
        target = c_function(self.board, 'static void set_under_key_target(')
        self.assertIn('limit_under_key_channel(red)', target)
        render = c_function(self.board, 'static esp_err_t render_under_key_target(void)\n{')
        self.assertIn('led_power_model_render(', render)

    def test_board_poll_advances_a_pending_ramp(self):
        poll = c_function(self.board, 'void board_poll(void)')
        self.assertIn('if (s_led_power_ramping) {', poll)
        self.assertIn('render_under_key_target();', poll)

    def test_model_is_built_into_the_board_component(self):
        self.assertIn('"led_power_model.c"', (ROOT / 'CMakeLists.txt').read_text())


if __name__ == '__main__':
    unittest.main()
