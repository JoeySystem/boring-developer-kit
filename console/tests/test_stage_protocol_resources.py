from pathlib import Path

import pytest

from controller_config.packaging import stage_protocol_resources


def test_stage_protocol_resources_copies_authoritative_assets(
    protocol_dir: Path, tmp_path: Path
) -> None:
    destination = tmp_path / "protocol"

    stage_protocol_resources(protocol_dir, destination)

    assert (destination / "protocol.md").read_bytes() == (
        protocol_dir / "protocol.md"
    ).read_bytes()
    assert (destination / "config-schema.json").read_bytes() == (
        protocol_dir / "config-schema.json"
    ).read_bytes()
    assert (destination / "fixtures" / "usb-descriptor-v1.json").read_bytes() == (
        protocol_dir / "fixtures" / "usb-descriptor-v1.json"
    ).read_bytes()
    assert (
        destination / "fixtures" / "config-matrix12-power-v2-v1.json"
    ).read_bytes() == (
        protocol_dir / "fixtures" / "config-matrix12-power-v2-v1.json"
    ).read_bytes()
    assert (
        destination / "fixtures" / "capabilities-matrix12-power-v2-v1.json"
    ).read_bytes() == (
        protocol_dir / "fixtures" / "capabilities-matrix12-power-v2-v1.json"
    ).read_bytes()


def test_stage_protocol_resources_excludes_test_private_keys(
    protocol_dir: Path, tmp_path: Path
) -> None:
    destination = tmp_path / "protocol"

    stage_protocol_resources(protocol_dir, destination)

    assert not (destination / "fixtures" / "device-auth-v1.json").exists()
    staged_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in destination.rglob("*")
        if path.is_file()
    )
    assert "PRIVATE KEY" not in staged_text
    assert "test_private_keys" not in staged_text


def test_stage_protocol_resources_does_not_overwrite_existing_destination(
    protocol_dir: Path, tmp_path: Path
) -> None:
    destination = tmp_path / "protocol"
    destination.mkdir()

    with pytest.raises(ValueError, match="目标已存在"):
        stage_protocol_resources(protocol_dir, destination)


def test_windows_build_checks_native_tool_exit_codes() -> None:
    script = (
        Path(__file__).resolve().parents[1] / "deploy" / "build_windows.ps1"
    ).read_text(encoding="utf-8")

    assert "UI text catalog validation failed" in script
    assert "Protocol resource staging failed" in script
