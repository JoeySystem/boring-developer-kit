from __future__ import annotations

import subprocess

import pytest

from controller_config.host_selection import HostActionError, SystemHostServices


class Clipboard:
    def __init__(self, text: str = "") -> None:
        self.value = text

    def text(self) -> str:
        return self.value


def test_macos_capture_reads_exact_unicode_after_copy_shortcut() -> None:
    clipboard = Clipboard("第一行中文\nSecond line")
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "", "")

    services = SystemHostServices(
        clipboard,
        platform="darwin",
        runner=runner,
        wait=lambda _seconds: None,
        clipboard_sequence=iter((1, 2)).__next__,
    )

    assert services.capture_selection() == "第一行中文\nSecond line"
    assert calls[0][0][:3] == ["/usr/bin/osascript", "-e", services._MAC_COPY_SCRIPT]


def test_capture_permission_failure_is_actionable() -> None:
    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, "", "not authorized")

    services = SystemHostServices(
        Clipboard("old"),
        platform="darwin",
        runner=runner,
        wait=lambda _seconds: None,
    )

    with pytest.raises(HostActionError, match="辅助功能"):
        services.capture_selection()


def test_capture_does_not_reuse_stale_clipboard_when_selection_never_updates() -> None:
    services = SystemHostServices(
        Clipboard("旧剪贴板"),
        platform="darwin",
        runner=lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, "", ""
        ),
        wait=lambda _seconds: None,
        clipboard_sequence=lambda: 7,
    )

    with pytest.raises(HostActionError, match="未检测到新的选中文字"):
        services.capture_selection()


def test_append_preserves_utf8_and_uses_separator_only_for_existing_content(
    tmp_path,
) -> None:
    target = tmp_path / "notes.md"
    services = SystemHostServices(Clipboard(), platform="darwin")

    services.append_text_file(str(target), "第一段", "\n\n")
    services.append_text_file(str(target), "Second paragraph", "\n\n")

    assert target.read_text(encoding="utf-8") == "第一段\n\nSecond paragraph"


def test_append_rejects_empty_text_and_missing_directory(tmp_path) -> None:
    services = SystemHostServices(Clipboard(), platform="darwin")
    with pytest.raises(HostActionError, match="没有可追加"):
        services.append_text_file(str(tmp_path / "notes.md"), "", "\n")
    with pytest.raises(HostActionError, match="目标目录不存在"):
        services.append_text_file(
            str(tmp_path / "missing" / "notes.md"), "text", "\n"
        )


def test_open_target_rejects_missing_local_path_and_reports_os_failure(tmp_path) -> None:
    services = SystemHostServices(
        Clipboard(), platform="darwin", opener=lambda _url: False
    )
    with pytest.raises(HostActionError, match="不存在"):
        services.open_target(str(tmp_path / "missing"))
    with pytest.raises(HostActionError, match="无法打开"):
        services.open_target("https://example.com")
