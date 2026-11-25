import ctypes
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BOARD = ROOT / "components/board"
ACTION = ROOT / "components/action_engine/action_engine.c"
PROTOCOL = ROOT / "components/config_protocol/config_protocol.c"
APP = ROOT / "main/app_main.c"


class JoystickSuspectModel(ctypes.Structure):
    _fields_ = [
        ("held_directions", ctypes.c_uint8),
        ("held_since_ms", ctypes.c_uint32),
        ("tracking", ctypes.c_bool),
        ("suspect", ctypes.c_bool),
    ]


class JoystickSuspectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        library = Path(cls.temp.name) / "libjoystick_suspect.dylib"
        subprocess.run(
            [
                "cc", "-std=c11", "-shared", "-fPIC",
                "-I", str(BOARD / "include"),
                str(BOARD / "joystick_suspect_model.c"),
                "-o", str(library),
            ],
            check=True,
        )
        cls.lib = ctypes.CDLL(str(library))
        cls.lib.joystick_suspect_model_reset.argtypes = [
            ctypes.POINTER(JoystickSuspectModel)
        ]
        cls.lib.joystick_suspect_model_update.argtypes = [
            ctypes.POINTER(JoystickSuspectModel),
            ctypes.c_uint8,
            ctypes.c_uint32,
        ]
        cls.lib.joystick_suspect_model_update.restype = ctypes.c_bool

    def update(self, model, directions, now_ms):
        return self.lib.joystick_suspect_model_update(
            ctypes.byref(model), directions, now_ms
        )

    def test_same_direction_latches_after_ten_seconds_and_center_clears(self):
        model = JoystickSuspectModel()
        self.lib.joystick_suspect_model_reset(ctypes.byref(model))
        self.assertFalse(self.update(model, 0x08, 500))
        self.assertFalse(self.update(model, 0x08, 10_499))
        self.assertTrue(self.update(model, 0x08, 10_500))
        self.assertTrue(model.suspect)
        self.assertFalse(self.update(model, 0, 10_510))
        self.assertFalse(model.suspect)

    def test_direction_changes_keep_timer_and_tick_wrap_is_supported(self):
        model = JoystickSuspectModel()
        self.assertFalse(self.update(model, 0x08, 1_000))
        self.assertFalse(self.update(model, 0x09, 9_000))
        self.assertFalse(self.update(model, 0x08, 10_999))
        self.assertTrue(self.update(model, 0x09, 11_000))

        self.lib.joystick_suspect_model_reset(ctypes.byref(model))
        self.assertFalse(self.update(model, 0x01, 0xFFFF_F000))
        self.assertTrue(self.update(model, 0x01, 0x0000_1710))

    def test_operational_waits_ignore_drift_but_diagnostics_keep_full_neutral(self):
        action = ACTION.read_text()
        protocol = PROTOCOL.read_text()
        app = APP.read_text()

        self.assertIn("board_keys_neutral();", app)
        self.assertIn("!board_keys_neutral() || codex_micro_has_active_control()", protocol)
        self.assertIn("board_keys_neutral()) {\n        s_alert_ack_armed = true;", action)
        self.assertIn("board_keys_neutral();", action)

        diagnostic_start = action.index(
            "static void poll_diagnostic_capture(void)\n{"
        )
        diagnostic_poll = action[
            diagnostic_start:
            action.index("static void poll_platform_context(void)\n{", diagnostic_start)
        ]
        self.assertIn("board_inputs_neutral()", diagnostic_poll)

    def test_suspect_directions_are_filtered_after_diagnostic_capture(self):
        action = ACTION.read_text()
        handler = action[
            action.index("void action_engine_handle_event("):
            action.index("void action_engine_request_shutdown(")
        ]
        diagnostic = handler.index("diagnostic_capture_model_owns_input")
        isolation = handler.index("if (isolated_joystick) {\n        return;")
        self.assertLess(diagnostic, isolation)
        self.assertIn("joystick_direction_control(event->control)", handler)
        self.assertIn("if (!isolated_joystick) {\n        light_idle_note_activity();", handler)
        self.assertIn("if (isolated_joystick) {\n            light_idle_note_activity();", handler)
        self.assertIn("route_main_event(&release);", action)

    def test_status_and_home_expose_the_latched_condition(self):
        protocol = PROTOCOL.read_text()
        action = ACTION.read_text()
        mist = (BOARD / "mist_glyph_pages.c").read_text()
        self.assertIn('result, "joystick_suspect"', protocol)
        self.assertIn("board_joystick_suspect()", protocol)
        self.assertIn('effective_hint = "CALIBRATE JOYSTICK";', action)
        self.assertIn('add_overlay_centered(scene, "CAL JOY"', mist)


if __name__ == "__main__":
    unittest.main()
