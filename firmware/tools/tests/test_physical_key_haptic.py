"""Exercise the production Power V2 key-feedback functions on host."""

import ctypes
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_hid_report_sequence import function


ROOT = Path(__file__).resolve().parents[2]
ENGINE = ROOT / "components/action_engine/action_engine.c"


class PhysicalKeyHapticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp_dir.cleanup)
        work = Path(cls.temp_dir.name)
        (work / "esp_err.h").write_text("typedef int esp_err_t;\n#define ESP_OK 0\n")
        source = ENGINE.read_text()
        functions = "\n".join(
            function(source, name)
            for name in (
                "pulse_press_feedback",
                "pulse_physical_button_feedback",
                "pulse_control_press_feedback",
            )
        )
        code = r'''
#include "board.h"
#define CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2 1
#define POMODORO_ALERT 4
static struct {
    bool haptic_enabled;
    bool haptic_on_press;
    uint8_t haptic_strength;
    uint16_t haptic_duration_ms;
} s_feedback = {true, true, 60, 120};
static struct { int state; } s_pomodoro;
static struct { bool active; } s_codex_attention_haptic;
static bool s_shutdown_active, s_usb_standby_active, s_battery_splash_active;
static bool s_function_key_press_feedback_sent;
static bool s_prompt_palette_key_feedback_sent;
static unsigned pulses;
esp_err_t board_haptic_pulse(uint8_t strength, uint16_t duration) {
    if (strength && duration) ++pulses;
    return ESP_OK;
}
static void cancel_codex_attention_haptic(void) {
    s_codex_attention_haptic.active = false;
}
static bool encoder_rotation_control(board_control_t control) {
    return control == BOARD_CONTROL_ENCODER_CW ||
           control == BOARD_CONTROL_ENCODER_CCW;
}
static bool joystick_direction_control(board_control_t control) {
    return control >= BOARD_CONTROL_JOYSTICK_UP &&
           control <= BOARD_CONTROL_JOYSTICK_RIGHT;
}
static void pulse_navigation_feedback_for_control(board_control_t control) {
    (void)control;
}
static board_control_t function_center_control(void) {
    return BOARD_CONTROL_KEY_3;
}
''' + functions + r'''
void reset_haptic(void) {
    pulses = 0;
    s_feedback.haptic_enabled = true;
    s_feedback.haptic_on_press = true;
    s_feedback.haptic_strength = 60;
    s_shutdown_active = s_usb_standby_active = s_battery_splash_active = false;
    s_codex_attention_haptic.active = false;
}
void physical_event(int key, bool down, bool feedback_only) {
    board_event_t event = {
        .control = (board_control_t)key,
        .pressed = down,
        .feedback_only = feedback_only,
    };
    pulse_physical_button_feedback(&event);
}
void resolved_action(int key) {
    pulse_control_press_feedback((board_control_t)key);
}
void set_press_enabled(bool enabled) { s_feedback.haptic_on_press = enabled; }
void set_notification_active(bool active) { s_codex_attention_haptic.active = active; }
bool notification_active(void) { return s_codex_attention_haptic.active; }
unsigned pulse_count(void) { return pulses; }
'''
        source_file = work / "key_haptic.c"
        source_file.write_text(code)
        library_file = work / "key_haptic.dylib"
        subprocess.run(
            [
                "cc", "-std=c11", "-shared", "-fPIC",
                "-I", str(work),
                "-I", str(ROOT / "components/board/include"),
                str(source_file), "-o", str(library_file),
            ],
            check=True,
        )
        cls.library = ctypes.CDLL(str(library_file))
        cls.library.physical_event.argtypes = [ctypes.c_int, ctypes.c_bool, ctypes.c_bool]
        cls.library.resolved_action.argtypes = [ctypes.c_int]
        cls.library.set_press_enabled.argtypes = [ctypes.c_bool]
        cls.library.set_notification_active.argtypes = [ctypes.c_bool]

    def setUp(self):
        self.library.reset_haptic()

    def test_each_matrix_key_vibrates_on_down_without_action_duplicate(self):
        for key in (*range(7), *range(15, 20)):
            with self.subTest(key=key):
                self.library.reset_haptic()
                self.library.physical_event(key, True, False)
                self.assertEqual(self.library.pulse_count(), 1)
                self.library.resolved_action(key)
                self.assertEqual(self.library.pulse_count(), 1)
                self.library.physical_event(key, False, False)
                self.assertEqual(self.library.pulse_count(), 1)

    def test_double_click_vibrates_per_down_not_on_late_send(self):
        voice_key = 15
        for _ in range(2):
            self.library.physical_event(voice_key, True, False)
            self.library.physical_event(voice_key, False, False)
        self.assertEqual(self.library.pulse_count(), 2)
        self.library.resolved_action(voice_key)
        self.assertEqual(self.library.pulse_count(), 2)

    def test_joystick_and_encoder_press_use_physical_edges(self):
        self.library.physical_event(14, True, False)  # Joystick center.
        self.assertEqual(self.library.pulse_count(), 1)
        self.library.resolved_action(14)
        self.assertEqual(self.library.pulse_count(), 1)
        self.library.physical_event(9, True, True)  # Encoder down edge.
        self.assertEqual(self.library.pulse_count(), 2)
        self.library.physical_event(9, True, False)  # Classified short press.
        self.library.resolved_action(9)
        self.assertEqual(self.library.pulse_count(), 2)

    def test_silent_setting_and_non_key_events_do_not_interrupt_reminder(self):
        self.library.set_notification_active(True)
        self.library.set_press_enabled(False)
        self.library.physical_event(15, True, False)
        self.assertEqual(self.library.pulse_count(), 0)
        self.assertTrue(self.library.notification_active())
        self.library.set_press_enabled(True)
        self.library.physical_event(15, True, True)
        self.library.physical_event(9, True, False)  # Encoder press.
        self.assertEqual(self.library.pulse_count(), 0)
        self.library.physical_event(15, True, False)
        self.assertEqual(self.library.pulse_count(), 1)
        self.assertFalse(self.library.notification_active())


if __name__ == "__main__":
    unittest.main()
