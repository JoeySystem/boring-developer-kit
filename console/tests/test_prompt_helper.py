from __future__ import annotations

import subprocess

import pytest

from controller_config.prompt_helper import (
    MacClipboardPaster,
    PromptHelperRuntime,
    PromptPasteError,
)
from controller_config.prompt_library import PromptEntry, PromptLibrary


class FakeClipboard:
    def __init__(self) -> None:
        self.value = ""

    def setText(self, text: str) -> None:  # noqa: N802 - Qt-compatible fake
        self.value = text

    def text(self) -> str:
        return self.value


def test_mac_helper_pastes_exact_utf8_without_enter() -> None:
    clipboard = FakeClipboard()
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "", "")

    paster = MacClipboardPaster(clipboard, platform="darwin", runner=runner)
    text = "请解释这段代码。\n先给结论，不要自动提交。"

    body_bytes = paster.paste(text)

    assert clipboard.value == text
    assert body_bytes == len(text.encode("utf-8"))
    command, options = calls[0]
    assert command[:2] == ["/usr/bin/osascript", "-e"]
    assert 'keystroke "v"' in command[2]
    assert "return" not in command[2].lower()
    assert "enter" not in command[2].lower()
    assert options["timeout"] == 3


def test_mac_helper_reports_accessibility_failure_but_keeps_clipboard_text() -> None:
    clipboard = FakeClipboard()

    def runner(command, **_kwargs):
        return subprocess.CompletedProcess(command, 1, "", "not authorized")

    paster = MacClipboardPaster(clipboard, platform="darwin", runner=runner)

    with pytest.raises(PromptPasteError, match="辅助功能"):
        paster.paste("中文提示词")

    assert clipboard.value == "中文提示词"


def test_non_macos_helper_is_explicitly_unavailable() -> None:
    with pytest.raises(PromptPasteError, match="只支持 macOS"):
        MacClipboardPaster(FakeClipboard(), platform="win32").paste("text")


def test_runtime_uses_device_confirmed_prompt_not_unsent_draft() -> None:
    library = PromptLibrary(
        "CP01-AABBCCDDEEFF",
        confirmed=(PromptEntry(1, "已确认", "设备正文"),),
    )
    library.set_draft(1, "草稿", "本地未写入正文")
    clipboard = FakeClipboard()
    paster = MacClipboardPaster(
        clipboard,
        platform="darwin",
        runner=lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "", ""),
    )

    result = PromptHelperRuntime(paster).handle_trigger(library, 1)

    assert clipboard.value == "设备正文"
    assert result.prompt_id == 1


def test_runtime_rejects_prompt_missing_from_confirmed_readback() -> None:
    library = PromptLibrary("CP01-AABBCCDDEEFF")
    library.set_draft(1, "草稿", "未写入")
    paster = MacClipboardPaster(FakeClipboard(), platform="darwin")

    with pytest.raises(PromptPasteError, match="没有它的已确认读回内容"):
        PromptHelperRuntime(paster).handle_trigger(library, 1)
