from __future__ import annotations

import ctypes
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from controller_config.prompt_library import PromptEntry, PromptLibrary


class Clipboard(Protocol):
    def setText(self, text: str) -> None: ...  # noqa: N802 - Qt API

    def text(self) -> str: ...


class PromptPasteError(RuntimeError):
    """The host helper could not insert one confirmed device prompt."""


@dataclass(frozen=True)
class PromptPasteResult:
    prompt_id: int
    body_bytes: int


def _send_mac_paste_shortcut() -> None:
    """Post Command+V from Console itself, not an independently authorized osascript."""
    core_graphics = ctypes.CDLL(
        "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
    )
    core_graphics.CGPreflightPostEventAccess.restype = ctypes.c_bool
    if not core_graphics.CGPreflightPostEventAccess():
        raise PromptPasteError(
            "当前运行的 BORING Console 没有发送按键的权限。"
            "请在 macOS 辅助功能中重新添加当前安装版，并重启控制台。"
        )

    core_graphics.CGEventCreateKeyboardEvent.argtypes = (
        ctypes.c_void_p, ctypes.c_uint16, ctypes.c_bool
    )
    core_graphics.CGEventCreateKeyboardEvent.restype = ctypes.c_void_p
    core_graphics.CGEventSetFlags.argtypes = (ctypes.c_void_p, ctypes.c_uint64)
    core_graphics.CGEventPost.argtypes = (ctypes.c_uint32, ctypes.c_void_p)
    core_foundation = ctypes.CDLL(
        "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
    )
    core_foundation.CFRelease.argtypes = (ctypes.c_void_p,)

    # macOS virtual key 9 is V; the Command flag makes it a paste shortcut.
    for key_down in (True, False):
        event = core_graphics.CGEventCreateKeyboardEvent(None, 9, key_down)
        if not event:
            raise PromptPasteError("无法创建 macOS 粘贴按键事件")
        try:
            core_graphics.CGEventSetFlags(event, 1 << 20)
            core_graphics.CGEventPost(0, event)
        finally:
            core_foundation.CFRelease(event)


class MacClipboardPaster:
    """Insert exact Unicode text through the macOS clipboard and Command+V.

    The AppleScript intentionally contains no Return/Enter keystroke. The
    prompt is left on the clipboard after insertion so the helper never races
    the receiving application by restoring older clipboard data too early.
    """

    def __init__(
        self,
        clipboard: Clipboard,
        *,
        platform: str | None = None,
        send_paste_shortcut: Callable[[], None] = _send_mac_paste_shortcut,
    ) -> None:
        self._clipboard = clipboard
        self._platform = platform or sys.platform
        self._send_paste_shortcut = send_paste_shortcut

    def paste(self, text: str) -> int:
        if self._platform != "darwin":
            raise PromptPasteError("当前后台提示词助手第一阶段只支持 macOS")
        if not text:
            raise PromptPasteError("提示词正文为空，未执行粘贴")
        self._clipboard.setText(text)
        if self._clipboard.text() != text:
            raise PromptPasteError("未能把完整提示词写入系统剪贴板")
        try:
            self._send_paste_shortcut()
        except OSError as exc:
            raise PromptPasteError(f"无法调用 macOS 粘贴服务：{exc}") from exc
        return len(text.encode("utf-8"))


class PromptHelperRuntime:
    def __init__(self, paster: MacClipboardPaster) -> None:
        self._paster = paster

    def handle_trigger(
        self, library: PromptLibrary, prompt_id: int
    ) -> PromptPasteResult:
        entry = library.confirmed_entry(prompt_id)
        if entry is None:
            raise PromptPasteError(
                f"设备触发了提示词 {prompt_id}，但电脑端没有它的已确认读回内容"
            )
        return self.handle_entry(entry)

    def handle_entry(self, entry: PromptEntry) -> PromptPasteResult:
        """Paste the exact prompt returned for one physical trigger event."""
        entry.validate()
        body_bytes = self._paster.paste(entry.body)
        return PromptPasteResult(prompt_id=entry.prompt_id, body_bytes=body_bytes)
