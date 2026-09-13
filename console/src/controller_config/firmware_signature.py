"""Publisher authentication for online firmware manifests.

The trusted key ships with the Console; it is never taken from the feed.
The existing image SHA256, covered by this signature, binds the image bytes.
"""
from __future__ import annotations

import base64
from importlib.resources import files
import json

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


class FirmwareSignatureError(ValueError):
    """A manifest does not authenticate as a BORING firmware publication."""


def manifest_signing_bytes(manifest: dict) -> bytes:
    payload = {key: value for key, value in manifest.items() if key != "signature"}
    return b"BORING-FIRMWARE-MANIFEST-V1\n" + json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def default_firmware_public_key() -> bytes:
    try:
        config = json.loads(files("controller_config.assets").joinpath(
            "firmware-signing.json"
        ).read_text(encoding="utf-8"))
        return base64.b64decode(config["public_key"], validate=True)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise FirmwareSignatureError("固件发布公钥配置无效，请重新安装可信的控制台") from exc


def sign_manifest(manifest: dict, private_key: Ed25519PrivateKey) -> dict:
    if not isinstance(private_key, Ed25519PrivateKey):
        raise FirmwareSignatureError("固件发布密钥必须是 Ed25519 私钥")
    result = dict(manifest)
    result["signature"] = "ed25519-v1:" + base64.b64encode(
        private_key.sign(manifest_signing_bytes(result))
    ).decode("ascii")
    return result


def verify_manifest_signature(manifest: dict, public_key: bytes | None = None) -> None:
    signature = manifest.get("signature")
    if not isinstance(signature, str) or not signature.startswith("ed25519-v1:"):
        raise FirmwareSignatureError("在线固件缺少受支持的发布签名，验签失败，已拒绝更新")
    try:
        key = Ed25519PublicKey.from_public_bytes(
            default_firmware_public_key() if public_key is None else public_key
        )
        key.verify(
            base64.b64decode(signature.removeprefix("ed25519-v1:"), validate=True),
            manifest_signing_bytes(manifest),
        )
    except (InvalidSignature, ValueError, TypeError) as exc:
        raise FirmwareSignatureError("固件发布签名验签失败，文件可能被替换，已拒绝更新") from exc
