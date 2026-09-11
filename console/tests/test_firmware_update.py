from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct
from zipfile import ZipFile

import pytest

from controller_config.firmware_update import (
    APP_DESC_OFFSET,
    CUSTOM_DESC_OFFSET,
    ESP_APP_DESC_MAGIC,
    FirmwarePackage,
    FirmwarePackageError,
    FirmwareStatus,
    FirmwareUpdateState,
    FirmwareUpdateTransaction,
    WMP_DESCRIPTOR,
    WMP_DESCRIPTOR_MAGIC,
    WMP_DESCRIPTOR_VERSION,
)
from controller_config.transport.demo import _power_v2_snapshot


def _write_package(
    root: Path,
    contract,
    *,
    hardware_id: str = "WMP-S3-MATRIX12-POWER-V2",
    version: str = "0.3.0-alpha.1",
    build_id: str = "test-build",
) -> Path:
    image = bytearray(1024)
    image[0] = 0xE9
    struct.pack_into("<I", image, APP_DESC_OFFSET, ESP_APP_DESC_MAGIC)
    image[APP_DESC_OFFSET + 16 : APP_DESC_OFFSET + 48] = _fixed(version, 32)
    image[APP_DESC_OFFSET + 48 : APP_DESC_OFFSET + 80] = _fixed("wired_macro_pad", 32)
    WMP_DESCRIPTOR.pack_into(
        image,
        CUSTOM_DESC_OFFSET,
        WMP_DESCRIPTOR_MAGIC,
        WMP_DESCRIPTOR_VERSION,
        WMP_DESCRIPTOR.size,
        _fixed(contract.product_id, 32),
        _fixed(hardware_id, 32),
    )
    image_path = root / "wired_macro_pad.bin"
    image_path.write_bytes(image)
    manifest = {
        "format": "wmp-firmware-update-v1",
        "product_id": contract.product_id,
        "hardware_id": hardware_id,
        "version": version,
        "image": image_path.name,
        "size": len(image),
        "sha256": hashlib.sha256(image).hexdigest(),
        "build_id": build_id,
        "validation_state": "built",
        "git_dirty": True,
    }
    manifest_path = root / "firmware-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path


def _fixed(value: str, size: int) -> bytes:
    encoded = value.encode("ascii")
    return encoded + b"\0" + bytes(size - len(encoded) - 1)


def _zip_package(root: Path, contract, *, folder: str = "release") -> Path:
    source = root / "source"
    source.mkdir()
    manifest_path = _write_package(source, contract)
    archive_path = root / "boring-firmware.zip"
    with ZipFile(archive_path, "w") as archive:
        archive.write(manifest_path, f"{folder}/firmware-manifest.json")
        archive.write(
            source / "wired_macro_pad.bin",
            f"{folder}/wired_macro_pad.bin",
        )
    return archive_path


def test_package_load_validates_manifest_image_and_builds_chunks(tmp_path, contract) -> None:
    package = FirmwarePackage.load(_write_package(tmp_path, contract), contract)

    assert package.hardware_id == "WMP-S3-MATRIX12-POWER-V2"
    assert package.build_id == "test-build"
    command, size = package.data_command(0, 512)
    assert command.name == "FW_DATA"
    assert command.payload["offset"] == 0
    assert size == 512
    assert command.retries == 2


def test_package_loads_downloaded_zip_without_extracting_it(tmp_path, contract) -> None:
    archive_path = _zip_package(tmp_path, contract)

    package = FirmwarePackage.load(archive_path, contract)

    assert package.source_path == archive_path.resolve()
    assert package.image_path.name == "wired_macro_pad.bin"
    assert package.hardware_id == "WMP-S3-MATRIX12-POWER-V2"
    assert package.image_data.startswith(b"\xe9")


