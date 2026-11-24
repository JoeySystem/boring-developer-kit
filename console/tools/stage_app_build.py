"""Stage an explicit Console build origin in a disposable asset directory."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from controller_config.build_identity import BuildIdentity, dev_build_issue


def stage(assets: Path, origin: str, *, dev_label: str = "",
          dev_date: str = "") -> BuildIdentity:
    """Write the build origin; ``dev_label`` marks an internal development
    package, which never carries an in-app update source (an update would
    replace the feature under test with the official build)."""
    if origin not in {"official", "custom"}:
        raise ValueError("Console build origin must be official or custom")
    build = {"origin": origin}
    if dev_label or dev_date:
        dev = {"label": dev_label, "date": dev_date}
        issue = dev_build_issue(dev)
        if issue:
            raise ValueError(f"Development build marker is invalid: {issue}")
        build["dev"] = dev

    identity = BuildIdentity(origin=origin, dev_label=dev_label, dev_date=dev_date)
    (assets / "app-build.json").write_text(
        json.dumps(build, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    if origin == "custom" or identity.is_dev_build:
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
    parser.add_argument("--dev-label", default="")
    parser.add_argument("--dev-date", default="")
    args = parser.parse_args()
    stage(args.assets, args.origin, dev_label=args.dev_label, dev_date=args.dev_date)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
