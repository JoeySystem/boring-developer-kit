from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication


class HostActionError(RuntimeError):
    """The operating system could not complete one official host action."""


class ClipboardLike(Protocol):
    def text(self) -> str: ...


class HostServices(Protocol):
    platform: str

    def capture_selection(self) -> str: ...

    def read_clipboard(self) -> str: ...

    def append_text_file(self, path: str, text: str, separator: str) -> None: ...

    def open_target(self, target: str) -> None: ...

    def show_notification(self, message: str) -> None: ...


class SystemHostServices:
    """Small OS adapter used only by BORING's built-in actions."""

    _MAC_COPY_SCRIPT = (
        'tell application "System Events" to keystroke "c" using command down'
    )
    _MAC_NOTIFICATION_SCRIPT = (
        'on run argv\n'
        'display notification (item 1 of argv) with title "BORING 控制台"\n'
        "end run"
    )

    def __init__(
        self,
        clipboard: ClipboardLike | None = None,
        *,
        platform: str | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        wait: Callable[[float], None] = time.sleep,
        opener: Callable[[QUrl], bool] = QDesktopServices.openUrl,
        clipboard_sequence: Callable[[], int | None] | None = None,
    ) -> None:
        self._clipboard = clipboard
        self.platform = platform or sys.platform
        self._runner = runner
        self._wait = wait
        self._opener = opener
        self._sequence = clipboard_sequence or self._system_clipboard_sequence

    @property
    def clipboard(self) -> ClipboardLike:
        if self._clipboard is not None:
            return self._clipboard
        application = QApplication.instance()
        if not isinstance(application, QApplication):
            raise HostActionError("系统剪贴板尚未就绪")
        return application.clipboard()

    def capture_selection(self) -> str:
        before = self._sequence()
        self.send_copy_shortcut()
        if before is None:
            self._wait(0.12)
        else:
            changed = False
            for _attempt in range(8):
                self._wait(0.05)
                if self._sequence() != before:
                    changed = True
                    break
            if not changed:
                raise HostActionError(
                    "未检测到新的选中文字。请确认目标应用中已有文字选区，并允许 BORING 控制台使用辅助功能。"
                )
        return self.read_clipboard()

    def selection_sequence(self) -> int | None:
        """Read on the GUI thread, alongside Qt's clipboard."""
        return self._sequence()

    def send_copy_shortcut(self) -> None:
        """May wait for the OS; the asynchronous executor runs this off-thread."""
        if self.platform == "darwin":
            self._run_macos_script(self._MAC_COPY_SCRIPT, "复制当前选中文字")
        elif self.platform == "win32":
            self._send_windows_copy_shortcut()
        else:
            raise HostActionError("当前系统尚不支持获取前台选中文字")

    def read_clipboard(self) -> str:
        text = self.clipboard.text()
        if not text:
            raise HostActionError("剪贴板中没有可用文字")
        return text

    def append_text_file(self, path: str, text: str, separator: str) -> None:
        if not text:
            raise HostActionError("当前工作流没有可追加的文字")
        target = Path(path).expanduser()
        if target.suffix.lower() not in {".md", ".txt"}:
            raise HostActionError("追加目标必须是 .md 或 .txt 文件")
        if not target.parent.is_dir():
            raise HostActionError(f"目标目录不存在：{target.parent}")
        try:
            needs_separator = target.exists() and target.stat().st_size > 0
            with target.open("a", encoding="utf-8", newline="") as output:
                if needs_separator:
                    output.write(separator)
                output.write(text)
        except OSError as exc:
            raise HostActionError(f"无法追加到文件：{exc}") from exc

    def open_target(self, target: str) -> None:
        self.open_prepared_target(self.prepare_open_target(target), target)

    def prepare_open_target(self, target: str) -> QUrl:
        """Resolve local paths off-thread before asking Qt to open them."""
        value = target.strip()
        if value.startswith(("http://", "https://")):
            url = QUrl(value)
        else:
            path = Path(value).expanduser()
            if not path.exists():
                raise HostActionError(f"要打开的目标不存在：{path}")
            url = QUrl.fromLocalFile(str(path.resolve()))
        return url

    def open_prepared_target(self, url: QUrl, target: str) -> None:
        if not self._opener(url):
            raise HostActionError(f"系统无法打开目标：{target}")

    def show_notification(self, message: str) -> None:
        if self.platform == "darwin":
            self._run_macos_script(
                self._MAC_NOTIFICATION_SCRIPT,
                "显示系统通知",
                arguments=(message,),
            )
            return
        if self.platform == "win32":
            # The packaged Windows build has no guaranteed notification center
            # bridge yet. Keep the workflow result in BORING's run history.
            raise HostActionError("Windows 系统通知适配尚未完成")
        raise HostActionError("当前系统尚不支持 BORING 系统通知")

    def _run_macos_script(
        self,
        script: str,
        operation: str,
        *,
        arguments: tuple[str, ...] = (),
    ) -> None:
        try:
            command = ["/usr/bin/osascript", "-e", script]
            if arguments:
                command.extend(("--", *arguments))
            completed = self._runner(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=3,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise HostActionError(f"无法{operation}：{exc}") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "系统拒绝操作").strip()
            raise HostActionError(
                f"无法{operation}。请允许 BORING 控制台使用辅助功能：{detail}"
            )

    def _system_clipboard_sequence(self) -> int | None:
        if self.platform == "darwin":
            try:
                from AppKit import NSPasteboard

                return int(NSPasteboard.generalPasteboard().changeCount())
            except (ImportError, AttributeError, TypeError):
                return None
        if self.platform == "win32":
            try:
                return int(ctypes.windll.user32.GetClipboardSequenceNumber())
            except (AttributeError, OSError, TypeError):
                return None
        return None

    @staticmethod
    def _send_windows_copy_shortcut() -> None:
        try:
            user32 = ctypes.windll.user32
            key_up = 0x0002
            user32.keybd_event(0x11, 0, 0, 0)  # Control down
            user32.keybd_event(0x43, 0, 0, 0)  # C down
            user32.keybd_event(0x43, 0, key_up, 0)
            user32.keybd_event(0x11, 0, key_up, 0)
        except (AttributeError, OSError) as exc:
            raise HostActionError(f"无法发送 Windows 复制快捷键：{exc}") from exc
