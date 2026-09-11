from __future__ import annotations

import subprocess
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


class MacClipboardPaster:
    """Insert exact Unicode text through the macOS clipboard and Command+V.

    The AppleScript intentionally contains no Return/Enter keystroke. The
    prompt is left on the clipboard after insertion so the helper never races
    the receiving application by restoring older clipboard data too early.
    """

    _PASTE_SCRIPT = 'tell application "System Events" to keystroke "v" using command down'

    def __init__(
        self,
        clipboard: Clipboard,
        *,
        platform: str | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self._clipboard = clipboard
        self._platform = platform or sys.platform
        self._runner = runner

    def paste(self, text: str) -> int:
        if self._platform != "darwin":
            raise PromptPasteError("当前后台提示词助手第一阶段只支持 macOS")
        if not text:
            raise PromptPasteError("提示词正文为空，未执行粘贴")
        self._clipboard.setText(text)
        if self._clipboard.text() != text:
            raise PromptPasteError("未能把完整提示词写入系统剪贴板")
        try:
            completed = self._runner(
                ["/usr/bin/osascript", "-e", self._PASTE_SCRIPT],
                check=False,
                capture_output=True,
                text=True,
                timeout=3,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise PromptPasteError(f"无法调用 macOS 粘贴服务：{exc}") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "系统拒绝全局粘贴").strip()
            raise PromptPasteError(
                "提示词已复制到剪贴板，但无法粘贴到当前输入框。"
                f"请允许 BORING 控制台使用辅助功能：{detail}"
            )
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
