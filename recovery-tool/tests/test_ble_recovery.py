from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest import mock

from provisioner.ble_recovery import clear_single_slot, inspect_device
from provisioner.errors import VerificationError
from provisioner.models import WmpVerification
from provisioner.protocol import BLE_SLOT_CLEAR, GET_CONFIG, WmpClient


SERIAL = "CP01-001122334455"


def status(
    *,
    slot_1=True,
    slot_2=True,
    slot_3=False,
    bond_count=None,
    active_slot=1,
    active_slot_bond_state=None,
    generation_marker=0,
    directions=0,
):
    if bond_count is None:
        bond_count = sum((slot_1, slot_2, slot_3))
    if active_slot_bond_state is None:
        active_slot_bond_state = int((slot_1, slot_2, slot_3)[active_slot - 1])
    return {
        "joystick_diagnostics": {
            "raw_x": 2048,
            "raw_y": 1647 if directions else 2048,
            "filtered_x": 2048,
            "filtered_y": 1647 if directions else 2048,
            "center_x": 2048,
            "center_y": 2048,
            "deadzone_x": 180,
            "deadzone_y": 180,
            "directions": directions,
        },
        "codex_micro": {
            "active_slot": active_slot,
            "bond_count": bond_count,
            "active_slot_bond_state": active_slot_bond_state,
            "auth_fail_events": 2,
            "last_auth_fail_reason": 5,
            "ble_services_ready": True,
            "slots": [
                {"slot": 1, "paired": slot_1, "connected": False},
                {"slot": 2, "paired": slot_2, "connected": True},
                {"slot": 3, "paired": slot_3, "connected": False},
            ],
            "ble_slots": [
                {"slot": 1, "paired": slot_1, "address_kind": "public", "identity_known": slot_1},
                {"slot": 2, "paired": slot_2, "address_kind": "random", "identity_known": slot_2},
                {"slot": 3, "paired": slot_3, "address_kind": "unknown", "identity_known": slot_3},
            ],
            "test_marker": generation_marker,
        },
        "firmware_update": {
            "state": "IDLE",
            "running_partition": "ota_0",
            "rollback_pending": False,
            "current_boot_stage": "MAIN_LOOP",
        },
    }


class FakeClient:
    def __init__(self, statuses, generations=(7, 7), calibrated=True):
        self.statuses = iter(statuses)
        self.generations = iter(generations)
        self.calibrated = calibrated
        self.last_generation = None
        self.cleared = []

    def verify(self, *, expected_hardware_id):
        self.last_generation = next(self.generations)
        return WmpVerification(
            product_id="wired-macro-pad-v1",
            hardware_id=expected_hardware_id,
            build_id="20261016.02-g0c528304",
            serial=SERIAL,
            config_generation=self.last_generation,
        )

    def get_config(self):
        return {
            "generation": self.last_generation,
            "config": {
                "joystick": {
                    "calibrated": self.calibrated,
                    "x": {"center": 2048, "deadzone": 180},
                    "y": {"center": 2048, "deadzone": 180},
                },
            },
        }

    def get_status(self):
        return next(self.statuses)

    def clear_ble_slot(self, slot):
        self.cleared.append(slot)
        return {"command": "BLE_SLOT_CLEAR", "result": {"slot": slot}}


