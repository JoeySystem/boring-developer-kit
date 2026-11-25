from __future__ import annotations

import json
import sys
import os
import subprocess
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from provisioner.devices import DeviceKind
from provisioner.authority_update_server import (
    AuthorityUpdateState,
    _describe_serial_failure,
    _serial_port_holders,
    create_authority_update_server,
)
from provisioner.errors import ProvisioningError, TransportError, VerificationError
from provisioner.models import RuntimeVerification
from tests.helpers import make_ota_release


_DATA_ROOT = None
_DATA_PATCH = None


def setUpModule():
    # Repairs now write a session record on every pass or failure; keep test
    # runs out of the operator's real history folder.
    global _DATA_ROOT, _DATA_PATCH
    _DATA_ROOT = tempfile.TemporaryDirectory()
    _DATA_PATCH = mock.patch.dict(
        os.environ, {"CODEX_MICRO_PROVISIONER_DATA": _DATA_ROOT.name}
    )
    _DATA_PATCH.start()


def tearDownModule():
    _DATA_PATCH.stop()
    _DATA_ROOT.cleanup()


def port(device: str, *, vid: int, pid: int, product: str = ""):
    return SimpleNamespace(
        device=device,
        vid=vid,
        pid=pid,
        product=product,
        description=product,
        manufacturer="Espressif",
        hwid="",
        serial_number=None,
        location=None,
        interface=None,
    )


class AuthorityUpdateStateTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.release = make_ota_release(Path(self.temporary.name))
        self.state = AuthorityUpdateState()

    def tearDown(self):
        self.state.shutdown()
        self.temporary.cleanup()

    def authority_patch(self):
        return mock.patch(
            "provisioner.authority_update_server.discover_production_authority_path",
            return_value=self.release,
        )

    def busy_worker_fixture(self):
        job_id = "preflight-busy-job"
        self.state.jobs[job_id] = {
            "id": job_id, "status": "queued", "progress": 0, "stage": "queued",
            "message": "", "events": [], "event_base": 0,
            "created_at": "2026-09-18T00:00:00+00:00",
        }
        self.state.busy_job = job_id
        package = mock.MagicMock()
        package.__enter__.return_value = package
        package.__exit__.return_value = False
        device = SimpleNamespace(device="COM7", kind=DeviceKind.RUNTIME)
        runtime = RuntimeVerification(
            serial="CP01-001122334455", port="COM7", build_id="target-build",
            product="Codex Micro", vid="303A", pid="8360",
            product_id="wired-macro-pad-v1",
            hardware_id="WMP-S3-MATRIX12-POWER-V2",
            config_generation=4, running_partition="ota_1", rollback_pending=False,
        )
        result = SimpleNamespace(runtime=runtime, receipt_path=Path(self.temporary.name) / "receipt.json")
        return job_id, package, device, result

    def test_scan_accepts_exactly_one_runtime_device(self):
        runtime = port(
            "/dev/cu.runtime",
            vid=0x303A,
            pid=0x8360,
            product="Codex Micro",
        )
        with self.authority_patch(), mock.patch(
            "provisioner.devices.load_ports", return_value=[runtime]
        ):
            result = self.state.scan()

        self.assertEqual(result["readiness"], "ready")
        self.assertEqual(result["devices"][0]["kind"], "runtime")
        self.assertTrue(result["authority"]["ota_supported"])

    def test_scan_rejects_boot_mode_and_multiple_devices(self):
        boot = port("/dev/cu.boot", vid=0x303A, pid=0x1001)
        runtime_a = port("/dev/cu.a", vid=0x303A, pid=0x8360)
        runtime_b = port("/dev/cu.b", vid=0x303A, pid=0x8360)
        with self.authority_patch(), mock.patch(
            "provisioner.devices.load_ports", return_value=[boot]
        ):
            boot_result = self.state.scan()
        with self.authority_patch(), mock.patch(
            "provisioner.devices.load_ports", return_value=[runtime_a, runtime_b]
        ):
            multiple_result = self.state.scan()

        self.assertEqual(boot_result["readiness"], "blocked")
        self.assertIn("没有正常启动", boot_result["message"])
        self.assertNotIn("BOOT", boot_result["message"])
        self.assertEqual(multiple_result["readiness"], "blocked")
        self.assertIn("只能连接 1 台", multiple_result["message"])

    def test_scan_reports_the_program_holding_the_runtime_port(self):
        runtime = port("/dev/cu.runtime", vid=0x303A, pid=0x8360)
        with self.authority_patch(), mock.patch(
            "provisioner.devices.load_ports", return_value=[runtime]
        ), mock.patch(
            "provisioner.authority_update_server._serial_port_holders",
            return_value=[{"pid": 123, "command": "BORING Console"}],
        ):
            result = self.state.scan()

        self.assertEqual(result["readiness"], "blocked")
        self.assertEqual(result["block_reason"], "port-busy")
        self.assertIn("BORING Console", result["message"])
        self.assertIn("自动允许重试", result["message"])

    @mock.patch("provisioner.authority_update_server.sys.platform", "darwin")
    @mock.patch("provisioner.authority_update_server.subprocess.run")
    def test_serial_port_holders_reads_lsof_process_names(self, run):
        run.return_value = SimpleNamespace(
            returncode=0,
            stdout="p123\ncBORING Console\np456\ncPython\n",
        )

        holders = _serial_port_holders("/dev/cu.runtime")

        self.assertEqual(
            holders,
            [
                {"pid": 123, "command": "BORING Console"},
                {"pid": 456, "command": "Python"},
            ],
        )
        run.assert_called_once_with(
            ["lsof", "-Fpc", "--", "/dev/cu.runtime"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )

    def test_resource_busy_failure_is_actionable_and_retryable(self):
        failure = _describe_serial_failure(
            "/dev/cu.runtime",
            "[Errno 16] could not open port: Resource busy",
            holders=[{"pid": 123, "command": "BORING Console"}],
        )

        self.assertTrue(failure["retryable"])
        self.assertIn("串口被 BORING Console（PID 123）占用", failure["message"])
        self.assertNotIn("Resource busy", failure["message"])

    @mock.patch("provisioner.authority_update_server.sys.platform", "win32")
    def test_windows_access_denied_can_retry_without_lsof(self):
        with mock.patch("provisioner.authority_update_server.subprocess.run") as run:
            self.assertEqual([], _serial_port_holders("COM12"))
            result = _describe_serial_failure(
                "COM12",
                "could not open port 'COM12': PermissionError(13, 'Access is denied.', None, 5)",
            )
        run.assert_not_called()
        self.assertTrue(result["retryable"])
        self.assertIn("COM12", result["message"])
        self.assertIn("BORING Console", result["message"])
        self.assertIn("重试", result["message"])

    @mock.patch("provisioner.authority_update_server.sys.platform", "win32")
    def test_windows_missing_device_is_not_reported_as_access_denied(self):
        result = _describe_serial_failure("COM12", "FileNotFoundError(2, '找不到指定的文件。')")
        self.assertFalse(result["retryable"])
        self.assertIn("FileNotFoundError", result["message"])

    def test_start_forces_runtime_update_and_validation_record(self):
        runtime = port("/dev/cu.runtime", vid=0x303A, pid=0x8360)
        with self.authority_patch(), mock.patch(
            "provisioner.devices.load_ports", return_value=[runtime]
        ), mock.patch(
            "provisioner.authority_update_server.load_ports", return_value=[runtime]
        ), mock.patch.object(self.state, "_run_job"):
            job_id = self.state.start_authority_update("/dev/cu.runtime")

        job = self.state.jobs[job_id]
        self.assertEqual(job["release_id"], "matrix12-power-v2-test")
        self.assertEqual(job["ports"], ["/dev/cu.runtime"])
        self.assertEqual(job["mode"], "update")
        self.assertEqual(job["record_kind"], "validation")

    def test_repair_start_pins_serial_and_always_creates_ota_job(self):
        inspection = {"identity": {"serial": "CP01-001122334455"}}
        with mock.patch.object(
            self.state, "inspect_runtime", return_value=inspection,
        ), mock.patch.object(
            self.state, "start_authority_update", return_value="ota-job",
        ) as start_update:
            result = self.state.start_repair("/dev/cu.runtime")

        self.assertEqual(result["phase"], "ota")
        self.assertEqual(result["serial"], "CP01-001122334455")
        self.assertEqual(result["job_id"], "ota-job")
        start_update.assert_called_once_with("/dev/cu.runtime")

    def mark_unverified(self):
        from tests.helpers import mark_release_unverified

        mark_release_unverified(self.release)

    def test_unverified_firmware_is_refused_by_a_normal_build(self):
        self.mark_unverified()
        with self.authority_patch(), self.assertRaisesRegex(
            ProvisioningError, "尚未通过样机验证"
        ):
            self.state.authority()

    def test_acceptance_test_build_accepts_and_labels_unverified_firmware(self):
        self.mark_unverified()
        with self.authority_patch(), mock.patch(
            "provisioner.authority_update_server.bundled_acceptance_test", return_value=True
        ), mock.patch.object(sys, "frozen", True, create=True):
            authority = self.state.authority()
        self.assertTrue(authority["acceptance_test"])
        self.assertEqual(authority["validation_state"], "built")

    def test_acceptance_flag_is_ignored_when_running_from_source(self):
        self.mark_unverified()
        with self.authority_patch(), mock.patch(
            "provisioner.authority_update_server.bundled_acceptance_test", return_value=True
        ), self.assertRaisesRegex(ProvisioningError, "尚未通过样机验证"):
            self.state.authority()

    def running_inspection(self, build_id, **update):
        status = {"state": "IDLE", "rollback_pending": False, "reboot_pending": False,
                  "running_partition": "ota_1", "previous_boot_stage": "main-loop",
                  "previous_reset_reason": 3, **update}
        return {
            "identity": {"serial": "CP01-001122334455", "build_id": build_id,
                         "hardware_id": "WMP-S3-MATRIX12-POWER-V2"},
            "raw_status": {"firmware_update": status},
        }

    def test_repair_skips_flashing_a_device_that_already_runs_the_build(self):
        inspection = self.running_inspection("20261025.01-gabc")
        with mock.patch.object(
            self.state, "inspect_runtime", return_value=inspection,
        ), mock.patch.object(
            self.state, "authority", return_value={"build_id": "20261025.01-gabc"},
        ), mock.patch.object(self.state, "start_authority_update") as start_update:
            repair = self.state.start_repair("COM5")

        start_update.assert_not_called()
        self.assertTrue(repair["ota_skipped"])
        job = self.state.job(repair["job_id"])
        self.assertEqual(job["status"], "passed")
        result = job["results"][0]
        self.assertTrue(result["skipped"])
        self.assertEqual(result["runtime_port"], "COM5")
        self.assertEqual(result["runtime"]["serial"], "CP01-001122334455")
        self.assertIn("跳过烧录", job["message"])

    def test_repair_still_flashes_unconfirmed_or_different_firmware(self):
        for inspection in (
            self.running_inspection("20261016.02-g0c528304"),
            self.running_inspection("20261025.01-gabc", rollback_pending=True),
            self.running_inspection("20261025.01-gabc", state="READY_TO_REBOOT"),
        ):
            with self.subTest(inspection=inspection), mock.patch.object(
                self.state, "inspect_runtime", return_value=inspection,
            ), mock.patch.object(
                self.state, "authority", return_value={"build_id": "20261025.01-gabc"},
            ), mock.patch.object(
                self.state, "start_authority_update", return_value="ota-job",
            ) as start_update:
                self.state.repair = None
                repair = self.state.start_repair("COM5")
            start_update.assert_called_once_with("COM5")
            self.assertFalse(repair["ota_skipped"])

    def test_repair_record_keeps_firmware_before_and_after(self):
        from provisioner.authority_update_server import _firmware_snapshot

        snapshot = _firmware_snapshot(self.running_inspection("old-build"))
        self.assertEqual(snapshot["build_id"], "old-build")
        self.assertEqual(snapshot["previous_boot_stage"], "main-loop")
        self.assertEqual(snapshot["previous_reset_reason"], 3)
        self.assertIsNone(_firmware_snapshot(None))

    def test_stale_windows_device_error_is_explained_in_chinese(self):
        result = _describe_serial_failure(
            "COM5",
            "OTA 传输完成，但 180 秒内未读回目标 build_id：could not open port 'COM5': "
            "OSError(22, '指定不存在的设备。', None, 433)",
        )
        self.assertTrue(result["message"].startswith("电脑没有重新识别到设备（Windows 错误 433）"))
        self.assertIn("拔下 USB", result["message"])
        self.assertIn("原始信息", result["message"])

    def test_idle_failure_names_the_failed_check(self):
        from provisioner.authority_update_server import idle_failure_reasons

        sample = {"directions": 0, "inputs_neutral": False, "joystick_suspect": False,
                  "sample_count": 10, "adc_failure_count": 0}
        later = {**sample, "sample_count": 20}
        text = idle_failure_reasons([sample, later], 0)
        self.assertIn("等待回中", text)
        self.assertNotIn("方向输入", text)
        self.assertNotIn("采样没有更新", text)
        self.assertIn("采样没有更新", idle_failure_reasons([sample, sample], 0))

    def test_calibration_busy_is_explained_in_chinese(self):
        from provisioner.authority_update_server import explain_calibration_busy

        text = explain_calibration_busy({
            "pending": None,
            "action_engine": {"usb_standby_active": True, "local_page": "settings",
                              "host_output_state": "local_ui"},
            "calibration": {"state": "INACTIVE"},
            "firmware_update": {"state": "IDLE"},
        })
        self.assertIn("USB 关机待机", text)
        self.assertIn("本机页面（settings）", text)
        self.assertNotIn("未结束的校准", text)
        self.assertIn("重新开始", text)
        self.assertIn("输出状态为 未知", explain_calibration_busy(None))

    def test_calibration_start_retries_a_busy_device_then_explains(self):
        from provisioner.errors import VerificationError

        busy = VerificationError("设备拒绝请求：BUSY another configuration operation is active")
        client = mock.Mock()
        client.get_capabilities.return_value = {"features": {"joystick_calibration": True}}
        client.calibration_start.side_effect = busy
        client.get_status.return_value = {"action_engine": {"quick_config_active": True}}
        clock = [0.0]

        def sleep(seconds):
            clock[0] += seconds

        connection = mock.MagicMock()
        with mock.patch.object(self.state, "_validated_runtime_port", return_value="COM5"), \
                mock.patch.dict(sys.modules, {"serial": SimpleNamespace(
                    Serial=mock.Mock(return_value=connection))}), \
                mock.patch("provisioner.authority_update_server.WmpClient", return_value=client), \
                mock.patch("provisioner.authority_update_server.inspect_device",
                           return_value={"identity": {"config_generation": 3}}), \
                mock.patch("provisioner.authority_update_server.time.monotonic",
                           side_effect=lambda: clock[0]), \
                mock.patch("provisioner.authority_update_server.time.sleep", side_effect=sleep), \
                self.assertRaisesRegex(ProvisioningError, "快捷设置界面"):
            self.state.start_joystick_calibration("COM5", expected_serial="CP01-001122334455")
        self.assertGreater(client.calibration_start.call_count, 10)
        connection.close.assert_called()

    def slot_inspection(self, paired, *, serial="CP01-001122334455", bonds=0):
        return {
            "identity": {"serial": serial},
            "ble": {
                "bond_count": bonds,
                "slots": [{"slot": n, "paired": n in paired} for n in (1, 2, 3)],
            },
        }

    def test_clear_all_slots_clears_every_occupied_slot_and_verifies_empty(self):
        inspections = [self.slot_inspection({1, 3}, bonds=2), self.slot_inspection(set())]
        with mock.patch.object(
            self.state, "inspect_runtime", side_effect=inspections,
        ), mock.patch.object(
            self.state, "_clear_repair_slot",
            side_effect=lambda port, *, slot, expected_serial: {"receipt_path": f"slot-{slot}.json"},
        ) as clear:
            result = self.state._clear_all_repair_slots(
                "COM8", expected_serial="CP01-001122334455"
            )

        self.assertEqual([c.kwargs["slot"] for c in clear.call_args_list], [1, 3])
        for call in clear.call_args_list:
            self.assertEqual(call.kwargs["expected_serial"], "CP01-001122334455")
        self.assertEqual(result["cleared_slots"], [1, 3])
        self.assertEqual(result["receipts"], ["slot-1.json", "slot-3.json"])
        self.assertEqual(result["final_bond_count"], 0)

    def test_clear_all_slots_skips_when_already_empty(self):
        with mock.patch.object(
            self.state, "inspect_runtime", return_value=self.slot_inspection(set()),
        ) as inspect, mock.patch.object(self.state, "_clear_repair_slot") as clear:
            result = self.state._clear_all_repair_slots(
                "COM8", expected_serial="CP01-001122334455"
            )

        clear.assert_not_called()
        inspect.assert_called_once()
        self.assertEqual(result["cleared_slots"], [])

    def test_clear_all_slots_fails_if_any_slot_still_paired(self):
        inspections = [self.slot_inspection({2}), self.slot_inspection({2})]
        with mock.patch.object(
            self.state, "inspect_runtime", side_effect=inspections,
        ), mock.patch.object(
            self.state, "_clear_repair_slot", return_value={"receipt_path": "r.json"},
        ):
            with self.assertRaisesRegex(ProvisioningError, "槽位 2 仍报告已配对"):
                self.state._clear_all_repair_slots(
                    "COM8", expected_serial="CP01-001122334455"
                )

    def test_clear_all_slots_refuses_a_different_device(self):
        with mock.patch.object(
            self.state, "inspect_runtime",
            return_value=self.slot_inspection({1}, serial="CP01-AAAAAAAAAAAA"),
        ), mock.patch.object(self.state, "_clear_repair_slot") as clear:
            with self.assertRaisesRegex(ProvisioningError, "序列号发生变化"):
                self.state._clear_all_repair_slots(
                    "COM8", expected_serial="CP01-001122334455"
                )
        clear.assert_not_called()

    def test_failed_ota_stops_repair_before_clear_or_calibration(self):
        self.state.repair = {
            "id": "repair", "phase": "ota", "status": "running",
            "serial": "CP01-001122334455", "job_id": "job", "port": "COM7",
        }
        self.state.jobs["job"] = {
            "id": "job", "status": "failed", "message": "OTA failed", "results": [],
        }
        with mock.patch.object(self.state, "_clear_repair_slot") as clear, mock.patch.object(
            self.state, "_start_repair_calibration",
        ) as calibrate:
            result = self.state.advance_repair("job")

        self.assertEqual(result["repair"]["phase"], "failed")
        clear.assert_not_called()
        calibrate.assert_not_called()

    def test_repair_calibration_start_returns_reloadable_public_session(self):
        serial = "CP01-001122334455"

        def start_calibration(port, *, expected_serial):
            self.state.calibration = {
                "phase": "calibrating",
                "port": port,
                "serial": expected_serial,
                "session_id": 11,
                "base_generation": 4,
                "last_sample": {"session_id": 11, "state": "CENTERING"},
            }
            return {
                "serial": expected_serial,
                "base_generation": 4,
                "sample": {"session_id": 11, "state": "CENTERING"},
            }

        repair = {"id": "repair", "serial": serial, "phase": "post-ota"}
        with mock.patch.object(
            self.state, "start_joystick_calibration", side_effect=start_calibration,
        ):
            result = self.state._start_repair_calibration(repair, "COM8")

        self.assertEqual(result["calibration"]["phase"], "calibrating")
        self.assertEqual(result["calibration"]["port"], "COM8")
        self.assertEqual(result["calibration"]["session_id"], 11)
        self.assertEqual(result["calibration"]["sample"]["state"], "CENTERING")

    def test_successful_ota_clears_all_slots_then_starts_calibration(self):
        serial = "CP01-001122334455"
        self.state.repair = {
            "id": "repair", "phase": "ota", "status": "running",
            "serial": serial, "job_id": "job", "port": "COM7",
            "marks": {"started": 0.0},
        }
        self.state.jobs["job"] = {
            "id": "job", "status": "passed", "message": "ok",
            "results": [{
                "runtime_port": "COM8", "runtime": {"serial": serial},
                "receipt_path": "ota.json",
            }],
        }
        cleared = {"cleared_slots": [1, 3], "receipts": ["a", "b"], "final_bond_count": 0}
        voice = {"changed": True, "changes": ["Codex macOS：第 8 键「Space」改为「Voice/Send」"]}
        calls = []
        with mock.patch.object(
            self.state, "_clear_all_repair_slots", return_value=cleared,
            side_effect=lambda *a, **k: calls.append("slots") or cleared,
        ) as clear, mock.patch.object(
            self.state, "_apply_voice_key_default",
            side_effect=lambda *a, **k: calls.append("voice") or voice,
        ) as voice_key, mock.patch.object(
            self.state, "_start_repair_calibration", return_value={"repair": {}},
        ) as calibrate:
            self.state.advance_repair("job")

        clear.assert_called_once_with("COM8", expected_serial=serial)
        voice_key.assert_called_once_with("COM8", expected_serial=serial)
        self.assertEqual(calls, ["slots", "voice"])
        repair = calibrate.call_args.args[0]
        self.assertEqual(repair["voice_key"], voice)
        self.assertEqual(repair["step"], "calibration-start")
        self.assertEqual(repair["cleared_slots"], [1, 3])
        self.assertEqual(repair["clear_outcome"], "cleared-all")
        self.assertEqual(repair["clear_receipt_paths"], ["a", "b"])
        self.assertEqual(calibrate.call_args.args[1], "COM8")

    def test_slot_clear_failure_stops_before_calibration(self):
        serial = "CP01-001122334455"
        self.state.repair = {
            "id": "repair", "phase": "ota", "status": "running",
            "serial": serial, "job_id": "job", "port": "COM7",
        }
        self.state.jobs["job"] = {
            "id": "job", "status": "passed", "message": "ok",
            "results": [{"runtime_port": "COM8", "runtime": {"serial": serial}}],
        }
        with mock.patch.object(
            self.state, "_clear_all_repair_slots",
            side_effect=ProvisioningError("清空后槽位 2 仍报告已配对，请重新开始"),
        ), mock.patch.object(self.state, "_start_repair_calibration") as calibrate:
            with self.assertRaises(ProvisioningError):
                self.state.advance_repair("job")

        calibrate.assert_not_called()
        self.assertEqual(self.state.repair["phase"], "failed")

    def test_voice_key_failure_stops_before_calibration(self):
        serial = "CP01-001122334455"
        self.state.repair = {
            "id": "repair", "phase": "ota", "status": "running",
            "serial": serial, "job_id": "job", "port": "COM7",
        }
        self.state.jobs["job"] = {
            "id": "job", "status": "passed", "message": "ok",
            "results": [{"runtime_port": "COM8", "runtime": {"serial": serial}}],
        }
        cleared = {"cleared_slots": [], "receipts": [], "final_bond_count": 0}
        with mock.patch.object(
            self.state, "_clear_all_repair_slots", return_value=cleared,
        ), mock.patch.object(
            self.state, "_apply_voice_key_default",
            side_effect=VerificationError("第 8 键配置已写入但 10 秒内未生效，请松开所有按键和摇杆"),
        ), mock.patch.object(self.state, "_start_repair_calibration") as calibrate:
            with self.assertRaises(VerificationError):
                self.state.advance_repair("job")

        calibrate.assert_not_called()
        self.assertEqual(self.state.repair["phase"], "failed")
        self.assertEqual(self.state.repair["step"], "voice-key")

    def test_start_rechecks_port_and_rejects_changed_device(self):
        runtime = port("/dev/cu.new", vid=0x303A, pid=0x8360)
        with self.authority_patch(), mock.patch(
            "provisioner.devices.load_ports", return_value=[runtime]
        ):
            with self.assertRaisesRegex(ProvisioningError, "状态已变化"):
                self.state.start_authority_update("/dev/cu.old")

    def test_inspect_reads_power_v2_without_modifying_device(self):
        serial_port = mock.MagicMock()
        serial_class = mock.MagicMock()
        serial_class.return_value.__enter__.return_value = serial_port
        expected = {
            "identity": {"serial": "CP01-001122334455", "build_id": "build"},
            "ble": {"active_slot": 1, "bond_count": 2, "slots": []},
            "raw_status": {},
        }
        with mock.patch.object(
            self.state, "_validated_runtime_port", return_value="/dev/cu.runtime",
        ), mock.patch.dict(
            "sys.modules", {"serial": SimpleNamespace(Serial=serial_class)},
        ), mock.patch(
            "provisioner.authority_update_server.inspect_device", return_value=expected,
        ) as inspect:
            result = self.state.inspect_runtime("/dev/cu.runtime")

        self.assertEqual(result, expected)
        inspect.assert_called_once()

    def test_clear_slot_pins_serial_and_writes_receipt(self):
        serial_class = mock.MagicMock()
        serial_class.return_value.__enter__.return_value = mock.MagicMock()
        recovery = {
            "slot": 2,
            "serial": "CP01-001122334455",
            "command_result": {"slot": 2},
            "before": {"identity": {"serial": "CP01-001122334455"}},
            "after": {"identity": {"serial": "CP01-001122334455"}},
        }
        history = Path(self.temporary.name) / "history"
        with mock.patch.object(
            self.state, "_validated_runtime_port", return_value="/dev/cu.runtime",
        ), mock.patch.dict(
            "sys.modules", {"serial": SimpleNamespace(Serial=serial_class)},
        ), mock.patch(
            "provisioner.authority_update_server.clear_single_slot", return_value=recovery,
        ) as clear, mock.patch(
            "provisioner.authority_update_server.history_root", return_value=history,
        ):
            result = self.state.clear_runtime_slot(
                "/dev/cu.runtime",
                slot=2,
                expected_serial="CP01-001122334455",
                confirmation="清除槽位 2",
            )

        clear.assert_called_once()
        self.assertEqual(clear.call_args.kwargs["expected_serial"], "CP01-001122334455")
        receipt = json.loads(Path(result["receipt_path"]).read_text(encoding="utf-8"))
        self.assertEqual(receipt["mode"], "ble-slot-clear")
        self.assertEqual(receipt["slot"], 2)

    def test_lost_calibration_confirm_response_blocks_a_second_save(self):
        client = mock.MagicMock()
        client.calibration_confirm.side_effect = TransportError("回包中断")
        self.state.calibration = {
            "phase": "calibrating",
            "port": "/dev/cu.runtime",
            "serial": "CP01-001122334455",
            "session_id": 7,
            "base_generation": 3,
            "last_sample": {"ready_to_confirm": True},
        }
        self.state._calibration_client = client

        with mock.patch.object(
            self.state, "_validated_runtime_port", return_value="/dev/cu.runtime"
        ):
            with self.assertRaisesRegex(ProvisioningError, "只读对账"):
                self.state.confirm_joystick_calibration(
                    "/dev/cu.runtime",
                    expected_serial="CP01-001122334455",
                    session_id=7,
                )
            with self.assertRaisesRegex(ProvisioningError, "阶段"):
                self.state.confirm_joystick_calibration(
                    "/dev/cu.runtime",
                    expected_serial="CP01-001122334455",
                    session_id=7,
                )

        self.assertEqual(self.state.calibration["phase"], "readback-needed")
        client.calibration_confirm.assert_called_once_with(7, 3)

    def test_expired_calibration_session_releases_port_and_keeps_old_config(self):
        client = mock.MagicMock()
        client.calibration_sample.side_effect = VerificationError(
            "设备拒绝请求：NOT_FOUND calibration session is not active"
        )
        connection = mock.MagicMock()
        self.state.calibration = {
            "phase": "calibrating",
            "port": "/dev/cu.runtime",
            "serial": "CP01-001122334455",
            "session_id": 7,
            "base_generation": 3,
        }
        self.state._calibration_client = client
        self.state._calibration_connection = connection

        with mock.patch.object(
            self.state, "_validated_runtime_port", return_value="/dev/cu.runtime"
        ), self.assertRaisesRegex(ProvisioningError, "旧配置未改变"):
            self.state.sample_joystick_calibration(
                "/dev/cu.runtime",
                expected_serial="CP01-001122334455",
                session_id=7,
            )

        self.assertIsNone(self.state.calibration)
        connection.close.assert_called_once()

    def test_direction_acceptance_acquires_diagnostic_capture_before_prompting(self):
        client = mock.MagicMock()
        capture_active = False
        sample_count = 10

        def start_capture():
            nonlocal capture_active
            capture_active = True
            return {"active": True, "timeout_ms": 3000}

        def status():
            nonlocal sample_count
            sample_count += 1
            return {
                "active": {"generation": 4},
                "pending": None,
                "inputs_neutral": True,
                "joystick_suspect": False,
                "action_engine": {
                    "host_output_state": (
                        "diagnostic_capture" if capture_active else "enabled"
                    )
                },
                "diagnostic_capture": {"active": capture_active},
                "joystick_diagnostics": {
                    "center_x": 2050,
                    "deadzone_x": 180,
                    "center_y": 2040,
                    "deadzone_y": 180,
                    "directions": 0,
                    "raw_x": 2050,
                    "raw_y": 2040,
                    "sample_count": sample_count,
                    "adc_failure_count": 0,
                },
            }

        client.get_config.return_value = {
            "generation": 4,
            "config": {
                "joystick": {
                    "calibrated": True,
                    "filter": "ema",
                    "x": {"center": 2050, "deadzone": 180, "invert": False},
                    "y": {"center": 2040, "deadzone": 180, "invert": False},
                }
            },
        }
        client.get_status.side_effect = status
        client.diagnostic_start.side_effect = start_capture
        session = {
            "phase": "confirming",
            "port": "/dev/cu.runtime",
            "serial": "CP01-001122334455",
            "session_id": 7,
            "base_generation": 3,
            "before": {
                "raw_config": {
                    "config": {
                        "joystick": {
                            "filter": "ema",
                            "x": {"invert": False},
                            "y": {"invert": False},
                        }
                    }
                },
                "raw_status": {
                    "joystick_diagnostics": {"adc_failure_count": 0}
                },
            },
        }
        history = Path(self.temporary.name) / "history"

        with mock.patch(
            "provisioner.authority_update_server.history_root", return_value=history
        ), mock.patch("provisioner.authority_update_server.time.sleep"):
            result = self.state._complete_calibration_activation(session, client, 4)

        client.diagnostic_start.assert_called_once_with()
        self.assertTrue(result["diagnostic_capture_active"])
        self.assertTrue(self.state.calibration["diagnostic_capture_active"])

    def _idle_session_and_client(self, directions_by_read):
        """Client whose GET_STATUS reports the given directions, then neutral."""
        client = mock.MagicMock()
        reads = iter(directions_by_read)
        sample_count = 10

        def status():
            nonlocal sample_count
            sample_count += 1
            directions = next(reads, 0)
            return {
                "active": {"generation": 4},
                "pending": None,
                "inputs_neutral": directions == 0,
                "joystick_suspect": False,
                "action_engine": {"host_output_state": "diagnostic_capture"},
                "diagnostic_capture": {"active": True},
                "joystick_diagnostics": {
                    "center_x": 2050,
                    "deadzone_x": 180,
                    "center_y": 2040,
                    "deadzone_y": 180,
                    "directions": directions,
                    "raw_x": 0 if directions else 2050,
                    "raw_y": 2040,
                    "sample_count": sample_count,
                    "adc_failure_count": 0,
                },
            }

        client.get_config.return_value = {
            "generation": 4,
            "config": {
                "joystick": {
                    "calibrated": True,
                    "filter": "ema",
                    "x": {"center": 2050, "deadzone": 180, "invert": False},
                    "y": {"center": 2040, "deadzone": 180, "invert": False},
                }
            },
        }
        client.get_status.side_effect = status
        session = {
            "phase": "confirming",
            "port": "/dev/cu.runtime",
            "serial": "CP01-001122334455",
            "session_id": 7,
            "base_generation": 3,
            "before": {
                "raw_config": {
                    "config": {
                        "joystick": {
                            "filter": "ema",
                            "x": {"invert": False},
                            "y": {"invert": False},
                        }
                    }
                },
                "raw_status": {
                    "joystick_diagnostics": {"adc_failure_count": 0}
                },
            },
        }
        return session, client

    def test_idle_acceptance_waits_for_the_hand_to_leave_the_stick(self):
        # Readback, then the final calibration push still held for 3 reads.
        session, client = self._idle_session_and_client([0, 2, 2, 2])
        history = Path(self.temporary.name) / "history"

        with mock.patch(
            "provisioner.authority_update_server.history_root", return_value=history
        ), mock.patch("provisioner.authority_update_server.time.sleep"):
            result = self.state._complete_calibration_activation(session, client, 4)

        self.assertTrue(result["idle_neutral_passed"])
        self.assertTrue(
            all(item["directions"] == 0 for item in result["idle_neutral_samples"])
        )

    def test_idle_acceptance_still_fails_when_the_stick_never_settles(self):
        session, client = self._idle_session_and_client([0] + [2] * 1000)
        history = Path(self.temporary.name) / "history"
        clock = iter(range(0, 10000, 1))

        with mock.patch(
            "provisioner.authority_update_server.history_root", return_value=history
        ), mock.patch("provisioner.authority_update_server.time.sleep"), mock.patch(
            "provisioner.authority_update_server.time.monotonic",
            side_effect=lambda: next(clock),
        ):
            result = self.state._complete_calibration_activation(session, client, 4)

        self.assertFalse(result["idle_neutral_passed"])

    def test_four_directions_do_not_hide_failed_idle_acceptance(self):
        client = mock.MagicMock()
        client.get_status.return_value = {
            "action_engine": {"host_output_state": "diagnostic_capture"},
            "diagnostic_capture": {"active": True},
            "joystick_diagnostics": {"directions": 0},
        }
        connection = mock.MagicMock()
        self.state._calibration_client = client
        self.state._calibration_connection = connection
        self.state.repair = {
            "id": "repair", "phase": "verifying-joystick", "status": "running",
            "serial": "CP01-001122334455", "progress": 82,
        }
        self.state.calibration = {
            "phase": "direction-verification",
            "port": "/dev/cu.runtime",
            "serial": "CP01-001122334455",
            "session_id": 7,
            "base_generation": 3,
            "before": {},
            "idle_neutral_passed": False,
            "diagnostic_capture_active": True,
            "direction_verification": {
                "index": 3,
                "awaiting_neutral": True,
                "completed": ["up", "down", "left"],
                "complete": False,
            },
        }
        history = Path(self.temporary.name) / "history"

        with mock.patch.object(
            self.state, "_validated_runtime_port", return_value="/dev/cu.runtime"
        ), mock.patch(
            "provisioner.authority_update_server.history_root", return_value=history
        ):
            result = self.state.sample_direction_verification(
                "/dev/cu.runtime",
                expected_serial="CP01-001122334455",
                session_id=7,
            )

        self.assertTrue(result["direction_verification"]["complete"])
        self.assertFalse(result["success"])
        self.assertEqual(self.state.repair["phase"], "failed")
        self.assertIn("静止验收未通过", self.state.repair["message"])
        receipt = json.loads(Path(result["receipt_path"]).read_text(encoding="utf-8"))
        self.assertFalse(receipt["success"])
        client.diagnostic_start.assert_called_once_with()
        client.diagnostic_stop.assert_called_once_with()
        connection.close.assert_called_once()

    def test_repair_completes_only_after_idle_and_four_direction_acceptance(self):
        client = mock.MagicMock()
        client.get_status.return_value = {
            "action_engine": {"host_output_state": "diagnostic_capture"},
            "diagnostic_capture": {"active": True},
            "joystick_diagnostics": {"directions": 0},
        }
        connection = mock.MagicMock()
        self.state._calibration_client = client
        self.state._calibration_connection = connection
        self.state.repair = {
            "id": "repair", "phase": "verifying-joystick", "status": "running",
            "serial": "CP01-001122334455", "progress": 82,
        }
        self.state.calibration = {
            "phase": "direction-verification",
            "port": "/dev/cu.runtime",
            "serial": "CP01-001122334455",
            "session_id": 7,
            "base_generation": 3,
            "before": {},
            "idle_neutral_passed": True,
            "diagnostic_capture_active": True,
            "direction_verification": {
                "index": 3,
                "awaiting_neutral": True,
                "completed": ["up", "down", "left"],
                "complete": False,
            },
        }
        history = Path(self.temporary.name) / "history"

        with mock.patch.object(
            self.state, "_validated_runtime_port", return_value="/dev/cu.runtime"
        ), mock.patch(
            "provisioner.authority_update_server.history_root", return_value=history
        ), mock.patch(
            "provisioner.authority_update_server.sys.platform", "darwin"
        ), mock.patch.object(self.state, "_confirm_voice_key_final"):
            result = self.state.sample_direction_verification(
                "/dev/cu.runtime",
                expected_serial="CP01-001122334455",
                session_id=7,
            )

        self.assertTrue(result["success"])
        self.assertEqual(self.state.repair["phase"], "completed")
        self.assertEqual(self.state.repair["status"], "passed")
        self.assertEqual(self.state.repair["progress"], 100)
        self.assertIn("拔下 USB 即可交付", self.state.repair["message"])
        self.assertNotIn("重新配对", self.state.repair["message"])
        client.diagnostic_start.assert_called_once_with()
        client.diagnostic_stop.assert_called_once_with()

    def test_readback_reconciliation_can_close_an_unsaved_unknown_result(self):
        client = mock.MagicMock()
        client.get_config.return_value = {"generation": 3, "config": {}}
        client.get_status.return_value = {
            "active": {"generation": 3},
            "pending": None,
            "calibration": {"state": "INACTIVE"},
        }
        serial_class = mock.MagicMock(return_value=mock.MagicMock())
        self.state.calibration = {
            "phase": "readback-needed",
            "port": "/dev/cu.runtime",
            "serial": "CP01-001122334455",
            "session_id": 7,
            "base_generation": 3,
            "before": {},
        }

        with mock.patch.object(
            self.state, "_validated_runtime_port", return_value="/dev/cu.runtime"
        ), mock.patch.object(
            self.state, "_verify_calibration_device"
        ), mock.patch.object(
            self.state,
            "_complete_calibration_activation",
            side_effect=VerificationError("未激活"),
        ), mock.patch(
            "provisioner.authority_update_server.WmpClient", return_value=client
        ), mock.patch.dict(
            "sys.modules", {"serial": SimpleNamespace(Serial=serial_class)}
        ):
            result = self.state.reconcile_joystick_calibration(
                "/dev/cu.runtime",
                expected_serial="CP01-001122334455",
                session_id=7,
            )

        self.assertEqual(result["outcome"], "not-saved")
        self.assertIsNone(self.state.calibration)
        client.calibration_cancel.assert_not_called()

    def test_public_calibration_state_survives_page_reload_without_raw_config(self):
        self.state.calibration = {
            "phase": "direction-verification",
            "port": "/dev/cu.runtime",
            "serial": "CP01-001122334455",
            "session_id": 7,
            "base_generation": 3,
            "activation": {
                "generation": 4,
                "calibrated": True,
                "x": {"center": 2048, "deadzone": 180},
                "y": {"center": 2048, "deadzone": 180},
                "raw_config": {"config": {"prompts": ["private"]}},
            },
            "idle_neutral_passed": True,
            "direction_verification": {"complete": False},
        }

        with self.state.lock:
            result = self.state._public_calibration_locked()

        self.assertEqual(result["session_id"], 7)
        self.assertEqual(result["activation"]["generation"], 4)
        self.assertNotIn("raw_config", result["activation"])

    def test_runtime_resource_busy_stays_retryable_and_does_not_show_100_percent(self):
        job_id = "busy-job"
        self.state.jobs[job_id] = {
            "id": job_id,
            "status": "running",
            "progress": 5,
            "stage": "ota",
            "message": "正在核对设备",
            "events": [],
            "event_base": 0,
            "created_at": "2026-09-18T00:00:00+00:00",
        }
        self.state.busy_job = job_id
        package = mock.MagicMock()
        package.__enter__.return_value = package
        package.__exit__.return_value = False
        device = SimpleNamespace(device="/dev/cu.runtime")
        failed = SimpleNamespace(
            success=False,
            error="[Errno 16] could not open port: Resource busy",
            action_required=False,
        )
        with mock.patch(
            "provisioner.authority_update_server.SequentialBatch.run",
            return_value=[failed],
        ), mock.patch(
            "provisioner.authority_update_server._read_target_serial",
            return_value="CP01-001122334455",
        ), mock.patch(
            "provisioner.authority_update_server._serial_port_holders",
            return_value=[{"pid": 123, "command": "BORING Console"}],
        ):
            self.state._run_job(job_id, package, device)

        job = self.state.jobs[job_id]
        self.assertEqual(job["status"], "retryable")
        self.assertEqual(job["stage"], "port-busy")
        self.assertLess(job["progress"], 100)
        self.assertIn("BORING Console", job["message"])

    def test_worker_pins_preflight_serial_before_ota(self):
        job_id = "pinned-job"
        self.state.jobs[job_id] = {
            "id": job_id, "status": "queued", "progress": 0, "stage": "queued",
            "message": "", "events": [], "event_base": 0,
            "created_at": "2026-09-18T00:00:00+00:00",
        }
        self.state.busy_job = job_id
        package = mock.MagicMock()
        package.__enter__.return_value = package
        package.__exit__.return_value = False
        package.ota_application.build_id = "target-build"
        device = SimpleNamespace(device="COM7")
        preflight_serial = "CP01-001122334455"
        success = SimpleNamespace(
            success=True,
            value=SimpleNamespace(
                runtime=RuntimeVerification(
                    serial=preflight_serial, port="COM7", build_id="target-build",
                    product="Codex Micro", vid="303A", pid="8360",
                    product_id="wired-macro-pad-v1",
                    hardware_id="WMP-S3-MATRIX12-POWER-V2",
                    config_generation=4,
                    running_partition="ota_1", rollback_pending=False,
                ),
                receipt_path=Path(self.temporary.name) / "receipt.json",
            ),
        )
        with mock.patch(
            "provisioner.authority_update_server._read_target_serial",
            return_value=preflight_serial,
        ), mock.patch(
            "provisioner.authority_update_server.Provisioner.run_ota",
            return_value=success.value,
        ) as run_ota:
            self.state._run_job(job_id, package, device)

        self.assertEqual(self.state.jobs[job_id]["status"], "passed")
        self.assertEqual(run_ota.call_args.kwargs["expected_serial"], preflight_serial)
        self.assertEqual(run_ota.call_args.args[0].verify_timeout_s, 60.0)

    @mock.patch("provisioner.authority_update_server.sys.platform", "win32")
    def test_preflight_busy_waits_then_updates_only_pinned_device(self):
        job_id, package, device, result = self.busy_worker_fixture()
        denied = OSError("could not open COM7: PermissionError(13, 'Access is denied.', None, 5)")
        with mock.patch(
            "provisioner.authority_update_server._read_target_serial",
            side_effect=[denied, result.runtime.serial],
        ) as read_serial, mock.patch(
            "provisioner.authority_update_server.detect_devices", return_value=[device],
        ) as detect, mock.patch(
            "provisioner.authority_update_server.Provisioner.run_ota", return_value=result,
        ) as run_ota, mock.patch("provisioner.authority_update_server.time.sleep"):
            self.state._run_job(job_id, package, device)

        self.assertEqual(self.state.jobs[job_id]["status"], "passed")
        self.assertEqual(read_serial.call_count, 2)
        detect.assert_called_once_with(package.runtime_usb)
        self.assertEqual(run_ota.call_count, 1)
        self.assertEqual(run_ota.call_args.kwargs["expected_serial"], result.runtime.serial)
        self.assertTrue(any("等待原设备释放" in event["message"] for event in self.state.jobs[job_id]["events"]))

    @mock.patch("provisioner.authority_update_server.sys.platform", "win32")
    def test_preflight_busy_stops_if_selected_device_disappears(self):
        job_id, package, device, _ = self.busy_worker_fixture()
        denied = OSError("could not open COM7: PermissionError(13, 'Access is denied.', None, 5)")
        with mock.patch(
            "provisioner.authority_update_server._read_target_serial", side_effect=denied,
        ) as read_serial, mock.patch(
            "provisioner.authority_update_server.detect_devices", return_value=[],
        ), mock.patch(
            "provisioner.authority_update_server.Provisioner.run_ota",
        ) as run_ota, mock.patch("provisioner.authority_update_server.time.sleep"):
            self.state._run_job(job_id, package, device)

        self.assertEqual(self.state.jobs[job_id]["status"], "failed")
        self.assertIn("设备状态已变化", self.state.jobs[job_id]["message"])
        read_serial.assert_called_once()
        run_ota.assert_not_called()

    def test_preflight_rejects_replaced_board_on_same_port_before_ota(self):
        job_id, package, device, result = self.busy_worker_fixture()
        device.serial_number = "CP01-AAAAAAAAAAAA"
        with mock.patch(
            "provisioner.authority_update_server._read_target_serial",
            return_value=result.runtime.serial,
        ), mock.patch(
            "provisioner.authority_update_server.Provisioner.run_ota",
        ) as run_ota:
            self.state._run_job(job_id, package, device)

        self.assertEqual(self.state.jobs[job_id]["status"], "failed")
        self.assertIn("序列号已变化", self.state.jobs[job_id]["message"])
        run_ota.assert_not_called()

    @mock.patch("provisioner.authority_update_server.sys.platform", "win32")
    def test_preflight_busy_timeout_stays_retryable_without_ota(self):
        job_id, package, device, _ = self.busy_worker_fixture()
        denied = OSError("could not open COM7: PermissionError(13, 'Access is denied.', None, 5)")
        with mock.patch(
            "provisioner.authority_update_server._read_target_serial", side_effect=denied,
        ), mock.patch(
            "provisioner.authority_update_server.PREFLIGHT_BUSY_TIMEOUT_S", 0.5,
        ), mock.patch(
            "provisioner.authority_update_server.time.monotonic", side_effect=[0, 1],
        ), mock.patch(
            "provisioner.authority_update_server.Provisioner.run_ota",
        ) as run_ota:
            self.state._run_job(job_id, package, device)

        self.assertEqual(self.state.jobs[job_id]["status"], "retryable")
        self.assertIn("超过 0.5 秒", self.state.jobs[job_id]["message"])
        run_ota.assert_not_called()


class WindowsReadinessTest(unittest.TestCase):
    """Behaviour needed for the internal Windows build."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.release = make_ota_release(Path(self.temporary.name))
        self.state = AuthorityUpdateState()
        self.patch = mock.patch(
            "provisioner.authority_update_server.discover_production_authority_path",
            return_value=self.release,
        )
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.state.shutdown()
        self.temporary.cleanup()

    def test_scan_ignores_unrelated_usb_serial_adapters(self):
        runtime = port("COM5", vid=0x303A, pid=0x8360, product="Codex Micro")
        ch340 = port("COM3", vid=0x1A86, pid=0x7523, product="USB-SERIAL CH340")
        cp210x = port("COM4", vid=0x10C4, pid=0xEA60, product="CP2102 USB to UART")
        generic = port("COM6", vid=0x2341, pid=0x0043, product="USB Serial Device")
        with mock.patch(
            "provisioner.devices.load_ports", return_value=[ch340, runtime, cp210x, generic]
        ), mock.patch(
            "provisioner.authority_update_server._serial_port_holders", return_value=[]
        ):
            result = self.state.scan()

        self.assertEqual(result["readiness"], "ready")
        self.assertEqual([item["device"] for item in result["devices"]], ["COM5"])

    def test_scan_still_reports_esp_rom_download_mode(self):
        boot = port("COM9", vid=0x303A, pid=0x1001)
        ch340 = port("COM3", vid=0x1A86, pid=0x7523, product="USB-SERIAL CH340")
        with mock.patch("provisioner.devices.load_ports", return_value=[boot, ch340]):
            result = self.state.scan()

        self.assertEqual(result["block_reason"], "boot-mode")

    def test_package_is_validated_once_and_reused_by_scans(self):
        from provisioner import authority_update_server as module

        with mock.patch(
            "provisioner.devices.load_ports", return_value=[]
        ), mock.patch.object(
            module.ReleasePackage, "open", wraps=module.ReleasePackage.open
        ) as opened:
            for _ in range(5):
                self.state.scan()
            self.state.bootstrap()

        self.assertEqual(opened.call_count, 1)

    def test_changed_package_is_rejected_and_cache_invalidated(self):
        runtime = port("COM5", vid=0x303A, pid=0x8360, product="Codex Micro")
        with mock.patch("provisioner.devices.load_ports", return_value=[runtime]), mock.patch(
            "provisioner.authority_update_server._serial_port_holders", return_value=[]
        ), mock.patch(
            "provisioner.authority_update_server.package_fingerprint", return_value="changed"
        ):
            self.state.scan()
            with self.assertRaisesRegex(ProvisioningError, "已变化"):
                self.state.start_authority_update("COM5")

        self.assertIsNone(self.state._authority_cache)
        self.assertIsNone(self.state.busy_job)

    def test_completion_message_never_asks_for_pairing(self):
        from provisioner.authority_update_server import COMPLETION_MESSAGE

        self.assertIn("蓝牙槽位已全部清空", COMPLETION_MESSAGE)
        self.assertIn("请勿用本机测试蓝牙连接", COMPLETION_MESSAGE)
        self.assertNotIn("重新配对", COMPLETION_MESSAGE)

    def test_screen_orientation_combines_board_and_user_inversion(self):
        from provisioner.authority_update_server import screen_orientation

        def before(invert_x, invert_y):
            return {"raw_config": {"config": {"joystick": {
                "x": {"invert": invert_x}, "y": {"invert": invert_y},
            }}}}

        self.assertEqual(screen_orientation(before(False, False)), {"flip_x": True, "flip_y": True})
        self.assertEqual(screen_orientation(before(True, False)), {"flip_x": False, "flip_y": True})
        self.assertEqual(screen_orientation(None), {"flip_x": True, "flip_y": True})

    def test_bootstrap_reports_platform(self):
        with mock.patch("provisioner.devices.load_ports", return_value=[]), mock.patch(
            "provisioner.authority_update_server.sys.platform", "win32"
        ):
            self.assertEqual(self.state.bootstrap()["platform"], "windows")

    def test_reset_repair_allows_next_device_only_after_terminal_phase(self):
        self.state.repair = {"id": "r", "phase": "calibrating", "serial": "CP01-AAAAAAAAAAAA"}
        with self.assertRaisesRegex(ProvisioningError, "上一台还在进行中"):
            self.state.reset_repair()

        for phase in ("completed", "failed"):
            self.state.repair = {"id": "r", "phase": phase, "serial": "CP01-AAAAAAAAAAAA"}
            self.assertEqual(self.state.reset_repair(), {"repair": None})
            self.assertIsNone(self.state.repair)

    def test_failed_repair_does_not_block_a_different_device(self):
        self.state.repair = {"id": "r", "phase": "failed", "serial": "CP01-AAAAAAAAAAAA"}
        inspection = {"identity": {"serial": "CP01-BBBBBBBBBBBB"}}
        with mock.patch.object(
            self.state, "inspect_runtime", return_value=inspection
        ), mock.patch.object(
            self.state, "start_authority_update", return_value="job-2"
        ):
            repair = self.state.start_repair("COM5")

        self.assertEqual(repair["serial"], "CP01-BBBBBBBBBBBB")
        self.assertEqual(repair["phase"], "ota")

    def test_exit_is_refused_only_while_firmware_is_written(self):
        self.state.calibration = {"phase": "direction-verification"}
        self.state.require_shutdown_idle()
        self.assertTrue(self.state.closing)

        other = AuthorityUpdateState()
        other.busy_job = "job"
        with self.assertRaisesRegex(ProvisioningError, "固件正在写入"):
            other.require_shutdown_idle()
        self.assertFalse(other.closing)

    def test_shutdown_cancels_open_calibration_and_closes_port(self):
        client = mock.MagicMock()
        connection = mock.MagicMock()
        self.state.calibration = {"phase": "calibrating", "session_id": 9}
        self.state._calibration_client = client
        self.state._calibration_connection = connection

        self.state.shutdown()

        client.calibration_cancel.assert_called_once_with(9)
        connection.close.assert_called_once_with()
        self.assertIsNone(self.state._calibration_client)

    def test_shutdown_stops_diagnostic_capture_during_direction_check(self):
        client = mock.MagicMock()
        self.state.calibration = {"phase": "direction-verification", "session_id": 9}
        self.state._calibration_client = client
        self.state._calibration_connection = mock.MagicMock()

        self.state.shutdown()

        client.diagnostic_stop.assert_called_once_with()
        client.calibration_cancel.assert_not_called()

    def test_shutdown_waits_for_in_flight_device_request(self):
        self.state.begin_request()
        finished = threading.Event()

        def finish_later():
            finished.wait(0.2)
            self.state.end_request()

        threading.Thread(target=finish_later).start()
        client = mock.MagicMock()
        client.calibration_cancel.side_effect = lambda _sid: self.assertEqual(
            self.state._active_requests, 0
        )
        self.state.calibration = {"phase": "calibrating", "session_id": 3}
        self.state._calibration_client = client
        self.state._calibration_connection = mock.MagicMock()

        self.state.shutdown()

        client.calibration_cancel.assert_called_once_with(3)
        with self.assertRaisesRegex(ProvisioningError, "正在关闭"):
            self.state.begin_request()


class SpeedAndTimingTest(unittest.TestCase):
    """Calibration polling cost and per-stage timing records."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.state = AuthorityUpdateState()

    def tearDown(self):
        self.state.shutdown()
        self.temporary.cleanup()

    def open_calibration(self, phase="calibrating"):
        client = mock.MagicMock()
        self.state.calibration = {
            "phase": phase,
            "port": "COM5",
            "serial": "CP01-001122334455",
            "session_id": 4,
            "base_generation": 2,
        }
        self.state._calibration_client = client
        self.state._calibration_connection = mock.MagicMock()
        return client

    def test_calibration_poll_does_not_enumerate_serial_ports(self):
        client = self.open_calibration()
        client.calibration_sample.return_value = {
            "session_id": 4, "state": "CAPTURING", "raw": {"x": 2048, "y": 2050},
            "travel_complete": False, "ready_to_confirm": False,
        }
        with mock.patch(
            "provisioner.devices.load_ports", side_effect=AssertionError("enumerated")
        ), mock.patch(
            "provisioner.authority_update_server.detect_devices",
            side_effect=AssertionError("enumerated"),
        ):
            for _ in range(8):
                result = self.state.sample_joystick_calibration(
                    "COM5", expected_serial="CP01-001122334455", session_id=4
                )

        self.assertEqual(result["sample"]["raw"], {"x": 2048, "y": 2050})
        self.assertEqual(client.calibration_sample.call_count, 8)

    def test_calibration_poll_still_rejects_changed_port_and_busy_ota(self):
        self.open_calibration()
        with self.assertRaisesRegex(ProvisioningError, "端口发生变化"):
            self.state.sample_joystick_calibration(
                "COM9", expected_serial="CP01-001122334455", session_id=4
            )
        self.state.busy_job = "job"
        with self.assertRaisesRegex(ProvisioningError, "固件更新正在进行"):
            self.state.sample_joystick_calibration(
                "COM5", expected_serial="CP01-001122334455", session_id=4
            )

    def test_cancel_succeeds_when_device_was_unplugged(self):
        client = self.open_calibration()
        client.calibration_cancel.side_effect = TransportError("串口传输中断")
        self.state.repair = {
            "id": "r", "phase": "calibrating", "serial": "CP01-001122334455",
        }

        result = self.state.cancel_joystick_calibration(
            "COM5", expected_serial="CP01-001122334455", session_id=4
        )

        self.assertEqual(result["result"]["state"], "DEVICE_UNREACHABLE")
        self.assertIsNone(self.state.calibration)
        self.assertEqual(self.state.repair["phase"], "failed")

    def test_failed_repair_writes_session_record_with_reached_stages(self):
        now = 1000.0
        repair = {
            "id": "r", "phase": "calibrating", "serial": "CP01-001122334455",
            "started_at": "2026-10-22T01:00:00+00:00",
            "marks": {"started": now, "ota_done": now + 48.5, "calibration_started": now + 51.0},
        }
        with mock.patch(
            "provisioner.authority_update_server.time.monotonic", return_value=now + 90.0
        ):
            self.state._repair_failed(repair, ProvisioningError("摇杆校准已取消"))

        record_path = Path(self.state.repair["session_receipt_path"])
        record = json.loads(record_path.read_text(encoding="utf-8"))
        self.assertEqual(record["mode"], "repair-session")
        self.assertEqual(record["outcome"], "failed")
        self.assertFalse(record["success"])
        self.assertEqual(record["timings_s"]["ota_s"], 48.5)
        self.assertEqual(record["timings_s"]["slot_check_and_clear_s"], 2.5)
        self.assertIsNone(record["timings_s"]["calibration_capture_s"])
        self.assertEqual(record["timings_s"]["total_s"], 90.0)
        self.assertIn("repair-sessions", str(record_path))
        self.assertEqual(self.state._public_repair_locked()["timings_s"]["total_s"], 90.0)

    def test_passed_repair_records_every_stage(self):
        client = self.open_calibration("direction-verification")
        self.state.calibration.update(
            idle_neutral_passed=True,
            diagnostic_capture_active=True,
            direction_verification={
                "index": 3, "awaiting_neutral": True,
                "completed": ["up", "down", "left"], "complete": False,
            },
            before={},
        )
        client.diagnostic_start.return_value = {"active": True}
        client.get_status.return_value = {
            "diagnostic_capture": {"active": True},
            "action_engine": {"host_output_state": "diagnostic_capture"},
            "joystick_diagnostics": {"directions": 0},
        }
        base = 500.0
        self.state.repair = {
            "id": "r", "phase": "verifying-joystick", "serial": "CP01-001122334455",
            "marks": {
                "started": base, "ota_done": base + 40, "calibration_started": base + 42,
                "calibration_confirm": base + 60, "calibration_saved": base + 63,
            },
        }

        with mock.patch(
            "provisioner.authority_update_server.time.monotonic", return_value=base + 75
        ), mock.patch.object(self.state, "_confirm_voice_key_final") as final_key8:
            result = self.state.sample_direction_verification(
                "COM5", expected_serial="CP01-001122334455", session_id=4
            )

        final_key8.assert_called_once_with("COM5", expected_serial="CP01-001122334455")
        self.assertTrue(result["success"])
        timings = self.state.repair["timings_s"]
        self.assertEqual(
            timings,
            {
                "ota_s": 40.0, "slot_check_and_clear_s": 2.0,
                "calibration_capture_s": 18.0, "calibration_save_readback_s": 3.0,
                "direction_verification_s": 12.0, "total_s": 75.0,
            },
        )
        record = json.loads(
            Path(self.state.repair["session_receipt_path"]).read_text(encoding="utf-8")
        )
        self.assertEqual(record["outcome"], "passed")
        self.assertTrue(record["success"])

    def test_repair_is_not_passed_when_final_key8_readback_is_not_the_factory_gesture(self):
        from provisioner.errors import VerificationError

        client = self.open_calibration("direction-verification")
        self.state.calibration.update(
            idle_neutral_passed=True,
            diagnostic_capture_active=True,
            direction_verification={
                "index": 3, "awaiting_neutral": True,
                "completed": ["up", "down", "left"], "complete": False,
            },
            before={},
        )
        client.diagnostic_start.return_value = {"active": True}
        client.get_status.return_value = {
            "diagnostic_capture": {"active": True},
            "action_engine": {"host_output_state": "diagnostic_capture"},
            "joystick_diagnostics": {"directions": 0},
        }
        self.state.repair = {
            "id": "r", "phase": "verifying-joystick", "serial": "CP01-001122334455",
            "port": "COM5", "marks": {"started": 500.0},
        }
        with mock.patch.object(
            self.state, "_confirm_voice_key_final",
            side_effect=VerificationError("交付前最终读回：第 8 键不是出厂语音手势，停止交付"),
        ):
            result = self.state.sample_direction_verification(
                "COM5", expected_serial="CP01-001122334455", session_id=4
            )

        self.assertFalse(result["success"])
        self.assertEqual(self.state.repair["phase"], "failed")
        self.assertIn("第 8 键", self.state.repair["message"])
        self.assertNotIn("摇杆静止验收", self.state.repair["message"])

    def test_mark_keeps_first_time_a_stage_was_reached(self):
        from provisioner.authority_update_server import _with_mark

        with mock.patch(
            "provisioner.authority_update_server.time.monotonic", side_effect=[1.0, 9.0]
        ):
            first = _with_mark({}, "ota_done")
            second = _with_mark(first, "ota_done")
        self.assertEqual(second["marks"]["ota_done"], 1.0)

    def test_windows_build_uses_folder_package_with_path_guard(self):
        script = (
            Path(__file__).parents[1] / "scripts/build-authority-updater-windows.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn('"--onedir"', script)
        self.assertNotIn('"--onefile"', script)
        self.assertIn("BORING-MIST-BLE-Recovery/BORING-MIST-BLE-Recovery.exe", script)
        self.assertIn("Packaged path too long", script)
        self.assertIn('$delivery = Join-Path $buildRoot "BORING-MIST"', script)
        self.assertIn('"--contents-directory", "lib"', script)

    def test_recovery_icon_is_bundled_for_both_platforms(self):
        tool_root = Path(__file__).parents[1]
        windows = (tool_root / "scripts/build-authority-updater-windows.ps1").read_text(
            encoding="utf-8"
        )
        mac = (tool_root / "scripts/build-authority-updater-macos.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn('"--icon", (Join-Path $toolRoot "packaging/icons/recovery-icon.ico")', windows)
        self.assertIn("recovery-icon.ico'):icons", windows)
        self.assertIn('--icon "$tool_root/packaging/icons/recovery-icon.icns"', mac)
        self.assertIn("packaging/make_icon.py", windows)
        self.assertIn("packaging/make_icon.py", mac)
        # The public kit generates a generic icon at build time.
        with tempfile.TemporaryDirectory() as directory:
            icons = Path(directory)
            subprocess.run(
                [sys.executable, str(tool_root / "packaging/make_icon.py"), str(icons)],
                check=True,
            )
            with (icons / "recovery-icon.ico").open("rb") as handle:
                header = handle.read(6)
            self.assertEqual(header[:4], b"\x00\x00\x01\x00")
            self.assertGreaterEqual(int.from_bytes(header[4:6], "little"), 7)
            self.assertEqual((icons / "recovery-icon.icns").read_bytes()[:4], b"icns")


FIRMWARE_ROOT = Path(__file__).resolve().parents[2] / "firmware"


@unittest.skipUnless(
    (FIRMWARE_ROOT / "components/board/board_matrix12_v1.c").is_file(),
    "firmware sources are not checked out",
)
class FirmwareConstantsTest(unittest.TestCase):
    """The page draws the joystick with constants copied from the firmware."""

    def test_page_travel_threshold_matches_firmware(self):
        import re

        header = (
            FIRMWARE_ROOT
            / "components/config_protocol/include/joystick_calibration_model.h"
        ).read_text(encoding="utf-8")
        firmware = int(re.search(r"JOYSTICK_CALIBRATION_MIN_SIDE_TRAVEL = (\d+)", header).group(1))
        script = (Path(__file__).parents[1] / "static/authority-update.js").read_text(
            encoding="utf-8"
        )
        page = int(re.search(r"const MIN_SIDE_TRAVEL = (\d+);", script).group(1))
        self.assertEqual(page, firmware)

    def test_hardware_axis_inversion_matches_firmware(self):
        import re
        from provisioner.authority_update_server import POWER_V2_JOYSTICK_HARDWARE_INVERTED

        board = (FIRMWARE_ROOT / "components/board/board_matrix12_v1.c").read_text(
            encoding="utf-8"
        )
        values = tuple(
            re.search(rf"#define JOYSTICK_{axis}_HARDWARE_INVERTED (true|false)", board).group(1)
            == "true"
            for axis in ("X", "Y")
        )
        self.assertEqual(POWER_V2_JOYSTICK_HARDWARE_INVERTED, values)


class AuthorityUpdateHttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server, cls.state = create_authority_update_server()
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def test_serves_dedicated_page_without_production_actions(self):
        with urlopen(self.base + "/", timeout=2) as response:
            body = response.read().decode("utf-8")
        self.assertIn("固件更新与校准", body)
        self.assertNotIn("Power V2", body)
        self.assertNotIn("修复", body)
        self.assertNotIn("重新配对", body)
        self.assertIn('id="primaryButton"', body)
        self.assertIn("清空蓝牙槽位", body)
        self.assertIn("拔下 USB 即可交付", body)
        self.assertNotIn('id="slotChoiceCard"', body)
        for view in ("viewConnect", "viewFirmware", "viewCalibrate", "viewDone", "viewFailed"):
            self.assertIn(f'id="{view}"', body)
        self.assertIn('id="stickDot"', body)
        self.assertNotIn('id="resumeCalibrationButton"', body)
        self.assertNotIn("上次停在校准", body)
        self.assertNotIn('id="confirmationInput"', body)
        self.assertNotIn('id="startCalibrationButton"', body)
        self.assertNotIn("startProductionBatch", body)
        self.assertNotIn("startProductionIdentity", body)
        self.assertNotIn("BLOCK_KEY1", body)

        with urlopen(self.base + "/authority-update.css", timeout=2) as response:
            css = response.read().decode("utf-8")
        self.assertIn("[hidden] { display: none !important; }", css)

    def test_does_not_expose_main_provisioner_api(self):
        with self.assertRaises(HTTPError) as context:
            urlopen(self.base + "/api/factory-identity", timeout=2)
        self.assertEqual(context.exception.code, 404)

    def test_customer_readmes_use_boring_mist_and_normal_connection_language(self):
        tool_root = Path(__file__).parents[1]
        for relative in (
            "packaging/macos/README-template.txt",
            "packaging/windows/README-template.txt",
        ):
            body = (tool_root / relative).read_text(encoding="utf-8")
            self.assertIn("BORING MIST", body)
            self.assertIn("正常开机", body)
            self.assertIn("支持数据传输", body)
            self.assertNotIn("Power V2", body)
            self.assertNotIn("BOOT", body)

    def test_customer_package_names_and_display_hide_internal_release_id(self):
        tool_root = Path(__file__).parents[1]
        mac_script = (tool_root / "scripts/build-authority-updater-macos.sh").read_text(
            encoding="utf-8"
        )
        windows_script = (
            tool_root / "scripts/build-authority-updater-windows.ps1"
        ).read_text(encoding="utf-8")
        page_script = (tool_root / "static/authority-update.js").read_text(
            encoding="utf-8"
        )
        self.assertIn('package_name="BORING-MIST-${build_id}-macOS-${arch}"', mac_script)
        self.assertIn('$packageName = "BORING-MIST-$($meta.build_id)-windows-x64"', windows_script)
        self.assertIn("function firmwareVersion()", page_script)
        self.assertIn('"sample-verified": "已通过样机验证"', page_script)
        self.assertIn('button.textContent = "更新下一台"', page_script)
        self.assertNotIn("/api/authority-update/repair/slot", page_script)
        self.assertIn("const MIN_SIDE_TRAVEL = 400;", page_script)
        self.assertNotIn('event.code !== "Space"', page_script)
        self.assertNotIn("开始修复另一台设备", page_script)
        self.assertNotIn(
            '$("#releaseId").textContent = state.authority.release_id',
            page_script,
        )

    def test_reset_endpoint_clears_finished_repair(self):
        self.state.repair = {"id": "r", "phase": "completed"}
        request = Request(
            self.base + "/api/authority-update/repair/reset",
            data=b"{}",
            headers={
                "Content-Type": "application/json",
                "X-Authority-Updater-Token": self.state.token,
            },
            method="POST",
        )
        with urlopen(request, timeout=2) as response:
            self.assertEqual(json.load(response), {"repair": None})
        self.assertIsNone(self.state.repair)
        # The handler releases its request slot right after writing the reply.
        deadline = time.monotonic() + 2
        while self.state._active_requests and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.state._active_requests, 0)

    def test_page_offers_cancel_with_two_step_confirmation(self):
        with urlopen(self.base + "/", timeout=2) as response:
            body = response.read().decode("utf-8")
        self.assertIn('id="cancelCalibrationButton"', body)
        with urlopen(self.base + "/authority-update.js", timeout=2) as response:
            script = response.read().decode("utf-8")
        self.assertIn("/api/authority-update/calibration/cancel", script)
        self.assertIn("MAX_POLL_FAILURES", script)
        self.assertIn("armedClick", script)

    def test_post_requires_dedicated_token(self):
        request = Request(
            self.base + "/api/authority-update/jobs",
            data=json.dumps({"port": "/dev/cu.runtime"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(HTTPError) as context:
            urlopen(request, timeout=2)
        self.assertEqual(context.exception.code, 403)


if __name__ == "__main__":
    unittest.main()
