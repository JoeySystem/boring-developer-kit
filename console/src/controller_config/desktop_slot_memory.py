"""Local memory of which conversation holds which Agent light.

Saved per device (serial), per app and per app account, so a restart keeps the
lights and another account never inherits them (plan 豆包联动V2 §5.7
doubao_slots 小修, acceptance item 2). Holds both the automatic assignment and
the user's pins. Conversation IDs stay in this machine's Console settings; the
account is an opaque hash. No title is ever stored.
"""
from __future__ import annotations

import json
import re

FORMAT_VERSION = 1
SLOTS = 6
_ID = re.compile(r"^\d{1,32}$")
_KEY_PART = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def _key(serial: str, app_id: str, account: str) -> str:
    for part in (serial, app_id, account):
        if not isinstance(part, str) or not _KEY_PART.fullmatch(part):
            raise ValueError("槽位记忆的设备、App 或账号标识无效")
    return f"desktop_agent/{serial}/{app_id}/{account}"


def _valid_id(value) -> bool:
    return isinstance(value, str) and _ID.fullmatch(value) is not None


def load(settings, serial: str, app_id: str, account: str):
    """(slots, pins) or None when nothing valid is saved. A damaged entry is
    ignored (and reported as None) rather than half-applied."""
    raw = settings.value(_key(serial, app_id, account), "")
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if data["version"] != FORMAT_VERSION:
            return None
        slots = tuple(data["slots"])
        pins = {int(slot): cid for slot, cid in data["pins"].items()}
    except (TypeError, KeyError, ValueError, AttributeError):
        return None
    if (len(slots) != SLOTS or any(s is not None and not _valid_id(s) for s in slots)
            or any(not 0 <= slot < SLOTS or not _valid_id(cid) for slot, cid in pins.items())):
        return None
    return slots, pins


def save(settings, serial: str, app_id: str, account: str, slots, pins) -> None:
    slots = tuple(slots)
    if len(slots) != SLOTS or any(s is not None and not _valid_id(s) for s in slots):
        raise ValueError("槽位记忆必须是六个对话 ID 或空")
    if any(not 0 <= slot < SLOTS or not _valid_id(cid) for slot, cid in pins.items()):
        raise ValueError("固定项必须是 0–5 号槽位与对话 ID")
    settings.setValue(_key(serial, app_id, account), json.dumps(
        {"version": FORMAT_VERSION, "slots": list(slots),
         "pins": {str(slot): cid for slot, cid in sorted(pins.items())}}))
    settings.sync()
