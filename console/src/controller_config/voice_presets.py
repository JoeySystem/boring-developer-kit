"""Single source of truth for voice-key software, presets and guidance.

B-01 / B-05 (2026-10-26 review): every voice entry point — the device-wide
voice key dialog, the per-Profile voice setup guide and the native voice task —
reads its software list, quick presets and setup copy from here, keyed by
``host_platform()``. Nothing outside this module hard-codes ``"macos"`` /
``"darwin"`` for voice behaviour.

Firmware gesture semantics (protocol.md, "NORMAL single/double keyboard
gesture"): a ``key_gesture`` emits a press/release *pulse* on single click
(after the double-click window) and the ``double_usage`` chord on double
click; it never holds a key. A held key is a plain ``key`` action. Hold-to-talk
therefore cannot also double-click to send on the same key with the current
firmware; it sends with the separate send key instead.
"""

from __future__ import annotations

import copy
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

MACOS = "macos"
WINDOWS = "windows"

ENTER = 40
SPACE = 44
F13 = 104
RIGHT_CTRL = 228
RIGHT_ALT = 230  # Right Option on macOS
RIGHT_GUI = 231

# The current default start/stop key for software that records its own
# shortcut (A-16: change it here only).
DEFAULT_VOICE_USAGE = F13
DEFAULT_VOICE_ACTION = {"type": "key_gesture", "usage": DEFAULT_VOICE_USAGE, "double_usage": ENTER}


def host_platform(platform: str | None = None) -> str:
    """``macos`` or ``windows`` for a ``sys.platform`` value (default: this host)."""
    value = sys.platform if platform is None else platform
    if value in {MACOS, "darwin"}:
        return MACOS
    return WINDOWS


def sys_platform_of(platform: str) -> str:
    """Inverse of :func:`host_platform` for code that still takes sys.platform."""
    return "darwin" if host_platform(platform) == MACOS else "win32"


@dataclass(frozen=True)
class VoicePreset:
    """A one-click device action for one voice app on one platform."""

    action: dict
    summary: str
    guide: str
    # Single-click start/stop waits for the double-click window (B-03).
    send: str = "double_click"


@dataclass(frozen=True)
class VoiceSoftware:
    key: str
    label: str
    platforms: frozenset[str]
    presets: dict[str, VoicePreset] = field(default_factory=dict)
    hints: dict[str, str] = field(default_factory=dict)
    # Bundle identifiers of the input method (or of its settings app), per
    # platform (B-06). Values are pending confirmation on a Mac with the app
    # installed: `mdls -name kMDItemCFBundleIdentifier "/Library/Input Methods/"*.app`.
    settings_bundle_ids: dict[str, tuple[str, ...]] = field(default_factory=dict)


_DOUBLE_CLICK_GUIDE_TAIL = (
    "然后在常用 AI 的输入框里，按一下开始说话、再按一下结束；文字出现后双击发送。"
    "目标应用须设为 Enter 发送；其他文字框中的双击也可能提交内容。"
)

SOFTWARE: tuple[VoiceSoftware, ...] = (
    VoiceSoftware(
        "wechat", "微信输入法", frozenset({MACOS}),
        presets={MACOS: VoicePreset(
            DEFAULT_VOICE_ACTION,
            "设备单击 F13 启停微信输入法语音、双击发送 Enter。",
            "在 Mac 选中微信输入法，并在“语音输入”设置中把“启动语音输入”设为 F13；"
            "按系统提示允许辅助功能和麦克风。退出其他同样响应 F13 的语音软件。",
        )},
        hints={MACOS: "先在 Mac 启用微信输入法；打开其设置 → 语音输入 → 启动语音输入，把快捷键设为 F13。"},
        # To be confirmed on a Mac with WeType installed (plan §6, B-06).
        settings_bundle_ids={MACOS: ("com.tencent.inputmethod.wetype",)},
    ),
    VoiceSoftware(
        "typeless", "Typeless", frozenset({MACOS, WINDOWS}),
        presets={platform: VoicePreset(
            DEFAULT_VOICE_ACTION,
            "设备单击 F13 启停 Typeless 听写、双击发送 Enter。",
            "在 Typeless 设置的“Keyboard shortcuts → Dictate”中点“Add another”，按设备语音键录入 F13；"
            "原来的快捷键可以保留。允许麦克风等权限，并退出其他同样响应 F13 的语音软件。",
        ) for platform in (MACOS, WINDOWS)},
        hints={platform: "Typeless 设置 → 快捷键 → 语音输入：查看正在使用的启停快捷键。"
               for platform in (MACOS, WINDOWS)},
    ),
    VoiceSoftware(
        "doubao", "豆包输入法", frozenset({MACOS, WINDOWS}),
        presets={
            MACOS: VoicePreset(
                {"type": "key_gesture", "usage": RIGHT_CTRL, "double_usage": ENTER},
                "设备单击右 Control 启停豆包语音、双击发送 Enter。",
                "在豆包输入法设置 → 语音输入 → 免按模式中，把快捷键设为{shortcut}；"
                "切换到豆包输入法，或开启“全局唤起语音”。按系统提示允许麦克风及相关权限。",
            ),
            # Doubao for Windows only recognises the *right* Alt, in both hold
            # and hands-free mode (confirmed 2026-10-26). Hands-free mode uses
            # right Alt + Space.
            WINDOWS: VoicePreset(
                {"type": "key_gesture", "usage": SPACE, "modifiers": [RIGHT_ALT], "double_usage": ENTER},
                "设备单击发送右 Alt+空格启停豆包语音（免按模式）、双击发送 Enter。",
                "在豆包输入法设置 → 语音输入中选择免按模式，快捷键保持默认的右 Alt+空格；"
                "切换到豆包输入法，或开启全局唤起语音。若装有 PowerToys Run 等同样使用 Alt+空格的工具，"
                "请先改掉它们的快捷键。使用德语、法语等带 AltGr 的键盘布局时，右 Alt 可能无法被豆包识别。",
            ),
        },
        hints={
            MACOS: "在豆包的“语音输入”中开启全局唤起，并完成 macOS 弹出的系统确认。已经设置好可直接继续。",
            WINDOWS: "豆包输入法 Windows 版只识别右侧 Alt：按住模式为右 Alt，免按模式为右 Alt+空格。",
        },
        settings_bundle_ids={MACOS: ("com.bytedance.inputmethod.doubaoime",)},
    ),
    VoiceSoftware(
        "qianwen", "千问输入法", frozenset({MACOS}),
        hints={MACOS: "从 Mac 顶部的输入法菜单打开千问设置 → 语音输入。查看现有快捷键，并打开“短按也唤起语音输入”。"},
    ),
)

