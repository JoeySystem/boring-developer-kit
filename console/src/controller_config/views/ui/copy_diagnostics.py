"""「复制诊断信息」: technical details go to the clipboard, not onto the page (design decision).

Paths, internal codes, versions and session lists help the firmware owner and support, not
the person using the page. A page keeps one button; pressing it copies one
block of plain text and a banner says so.

    button = copy_diagnostics_button(lambda: "\\n".join(lines))
    copy_diagnostics(widget, text)   # the same without a button

The copied text starts with diagnostics_header() (Console version, system,
time) so a pasted block says where it came from. It is not translated: it is
read by support, and the same words make reports comparable.

The device's block (Settings › 设备与连接 › 设备信息, design decision) is
console_diagnostics_block(): the same text the self-help diagnosis prompt takes
in place of its `[[BORING_CONSOLE_DIAGNOSTICS]]` line (internal support notes), “Console 自动填入的约定”), so a user who copied it
can paste it into the prompt as is. fill_prompt() does that swap.
"""
from __future__ import annotations

import platform
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QPushButton, QWidget

from controller_config.views.ui.dialog import dialog_button

BUTTON_TEXT = "复制诊断信息"
COPIED_TEXT = "诊断信息已复制，可以粘贴给客服"
BANNER_KEY = "copy-diagnostics"

# The self-help prompt's contract (用户自助诊断提示词-通用版.md). Bump the format to
# v2 when a field changes, and change the prompt's field notes with it.
PROMPT_PLACEHOLDER = "[[BORING_CONSOLE_DIAGNOSTICS]]"
PROMPT_WITHOUT_CONSOLE = "（未带上 Console 诊断信息）"
PROMPT_DIAG_FORMAT = "boring-prompt-diag-v1"
BLOCK_START = "--- BORING Console 诊断（自动填入）---"
BLOCK_END = "--- 诊断结束 ---"


def diagnostics_header(now: datetime | None = None) -> list[str]:
    from controller_config import __version__

    moment = (now or datetime.now()).astimezone().isoformat(timespec="seconds")
    return [f"BORING Console {__version__}", f"系统：{platform.platform()}", f"时间：{moment}"]


def console_diagnostics_block(
    *,
    link: str,
    build_origin: str,
    device_lines: Sequence[str] = (),
    report_text: str = "",
    extras: Sequence[str] = (),
    now: datetime | None = None,
    home: str | None = None,
) -> str:
    """The Console's diagnostics block, laid out as the self-help prompt expects.

    link is usb / ble / none; device_lines are the 设备技术详情 lines (only while
    a device is connected); report_text is DiagnosticsSession.report_text(), whose
    JSON carries connection_timing. The home folder becomes "~" everywhere
    (technical messages can carry local paths).
    """
    lines = [
        BLOCK_START,
        f"format: {PROMPT_DIAG_FORMAT}",
        *diagnostics_header(now),
        f"build_origin: {build_origin}",
        f"link: {link}",
        *device_lines,
    ]
    if report_text.strip():
        lines.append(report_text.rstrip())
    lines.extend(extra.rstrip() for extra in extras if extra.strip())
    lines.append(BLOCK_END)
    text = "\n".join(lines)
    home = str(Path.home()) if home is None else home
    if len(home) > 1:
        # As written, and as it appears inside the JSON (Windows backslashes doubled).
        for spelled in dict.fromkeys((home.replace("\\", "\\\\"), home)):
            text = text.replace(spelled, "~")
    return text


def fill_prompt(prompt: str, block: str | None) -> str:
    """Put `block` where the prompt's placeholder line is (or say it is missing)."""
    filler = block if block else PROMPT_WITHOUT_CONSOLE
    return "\n".join(filler if line.strip() == PROMPT_PLACEHOLDER else line for line in prompt.split("\n"))


def copy_diagnostics(widget: QWidget, text: str) -> None:
    """Put `text` on the clipboard and tell the user with a banner."""
    QGuiApplication.clipboard().setText(text)
    banners = _banners_for(widget)
    if banners is not None:
        banners.notify(COPIED_TEXT, key=BANNER_KEY)


def copy_diagnostics_button(collect: Callable[[], str], *, object_name: str = "copyDiagnostics") -> QPushButton:
    """A text button that copies `collect()` when pressed."""
    button = dialog_button(BUTTON_TEXT, "text", object_name=object_name)
    button.clicked.connect(lambda: copy_diagnostics(button, collect()))
    return button


def _banners_for(widget: QWidget):
    """The main window's BannerStack, also from inside a dialog it owns."""
    current: QWidget | None = widget
    while current is not None:
        banners = getattr(current, "_banners", None)
        if banners is not None:
            return banners
        current = current.parentWidget()
    return None
