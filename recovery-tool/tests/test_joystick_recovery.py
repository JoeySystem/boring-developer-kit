from __future__ import annotations

import unittest
from unittest import mock

from provisioner.errors import VerificationError
from provisioner.joystick_recovery import (
    advance_direction_verification,
    new_direction_verification,
    parse_calibration_sample,
    validate_calibration_activation,
)
from provisioner.protocol import (
    CALIBRATION_CANCEL,
    CALIBRATION_CONFIRM,
    CALIBRATION_SAMPLE,
    CALIBRATION_START,
    WmpClient,
)


class JoystickRecoveryTest(unittest.TestCase):
    def test_protocol_uses_existing_calibration_commands_and_restores_timeout(self):
        client = WmpClient(mock.MagicMock(), timeout_s=1.0)
        client.exchange = mock.MagicMock(
            side_effect=[
                {"command": "CALIBRATION_START", "result": {"session_id": 7}},
                {"command": "CALIBRATION_SAMPLE", "result": {"session_id": 7}},
                {"command": "CALIBRATION_CONFIRM", "result": {"generation": 4}},
                {"command": "CALIBRATION_CANCEL", "result": {"session_id": 7}},
            ]
        )

        client.calibration_start()
        client.calibration_sample(7)
        client.calibration_confirm(7, 3)
        client.calibration_cancel(7)

        self.assertEqual(
            [call.args for call in client.exchange.call_args_list],
            [
                (CALIBRATION_START, {}),
                (CALIBRATION_SAMPLE, {"session_id": 7}),
                (CALIBRATION_CONFIRM, {"session_id": 7, "base_generation": 3}),
                (CALIBRATION_CANCEL, {"session_id": 7}),
            ],
        )
        self.assertEqual(client.timeout_s, 1.0)

    def test_sample_requires_travel_before_ready_to_confirm(self):
        with self.assertRaisesRegex(VerificationError, "不一致"):
            parse_calibration_sample(
                {
                    "session_id": 7,
                    "state": "CAPTURING",
                    "raw": {"x": 2048, "y": 2048},
                    "travel_complete": False,
                    "ready_to_confirm": True,
                }
            )

    def test_activation_requires_matching_saved_and_runtime_values(self):
        config = {
            "generation": 4,
            "config": {
                "joystick": {
                    "calibrated": True,
                    "x": {"center": 2050, "deadzone": 181},
                    "y": {"center": 2040, "deadzone": 179},
                }
            },
        }
        status = {
            "active": {"generation": 4},
            "pending": None,
            "joystick_diagnostics": {
                "center_x": 2050,
                "deadzone_x": 181,
                "center_y": 2040,
                "deadzone_y": 179,
                "directions": 0,
            },
        }

        result = validate_calibration_activation(
            config, status, expected_generation=4
        )

        self.assertTrue(result["calibrated"])
        self.assertEqual(result["x"]["center"], 2050)
        with self.assertRaisesRegex(VerificationError, "读回无效"):
            validate_calibration_activation(config, None, expected_generation=4)

    def test_direction_acceptance_requires_each_direction_then_neutral(self):
        state = new_direction_verification()
        for direction in (4, 8, 1, 2):
            state = advance_direction_verification(state, direction)
            self.assertTrue(state["awaiting_neutral"])
            state = advance_direction_verification(state, 0)

        self.assertTrue(state["complete"])
        self.assertEqual(state["completed"], ["up", "down", "left", "right"])


if __name__ == "__main__":
    unittest.main()
