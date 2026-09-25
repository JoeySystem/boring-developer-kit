from __future__ import annotations

import copy
from dataclasses import dataclass

from controller_config.official_controls import (
    MATRIX12_AGENT_STATUS_KEYS,
    MATRIX12_HARDWARE_IDS,
)


@dataclass(frozen=True)
class AgentProfileTemplate:
    key: str
    profile_name: str
    menu_label: str


_AGENT_PROFILE_LABELS = (
    ("workbuddy", "WorkBuddy", "WorkBuddy"),
    ("qwen-work", "千问办公", "千问办公"),
    ("doubao", "豆包", "豆包（基础）"),
)


def agent_profile_templates_for_platform(platform: str) -> tuple[AgentProfileTemplate, ...]:
    platform_label = _platform_label(platform)
    if platform_label is None:
        return ()
    return tuple(
        AgentProfileTemplate(key, f"{profile_name} {platform_label}", menu_label)
        for key, profile_name, menu_label in _AGENT_PROFILE_LABELS
    )


def agent_profile_mappings(
    template_key: str, *, platform: str, hardware_id: str = ""
) -> list[dict]:
    primary_modifier = _primary_modifier(platform)
    assignments = _common_assignments(
        primary_modifier,
        windows=platform.startswith("win"),
    )
    if template_key == "workbuddy":
        assignments.update(
            {
                "key.4": ("发送", _key(40)),
                "key.6": ("换行", _key(40, primary_modifier)),
            }
        )
    elif template_key == "qwen-work":
        assignments.update(
            {
                "key.1": ("新建任务", _key(17, primary_modifier)),
                "key.2": ("搜索全部任务", _key(10, primary_modifier)),
                "key.4": ("发送", _key(40)),
                "key.6": ("换行", _key(40, 225)),
                "key.7": ("快速切换任务", _key(43, 224)),
                "joystick.left": ("切换侧边栏", _key(49, primary_modifier)),
                "joystick.right": ("任务监控", _key(56, primary_modifier)),
                "joystick.press": ("打开设置", _key(54, primary_modifier)),
            }
        )
    elif template_key in {"doubao", "basic"}:
        assignments.update(
            {
                "key.4": ("发送", _key(40)),
                "key.6": ("换行", _key(40, 225)),
            }
        )
    else:
        raise ValueError(f"未知的 Agent 方案：{template_key}")

    if hardware_id in MATRIX12_HARDWARE_IDS:
        # NORMAL mode routes these six keys to Codex before profile mappings.
        # Keep their required config slots empty and put the working shortcuts
        # on controls that the firmware actually dispatches from the profile.
        send = assignments["key.4"]
        newline = assignments["key.6"]
        undo = assignments["key.9"]
        redo = assignments["key.10"]
        assignments.update(
            {
                "key.8": send,
                "key.9": newline,
                "key.10": undo,
                "joystick.left": redo,
                "joystick.right": ("空格", _key(44)),
                "joystick.press": ("取消", _key(41)),
            }
        )
        if template_key == "qwen-work":
            assignments.update(
                {
                    "joystick.up": assignments["key.1"],
                    "joystick.down": assignments["key.2"],
                    "joystick.left": ("切换侧边栏", _key(49, primary_modifier)),
                    "joystick.right": assignments["key.7"],
                }
            )
        for control_id in MATRIX12_AGENT_STATUS_KEYS:
            assignments[control_id] = ("", _NONE)

    return [
        {
            "control_id": control_id,
            "short_name": short_name,
            "action": copy.deepcopy(action),
        }
        for control_id, (short_name, action) in assignments.items()
    ]


def is_other_platform_profile(profile_name: str, *, platform: str) -> bool:
    normalized = profile_name.strip().casefold()
    if platform == "darwin":
        return normalized.endswith(" windows")
    if platform.startswith("win"):
        return normalized.endswith(" macos")
    return False


def _platform_label(platform: str) -> str | None:
    if platform == "darwin":
        return "macOS"
    if platform.startswith("win"):
        return "Windows"
    return None


def _primary_modifier(platform: str) -> int:
    if platform == "darwin":
        return 227
    if platform.startswith("win"):
        return 224
    raise ValueError(f"不支持的快捷键平台：{platform}")


def _key(usage: int, *modifiers: int) -> dict:
    return {"type": "key", "usage": usage, "modifiers": list(modifiers)}


_NONE = {"type": "none"}
_SCROLL_UP = {
    "type": "mouse",
    "button": 0,
    "x": 0,
    "y": 0,
    "wheel": 1,
    "pan": 0,
}
_SCROLL_DOWN = {
    "type": "mouse",
    "button": 0,
    "x": 0,
    "y": 0,
    "wheel": -1,
    "pan": 0,
}

def _common_assignments(
    primary_modifier: int, *, windows: bool
) -> dict[str, tuple[str, dict]]:
    return {
        "key.1": ("", _NONE),
        "key.2": ("", _NONE),
        "key.3": ("删除", _key(42)),
        "key.4": ("发送", _key(40)),
        "key.5": ("取消", _key(41)),
        "key.6": ("换行", _key(40, 225)),
        "key.7": ("", _NONE),
        "key.8": ("空格", _key(44)),
        "key.9": ("撤销", _key(29, primary_modifier)),
        "key.10": (
            "重做",
            _key(28, primary_modifier)
            if windows
            else _key(29, 225, primary_modifier),
        ),
        "key.11": ("复制", _key(6, primary_modifier)),
        "key.12": ("粘贴", _key(25, primary_modifier)),
        "encoder.ccw": ("逆时针", _SCROLL_UP),
        "encoder.cw": ("顺时针", _SCROLL_DOWN),
        "encoder.press": ("到末尾", _key(77)),
        "joystick.up": ("向上翻页", _key(75)),
        "joystick.down": ("向下翻页", _key(78)),
        "joystick.left": ("", _NONE),
        "joystick.right": ("", _NONE),
        "joystick.press": ("", _NONE),
    }
