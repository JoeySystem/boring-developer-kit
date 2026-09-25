"""Common AI choices and device profiles; selections are distinct from activation."""
from __future__ import annotations

import copy
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from controller_config.official_controls import MATRIX12_HARDWARE_IDS
from controller_config.profile_templates import agent_profile_mappings

AI_SELECTION_KEY = "ui/common_ai_selection"
AI_DEVICE_KEY = "ui/common_ai_devices"
AI_SETUP_COMPLETED_KEY = "ui/ai_setup_completed"
AI_SETUP_STARTED_KEY = "ui/ai_setup_started"


@dataclass(frozen=True)
class AIApplication:
    key: str
    name: str
    template: str
    url: str


AI_APPLICATIONS = (
    AIApplication("codex", "Codex / ChatGPT", "basic", "https://chatgpt.com"),
    AIApplication("claude", "Claude", "basic", "https://claude.ai"),
    AIApplication("doubao", "豆包", "doubao", "https://www.doubao.com"),
    AIApplication("qwen-work", "千问办公", "qwen-work", "https://qwenwork.cn/"),
    AIApplication("workbuddy", "WorkBuddy", "workbuddy", "https://www.workbuddy.cn/work/"),
    AIApplication("deepseek-harness", "DeepSeek Harness", "basic", "https://www.deepseek.com/harness/"),
)
AI_BY_KEY = {app.key: app for app in AI_APPLICATIONS}
VOICE_ACTION = {"type": "key_gesture", "usage": 104, "double_usage": 40}


def installed_desktop_app(name):
    if sys.platform == "darwin":
        names = ("Codex", "ChatGPT") if name == "Codex / ChatGPT" else (name,)
        for candidate in names:
            for folder in (Path("/Applications"), Path.home() / "Applications"):
                path = folder / f"{candidate}.app"
                if path.is_dir():
                    return path
    return None


def load_choices(settings):
    if settings is None:
        return []
    value = settings.value(AI_SELECTION_KEY, [], type=list)
    value = ["codex" if key == "chatgpt" else key for key in value]
    return list(dict.fromkeys(key for key in value if key in AI_BY_KEY))


def load_device_choices(settings, serial):
    if settings is None:
        return {}
    try:
        devices = json.loads(settings.value(AI_DEVICE_KEY, "{}"))
        entries = devices.get(serial, {}) if isinstance(devices, dict) else {}
        # Reuse an existing ChatGPT profile for the combined choice. Keep both
        # saved entries if present; merging the UI must never delete a profile.
        if "codex" not in entries and "chatgpt" in entries:
            entries["codex"] = copy.deepcopy(entries["chatgpt"])
        return entries
    except (ValueError, TypeError):
        return {}


def save_device_choices(settings, serial, entries):
    if settings is None:
        return
    try:
        devices = json.loads(settings.value(AI_DEVICE_KEY, "{}"))
    except (ValueError, TypeError):
        devices = {}
    if not isinstance(devices, dict):
        devices = {}
    devices[serial] = entries
    settings.setValue(AI_DEVICE_KEY, json.dumps(devices, ensure_ascii=False))
    settings.sync()


def linked_profile(draft, entry):
    if not isinstance(entry, dict):
        return None
    return next((p for p in draft.profiles
                 if p.get("id") == entry.get("profile_id")
                 and p.get("name") == entry.get("profile_name")), None)


def prepare_ai_profiles(draft, selected, current, voice, saved, *, platform):
    """Prepare on a copy so capacity or capability failure cannot leave half a set."""
    if draft.hardware_id not in MATRIX12_HARDWARE_IDS:
        raise ValueError("这台设备暂不支持快捷设置，请使用现有按键配置。")
    if not selected or current not in selected or any(key not in AI_BY_KEY for key in selected):
        raise ValueError("请选择常用 AI，并选择先试用哪一个。")
    if voice not in {"none", "external", "native"}:
        raise ValueError("请选择语音输入方式。")
    if voice == "external" and "key_gesture" not in draft.actions:
        raise ValueError("当前固件不支持语音键的单击和双击，请先更新固件或暂不设置语音。")
    candidate = copy.deepcopy(draft)
    new_count = sum(linked_profile(draft, saved.get(key)) is None for key in set(selected))
    if len(draft.profiles) + new_count > draft.max_profiles:
        raise ValueError("设备配置空间不足，请减少本次添加的 AI，或先整理已有配置。")
    entries = copy.deepcopy(saved)
    for key in dict.fromkeys(selected):
        app = AI_BY_KEY[key]
        profile = linked_profile(candidate, saved.get(key))
        if profile is None:
            suffix = "macOS" if platform == "darwin" else "Windows"
            name = candidate._unique_profile_name(f"{app.name} 日常 {suffix}")
            mappings = agent_profile_mappings(
                app.template,
                platform=platform, hardware_id=draft.hardware_id,
            )
            # Empty modifiers are optional in the protocol. Omit them to leave
            # more device configuration space for the user's profiles.
            for mapping in mappings:
                action = mapping["action"]
                if action.get("modifiers") == []:
                    action.pop("modifiers")
            profile_id = candidate.create_profile_from_template(name, mappings)
            profile = candidate.profile(profile_id)
        entries[key] = {**entries.get(key, {}), "profile_id": profile["id"], "profile_name": profile["name"]}
        # Voice is a per-application choice. Other selected applications keep theirs.
        if key == current:
            previous_voice = entries[key].get("voice", "none")
            previous_action = copy.deepcopy(candidate.mapping(profile["id"], "key.8")["action"])
            if voice == "external":
                old = candidate.mapping(profile["id"], "key.8")
                if old["action"].get("type") != "key_gesture":
                    entries[key]["before_voice"] = copy.deepcopy(old)
                    candidate.set_mapping(profile["id"], "key.8", "语音输入", VOICE_ACTION)
            elif entries[key].get("before_voice"):
                old = entries[key].get("before_voice")
                actual = candidate.mapping(profile["id"], "key.8")
                # Restore only our own preset, never a later custom shortcut.
                if old and actual["action"] == entries[key].get("voice_action", VOICE_ACTION):
                    candidate.set_mapping(profile["id"], "key.8", old["short_name"], old["action"])
            entries[key]["voice"] = voice
            current_action = candidate.mapping(profile["id"], "key.8")["action"]
            if voice != "external" and (current_action.get("type") != "key" or current_action.get("usage") != 40):
                raise ValueError("发送键已被自定义，请先在按键配置中调整，或继续使用原来的语音方式。")
            if previous_voice != voice or previous_action != candidate.mapping(profile["id"], "key.8")["action"]:
                entries[key]["tried"] = False
    candidate.set_active_profile(entries[current]["profile_id"])
    return candidate.config, entries
