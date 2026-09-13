"""Validate staged Windows desktop-update settings and packaging policy."""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
from urllib.parse import urlparse

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


def validate(
    path: Path,
    *,
    skip_installer: bool = False,
    has_signing_certificate: bool = False,
) -> str:
    document = json.loads(path.read_text(encoding="utf-8-sig"))
    config = document["windows"]
    channel = config.get("channel", "stable")
    if channel not in {"stable", "trial"}:
        raise ValueError("Windows desktop update channel must be stable or trial")

    url = config.get("feed_url", "")
    key = config.get("public_key", "")
    if url or key:
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise ValueError("Windows desktop updates require an HTTPS feed URL")
        try:
            Ed25519PublicKey.from_public_bytes(base64.b64decode(key, validate=True))
        except (TypeError, ValueError) as exc:
            raise ValueError("Windows desktop updates require an Ed25519 public key") from exc

    updates_enabled = bool(url)
    if channel == "trial" and not updates_enabled:
        raise ValueError("Windows trial updates require a feed URL and public key")
    if updates_enabled and skip_installer:
        raise ValueError("Enabled Windows desktop updates require an installer build")
    if updates_enabled and channel == "stable" and not has_signing_certificate:
        raise ValueError("Stable Windows desktop updates require an Authenticode certificate")

    path.write_text(json.dumps(document), encoding="utf-8")
    return channel


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    parser.add_argument("--skip-installer", action="store_true")
    parser.add_argument("--has-signing-certificate", action="store_true")
    args = parser.parse_args()
    print(
        validate(
            args.config,
            skip_installer=args.skip_installer,
            has_signing_certificate=args.has_signing_certificate,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
