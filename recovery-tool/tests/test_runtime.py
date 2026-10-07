from __future__ import annotations

from types import SimpleNamespace
import sys
import unittest
from unittest import mock

from provisioner.errors import OperatorActionRequired, VerificationError
from provisioner.models import UsbIdentity, WmpVerification
from provisioner.runtime import RuntimeVerifier, select_runtime_port, serial_for_mac


def port(device, vid, pid, product="", location=None, serial_number=None):
    return SimpleNamespace(
        device=device,
        vid=vid,
        pid=pid,
        product=product,
        location=location,
        serial_number=serial_number,
    )


class RuntimeSelectionTest(unittest.TestCase):
    def setUp(self):
        self.identity = UsbIdentity("Codex Micro", "303A", "8360")

    def test_selects_exact_manifest_usb_identity(self):
        self.assertEqual(
            "/dev/cu.codex",
            select_runtime_port(
                [
                    port("/dev/cu.old", 0x303A, 0x4005),
                    port("/dev/cu.codex", 0x303A, 0x8360, "Codex Micro"),
                ],
                self.identity,
            ).device,
        )

    def test_refuses_ambiguous_runtime_devices(self):
        with self.assertRaisesRegex(VerificationError, "多个"):
            select_runtime_port(
                [
                    port("/dev/cu.one", 0x303A, 0x8360, "Codex Micro"),
                    port("/dev/cu.two", 0x303A, 0x8360, "Codex Micro"),
                ],
                self.identity,
            )

    def test_existing_runtime_can_be_excluded_before_next_batch_board(self):
        ports = [
            port("/dev/cu.existing", 0x303A, 0x8360, "Codex Micro"),
            port("/dev/cu.new", 0x303A, 0x8360, "Codex Micro"),
        ]
        new_only = [item for item in ports if item.device != "/dev/cu.existing"]
        self.assertEqual(
            "/dev/cu.new",
            select_runtime_port(new_only, self.identity).device,
        )

    def test_factory_mac_maps_to_wmp_serial(self):
        self.assertEqual(
            "CP01-288485AA3E04",
            serial_for_mac("28:84:85:AA:3E:04"),
        )

    def test_missing_runtime_after_flash_requests_power_cycle_instead_of_board_failure(self):
        verifier = RuntimeVerifier(poll_interval_s=0)

        with (
            mock.patch("provisioner.runtime.load_ports", return_value=[]),
            mock.patch.dict(
                sys.modules,
                {"serial": SimpleNamespace(SerialException=OSError)},
            ),
            self.assertRaisesRegex(
                OperatorActionRequired,
                "松开 BOOT.*拔下 USB.*重新插入",
            ),
        ):
            verifier.verify(
                self.identity,
                timeout_s=0,
                existing_runtime_devices=frozenset(),
                expected_mac="28:84:85:AA:3E:04",
                expected_hardware_id="WMP-S3-MATRIX12-POWER-V2",
                expected_build_id="build-2",
            )

    def test_post_ota_verification_requires_target_partition_and_no_rollback(self):
        connection = mock.MagicMock()
        connection.__enter__.return_value = connection
        client = mock.Mock()
        client.verify.return_value = WmpVerification(
            product_id="wired-macro-pad-v1",
            hardware_id="WMP-S3-MATRIX12-POWER-V2",
            build_id="build-2",
            serial="CP01-288485AA3E04",
            config_generation=4,
        )
        client.get_status.return_value = {
            "firmware_update": {
                "running_partition": "ota_1",
                "rollback_pending": False,
            }
        }
        serial_module = SimpleNamespace(Serial=mock.Mock(return_value=connection))

        with mock.patch("provisioner.runtime.WmpClient", return_value=client):
            result = RuntimeVerifier._verify_candidate(
                serial_module,
                port("/dev/cu.runtime", 0x303A, 0x8360),
                self.identity,
                "WMP-S3-MATRIX12-POWER-V2",
                "build-2",
                "ota_1",
            )

        self.assertEqual("ota_1", result.running_partition)
        self.assertFalse(result.rollback_pending)

    def test_post_ota_verification_rejects_pending_rollback(self):
        connection = mock.MagicMock()
        connection.__enter__.return_value = connection
        client = mock.Mock()
        client.verify.return_value = WmpVerification(
            product_id="wired-macro-pad-v1",
            hardware_id="WMP-S3-MATRIX12-POWER-V2",
            build_id="build-2",
            serial="CP01-288485AA3E04",
            config_generation=4,
        )
        client.get_status.return_value = {
            "firmware_update": {
                "running_partition": "ota_1",
                "rollback_pending": True,
            }
        }
        serial_module = SimpleNamespace(Serial=mock.Mock(return_value=connection))

        with (
            mock.patch("provisioner.runtime.WmpClient", return_value=client),
            self.assertRaisesRegex(VerificationError, "rollback"),
        ):
            RuntimeVerifier._verify_candidate(
                serial_module,
                port("/dev/cu.runtime", 0x303A, 0x8360),
                self.identity,
                "WMP-S3-MATRIX12-POWER-V2",
                "build-2",
                "ota_1",
            )


if __name__ == "__main__":
    unittest.main()
