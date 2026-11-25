"""设备信息卡「复制诊断信息」(design decision, 单 design ticket 第 2 件).

The same button as the Doubao and Claude cards. What it copies is the block the
self-help diagnosis prompt takes in place of its `[[BORING_CONSOLE_DIAGNOSTICS]]`
line (internal support notes), with the connection's
per-step timing (PR #98) in it.
"""
from __future__ import annotations

import json
import time
from dataclasses import replace
from datetime import datetime, timezone

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QPushButton

from controller_config import __version__
from controller_config.connect_timing import TimingMark
from controller_config.views.ui.copy_diagnostics import (
    BLOCK_END,
    BLOCK_START,
    PROMPT_PLACEHOLDER,
    PROMPT_WITHOUT_CONSOLE,
    console_diagnostics_block,
    fill_prompt,
)
from test_session_recovery import session  # noqa: F401


def _copy_button(window, qtbot):
    def visible():
        return next((button for button in window.findChildren(QPushButton, "copyDeviceDiagnostics")
                     if button.isVisible()), None)

    qtbot.waitUntil(lambda: visible() is not None, timeout=2000)
    return visible()


def _block_json(text: str) -> dict:
    """The diagnostics JSON inside a copied block."""
    start = text.index("\n{") + 1
    end = text.rindex("}") + 1
    return json.loads(text[start:end])


def test_the_block_is_laid_out_as_the_prompt_expects_and_hides_the_home_folder():
    report = json.dumps({"application": {"technical_message": "/Users/someone/Library/x.json"}})
    text = console_diagnostics_block(
        link="ble", build_origin="official", device_lines=("固件           20261118.01",),
        report_text=report, now=datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc), home="/Users/someone",
    )
    lines = text.splitlines()
    assert lines[0] == BLOCK_START == "--- BORING Console 诊断（自动填入）---"
    assert lines[1] == "format: boring-prompt-diag-v1"
    assert lines[2] == f"BORING Console {__version__}"
    assert lines[3].startswith("系统：") and lines[4].startswith("时间：2026-11-23")
    assert lines[5:8] == ["build_origin: official", "link: ble", "固件           20261118.01"]
    assert lines[-1] == BLOCK_END == "--- 诊断结束 ---"
    assert "/Users/someone" not in text and "~/Library/x.json" in text


def test_windows_home_is_hidden_inside_the_json_too():
    report = json.dumps({"path": "C:\\Users\\someone\\AppData"})
    text = console_diagnostics_block(link="usb", build_origin="custom", report_text=report,
                                     home="C:\\Users\\someone")
    assert "someone" not in text and _block_json(text)["path"] == "~\\AppData"


def test_fill_prompt_swaps_the_placeholder_line_or_says_it_is_missing():
    prompt = "【Console 诊断信息】\n[[BORING_CONSOLE_DIAGNOSTICS]]\n如果上面写的是…"
    block = console_diagnostics_block(link="none", build_origin="official")
    filled = fill_prompt(prompt, block)
    assert PROMPT_PLACEHOLDER not in filled
    assert filled.startswith("【Console 诊断信息】\n" + BLOCK_START) and filled.endswith(BLOCK_END + "\n如果上面写的是…")
    assert fill_prompt(prompt, None).splitlines()[1] == PROMPT_WITHOUT_CONSOLE


def test_device_card_copies_versions_link_and_connection_timing(session, qtbot):  # noqa: F811
    window, vm, gateway, snapshot, _ = session
    now = time.monotonic()
    marks = (TimingMark("device_found", now - 6.8, "ble"), TimingMark("connect", now - 6.7, "bluetooth"),
             TimingMark("channel_open", now - 5.5), TimingMark("HELLO", now - 5.0, "180 bytes"),
             TimingMark("GET_CONFIG", now - 0.2, "skipped: cached config matches HELLO digest"))
    gateway.snapshot_ready.emit(replace(snapshot, port_name="ble:AA-BB", connect_timing=marks))
    window._select_settings_section("device")
    button = _copy_button(window, qtbot)
    assert button.text() == "复制诊断信息"
    QGuiApplication.clipboard().clear()
    button.click()
    text = QGuiApplication.clipboard().text()
    assert text.startswith(BLOCK_START) and text.endswith(BLOCK_END)
    assert f"BORING Console {__version__}" in text            # Console version
    assert "link: ble" in text and "build_origin: " in text    # how it is connected, which build
    assert f"固件           {snapshot.versions['firmware']}" in text
    assert "连接耗时       total=" in text                     # the one-line timing ...
    timing = _block_json(text)["connection_timing"]            # ... and every step in the JSON
    assert [step["step"] for step in timing["steps"]][:5] == [
        "device_found", "connect", "channel_open", "HELLO", "GET_CONFIG"]


def test_without_a_device_the_card_still_copies_what_the_console_knows(session, qtbot):  # noqa: F811
    window, vm, gateway, *_ = session
    gateway.disconnected.emit("unplugged")
    window._select_settings_section("device")
    button = _copy_button(window, qtbot)
    button.click()
    text = QGuiApplication.clipboard().text()
    assert "link: none" in text and "Hardware ID" not in text
    assert "events" in _block_json(text)
