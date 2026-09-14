#!/usr/bin/env python3
"""Package a custom build for explicit local installation; never signs or publishes."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from zipfile import ZIP_DEFLATED, ZipFile

from firmware_manifest import ManifestError, load_manifest


def pack_custom_firmware(manifest_path: Path, output: Path, *, minimum_app_version: str = "0.1.24") -> Path:
    manifest, image = load_manifest(manifest_path)
    build_id = manifest.get("build_id", "")
    if not isinstance(build_id, str) or re.fullmatch(r"custom-[A-Za-z0-9._-]{1,57}", build_id) is None:
        raise ManifestError("Compile with a custom- build ID first; do not rename an official manifest.")
    if "signature" in manifest:
        raise ManifestError("Do not reuse an official publisher signature for a custom build.")
    if build_id.encode("ascii") + b"\0" not in image.read_bytes():
        raise ManifestError("Custom build ID is missing from the image; rebuild with WMP_BUILD_ID.")
    if re.fullmatch(r"\d+\.\d+\.\d+", minimum_app_version) is None:
        raise ManifestError("minimum_app_version must be a three-part Console version.")
    manifest.update(origin="custom", validation_state="built", minimum_app_version=minimum_app_version)
    # These describe official online publication, not this local custom package.
    for key in ("channel", "download_url", "published_at", "mandatory"):
        manifest.pop(key, None)
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "x", compression=ZIP_DEFLATED) as archive:
        archive.writestr("firmware-manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        archive.write(image, manifest["image"])
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--minimum-app-version", default="0.1.24")
    args = parser.parse_args()
    try:
        result = pack_custom_firmware(args.manifest, args.output, minimum_app_version=args.minimum_app_version)
    except (ManifestError, OSError, ValueError) as exc:
        parser.exit(2, f"Custom package not created: {exc}\n")
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
