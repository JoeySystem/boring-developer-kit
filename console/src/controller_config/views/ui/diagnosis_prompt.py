"""「让你的 AI 先帮忙查」: the self-help diagnosis prompt (design ticket).

The user picks the kind of problem closest to theirs; one press copies the firmware owner's
prompt with the Console's diagnostics filled in (copy_diagnostics.fill_prompt,
the `[[BORING_CONSOLE_DIAGNOSTICS]]` line) and they paste it to the AI
assistant on their own computer. The dialog never shows the prompt itself.

the designer's choices (11-23, in the 自助诊断提示词 session):
  entry    Settings › 通用 › 「遇到问题」, and 「让 AI 帮忙查…」 in the
           查看原因 dialogs (save failed, Doubao did not respond)
  look     three short steps; step 1 is a list of common problems, one copy
           button each (乙 for the 分场景 question), plus 「其他问题」
  words    ui_text.json; en_US / ja_JP drafted by the Agent, not proofread

The prompt body is the firmware owner's (internal support notes),
section 一, kept verbatim in assets/self-help-diagnosis-prompt.md; copy it
again when he bumps diag-vN). A chosen kind adds one line above it, in the
prompt's own words for that kind, so the assistant skips that question.
The prompt is Chinese only for now; English and Japanese screens copy it too.
"""
from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache
from importlib.resources import files

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from controller_config.i18n import set_translatable_text, translate_ui_text as tr
from controller_config.style import tokens
from controller_config.views.ui.button import Button
from controller_config.views.ui.copy_diagnostics import PROMPT_PLACEHOLDER, fill_prompt
from controller_config.views.ui.dialog import MEDIUM, Dialog
from controller_config.views.ui.step_number import StepNumber

PROMPT_RESOURCE = "self-help-diagnosis-prompt.md"
ENTRY_TEXT = "让 AI 帮忙查…"
TITLE = "让你的 AI 先帮忙查"
COPIED = "已复制，去贴给 AI 助手"

# (key, button text, the prompt's own line for that kind; "" = let the assistant ask)
KINDS: tuple[tuple[str, str, str], ...] = (
    ("not-found", "电脑找不到设备", "A 电脑或 Console 找不到设备"),
    ("bluetooth", "蓝牙连不上或经常断", "B 蓝牙连不上、连得慢、经常断"),
    ("lamps", "状态灯不跟着 AI 变", "D 状态灯不跟着 AI 任务变，或按状态灯键不跳到对应对话"),
    ("keys", "按键没反应", "E 按键或语音键没效果、发不出去"),
    ("other", "其他问题", ""),
)
KIND_LINE = "我遇到的问题类型：{kind}"

STEP_PICK = "选最接近的一类，点一下就复制好了。Console 会自动附上诊断信息。"
STEP_PASTE = "贴给你电脑上的 AI 助手（Codex、Claude Code、豆包等），回答它问你的几个问题。"
STEP_REPORT = "它只看、不改任何设置，查完在桌面写一份报告。你看过，再决定要不要发给我们。"


@lru_cache(maxsize=1)
def prompt_body() -> str:
    text = files("controller_config.assets").joinpath(PROMPT_RESOURCE).read_text(encoding="utf-8")
    if PROMPT_PLACEHOLDER not in text:
        raise ValueError(f"{PROMPT_RESOURCE} lost its {PROMPT_PLACEHOLDER} line")
    return text.rstrip("\n")


def prompt_text(kind: str, block: str | None) -> str:
    """The text one press copies: the kind line (if any), then the filled prompt."""
    line = next((line for key, _label, line in KINDS if key == kind), None)
    if line is None:
        raise ValueError(f"Unknown problem kind: {kind}")
    body = fill_prompt(prompt_body(), block)
    return f"{KIND_LINE.format(kind=line)}\n\n{body}" if line else body


class DiagnosisPromptDialog(Dialog):
    """See the module docstring. `collect` returns the Console diagnostics block."""

    def __init__(self, parent: QWidget | None, collect: Callable[[], str]) -> None:
        super().__init__(parent, TITLE, width=MEDIUM, object_name="diagnosisPromptDialog")
        self._collect = collect
        self.buttons: dict[str, Button] = {}
        self._labels: dict[str, str] = {}
        self._restore = QTimer(self)
        self._restore.setSingleShot(True)
        self._restore.setInterval(tokens.DIAGNOSIS_COPIED_HOLD_MS)
        self._restore.timeout.connect(self._reset_labels)

        body = self.body_layout()
        body.setSpacing(tokens.DIAGNOSIS_STEP_GAP)
        pick = self._step(1, STEP_PICK)
        kinds = QVBoxLayout()
        kinds.setContentsMargins(tokens.PROMPT_STEP_DIAMETER + tokens.DIAGNOSIS_STEP_TEXT_GAP, 0, 0, 0)
        kinds.setSpacing(tokens.DIAGNOSIS_KIND_GAP)
        for key, label, _line in KINDS:
            button = Button(label, "secondary", size="small", object_name=f"diagnosisKind_{key}")
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.clicked.connect(lambda _checked=False, key=key: self.copy(key))
            kinds.addWidget(button)
            self.buttons[key] = button
            self._labels[key] = label
        pick.addLayout(kinds)
        body.addLayout(pick)
        body.addLayout(self._step(2, STEP_PASTE))
        body.addLayout(self._step(3, STEP_REPORT))

    def _step(self, number: int, text: str) -> QVBoxLayout:
        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(tokens.DIAGNOSIS_KIND_GAP)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(tokens.DIAGNOSIS_STEP_TEXT_GAP)
        row.addWidget(StepNumber(number), 0, Qt.AlignmentFlag.AlignTop)
        label = QLabel(tr(text), objectName="diagnosisPromptStep")
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.PlainText)
        row.addWidget(label, 1)
        column.addLayout(row)
        return column

    def copy(self, kind: str) -> str:
        """Put the prompt for `kind` on the clipboard; the pressed button says so."""
        text = prompt_text(kind, self._collect())
        QGuiApplication.clipboard().setText(text)
        self._reset_labels()
        set_translatable_text(self.buttons[kind], COPIED)
        self._restore.start()
        return text

    def _reset_labels(self) -> None:
        for key, button in self.buttons.items():
            if button.text() != tr(self._labels[key]):
                set_translatable_text(button, self._labels[key])


def show_diagnosis_prompt(parent: QWidget, collect: Callable[[], str]) -> DiagnosisPromptDialog:
    dialog = DiagnosisPromptDialog(parent, collect)
    dialog.open()
    return dialog
