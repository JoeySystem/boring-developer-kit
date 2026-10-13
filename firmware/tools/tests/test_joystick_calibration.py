import ctypes
import subprocess
import tempfile
import unittest
from pathlib import Path


FIRMWARE_ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_ROOT = FIRMWARE_ROOT / "components/config_protocol"
BOARD_ROOT = FIRMWARE_ROOT / "components/board"

INACTIVE = 0
CENTERING = 1
CAPTURING = 2


class Candidate(ctypes.Structure):
    _fields_ = [
        ("valid", ctypes.c_bool),
        ("minimum_x", ctypes.c_int),
        ("center_x", ctypes.c_int),
        ("maximum_x", ctypes.c_int),
        ("minimum_y", ctypes.c_int),
        ("center_y", ctypes.c_int),
        ("maximum_y", ctypes.c_int),
        ("deadzone_x", ctypes.c_int),
        ("deadzone_y", ctypes.c_int),
    ]


class Model(ctypes.Structure):
    _fields_ = [
        ("state", ctypes.c_int),
        ("session_id", ctypes.c_uint32),
        ("started_ms", ctypes.c_uint32),
        ("last_activity_ms", ctypes.c_uint32),
        ("last_sample_ms", ctypes.c_uint32),
        ("center_sample_count", ctypes.c_uint32),
        ("center_sum_x", ctypes.c_int64),
        ("center_sum_y", ctypes.c_int64),
        ("center_min_x", ctypes.c_int),
        ("center_max_x", ctypes.c_int),
        ("center_min_y", ctypes.c_int),
        ("center_max_y", ctypes.c_int),
        ("center_x", ctypes.c_int),
        ("center_y", ctypes.c_int),
        ("minimum_x", ctypes.c_int),
        ("maximum_x", ctypes.c_int),
        ("minimum_y", ctypes.c_int),
        ("maximum_y", ctypes.c_int),
        ("raw_x", ctypes.c_int),
        ("raw_y", ctypes.c_int),
        ("away_since_return", ctypes.c_bool),
        ("return_started_ms", ctypes.c_uint32),
        ("return_sample_count", ctypes.c_uint32),
        ("return_sum_x", ctypes.c_int64),
        ("return_sum_y", ctypes.c_int64),
        ("return_min_x", ctypes.c_int),
        ("return_max_x", ctypes.c_int),
        ("return_min_y", ctypes.c_int),
        ("return_max_y", ctypes.c_int),
        ("accepted_return_count", ctypes.c_uint32),
        ("accepted_sample_count", ctypes.c_uint32),
        ("accepted_sum_x", ctypes.c_int64),
        ("accepted_sum_y", ctypes.c_int64),
        ("accepted_min_x", ctypes.c_int),
        ("accepted_max_x", ctypes.c_int),
        ("accepted_min_y", ctypes.c_int),
        ("accepted_max_y", ctypes.c_int),
    ]


class JoystickCalibrationModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        library = Path(cls.temp_dir.name) / "libjoystick_calibration.dylib"
        subprocess.run(
            [
                "cc", "-std=c11", "-shared", "-fPIC",
                "-I", str(PROTOCOL_ROOT / "include"),
                "-I", str(BOARD_ROOT / "include"),
                str(PROTOCOL_ROOT / "joystick_calibration_model.c"),
                str(BOARD_ROOT / "board_input_logic.c"),
                "-lm",
                "-o", str(library),
            ],
            check=True,
        )
        cls.library = ctypes.CDLL(str(library))
        cls.library.joystick_calibration_model_start.argtypes = [
            ctypes.POINTER(Model), ctypes.c_uint32, ctypes.c_uint32,
            ctypes.c_int, ctypes.c_int,
        ]
        cls.library.joystick_calibration_model_sample.argtypes = [
            ctypes.POINTER(Model), ctypes.c_uint32, ctypes.c_int, ctypes.c_int,
        ]
        cls.library.joystick_calibration_model_touch.argtypes = [
            ctypes.POINTER(Model), ctypes.c_uint32,
        ]
        cls.library.joystick_calibration_model_timed_out.argtypes = [
            ctypes.POINTER(Model), ctypes.c_uint32,
        ]
        cls.library.joystick_calibration_model_timed_out.restype = ctypes.c_bool
        cls.library.joystick_calibration_model_candidate.argtypes = [
            ctypes.POINTER(Model), ctypes.POINTER(Candidate),
        ]
        cls.library.joystick_calibration_model_candidate.restype = ctypes.c_bool
        cls.library.joystick_calibration_model_cancel.argtypes = [ctypes.POINTER(Model)]
        cls.library.board_joystick_radial_angle.argtypes = [
            *([ctypes.c_int] * 10), ctypes.POINTER(ctypes.c_float),
        ]
        cls.library.board_joystick_radial_angle.restype = ctypes.c_bool

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def centered_model(self):
        model = Model()
        self.library.joystick_calibration_model_start(
            ctypes.byref(model), 7, 0, 2048, 2050
        )
        for now in range(20, 500, 20):
            x, y = (2046, 2052) if now % 40 == 20 else (2050, 2048)
            self.library.joystick_calibration_model_sample(
                ctypes.byref(model), now, x, y
            )
        self.library.joystick_calibration_model_sample(
            ctypes.byref(model), 500, 2048, 2050
        )
        return model

    def sample(self, model, samples):
        for now, x, y in samples:
            self.library.joystick_calibration_model_sample(
                ctypes.byref(model), now, x, y
            )

    def complete_two_returns(self, model):
        self.sample(model, ((now, 2048, 2050) for now in range(1000, 1201, 20)))
        self.sample(model, ((1300, 180, 2050),))
        self.sample(model, ((now, 2048, 2050) for now in range(1400, 1601, 20)))

    def test_center_window_averages_resting_samples_before_capture(self):
        model = self.centered_model()
        self.assertEqual(CAPTURING, model.state)
        self.assertEqual(2048, model.center_x)
        self.assertEqual(2050, model.center_y)

    def test_full_travel_produces_bounded_candidate(self):
        model = self.centered_model()
        self.sample(model, (
            (600, 180, 2050), (700, 3900, 2050),
            (800, 2048, 170), (900, 2048, 3910),
        ))
        candidate = Candidate()
        self.assertFalse(self.library.joystick_calibration_model_candidate(
            ctypes.byref(model), ctypes.byref(candidate)
        ))
        self.complete_two_returns(model)
        self.assertTrue(
            self.library.joystick_calibration_model_candidate(
                ctypes.byref(model), ctypes.byref(candidate)
            )
        )
        self.assertEqual((180, 2048, 3900), (
            candidate.minimum_x, candidate.center_x, candidate.maximum_x
        ))
        self.assertEqual((170, 2050, 3910), (
            candidate.minimum_y, candidate.center_y, candidate.maximum_y
        ))
        self.assertGreaterEqual(candidate.deadzone_x, 180)
        self.assertLessEqual(candidate.deadzone_x, 512)

    def test_only_one_return_does_not_accept_short_clean_window(self):
        model = self.centered_model()
        self.sample(model, (
            (600, 180, 2050), (700, 3900, 2050),
            (800, 2048, 170), (900, 2048, 3910),
        ))
        self.sample(model, ((now, 2048, 2050) for now in range(1000, 1201, 20)))
        candidate = Candidate()
        self.assertFalse(self.library.joystick_calibration_model_candidate(
            ctypes.byref(model), ctypes.byref(candidate)
        ))
        self.assertEqual(1, model.accepted_return_count)

    def test_return_samples_correct_slightly_misplaced_initial_center(self):
        model = Model()
        self.library.joystick_calibration_model_start(
            ctypes.byref(model), 7, 0, 2100, 2050
        )
        self.sample(model, ((now, 2100, 2050) for now in range(20, 501, 20)))
        self.sample(model, (
            (600, 180, 2050), (700, 3900, 2050),
            (800, 2048, 170), (900, 2048, 3910),
        ))
        self.complete_two_returns(model)
        candidate = Candidate()
        self.assertTrue(self.library.joystick_calibration_model_candidate(
            ctypes.byref(model), ctypes.byref(candidate)
        ))
        self.assertEqual(2048, candidate.center_x)
        self.assertEqual(180, candidate.deadzone_x)

    def test_unstable_center_cannot_hide_behind_deadzone_clamp(self):
        model = Model()
        self.library.joystick_calibration_model_start(
            ctypes.byref(model), 7, 0, 2048, 2050
        )
        self.sample(model, ((now, 1548 if now % 40 == 20 else 2548, 2050)
                            for now in range(20, 500, 20)))
        self.sample(model, ((500, 2048, 2050),))
        self.sample(model, (
            (600, 180, 2050), (700, 3900, 2050),
            (800, 2048, 170), (900, 2048, 3910),
        ))
        self.complete_two_returns(model)
        candidate = Candidate()
        self.assertFalse(self.library.joystick_calibration_model_candidate(
            ctypes.byref(model), ctypes.byref(candidate)
        ))

    def test_deadzone_must_leave_each_direction_usable(self):
        model = Model()
        self.library.joystick_calibration_model_start(
            ctypes.byref(model), 7, 0, 2048, 2050
        )
        self.sample(model, ((now, 1748 if now % 40 == 20 else 2348, 2050)
                            for now in range(20, 500, 20)))
        self.sample(model, ((500, 2048, 2050),))
        self.sample(model, (
            (600, 1648, 2050), (700, 2448, 2050),
            (800, 2048, 1650), (900, 2048, 2450),
        ))
        self.complete_two_returns(model)
        candidate = Candidate()
        self.assertFalse(self.library.joystick_calibration_model_candidate(
            ctypes.byref(model), ctypes.byref(candidate)
        ))

    def test_gap_in_return_samples_does_not_count_as_stable_rest(self):
        model = self.centered_model()
        self.sample(model, (
            (600, 180, 2050), (700, 3900, 2050),
            (800, 2048, 170), (900, 2048, 3910),
            (1000, 2048, 2050), (1020, 2048, 2050),
            (1200, 2048, 2050),
        ))
        self.assertEqual(0, model.accepted_return_count)

    def test_gap_in_initial_samples_restarts_center_window(self):
        model = Model()
        self.library.joystick_calibration_model_start(
            ctypes.byref(model), 7, 0, 2048, 2050
        )
        self.sample(model, ((now, 2048, 2050) for now in range(20, 201, 20)))
        self.sample(model, ((500, 2048, 2050),))
        self.assertEqual(CENTERING, model.state)
        self.assertEqual(500, model.started_ms)

    def test_calibrated_deadzone_covers_correlated_two_axis_rest_noise(self):
        model = Model()
        self.library.joystick_calibration_model_start(
            ctypes.byref(model), 7, 0, 2048, 2050
        )
        self.sample(model, (
            (now, 2178 if now % 40 == 20 else 1918,
             2180 if now % 40 == 20 else 1920)
            for now in range(20, 500, 20)
        ))
        self.sample(model, ((500, 2048, 2050),))
        self.sample(model, (
            (600, 180, 2050), (700, 3900, 2050),
            (800, 2048, 170), (900, 2048, 3910),
        ))
        self.complete_two_returns(model)
        candidate = Candidate()
        self.assertTrue(self.library.joystick_calibration_model_candidate(
            ctypes.byref(model), ctypes.byref(candidate)
        ))
        self.assertGreater(candidate.deadzone_x, 184)
        self.assertGreater(candidate.deadzone_y, 184)
        angle = ctypes.c_float()
        self.assertFalse(self.library.board_joystick_radial_angle(
            candidate.center_x + 130, candidate.center_y + 130,
            candidate.center_x, candidate.center_y,
            candidate.minimum_x, candidate.maximum_x,
            candidate.minimum_y, candidate.maximum_y,
            candidate.deadzone_x * 3 // 4,
            candidate.deadzone_y * 3 // 4,
            ctypes.byref(angle),
        ))

    def test_incomplete_travel_is_rejected_without_changing_session(self):
        model = self.centered_model()
        self.library.joystick_calibration_model_sample(
            ctypes.byref(model), 700, 180, 2050
        )
        candidate = Candidate()
        self.assertFalse(
            self.library.joystick_calibration_model_candidate(
                ctypes.byref(model), ctypes.byref(candidate)
            )
        )
        self.assertEqual(CAPTURING, model.state)

    def test_activity_extends_timeout_and_cancel_discards_session(self):
        model = self.centered_model()
        self.library.joystick_calibration_model_touch(ctypes.byref(model), 100000)
        self.assertFalse(
            self.library.joystick_calibration_model_timed_out(
                ctypes.byref(model), 219999
            )
        )
        self.assertTrue(
            self.library.joystick_calibration_model_timed_out(
                ctypes.byref(model), 220000
            )
        )
        self.library.joystick_calibration_model_cancel(ctypes.byref(model))
        self.assertEqual(INACTIVE, model.state)
        self.assertEqual(0, model.session_id)


if __name__ == "__main__":
    unittest.main()
