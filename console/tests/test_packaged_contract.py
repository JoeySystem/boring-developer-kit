import shutil
import json
from pathlib import Path

import pytest

from controller_config.packaging import stage_production_trust_policy
from controller_config.protocol.contract import Contract


def test_contract_loads_protocol_resources_beside_windows_executable(
    monkeypatch, protocol_dir, tmp_path
) -> None:
    executable = tmp_path / "BORING Console.exe"
    executable.touch()
    shutil.copytree(protocol_dir, tmp_path / "protocol")
    monkeypatch.setattr("controller_config.protocol.contract.sys.executable", str(executable))
    monkeypatch.delenv("BORING_PROTOCOL_DIR", raising=False)

    contract = Contract.load()

    assert contract.protocol_dir == tmp_path / "protocol"
    assert (contract.protocol_dir / "fixtures" / "usb-descriptor-v1.json").is_file()


def test_contract_loads_protocol_resources_from_macos_app_resources(
    monkeypatch, protocol_dir, tmp_path
) -> None:
    macos = tmp_path / "BORING Console.app" / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    executable = macos / "BORING Console"
    executable.touch()
    resources = tmp_path / "BORING Console.app" / "Contents" / "Resources"
    shutil.copytree(protocol_dir, resources / "protocol")
    monkeypatch.setattr("controller_config.protocol.contract.sys.executable", str(executable))
    monkeypatch.delenv("BORING_PROTOCOL_DIR", raising=False)

    contract = Contract.load()

    assert contract.protocol_dir == resources / "protocol"
    assert contract.usb_vid == 0x303A
    assert contract.usb_pid == 0x8360


def test_production_policy_marker_requires_a_production_root(tmp_path) -> None:
    roots = tmp_path / "device-trust-roots.json"
    marker = tmp_path / "device-trust-policy.json"
    project = Path(__file__).resolve().parents[1]
    document = json.loads(
        (
            project
            / "src"
            / "controller_config"
            / "assets"
            / "device-trust-roots.json"
        ).read_text(encoding="utf-8")
    )
    roots.write_text(
        json.dumps(
            {
                "version": 1,
                "roots": [
                    root for root in document["roots"] if root["purpose"] == "test"
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="生产信任根"):
        stage_production_trust_policy(roots, marker)

    assert not marker.exists()


def test_production_policy_marker_is_fixed_by_packaging(tmp_path) -> None:
    roots = tmp_path / "device-trust-roots.json"
    marker = tmp_path / "device-trust-policy.json"
    project = Path(__file__).resolve().parents[1]
    document = json.loads(
        (
            project
            / "src"
            / "controller_config"
            / "assets"
            / "device-trust-roots.json"
        ).read_text(encoding="utf-8")
    )
    roots.write_text(
        json.dumps(document),
        encoding="utf-8",
    )

    stage_production_trust_policy(roots, marker)

    assert json.loads(marker.read_text(encoding="utf-8")) == {
        "version": 1,
        "policy": "production",
    }


def test_native_builds_stage_the_production_policy_marker() -> None:
    project = Path(__file__).resolve().parents[1]
    macos = (project / "deploy" / "build_macos.sh").read_text(encoding="utf-8")
    windows = (project / "deploy" / "build_windows.ps1").read_text(encoding="utf-8")

    assert "stage_device_trust_policy.py" in macos
    assert "stage_device_trust_policy.py" in windows
    assert "device-trust-policy.json" in macos
    assert "device-trust-policy.json" in windows
    assert "--include-package=CoreBluetooth" in macos
    assert "NSBluetoothAlwaysUsageDescription" in macos
