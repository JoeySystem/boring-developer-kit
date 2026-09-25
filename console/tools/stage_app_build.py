"""Stage an explicit Console build origin in a disposable asset directory."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from controller_config.build_identity import BuildIdentity


def stage(assets: Path, origin: str) -> BuildIdentity:
    if origin not in {"official", "custom"}:
        raise ValueError("Console build origin must be official or custom")

    identity = BuildIdentity(origin=origin)
    (assets / "app-build.json").write_text(
        json.dumps({"origin": origin}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    if origin == "custom":
        source = assets / "app-update-source.json"
        document = json.loads(source.read_text(encoding="utf-8-sig"))
        for platform in ("macos", "windows"):
            config = document.get(platform)
            if not isinstance(config, dict):
                raise ValueError(f"Missing {platform} desktop update configuration")
            config["feed_url"] = ""
            config["public_key"] = ""
        source.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return identity


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--origin", required=True)
    args = parser.parse_args()
    stage(args.assets, args.origin)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
