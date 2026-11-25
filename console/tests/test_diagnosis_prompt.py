"""「让你的 AI 先帮忙查」: the self-help diagnosis prompt (design ticket).

Settings › 通用 › 「遇到问题」 opens a dialog with the common problem kinds;
each copies the firmware owner's prompt with the Console diagnostics filled in. 「让 AI 帮忙查…」
in a failure's 查看原因 dialog opens the same dialog.
"""
from __future__ import annotations

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QPushButton

from controller_config.style import tokens
from controller_config.views.ui import diagnosis_prompt as dp
from controller_config.views.ui import notice
from controller_config.views.ui.copy_diagnostics import (
    BLOCK_END,
    BLOCK_START,
    PROMPT_PLACEHOLDER,
    PROMPT_WITHOUT_CONSOLE,
)
from test_session_recovery import session  # noqa: F401


def test_prompt_body_is_joeys_text_with_its_placeholder():
    body = dp.prompt_body()
    assert body.startswith("请帮我排查 BORING 设备遇到的问题")
    assert "提示词版本：diag-v1-" in body
    assert sum(line.strip() == PROMPT_PLACEHOLDER for line in body.splitlines()) == 1
    assert body.rstrip().endswith("现在先根据我说的情况开始。")


def test_each_kind_adds_the_prompts_own_line_and_other_adds_none():
    block = f"{BLOCK_START}\nlink: usb\n{BLOCK_END}"
    body = dp.prompt_body()
    for key, _label, line in dp.KINDS:
        text = dp.prompt_text(key, block)
        assert PROMPT_PLACEHOLDER not in text and BLOCK_START in text
        if line:
            first = text.splitlines()[0]
            assert first == f"我遇到的问题类型：{line}"
            # the line is word for word one of the prompt's A～J kinds
            assert any(row.strip() == line for row in body.splitlines())
        else:
            assert text.startswith("请帮我排查")
    assert PROMPT_WITHOUT_CONSOLE in dp.prompt_text("other", None)


def test_general_wall_has_the_entry_and_the_dialog_copies(session, qtbot):  # noqa: F811
    window, *_ = session
    window._select_settings_section("system")
    qtbot.waitUntil(lambda: any(b.isVisible() for b in window.findChildren(QPushButton, "openDiagnosisPrompt")),
                    timeout=2000)
    entry = next(b for b in window.findChildren(QPushButton, "openDiagnosisPrompt") if b.isVisible())
    entry.click()
    qtbot.waitUntil(lambda: isinstance(QApplication.activeModalWidget(), dp.DiagnosisPromptDialog)
                    or any(w.isVisible() for w in window.findChildren(dp.DiagnosisPromptDialog)), timeout=2000)
    dialog = next(w for w in window.findChildren(dp.DiagnosisPromptDialog) if w.isVisible())
    assert [b.text() for b in dialog.buttons.values()] == [label for _k, label, _l in dp.KINDS]
    assert dialog._shell_scroll.verticalScrollBar().maximum() == 0  # 960×640: no scrolling (design decision)

    dialog.buttons["bluetooth"].click()
    copied = QGuiApplication.clipboard().text()
    assert copied.startswith("我遇到的问题类型：B ")
    assert BLOCK_START in copied and BLOCK_END in copied and PROMPT_PLACEHOLDER not in copied
    assert dialog.buttons["bluetooth"].text() == dp.COPIED
    assert dialog._restore.interval() == tokens.DIAGNOSIS_COPIED_HOLD_MS
    dialog._restore.timeout.emit()
    assert dialog.buttons["bluetooth"].text() == "蓝牙连不上或经常断"
    dialog.reject()


def test_reason_dialog_offers_the_prompt(session, qtbot, monkeypatch):  # noqa: F811
    window, *_ = session
    seen = {}

    def run(dialog):
        seen["buttons"] = [b.text() for b in dialog.findChildren(QPushButton)]
        entry = dialog.findChild(QPushButton, "openDiagnosisPromptFromReason")
        entry.click()
        seen["closed"] = not dialog.isVisible()
        return 0

    opened = []
    monkeypatch.setattr(notice, "_run", run)
    monkeypatch.setattr(window, "_show_diagnosis_prompt", lambda: opened.append(True))
    window._show_reason_with_diagnosis("豆包没有响应", "豆包没有在 3 秒内打开对话窗口。")
    assert dp.ENTRY_TEXT in seen["buttons"]
    assert opened == [True]
