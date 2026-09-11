from __future__ import annotations

import json
import shutil
from pathlib import Path

from controller_config.protocol.device_auth import TrustBundle, TrustPolicy


REQUIRED_PROTOCOL_FILES = (
    "protocol.md",
    "config-schema.json",
    "fixtures/manifest.json",
    "fixtures/usb-descriptor-v1.json",
    "fixtures/config-matrix12-power-v2-v1.json",
    "fixtures/capabilities-matrix12-power-v2-v1.json",
)


def stage_protocol_resources(source: Path, destination: Path) -> None:
    """Copy the current authoritative protocol assets into a fresh package stage."""

    source = source.resolve()
    destination = destination.resolve()
    missing = [
        name for name in REQUIRED_PROTOCOL_FILES if not (source / name).is_file()
    ]
    if missing:
        raise ValueError("权威协议目录缺少：" + ", ".join(missing))
    if destination.exists():
        raise ValueError(f"协议资源目标已存在：{destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir()
    for relative_name in REQUIRED_PROTOCOL_FILES:
        target = destination / relative_name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative_name, target)


def stage_production_trust_policy(
    roots_path: Path,
    destination: Path,
) -> None:
    """Validate production roots before marking a native package as production."""

    if destination.exists():
        raise ValueError(f"设备信任策略目标已存在：{destination}")
    bundle = TrustBundle.load(roots_path, policy=TrustPolicy.PRODUCTION)
    if not any(root.purpose == "production" for root in bundle.roots.values()):
        raise ValueError("正式安装包至少需要一个有效的生产信任根")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(
            {"version": 1, "policy": TrustPolicy.PRODUCTION.value},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
