"""Set Key 8 to the BORING voice/send default on every unit this tool services.

Key 8 in both factory Profiles (Codex macOS / Codex Windows): a click sends
Right Option/Alt + Space (start or stop speaking), a double click sends Enter.
This is the factory value since 20261030.01, so later firmware migrations treat it
as untouched. A saved root ``voice_action`` would override Key 8 in Normal and
Codex for every Profile, so it is removed to restore the default routing.
User-created Profiles and the Codex-only ``codex_voice`` override are kept.

The change goes through the normal configuration transaction (full config,
digest, base generation), waits for activation and reads the result back.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import time
from typing import Callable

from .errors import VerificationError

SET_CONFIG = 0x12

FACTORY_PROFILES = ("Codex macOS", "Codex Windows")
VOICE_KEY_MAPPING = {
    "control_id": "key.8",
    "short_name": "Voice/Send",
    "action": {
        "type": "key_gesture",
        "usage": 44,          # Space
        "modifiers": [230],   # Right Alt / Right Option
        "double_usage": 40,   # Enter
        "double_modifiers": [],
    },
}


def config_digest(config: dict) -> str:
    """The digest firmware and Console use: SHA-256 of canonical JSON."""
    body = json.dumps(
        config, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def apply_voice_key_default(config: dict) -> tuple[dict, list[str]]:
    """Return (new config, human-readable changes); the input is not modified."""
    if not isinstance(config, dict) or not isinstance(config.get("profiles"), list):
        raise VerificationError("设备配置缺少 Profile 列表，无法设置第 8 键")
    updated = deepcopy(config)
    changes: list[str] = []
    found = set()
    for profile in updated["profiles"]:
        name = profile.get("name") if isinstance(profile, dict) else None
        if name not in FACTORY_PROFILES:
            continue
        found.add(name)
        mappings = profile.get("mappings")
        if not isinstance(mappings, list):
            raise VerificationError(f"Profile「{name}」的按键映射无效")
        index = next(
            (i for i, item in enumerate(mappings)
             if isinstance(item, dict) and item.get("control_id") == "key.8"),
            None,
        )
        if index is None:
            mappings.append(deepcopy(VOICE_KEY_MAPPING))
            changes.append(f"{name}：新增第 8 键")
        elif mappings[index] != VOICE_KEY_MAPPING:
            old = mappings[index].get("short_name") or "未命名"
            mappings[index] = deepcopy(VOICE_KEY_MAPPING)
            changes.append(f"{name}：第 8 键「{old}」改为「Voice/Send」")
    missing = [name for name in FACTORY_PROFILES if name not in found]
    if missing:
        raise VerificationError(f"设备配置里没有出厂 Profile：{'、'.join(missing)}")
    if "voice_action" in updated:
        updated.pop("voice_action")
        changes.append("移除全局语音键（voice_action），恢复按 Profile 生效")
    return updated, changes


def key8_matches_default(config: dict) -> bool:
    try:
        _, changes = apply_voice_key_default(config)
    except VerificationError:
        return False
    return not changes


def ensure_voice_key_default(
    client,
    *,
    timeout_s: float = 10.0,
    poll_s: float = 0.25,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
) -> dict:
    """Make Key 8 the default on the connected device; return a receipt."""
    current = client.get_config()
    config = current.get("config")
    generation = current.get("generation")
    digest = current.get("digest")
    if not isinstance(generation, int) or isinstance(generation, bool):
        raise VerificationError("GET_CONFIG 未返回有效 generation")
    if digest != config_digest(config):
        raise VerificationError("设备配置摘要与工具计算不一致，未写入第 8 键设置")
    updated, changes = apply_voice_key_default(config)
    if not changes:
        return {"changed": False, "changes": [], "generation": generation}

    new_digest = config_digest(updated)
    reply = client.exchange(
        SET_CONFIG,
        {"config": updated, "digest": new_digest, "base_generation": generation},
    )
    result = reply.get("result") if isinstance(reply, dict) else None
    if reply.get("command") != "SET_CONFIG" or not isinstance(result, dict):
        raise VerificationError("设备未返回有效的 SET_CONFIG 响应")
    expected_generation = result.get("generation")
    if result.get("digest") != new_digest or expected_generation != generation + 1:
        raise VerificationError("设备暂存的第 8 键配置与写入内容不一致")

    deadline = now() + timeout_s
    status: dict = {}
    while True:
        status = client.get_status()
        active = status.get("active") or {}
        if (status.get("pending") is None
                and active.get("generation") == expected_generation
                and active.get("digest") == new_digest):
            break
        if status.get("activation_failed"):
            raise VerificationError("第 8 键配置写入后激活失败，请重新开始")
        if now() >= deadline:
            hint = "请松开所有按键和摇杆" if status.get("inputs_neutral") is False else "请重新开始"
            raise VerificationError(f"第 8 键配置已写入但 {timeout_s:g} 秒内未生效，{hint}")
        sleep(poll_s)

    readback = client.get_config()
    if (readback.get("generation") != expected_generation
            or readback.get("digest") != new_digest
            or not key8_matches_default(readback.get("config"))):
        raise VerificationError("读回的第 8 键配置与写入内容不一致")
    return {
        "changed": True,
        "changes": changes,
        "previous_generation": generation,
        "generation": expected_generation,
        "digest": new_digest,
    }


__all__ = [
    "FACTORY_PROFILES",
    "VOICE_KEY_MAPPING",
    "apply_voice_key_default",
    "config_digest",
    "ensure_voice_key_default",
    "key8_matches_default",
]
