from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def partition_table(*, ota: bool = False) -> bytes:
    def entry(partition_type, subtype, offset, size, label):
        return struct.pack(
            "<HBBLL16sL",
            0x50AA,
            partition_type,
            subtype,
            offset,
            size,
            label.encode("ascii").ljust(16, b"\0"),
            0,
        )

    if ota:
        return (
            entry(0x00, 0x10, 0x10000, 0x200000, "ota_0")
            + entry(0x01, 0x02, 0x210000, 0x20000, "config_nvs")
            + entry(0x01, 0x03, 0x230000, 0x10000, "coredump")
            + entry(0x01, 0x00, 0x240000, 0x2000, "otadata")
            + entry(0x00, 0x11, 0x250000, 0x200000, "ota_1")
            + b"\xFF" * 32
        )
    return (
        entry(0x00, 0x00, 0x10000, 0x200000, "factory")
        + entry(0x01, 0x02, 0x210000, 0x6000, "config_nvs")
        + b"\xFF" * 32
    )


def make_release(root: Path) -> Path:
    release = root / "usb-codex-stable-test"
    files = {
        "bootloader/bootloader.bin": b"bootloader-test-image",
        "partition_table/partition-table.bin": partition_table(),
        "wired_macro_pad.bin": b"application-test-image",
        "flash_args": b"--flash_mode dio\n",
    }
    for relative, data in files.items():
        path = release / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    manifest = {
        "release_id": "usb-codex-stable-test",
        "git_tag": "usb-codex-stable-test",
        "source_commit": "1" * 40,
        "target": "rev-a-codex-usb",
        "esp_idf": "v6.0.2",
        "application_build_time": "2026-07-30T10:00:00+08:00",
        "hardware_validation_date": "2026-07-30",
        "runtime_usb": {
            "product": "Codex Micro",
            "vid": "303A",
            "pid": "8360",
        },
        "rollback": {
            "recommended_scope": "application-only",
            "application_offset": "0x10000",
            "erase_flash": False,
            "preserves_nvs": True,
        },
        "files": {
            relative: {
                **({"offset": offset} if offset else {}),
                "sha256": sha256(data),
            }
            for (relative, data), offset in zip(
                files.items(),
                ("0x0", "0x8000", "0x10000", None),
            )
        },
    }
    (release / "release-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return release


def make_ota_release(root: Path) -> Path:
    release = root / "matrix12-power-v2-test"
    application = b"power-v2-application-test-image"
    files = {
        "bootloader/bootloader.bin": b"bootloader-test-image",
        "partition_table/partition-table.bin": partition_table(ota=True),
        "wired_macro_pad.bin": application,
        "ota_data_initial.bin": b"ota-data-test-image",
        "power-v2.bin": application,
    }
    ota_manifest = {
        "format": "wmp-firmware-update-v1",
        "product_id": "wired-macro-pad-v1",
        "hardware_id": "WMP-S3-MATRIX12-POWER-V2",
        "version": "0.3.0-alpha.1",
        "image": "power-v2.bin",
        "size": len(application),
        "sha256": sha256(application),
        "build_id": "20260824.02-g1fafffab-dirty",
        "validation_state": "sample-verified",
    }
    files["firmware-manifest.json"] = (
        json.dumps(ota_manifest, ensure_ascii=False, indent=2).encode("utf-8")
    )
    for relative, data in files.items():
        path = release / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    offsets = {
        "bootloader/bootloader.bin": "0x0",
        "partition_table/partition-table.bin": "0x8000",
        "wired_macro_pad.bin": "0x10000",
        "ota_data_initial.bin": "0x240000",
    }
    manifest = {
        "release_id": "matrix12-power-v2-test",
        "git_tag": "matrix12-power-v2-test",
        "source_commit": "2" * 40,
        "target": "matrix12-power-v2-codex-usb",
        "hardware_id": "WMP-S3-MATRIX12-POWER-V2",
        "build_id": "20260824.02-g1fafffab-dirty",
        "validation_state": "sample-verified",
        "esp_idf": "v6.0.2",
        "runtime_usb": {
            "product": "Codex Micro",
            "vid": "303A",
            "pid": "8360",
        },
        "ota_application_manifest": "firmware-manifest.json",
        "rollback": {
            "recommended_scope": "application-only",
            "application_offset": "0x10000",
            "erase_flash": False,
            "preserves_nvs": True,
        },
        "files": {
            relative: {
                **({"offset": offsets[relative]} if relative in offsets else {}),
                "sha256": sha256(data),
            }
            for relative, data in files.items()
        },
    }
    (release / "release-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return release


def make_native_ota_release(root: Path) -> Path:
    release = make_ota_release(root)
    manifest_path = release / "firmware-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    image_name = (
        "wmp-matrix12-power-v2-codex-usb-v0.3.0-alpha.1-"
        "20260824.02-g1fafffab-dirty.bin"
    )
    (release / "power-v2.bin").rename(release / image_name)
    manifest.update(
        {
            "image": image_name,
            "build_target": "matrix12-power-v2-codex-usb",
            "git_commit": "1fafffabbdac9a06856e78b4cb726d74c5419b67",
            "git_dirty": True,
            "built_at": "2026-08-24T03:49:24Z",
        }
    )
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (release / "release-manifest.json").unlink()
    return release