OTHER = ("other", "自己设置其他软件")
BY_KEY = {software.key: software for software in SOFTWARE}

# Windows hold-to-talk preset (per-Profile mapping; send with key 9 / key 11).
DOUBAO_WINDOWS_HOLD = {"type": "key", "usage": RIGHT_ALT}


def software_choices(platform: str | None = None) -> tuple[tuple[str, str], ...]:
    """(key, label) choices for this platform, then the custom option."""
    current = host_platform(platform)
    return tuple((s.key, s.label) for s in SOFTWARE if current in s.platforms) + (OTHER,)


def preset(provider: str, platform: str | None = None) -> VoicePreset | None:
    software = BY_KEY.get(provider)
    if software is None:
        return None
    return software.presets.get(host_platform(platform))


def preset_action(provider: str, platform: str | None = None) -> dict | None:
    value = preset(provider, platform)
    return copy.deepcopy(value.action) if value is not None else None


def hint(provider: str, platform: str | None = None) -> str:
    software = BY_KEY.get(provider)
    return software.hints.get(host_platform(platform), "") if software is not None else ""


def preset_matches(provider: str, action: object, platform: str | None = None) -> bool:
    """Whether a saved action is (a compatible variant of) this provider's preset."""
    expected = preset(provider, platform)
    if expected is None or not isinstance(action, dict):
        return False
    if provider == "doubao" and host_platform(platform) == MACOS:
        # macOS Doubao accepts a recorded right Control or right Command.
        return (action.get("type") == "key_gesture"
                and action.get("usage") in {RIGHT_CTRL, RIGHT_GUI} and not action.get("modifiers")
                and action.get("double_usage") == ENTER and not action.get("double_modifiers"))
    return _normalized(action) == _normalized(expected.action)


def _normalized(action: dict) -> dict:
    result = {key: value for key, value in action.items() if value not in ([], None)}
    for key in ("modifiers", "double_modifiers"):
        if key in result:
            result[key] = sorted(result[key])
    return result


def describe_platform(platform: str | None = None) -> str:
    """Platform name for ``describe_action(platform=...)``."""
    return host_platform(platform)


def _mac_bundle_lookup(bundle_id: str) -> Path | None:
    try:  # pragma: no cover - requires pyobjc on macOS
        from AppKit import NSWorkspace
    except Exception:  # noqa: BLE001
        return None
    url = NSWorkspace.sharedWorkspace().URLForApplicationWithBundleIdentifier_(bundle_id)
    return Path(str(url.path())) if url is not None else None


def settings_app(
    provider: str,
    platform: str | None = None,
    *,
    lookup: Callable[[str], Path | None] | None = None,
) -> Path | None:
    """The installed settings app for this provider, or None (B-06).

    Located by bundle identifier, never by a hard-coded path. When nothing is
    found the caller hides its "open settings" button and shows text guidance.
    """
    software = BY_KEY.get(provider)
    if software is None:
        return None
    current = host_platform(platform)
    if current != MACOS:
        return None
    find = lookup or _mac_bundle_lookup
    for bundle_id in software.settings_bundle_ids.get(current, ()):
        path = find(bundle_id)
        if path is None or not path.exists():
            continue
        if "settings" in path.stem.lower():
            return path
        # An input-method bundle ships its settings window as a nested app.
        nested = sorted(
            candidate for depth in ("*", "*/*", "*/*/*")
            for candidate in (path / "Contents").glob(f"{depth}/*.app")
            if "settings" in candidate.stem.lower()
        )
        if nested:
            return nested[0]
    return None


def settings_fallback_text(provider: str, platform: str | None = None) -> str:
    software = BY_KEY.get(provider)
    name = software.label if software is not None else "语音软件"
    if host_platform(platform) == MACOS:
        return f"未找到{name}的设置程序；请从屏幕顶部的输入法菜单打开{name}设置 → 语音输入。"
    return f"请从任务栏的输入法菜单打开{name}设置 → 语音输入。"