def test_package_rejects_incomplete_or_ambiguous_zip(tmp_path, contract) -> None:
    missing_manifest = tmp_path / "missing-manifest.zip"
    with ZipFile(missing_manifest, "w") as archive:
        archive.writestr("wired_macro_pad.bin", b"not enough")
    with pytest.raises(FirmwarePackageError, match="firmware-manifest.json"):
        FirmwarePackage.load(missing_manifest, contract)

    duplicate_manifest = tmp_path / "duplicate-manifest.zip"
    duplicate_source = tmp_path / "duplicate-source"
    duplicate_source.mkdir()
    manifest_path = _write_package(duplicate_source, contract)
    with ZipFile(duplicate_manifest, "w") as archive:
        archive.write(manifest_path, "one/firmware-manifest.json")
        archive.write(manifest_path, "two/firmware-manifest.json")
    with pytest.raises(FirmwarePackageError, match="只能包含一份"):
        FirmwarePackage.load(duplicate_manifest, contract)

    missing_image = tmp_path / "missing-image.zip"
    with ZipFile(missing_image, "w") as archive:
        archive.write(manifest_path, "firmware-manifest.json")
    with pytest.raises(FirmwarePackageError, match="找不到固件镜像"):
        FirmwarePackage.load(missing_image, contract)


def test_package_rejects_a_standalone_bin(tmp_path, contract) -> None:
    image_path = tmp_path / "wired_macro_pad.bin"
    image_path.write_bytes(b"\xe9")

    with pytest.raises(FirmwarePackageError, match="不能单独导入 .bin"):
        FirmwarePackage.load(image_path, contract)


def test_package_rejects_changed_image_and_wrong_device(tmp_path, contract) -> None:
    manifest_path = _write_package(tmp_path, contract)
    (tmp_path / "wired_macro_pad.bin").write_bytes(b"changed")
    with pytest.raises(FirmwarePackageError, match="大小与 manifest"):
        FirmwarePackage.load(manifest_path, contract)

    other = tmp_path / "other"
    other.mkdir()
    package = FirmwarePackage.load(
        _write_package(other, contract, hardware_id="WMP-S3-MATRIX12-V1"),
        contract,
    )
    snapshot = _power_v2_snapshot(contract, read_only=False)
    with pytest.raises(FirmwarePackageError, match="当前设备"):
        package.validate_for_device(snapshot)


def test_package_rejects_version_outside_firmware_descriptor_alphabet(
    tmp_path, contract
) -> None:
    manifest_path = _write_package(tmp_path, contract)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["version"] = "测试版"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(FirmwarePackageError, match="版本号无效"):
        FirmwarePackage.load(manifest_path, contract)


def test_package_uses_device_chunk_and_image_limits(tmp_path, contract) -> None:
    package = FirmwarePackage.load(_write_package(tmp_path, contract), contract)
    snapshot = _power_v2_snapshot(contract, read_only=False)

    assert package.validate_for_device(snapshot) == 4096

    snapshot.capabilities["limits"]["firmware_image_bytes"] = 10
    with pytest.raises(FirmwarePackageError, match="OTA 镜像上限"):
        package.validate_for_device(snapshot)


def test_firmware_status_requires_protocol_shape_and_matches_package(
    tmp_path, contract
) -> None:
    package = FirmwarePackage.load(_write_package(tmp_path, contract), contract)
    status = FirmwareStatus.from_payload(
        {
            "command": "FW_STATUS",
            "result": {
                "firmware_update": {
                    "state": "RECEIVING",
                    "expected_size": package.size,
                    "received_size": 512,
                    "expected_sha256": package.sha256,
                    "expected_version": package.version,
                    "running_partition": "ota_0",
                    "target_partition": "ota_1",
                }
            },
        }
    )
    assert status.matches(package)
    assert status.received_size == 512

    with pytest.raises(FirmwarePackageError, match="FW_STATUS"):
        FirmwareStatus.from_payload({"command": "FW_STATUS", "result": {}})


def test_checking_transaction_can_be_cancelled() -> None:
    transaction = FirmwareUpdateTransaction(state=FirmwareUpdateState.CHECKING)

    assert transaction.can_abort is True