class BleRecoveryTest(unittest.TestCase):
    def test_inspect_reports_identity_and_ble_diagnostics(self):
        result = inspect_device(FakeClient([status()], generations=(7,)))

        self.assertEqual(result["identity"]["serial"], SERIAL)
        self.assertEqual(result["ble"]["bond_count"], 2)
        self.assertEqual(result["ble"]["firmware_update"]["current_boot_stage"], "MAIN_LOOP")
        self.assertTrue(result["ble"]["slots"][1]["connected"])
        self.assertEqual(result["joystick"]["decision"], "calibrated-neutral")
        self.assertTrue(result["cleanup_safety"]["runtime_writes_allowed"])
        self.assertFalse(result["cleanup_safety"]["boot_flash_allowed"])
        self.assertFalse(result["cleanup_safety"]["factory_default_allowed"])

    def test_inspect_separates_uncalibrated_down_from_ble_cleanup(self):
        client = FakeClient(
            [status(active_slot_bond_state=0, directions=2)],
            generations=(7,),
            calibrated=False,
        )

        result = inspect_device(client)

        self.assertEqual(result["joystick"]["decision"], "needs-calibration")
        self.assertEqual(result["joystick"]["directions"], 2)
        self.assertEqual(result["cleanup_safety"]["ble_decision"], "clear-active-slot")
        self.assertEqual(result["cleanup_safety"]["stale_active_slot"], 1)

    def test_inspect_rejects_config_change_during_preflight(self):
        client = FakeClient([status()], generations=(7,))
        client.get_config = lambda: {"generation": 8, "config": {}}
        with self.assertRaisesRegex(VerificationError, "generation"):
            inspect_device(client)

    def test_clear_requires_exact_typed_confirmation(self):
        client = FakeClient([status()], generations=(7,))
        with self.assertRaisesRegex(VerificationError, "清除槽位 1"):
            clear_single_slot(
                client, slot=1, expected_serial=SERIAL, confirmation="确认",
            )
        self.assertEqual(client.cleared, [])

    def test_clear_rejects_empty_slot_without_sending_command(self):
        client = FakeClient([status()], generations=(7,))
        with self.assertRaisesRegex(VerificationError, "当前为空"):
            clear_single_slot(
                client, slot=3, expected_serial=SERIAL, confirmation="清除槽位 3",
            )
        self.assertEqual(client.cleared, [])

    def test_clear_only_selected_slot_and_rechecks_configuration(self):
        client = FakeClient([status(), status(slot_1=False)])
        result = clear_single_slot(
            client, slot=1, expected_serial=SERIAL, confirmation="清除槽位 1",
        )

        self.assertEqual(client.cleared, [1])
        self.assertFalse(result["after"]["ble"]["slots"][0]["paired"])
        self.assertTrue(result["after"]["ble"]["slots"][1]["paired"])

    def test_clear_accepts_stale_active_slot_without_existing_bond(self):
        before = status(bond_count=1, active_slot_bond_state=0)
        after = status(slot_1=False, bond_count=1, active_slot_bond_state=0)
        client = FakeClient([before, after])

        result = clear_single_slot(
            client, slot=1, expected_serial=SERIAL, confirmation="清除槽位 1",
        )

        self.assertEqual(result["before"]["ble"]["bond_count"], 1)
        self.assertEqual(result["after"]["ble"]["bond_count"], 1)

    def test_clear_rejects_changes_to_another_slot(self):
        client = FakeClient([status(), status(slot_1=False, slot_2=False)])
        with self.assertRaisesRegex(VerificationError, "槽位 2"):
            clear_single_slot(
                client, slot=1, expected_serial=SERIAL, confirmation="清除槽位 1",
            )

    def test_clear_rejects_unexpected_bond_count(self):
        after = status(slot_1=False)
        after["codex_micro"]["bond_count"] = 0
        client = FakeClient([status(), after])
        with self.assertRaisesRegex(VerificationError, "bond 数量"):
            clear_single_slot(
                client, slot=1, expected_serial=SERIAL, confirmation="清除槽位 1",
            )

    def test_protocol_clear_uses_command_and_restores_timeout(self):
        client = WmpClient(SimpleNamespace(), timeout_s=2.0)
        with mock.patch.object(
            client,
            "exchange",
            return_value={"command": "BLE_SLOT_CLEAR", "result": {"slot": 2}},
        ) as exchange:
            result = client.clear_ble_slot(2)

        exchange.assert_called_once_with(BLE_SLOT_CLEAR, {"slot": 2})
        self.assertEqual(result["result"]["slot"], 2)
        self.assertEqual(client.timeout_s, 2.0)

    def test_protocol_get_config_requires_config_object(self):
        client = WmpClient(SimpleNamespace(), timeout_s=2.0)
        with mock.patch.object(
            client,
            "exchange",
            return_value={"command": "GET_CONFIG", "result": {"generation": 7}},
        ) as exchange:
            with self.assertRaisesRegex(VerificationError, "config object"):
                client.get_config()
        exchange.assert_called_once_with(GET_CONFIG, {})


if __name__ == "__main__":
    unittest.main()
