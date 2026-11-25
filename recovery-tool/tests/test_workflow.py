from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from provisioner.errors import CompatibilityError, OperatorActionRequired
from provisioner.models import (
    ChipInfo,
    OtaUpdateResult,
    ProvisionMode,
    ProvisionRequest,
    RecordKind,
    RuntimeVerification,
)
from provisioner.ota import OtaTransportInterrupted
from provisioner.release import ReleasePackage
from provisioner.workflow import Provisioner
from tests.helpers import make_ota_release, make_release


@dataclass
class FakeRunner:
    reads: dict[tuple[int, int], bytes]

    def __post_init__(self):
        self.inspections = []
        self.writes = []
        self.read_calls = []

    def inspect(self, port):
        self.inspections.append(port)
        return ChipInfo(chip="ESP32-S3", mac="28:84:85:AA:3E:04", flash_size="16MB")

    def read_flash(self, port, offset, size):
        self.read_calls.append((port, offset, size))
        if (offset, size) in self.reads:
            return self.reads[(offset, size)]
        result = bytearray(b"\xFF" * size)
        for (region_offset, region_size), data in self.reads.items():
            overlap_start = max(offset, region_offset)
            overlap_end = min(offset + size, region_offset + region_size)
            if overlap_start >= overlap_end:
                continue
            result[overlap_start - offset : overlap_end - offset] = data[
                overlap_start - region_offset : overlap_end - region_offset
            ]
        return bytes(result)

    def write_flash(self, port, images):
        self.writes.append((port, [(image.offset, image.path.name) for image in images]))


class FakeVerifier:
    def __init__(self):
        self.calls = []

    def capture_existing(self, identity):
        return frozenset()

    def verify(
        self,
        identity,
        timeout_s,
        existing_runtime_devices,
        expected_mac,
        expected_hardware_id="WMP-S3-REV-A",
        expected_build_id=None,
    ):
        self.calls.append(
            (
                identity,
                timeout_s,
                existing_runtime_devices,
                expected_mac,
                expected_hardware_id,
                expected_build_id,
            )
        )
        return RuntimeVerification(
            port="/dev/cu.runtime",
            product="Codex Micro",
            vid="303A",
            pid="8360",
            product_id="wired-macro-pad-v1",
            hardware_id="WMP-S3-REV-A",
            build_id=expected_build_id or "",
            serial="CP01-288485AA3E04",
            config_generation=3,
        )

    def verify_serial(
        self,
        identity,
        timeout_s,
        expected_serial,
        expected_hardware_id,
        expected_build_id,
        expected_partition,
        *,
        replug_after_s=None,
        on_replug_needed=None,
    ):
        self.replug_after_s = replug_after_s
        if getattr(self, "simulate_replug", False) and on_replug_needed is not None:
            on_replug_needed()
        self.calls.append(
            (
                "verify_serial",
                identity,
                timeout_s,
                expected_serial,
                expected_hardware_id,
                expected_build_id,
                expected_partition,
            )
        )
        return RuntimeVerification(
            port="/dev/cu.runtime",
            product="Codex Micro",
            vid="303A",
            pid="8360",
            product_id="wired-macro-pad-v1",
            hardware_id=expected_hardware_id,
            build_id=expected_build_id,
            serial=expected_serial,
            config_generation=4,
            running_partition=expected_partition,
            rollback_pending=False,
        )

    def find_serial_port(
        self,
        identity,
        timeout_s,
        expected_serial,
        expected_hardware_id,
    ):
        self.calls.append(
            (
                "find_serial_port",
                identity,
                timeout_s,
                expected_serial,
                expected_hardware_id,
            )
        )
        return "/dev/cu.reconnected"


class ForbiddenVerifier:
    def capture_existing(self, identity):
        raise AssertionError("bare-chip preload must not scan runtime USB")

    def verify(self, identity, timeout_s, existing_runtime_devices, expected_mac, *args):
        raise AssertionError("bare-chip preload must not require runtime USB")


class ReplugRequiredVerifier(FakeVerifier):
    def verify(self, *args, **kwargs):
        raise OperatorActionRequired("请松开 BOOT，拔下 USB 后重新插入")


class ProvisioningWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.package = ReleasePackage.open(make_release(Path(self.temp.name)))
        self.history = Path(self.temp.name) / "history"

    def runner_with_matching_baseline(self):
        return FakeRunner(
            {
                (image.offset, image.size): image.path.read_bytes()
                for image in self.package.flash_images[:2]
            }
        )

    def request(self, mode):
        return ProvisionRequest(
            package=self.package,
            port="/dev/cu.rom",
            mode=mode,
            operator="test",
        )

    def test_first_install_writes_all_segments_without_erasing(self):
        runner = self.runner_with_matching_baseline()
        result = Provisioner(runner, FakeVerifier(), self.history).run(
            self.request(ProvisionMode.FIRST_INSTALL)
        )

        self.assertEqual(
            [0x0, 0x8000, 0x10000],
            [offset for offset, _name in runner.writes[0][1]],
        )
        self.assertTrue(result.receipt_path.is_file())

    def test_receipt_records_production_or_validation_classification(self):
        request = ProvisionRequest(
            package=self.package,
            port="/dev/cu.rom",
            mode=ProvisionMode.CHIP_PRELOAD,
            operator="test",
            record_kind=RecordKind.PRODUCTION,
        )
        runner = self.runner_with_matching_baseline()
        runner.reads[(self.package.application.offset, self.package.application.size)] = (
            self.package.application.path.read_bytes()
        )
        result = Provisioner(runner, ForbiddenVerifier(), self.history).run(request)

        receipt_data = result.receipt_path.read_text(encoding="utf-8")
        self.assertIn('"record_kind": "production"', receipt_data)

    def test_download_flash_prompts_replug_before_runtime_verification(self):
        events = []

        Provisioner(
            self.runner_with_matching_baseline(),
            FakeVerifier(),
            self.history,
        ).run(
            self.request(ProvisionMode.FIRST_INSTALL),
            lambda stage, message, progress: events.append((stage, message, progress)),
        )

        self.assertTrue(
            any(
                stage == "operator-action"
                and "松开 BOOT" in message
                and "重新插入" in message
                and progress == 80
                for stage, message, progress in events
            )
        )

    def test_replug_timeout_is_saved_as_pending_verification(self):
        events = []

        with self.assertRaises(OperatorActionRequired):
            Provisioner(
                self.runner_with_matching_baseline(),
                ReplugRequiredVerifier(),
                self.history,
            ).run(
                self.request(ProvisionMode.FIRST_INSTALL),
                lambda stage, message, progress: events.append((stage, message, progress)),
            )

        receipt = next(self.history.glob("*.json"))
        content = receipt.read_text(encoding="utf-8")
        self.assertIn('"status": "operator-action-required"', content)
        self.assertIn('"success": null', content)
        self.assertEqual("operator-action-required", events[-1][0])

    def test_normal_update_requires_matching_bootloader_and_partition(self):
        runner = self.runner_with_matching_baseline()
        Provisioner(runner, FakeVerifier(), self.history).run(
            self.request(ProvisionMode.UPDATE)
        )
        self.assertEqual([0x10000], [offset for offset, _name in runner.writes[0][1]])
        self.assertEqual(1, len(runner.read_calls))

        mismatched = self.runner_with_matching_baseline()
        first = self.package.flash_images[0]
        mismatched.reads[(first.offset, first.size)] = b"x" * first.size
        with self.assertRaisesRegex(CompatibilityError, "首次安装"):
            Provisioner(mismatched, FakeVerifier(), self.history).run(
                self.request(ProvisionMode.UPDATE)
            )
        self.assertEqual([], mismatched.writes)
        failure_receipts = [
            path
            for path in self.history.glob("*.json")
            if '"success": false' in path.read_text(encoding="utf-8")
        ]
        self.assertEqual(1, len(failure_receipts))

    def test_default_recovery_preserves_nvs_and_uses_application_only(self):
        runner = self.runner_with_matching_baseline()
        Provisioner(runner, FakeVerifier(), self.history).run(
            self.request(ProvisionMode.RECOVERY)
        )
        self.assertEqual([0x10000], [offset for offset, _name in runner.writes[0][1]])

    def test_chip_preload_writes_and_reads_back_all_segments_without_runtime_usb(self):
        runner = self.runner_with_matching_baseline()
        runner.reads[(self.package.application.offset, self.package.application.size)] = (
            self.package.application.path.read_bytes()
        )

        result = Provisioner(runner, ForbiddenVerifier(), self.history).run(
            self.request(ProvisionMode.CHIP_PRELOAD)
        )

        self.assertIsNone(result.runtime)
        self.assertEqual(
            [0x0, 0x8000, 0x10000],
            [offset for offset, _name in runner.writes[0][1]],
        )
        self.assertEqual(1, len(runner.read_calls))
        receipt = result.receipt_path.read_text(encoding="utf-8")
        self.assertIn('"verification": "flash-readback"', receipt)

    def test_chip_preload_rejects_any_readback_mismatch(self):
        runner = self.runner_with_matching_baseline()
        runner.reads[(self.package.application.offset, self.package.application.size)] = (
            b"x" * self.package.application.size
        )
        with self.assertRaisesRegex(CompatibilityError, "读回校验失败"):
            Provisioner(runner, ForbiddenVerifier(), self.history).run(
                self.request(ProvisionMode.CHIP_PRELOAD)
            )

    def test_interrupted_flash_leaves_in_progress_receipt(self):
        runner = self.runner_with_matching_baseline()
        runner.write_flash = mock.Mock(side_effect=SystemExit("power loss"))

        with self.assertRaises(SystemExit):
            Provisioner(runner, ForbiddenVerifier(), self.history).run(
                self.request(ProvisionMode.CHIP_PRELOAD)
            )

        receipts = list(self.history.glob("*.json"))
        self.assertEqual(1, len(receipts))
        content = receipts[0].read_text(encoding="utf-8")
        self.assertIn('"status": "in-progress"', content)
        self.assertIn('"success": null', content)

    def test_power_v2_boot_recovery_writes_all_four_segments(self):
        ota_package = ReleasePackage.open(make_ota_release(Path(self.temp.name)))
        self.addCleanup(ota_package.close)
        runner = FakeRunner({})
        request = ProvisionRequest(
            package=ota_package,
            port="/dev/cu.rom",
            mode=ProvisionMode.RECOVERY,
        )

        Provisioner(runner, FakeVerifier(), self.history).run(request)

        self.assertEqual(
            [0x0, 0x8000, 0x10000, 0x240000],
            [offset for offset, _name in runner.writes[0][1]],
        )
        self.assertEqual([], runner.read_calls)

    def test_power_v2_runtime_update_uses_ota_then_reads_back_build_id(self):
        ota_package = ReleasePackage.open(make_ota_release(Path(self.temp.name)))
        self.addCleanup(ota_package.close)
        verifier = FakeVerifier()

        class FakeConnection:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        class FakeUpdater:
            def __init__(self):
                self.calls = []

            def update(self, client, application, **kwargs):
                self.calls.append((client, application, kwargs))
                kwargs["progress"](application.size, application.size)
                return OtaUpdateResult(
                    serial="CP01-288485AA3E04",
                    previous_version="0.2.0",
                    target_partition="ota_1",
                    received_size=application.size,
                )

        updater = FakeUpdater()
        provisioner = Provisioner(
            FakeRunner({}), verifier, self.history, ota_updater=updater
        )
        provisioner._open_serial = lambda _port: FakeConnection()
        request = ProvisionRequest(
            package=ota_package,
            port="/dev/cu.runtime",
            mode=ProvisionMode.UPDATE,
        )

        result = provisioner.run_ota(request)

        self.assertIsNone(result.chip)
        self.assertEqual(ota_package.build_id, result.runtime.build_id)
        self.assertEqual(1, len(updater.calls))
        self.assertFalse(updater.calls[0][2]["restart_mismatched"])
        self.assertEqual("verify_serial", verifier.calls[-1][0])
        self.assertEqual("ota_1", verifier.calls[-1][-1])
        receipt = result.receipt_path.read_text(encoding="utf-8")
        self.assertIn('"update_route": "cdc-ota"', receipt)
        self.assertIn('"target_partition": "ota_1"', receipt)
        timings = json.loads(receipt)["timings_s"]
        for key in (
            "preflight_s",
            "transfer_and_finalize_s",
            "reboot_reconnect_readback_s",
            "total_s",
        ):
            self.assertIsInstance(timings[key], float, key)
        self.assertEqual(timings["serial_reconnects"], 0)

    def test_ota_readback_waits_for_a_replug_instead_of_failing(self):
        ota_package = ReleasePackage.open(make_ota_release(Path(self.temp.name)))
        self.addCleanup(ota_package.close)
        verifier = FakeVerifier()
        verifier.simulate_replug = True

        class FakeConnection:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        class FakeUpdater:
            def update(self, client, application, **kwargs):
                return OtaUpdateResult(
                    serial="CP01-288485AA3E04",
                    previous_version="0.2.0",
                    target_partition="ota_1",
                    received_size=application.size,
                )

        provisioner = Provisioner(
            FakeRunner({}), verifier, self.history, ota_updater=FakeUpdater()
        )
        provisioner._open_serial = lambda _port: FakeConnection()
        events = []
        result = provisioner.run_ota(
            ProvisionRequest(
                package=ota_package, port="/dev/cu.runtime", mode=ProvisionMode.UPDATE,
                verify_timeout_s=60.0,
            ),
            lambda stage, message, progress: events.append((stage, message)),
        )

        call = verifier.calls[-1]
        self.assertEqual("verify_serial", call[0])
        self.assertGreaterEqual(call[2], 180.0)   # room for the operator
        self.assertEqual(15.0, verifier.replug_after_s)
        stages = [stage for stage, _message in events]
        self.assertIn("replug", stages)
        self.assertIn("拔下 USB", dict(events)["replug"])
        self.assertLess(stages.index("replug"), len(stages) - 1)
        timings = json.loads(result.receipt_path.read_text(encoding="utf-8"))["timings_s"]
        self.assertTrue(timings["replug_prompted"])

    def test_ota_timings_leave_unreached_stages_empty(self):
        from provisioner.workflow import ota_timings

        with mock.patch("provisioner.workflow.time.monotonic", return_value=20.0):
            timings = ota_timings({"start": 10.0, "transfer_started": 11.5, "reconnects": 2})

        self.assertEqual(timings["preflight_s"], 1.5)
        self.assertIsNone(timings["transfer_and_finalize_s"])
        self.assertIsNone(timings["reboot_reconnect_readback_s"])
        self.assertEqual(timings["total_s"], 10.0)
        self.assertEqual(timings["serial_reconnects"], 2)

    def test_power_v2_runtime_update_reconnects_same_serial_and_resumes(self):
        ota_package = ReleasePackage.open(make_ota_release(Path(self.temp.name)))
        self.addCleanup(ota_package.close)
        verifier = FakeVerifier()

        class FakeConnection:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        class InterruptedUpdater:
            def __init__(self):
                self.calls = []

            def update(self, _client, application, **kwargs):
                self.calls.append(kwargs)
                if len(self.calls) == 1:
                    raise OtaTransportInterrupted(
                        "cable disconnected",
                        serial="CP01-288485AA3E04",
                        target_partition="ota_1",
                        finalization_uncertain=False,
                    )
                return OtaUpdateResult(
                    serial="CP01-288485AA3E04",
                    previous_version="0.2.0",
                    target_partition="ota_1",
                    received_size=application.size,
                )

        updater = InterruptedUpdater()
        provisioner = Provisioner(
            FakeRunner({}), verifier, self.history, ota_updater=updater
        )
        opened_ports = []

        def open_serial(port):
            opened_ports.append(port)
            return FakeConnection()

        provisioner._open_serial = open_serial
        request = ProvisionRequest(
            package=ota_package,
            port="/dev/cu.runtime",
            mode=ProvisionMode.UPDATE,
        )

        provisioner.run_ota(request)

        self.assertEqual(
            ["/dev/cu.runtime", "/dev/cu.reconnected"],
            opened_ports,
        )
        self.assertEqual(
            "CP01-288485AA3E04",
            updater.calls[1]["expected_serial"],
        )
        self.assertEqual("ota_1", updater.calls[1]["expected_target_partition"])
        self.assertEqual("find_serial_port", verifier.calls[-2][0])

    def test_ota_reopens_transiently_unavailable_port_only_after_identity_is_known(self):
        ota_package = ReleasePackage.open(make_ota_release(Path(self.temp.name)))
        self.addCleanup(ota_package.close)
        verifier = FakeVerifier()

        class FakeConnection:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        class FakeUpdater:
            def __init__(self):
                self.calls = []

            def update(self, _client, application, **kwargs):
                self.calls.append(kwargs)
                return OtaUpdateResult(
                    serial="CP01-288485AA3E04", previous_version="0.2.0",
                    target_partition="ota_1", received_size=application.size,
                )

        updater = FakeUpdater()
        provisioner = Provisioner(FakeRunner({}), verifier, self.history, ota_updater=updater)
        opened = []

        def open_serial(port):
            opened.append(port)
            if len(opened) == 1:
                raise OSError("temporary COM port unavailable")
            return FakeConnection()

        provisioner._open_serial = open_serial
        request = ProvisionRequest(package=ota_package, port="COM7", mode=ProvisionMode.UPDATE)
        result = provisioner.run_ota(request, expected_serial="CP01-288485AA3E04")

        self.assertEqual(["COM7", "/dev/cu.reconnected"], opened)
        self.assertEqual(updater.calls[0]["expected_serial"], "CP01-288485AA3E04")
        self.assertEqual(result.runtime.serial, "CP01-288485AA3E04")
        self.assertIn("find_serial_port", [call[0] for call in verifier.calls])

    def test_ota_does_not_retry_initial_port_open_without_pinned_identity(self):
        ota_package = ReleasePackage.open(make_ota_release(Path(self.temp.name)))
        self.addCleanup(ota_package.close)
        verifier = FakeVerifier()
        provisioner = Provisioner(FakeRunner({}), verifier, self.history)
        provisioner._open_serial = mock.Mock(side_effect=OSError("COM port busy"))
        request = ProvisionRequest(package=ota_package, port="COM7", mode=ProvisionMode.UPDATE)

        with self.assertRaisesRegex(OSError, "COM port busy"):
            provisioner.run_ota(request)

        self.assertNotIn("find_serial_port", [call[0] for call in verifier.calls])


if __name__ == "__main__":
    unittest.main()
