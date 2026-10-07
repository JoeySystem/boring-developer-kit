"""Verify the bundled publisher record and register it with the local Console.

This records an authentic signed release, not a substitute device identity.
No private signing keys are distributed with this tool.
"""
import base64
import json
from pathlib import Path
import subprocess
import sys

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .paths import resource_root

DOMAIN = "com.boring.BORING Controller Config"
KEY = "firmware.official_releases"


def verified_manifest():
    root = resource_root()
    manifest = json.loads((root / "releases/recovery/firmware-manifest.json").read_text())
    public = json.loads((root / "firmware-signing.json").read_text())
    signature = manifest.get("signature", "")
    if not signature.startswith("ed25519-v1:"):
        raise ValueError("恢复固件缺少官方签名，请重新获取工具")
    payload = {k: v for k, v in manifest.items() if k != "signature"}
    message = b"BORING-FIRMWARE-MANIFEST-V1\n" + json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    Ed25519PublicKey.from_public_bytes(base64.b64decode(public["public_key"], validate=True)).verify(
        base64.b64decode(signature[11:], validate=True), message
    )
    if manifest.get("origin") == "custom" or manifest["build_id"].startswith("custom-"):
        raise ValueError("此包不是官方恢复固件")
    return manifest


def register_with_console(build_id, run=subprocess.run):
    manifest = verified_manifest()
    if build_id != manifest["build_id"]:
        raise ValueError("设备回读版本与官方签名记录不同，未登记")
    if sys.platform != "darwin":
        raise ValueError("自动登记官方来源目前仅支持 macOS")
    old = run(["/usr/bin/defaults", "read", DOMAIN, KEY], capture_output=True, text=True, timeout=5)
    # Missing preferences are normal on a Mac where Console has not run yet.
    if old.returncode:
        if "does not exist" not in old.stderr:
            raise ValueError("无法读取 Console 官方记录：" + old.stderr)
        records = []
    else:
        records = json.loads(old.stdout)
        if not isinstance(records, list):
            raise ValueError("Console 官方记录格式异常；保留原记录，请联系售后")
    fields = ("product_id", "hardware_id", "version", "build_id")
    identity = tuple(manifest.get(k) for k in fields)
    records = [m for m in records if not isinstance(m, dict) or tuple(m.get(k) for k in fields) != identity]
    records.append(manifest)
    result = run(["/usr/bin/defaults", "write", DOMAIN, KEY, "-string",
                  json.dumps(records, ensure_ascii=False)], capture_output=True, text=True, timeout=5)
    if result.returncode:
        raise ValueError("固件已恢复，但官方记录写入失败：" + result.stderr)
    readback = run(["/usr/bin/defaults", "read", DOMAIN, KEY], capture_output=True, text=True, timeout=5)
    if readback.returncode or manifest not in json.loads(readback.stdout):
        raise ValueError("固件已恢复，但官方记录读回失败")
    return "已为本机 Console 登记有效官方签名；重新打开 Console 即可识别"
