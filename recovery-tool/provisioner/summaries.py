from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path

from .release import ReleasePackage


def package_fingerprint(package: ReleasePackage) -> str:
    identity = {
        "archive_sha256": package.archive_sha256,
        "manifest": package.manifest,
    }
    encoded = json.dumps(
        identity,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def package_summary(package: ReleasePackage, source: str | Path | None = None) -> dict:
    summary = {
        "release_id": package.release_id,
        "target": package.target,
        "esp_idf": package.esp_idf,
        "git_tag": package.git_tag,
        "source_commit": package.source_commit,
        "hardware_id": package.hardware_id,
        "build_id": package.build_id,
        "validation_state": package.validation_state,
        "ota_supported": package.ota_application is not None,
        "runtime_usb": asdict(package.runtime_usb),
        "archive_sha256": package.archive_sha256,
        "archive_checksum_verified": package.archive_checksum_verified,
        "trusted_source_verified": package.trusted_source_verified,
        "fingerprint": package_fingerprint(package),
        "images": [
            {
                "path": image.relative_path,
                "offset": f"0x{image.offset:X}",
                "size": image.size,
                "sha256": image.sha256,
            }
            for image in package.flash_images
        ],
    }
    if source is not None:
        summary["path"] = str(source)
    return summary
