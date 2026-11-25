from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tool-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    # The public kit only bundles firmware; factory tooling is not part of it.
    parser.add_argument("--authority-only", action="store_true", help="Accepted for build-script compatibility")
    parser.add_argument("--authority", type=Path, help="Explicit approved firmware package")
    parser.add_argument(
        "--acceptance-test",
        action="store_true",
        help="Bundle an unverified ('built') package for a labelled acceptance test",
    )
    args = parser.parse_args()

    tool_root = args.tool_root.resolve()
    sys.path.insert(0, str(tool_root))
    from provisioner.paths import discover_source_authority_path
    from provisioner.release import ReleasePackage

    source = args.authority.resolve() if args.authority else discover_source_authority_path()
    if source is None:
        raise RuntimeError("pass --authority with an extracted firmware package")

    output = args.output.resolve()
    releases = output / "releases"
    releases.mkdir(parents=True, exist_ok=True)
    with ReleasePackage.open(source) as package:
        destination = releases / package.release_id
        destination.mkdir(parents=True, exist_ok=True)
        for relative in package.manifest["files"]:
            source_file = package.root / relative
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, target)
        (destination / "release-manifest.json").write_text(
            json.dumps(package.manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if package.validation_state == "built" and not args.acceptance_test:
            raise RuntimeError("unverified firmware needs --acceptance-test")
        metadata = {
            "release_id": package.release_id,
            "build_id": package.build_id,
            "relative_path": f"releases/{package.release_id}",
            "acceptance_test": bool(args.acceptance_test),
        }

    (output / "bundled-authority.json").write_text(
        json.dumps(
            {
                "release_id": metadata["release_id"],
                "relative_path": metadata["relative_path"],
                "acceptance_test": metadata["acceptance_test"],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (output / "bundle-metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
