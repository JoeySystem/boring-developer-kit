#!/usr/bin/env python3
"""Create and validate a WMP firmware-update manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct
from typing import Any, Mapping, Sequence


MANIFEST_FORMAT = "wmp-firmware-update-v1"
PRODUCT_ID = "wired-macro-pad-v1"
SUPPORTED_HARDWARE_IDS = {
    "WMP-S3-DEVKIT",
    "WMP-S3-REV-A",
    "WMP-S3-MATRIX12-V1",
    "WMP-S3-MATRIX12-POWER-V2",
}
ESP_IMAGE_MAGIC = 0xE9
ESP_APP_DESC_MAGIC = 0xABCD5432
WMP_DESCRIPTOR_MAGIC = 0x46504D57
WMP_DESCRIPTOR_VERSION = 1
APP_DESC_OFFSET = 24 + 8
CUSTOM_DESC_OFFSET = APP_DESC_OFFSET + 256
WMP_DESCRIPTOR = struct.Struct("<IHH32s32s")
VERSION_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-_+"
)
VALIDATION_STATES = {"built", "sample-verified", "rc", "released"}


class ManifestError(ValueError):
    """The update package is missing data or does not match its manifest."""


def file_digest_and_size(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(128 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _descriptor_string(raw: bytes, field_name: str) -> str:
    value, separator, _tail = raw.partition(b"\0")
    if not separator:
        raise ManifestError(f"firmware {field_name} is not null-terminated")
    try:
        return value.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ManifestError(f"firmware {field_name} is not ASCII") from exc


def _validate_version(version: str) -> None:
    if (
        not version
        or len(version.encode("utf-8")) >= 32
        or any(character not in VERSION_CHARACTERS for character in version)
    ):
        raise ManifestError(
            "version must be 1-31 ASCII letters, digits, dot, dash, underscore, or plus"
        )


def read_image_metadata(image_path: Path) -> tuple[str, str, str]:
    try:
        with image_path.open("rb") as source:
            prefix = source.read(CUSTOM_DESC_OFFSET + WMP_DESCRIPTOR.size)
    except OSError as exc:
        raise ManifestError(f"could not read firmware image: {exc}") from exc
    if len(prefix) < CUSTOM_DESC_OFFSET + WMP_DESCRIPTOR.size:
        raise ManifestError("firmware image is too small to contain its descriptors")
    if prefix[0] != ESP_IMAGE_MAGIC:
        raise ManifestError("firmware image has an invalid ESP image header")
    if struct.unpack_from("<I", prefix, APP_DESC_OFFSET)[0] != ESP_APP_DESC_MAGIC:
        raise ManifestError("firmware image has an invalid ESP app descriptor")
    version = _descriptor_string(
        prefix[APP_DESC_OFFSET + 16 : APP_DESC_OFFSET + 48], "version"
    )
    project_name = _descriptor_string(
        prefix[APP_DESC_OFFSET + 48 : APP_DESC_OFFSET + 80], "project name"
    )
    magic, descriptor_version, descriptor_size, product, hardware = (
        WMP_DESCRIPTOR.unpack_from(prefix, CUSTOM_DESC_OFFSET)
    )
    if (
        magic != WMP_DESCRIPTOR_MAGIC
        or descriptor_version != WMP_DESCRIPTOR_VERSION
        or descriptor_size != WMP_DESCRIPTOR.size
    ):
        raise ManifestError("firmware image has an invalid WMP descriptor")
    product_id = _descriptor_string(product, "product_id")
    hardware_id = _descriptor_string(hardware, "hardware_id")
    if project_name != "wired_macro_pad" or product_id != PRODUCT_ID:
        raise ManifestError("firmware image is not a WMP application")
    if hardware_id not in SUPPORTED_HARDWARE_IDS:
        raise ManifestError(f"unsupported hardware_id: {hardware_id}")
    _validate_version(version)
    return product_id, hardware_id, version


def build_manifest(
    image_path: Path,
    *,
    build_target: str | None = None,
    build_id: str | None = None,
    git_commit: str | None = None,
    git_dirty: bool | None = None,
    validation_state: str | None = None,
    built_at: str | None = None,
) -> dict[str, Any]:
    product_id, hardware_id, version = read_image_metadata(image_path)
    try:
        digest, size = file_digest_and_size(image_path)
    except OSError as exc:
        raise ManifestError(f"could not read firmware image: {exc}") from exc
    manifest = {
        "format": MANIFEST_FORMAT,
        "product_id": product_id,
        "hardware_id": hardware_id,
        "version": version,
        "image": image_path.name,
        "size": size,
        "sha256": digest,
    }
    optional = {
        "build_target": build_target,
        "build_id": build_id,
        "git_commit": git_commit,
        "git_dirty": git_dirty,
        "validation_state": validation_state,
        "built_at": built_at,
    }
    manifest.update({name: value for name, value in optional.items() if value is not None})
    return manifest


def validate_manifest(
    value: Mapping[str, Any], manifest_path: Path
) -> tuple[dict[str, Any], Path]:
    required = {
        "format": str,
        "product_id": str,
        "hardware_id": str,
        "version": str,
        "image": str,
        "size": int,
        "sha256": str,
    }
    for name, expected_type in required.items():
        field = value.get(name)
        if type(field) is not expected_type:
            raise ManifestError(f"manifest field {name!r} is missing or invalid")
    if value["format"] != MANIFEST_FORMAT:
        raise ManifestError(f"unsupported manifest format: {value['format']}")
    if value["product_id"] != PRODUCT_ID:
        raise ManifestError(f"unsupported product_id: {value['product_id']}")
    if value["hardware_id"] not in SUPPORTED_HARDWARE_IDS:
        raise ManifestError(f"unsupported hardware_id: {value['hardware_id']}")
    _validate_version(value["version"])
    if Path(value["image"]).name != value["image"]:
        raise ManifestError("image must be a filename beside the manifest")
    if value["size"] <= 0:
        raise ManifestError("firmware image size must be positive")
    if (
        len(value["sha256"]) != 64
        or any(character not in "0123456789abcdef" for character in value["sha256"])
    ):
        raise ManifestError("sha256 must be 64 lowercase hexadecimal characters")
    if "git_dirty" in value and type(value["git_dirty"]) is not bool:
        raise ManifestError("manifest field 'git_dirty' is invalid")
    if (
        "validation_state" in value
        and value["validation_state"] not in VALIDATION_STATES
    ):
        raise ManifestError("manifest validation_state is invalid")

    image_path = manifest_path.parent / value["image"]
    try:
        digest, size = file_digest_and_size(image_path)
    except OSError as exc:
        raise ManifestError(f"could not read firmware image: {exc}") from exc
    if size != value["size"]:
        raise ManifestError("firmware image size does not match manifest")
    if digest != value["sha256"]:
        raise ManifestError("firmware image SHA-256 does not match manifest")
    product_id, hardware_id, version = read_image_metadata(image_path)
    if (
        product_id != value["product_id"]
        or hardware_id != value["hardware_id"]
        or version != value["version"]
    ):
        raise ManifestError("firmware image identity does not match manifest")
    return dict(value), image_path


def load_manifest(manifest_path: Path) -> tuple[dict[str, Any], Path]:
    try:
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"could not read manifest: {exc}") from exc
    if not isinstance(value, dict):
        raise ManifestError("manifest root must be a JSON object")
    return validate_manifest(value, manifest_path)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--build-target")
    parser.add_argument("--build-id")
    parser.add_argument("--git-commit")
    parser.add_argument("--git-dirty", choices=("true", "false"))
    parser.add_argument("--validation-state")
    parser.add_argument("--built-at")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    manifest = build_manifest(
        args.image,
        build_target=args.build_target,
        build_id=args.build_id,
        git_commit=args.git_commit,
        git_dirty=(args.git_dirty == "true") if args.git_dirty is not None else None,
        validation_state=args.validation_state,
        built_at=args.built_at,
    )
    args.output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Firmware update manifest: {args.output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ManifestError as exc:
        raise SystemExit(f"error: {exc}") from exc
