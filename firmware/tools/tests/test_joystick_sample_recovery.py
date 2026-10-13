import ctypes
import subprocess
import tempfile
import unittest
from pathlib import Path


BOARD_ROOT = Path(__file__).resolve().parents[2] / "components/board"


class Model(ctypes.Structure):
    _fields_ = [
        ("last_success_at", ctypes.c_uint32),
        ("centered_samples", ctypes.c_uint8),
        ("has_success", ctypes.c_bool),
        ("waiting_for_center", ctypes.c_bool),
    ]


class JoystickSampleRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        library = Path(cls.temp_dir.name) / "libjoystick_sample_recovery.dylib"
        subprocess.run(
            [
                "cc", "-std=c11", "-shared", "-fPIC",
                "-I", str(BOARD_ROOT / "include"),
                str(BOARD_ROOT / "joystick_sample_recovery.c"),
                "-o", str(library),
            ],
            check=True,
        )
        cls.library = ctypes.CDLL(str(library))
        cls.library.joystick_sample_recovery_success.argtypes = [
            ctypes.POINTER(Model), ctypes.c_uint32, ctypes.c_bool,
        ]
        cls.library.joystick_sample_recovery_failure.argtypes = [
            ctypes.POINTER(Model), ctypes.c_uint32, ctypes.c_uint32,
        ]
        cls.library.joystick_sample_recovery_failure.restype = ctypes.c_bool

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def success(self, model, at, centered):
        self.library.joystick_sample_recovery_success(
            ctypes.byref(model), at, centered
        )

    def failure(self, model, at):
        return self.library.joystick_sample_recovery_failure(
            ctypes.byref(model), at, 100
        )

    def test_outage_releases_after_100_ms_and_requires_two_center_samples(self):
        model = Model()
        self.assertFalse(self.failure(model, 200))
        self.success(model, 250, False)
        self.assertFalse(self.failure(model, 349))
        self.assertTrue(self.failure(model, 350))
        self.success(model, 360, False)
        self.assertTrue(model.waiting_for_center)
        self.success(model, 370, True)
        self.assertTrue(model.waiting_for_center)
        self.success(model, 380, True)
        self.assertFalse(model.waiting_for_center)

    def test_fresh_pair_restarts_outage_and_failed_pair_breaks_center_confirmation(self):
        model = Model()
        self.success(model, 0, False)
        self.assertFalse(self.failure(model, 90))
        self.success(model, 95, False)
        self.assertFalse(self.failure(model, 150))
        self.assertTrue(self.failure(model, 195))
        self.success(model, 205, True)
        self.assertTrue(model.waiting_for_center)
        self.assertTrue(self.failure(model, 215))
        self.success(model, 225, True)
        self.assertTrue(model.waiting_for_center)
        self.success(model, 235, True)
        self.assertFalse(model.waiting_for_center)


if __name__ == "__main__":
    unittest.main()
