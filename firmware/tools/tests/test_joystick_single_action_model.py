import ctypes
import subprocess
import tempfile
import unittest
from pathlib import Path


ACTION_ROOT = Path(__file__).resolve().parents[2] / "components/action_engine"


class Model(ctypes.Structure):
    _fields_ = [
        ("pending_direction", ctypes.c_uint8),
        ("consistent_samples", ctypes.c_uint8),
        ("active_direction", ctypes.c_uint8),
        ("active", ctypes.c_bool),
        ("sample_seen", ctypes.c_bool),
        ("last_sample_count", ctypes.c_uint32),
    ]


class Result(ctypes.Structure):
    _fields_ = [
        ("press", ctypes.c_bool),
        ("release", ctypes.c_bool),
        ("direction", ctypes.c_uint8),
    ]


class JoystickSingleActionModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        library = Path(cls.temp_dir.name) / "libjoystick_single_action.dylib"
        subprocess.run(
            [
                "cc", "-std=c11", "-shared", "-fPIC",
                "-I", str(ACTION_ROOT / "include"),
                str(ACTION_ROOT / "joystick_single_action_model.c"),
                str(ACTION_ROOT / "prompt_joystick_model.c"),
                "-o", str(library),
            ],
            check=True,
        )
        cls.library = ctypes.CDLL(str(library))
        cls.library.joystick_single_action_model_update.argtypes = [
            ctypes.POINTER(Model), ctypes.c_bool, ctypes.c_float,
            ctypes.c_uint32,
        ]
        cls.library.joystick_single_action_model_update.restype = Result

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def setUp(self):
        self.sample_count = 0

    def update(self, model, active, angle=0.0, sample_count=None):
        if sample_count is None:
            self.sample_count += 1
            sample_count = self.sample_count
        return self.library.joystick_single_action_model_update(
            ctypes.byref(model), active, angle, sample_count
        )

    def test_repeated_poll_of_one_adc_sample_does_not_confirm(self):
        model = Model()
        self.assertFalse(self.update(model, True, 0.0, sample_count=1).press)
        self.assertFalse(self.update(model, True, 0.0, sample_count=1).press)
        self.assertTrue(self.update(model, True, 0.0, sample_count=2).press)

    def test_two_matching_samples_choose_one_cardinal_until_center(self):
        for angle, expected in ((0.0, 3), (0.25, 1),
                                (0.5, 2), (0.75, 0)):
            model = Model()
            self.assertFalse(self.update(model, True, angle).press)
            press = self.update(model, True, angle)
            self.assertTrue(press.press)
            self.assertEqual(expected, press.direction)
            for rotated in (angle + 0.25, angle + 0.5):
                self.assertFalse(self.update(model, True, rotated % 1.0).press)
            release = self.update(model, False)
            self.assertTrue(release.release)
            self.assertEqual(expected, release.direction)
            self.assertFalse(self.update(model, False).release)

    def test_boundary_jitter_requires_a_consistent_direction(self):
        model = Model()
        for angle in (0.124, 0.126, 0.124, 0.126):
            self.assertFalse(self.update(model, True, angle).press)
        self.assertTrue(self.update(model, True, 0.126).press)
        self.assertEqual(1, model.active_direction)

    def test_recenter_before_confirmation_cancels_pending_direction(self):
        model = Model()
        self.update(model, True, 0.75)
        self.assertFalse(self.update(model, False).release)
        self.assertFalse(self.update(model, True, 0.75).press)
        self.assertTrue(self.update(model, True, 0.75).press)


if __name__ == "__main__":
    unittest.main()
