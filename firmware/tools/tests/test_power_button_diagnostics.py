"""GET_STATUS.power_button: why a knob press did not shut down.

68DE8 twice ignored a knob hold (2026-11-10 and 11-11). The board counts
presses, short and long presses, whether the 6.5 s wake hold applies and
whether the button is armed; the action engine records each long press it
drops and why.
"""

import unittest
from pathlib import Path

from test_lighting_preview_protocol import c_function


ROOT = Path(__file__).resolve().parents[2]
BOARD = ROOT / "components/board/board_matrix12_v1.c"
ACTION = ROOT / "components/action_engine/action_engine.c"
PROTOCOL = ROOT / "components/config_protocol/config_protocol.c"
WEAK = ROOT / "components/board/board_control_logic.c"


class PowerButtonDiagnosticsTests(unittest.TestCase):
    def test_board_counts_every_outcome(self):
        poll = c_function(BOARD.read_text(), "static void poll_power_button(TickType_t now)")
        for counter in ("++s_power_button_presses;", "++s_power_button_short_presses;",
                        "++s_power_button_long_presses;"):
            self.assertIn(counter, poll)

    def test_every_dropped_long_press_records_a_reason(self):
        request = c_function(ACTION.read_text(), "void action_engine_request_shutdown(void)")
        for reason in ('"calibration"', '"diagnostic_capture"', '"shutdown_active"'):
            self.assertIn(f"board_power_button_note_ignored({reason});", request)

    def test_reported_and_safe_on_other_boards(self):
        status = c_function(PROTOCOL.read_text(), "static void add_power_button_status(cJSON *result)")
        for key in ('"presses"', '"long_presses"', '"ignored"', '"last_ignored"',
                    '"wake_mode"', '"armed"'):
            self.assertIn(key, status)
        self.assertIn("add_power_button_status(result);", PROTOCOL.read_text())
        self.assertIn("__attribute__((weak)) void board_power_button_get_diagnostics(",
                      WEAK.read_text())


if __name__ == "__main__":
    unittest.main()
