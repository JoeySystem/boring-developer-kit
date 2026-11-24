"""What goes inside the Prompt page's "诊断信息" dialog (the pulse button on the top row).

Spec: the designer, Prompt页-定稿-v1-20261118-图/4-看演示和三个弹窗.png (right: 诊断信息):
everything technical that used to be spread over the page is collected here
(`views/prompt_library_editor.py`: the helper badge and its reason, "设备存储
可用/不可用", "最近结果", the "操作记录" box and "粘贴说明").

    提示词助手                        ● 在线
    (the helper's reason, small, only when it is not online)
    ───────────────────────────────────────────
    设备存储                            可用
    (why not, small, only when it is not available)
    ───────────────────────────────────────────
    最近一次            摇杆往上「代码审查」已打出
    操作记录
    ┌ 01 · 正在建立 USB 提示词事件监听 (monospace, small) ┐
    粘贴说明
    提示词通过剪贴板和 Command+V 打进电脑，不会自动按回车。粘贴失败时内容仍留在剪贴板。
    (○ close)                                  [复制诊断信息]

Data: `update_runtime(...)` takes exactly the arguments of
`PromptLibraryEditor.update_runtime` (available, busy, message, helper_message,
listener, event_log, background_message), so the page can pass the same call
on. The copied text is plain and not translated (copy_diagnostics.py).

The page shows it in the shared dialog shell:

    dialog = prompt_diagnostics_dialog(parent, diagnostics)   # Dialog "诊断信息",
    dialog.exec()                                             # round close, 复制诊断信息
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QVBoxLayout, QWidget

from controller_config.i18n import set_translatable_text
from controller_config.i18n import translate_ui_text as tr
from controller_config.prompt_device import (
    PromptEventLogEntry, PromptListenerState, PromptListenerStatus,
)
from controller_config.style import tokens
from controller_config.views.ui.copy_diagnostics import BUTTON_TEXT, copy_diagnostics, diagnostics_header
from controller_config.views.ui.dialog import MEDIUM, Dialog
from controller_config.views.ui.status_icon import StatusIcon

TITLE = "诊断信息"
PASTE_HELP = "提示词通过剪贴板和 Command+V 打进电脑，不会自动按回车。粘贴失败时内容仍留在剪贴板。"
NO_EVENTS = "尚无事件记录"
LOG_LINES = 5   # the activity log shows about five lines, then scrolls

# The helper's state as one word and a status shape (spec: "● 在线").
HELPER_WORDS: dict[PromptListenerState, tuple[str, str]] = {
    PromptListenerState.READY: ("在线", "ok"),
    PromptListenerState.STARTING: ("正在连接", "busy"),
    PromptListenerState.RETRYING: ("正在重试", "busy"),
    PromptListenerState.PAUSED: ("暂时暂停", "wait"),
    PromptListenerState.OFFLINE: ("离线", "off"),
}


def event_log_text(entries: tuple[PromptEventLogEntry, ...]) -> str:
    """The activity log as the page showed it: "01 · message", technical detail indented."""
    if not entries:
        return tr(NO_EVENTS)
    lines = []
    for entry in entries:
        line = f"{entry.sequence:02d} · {tr(entry.message)}"
        if entry.technical:
            line = f"{line}\n     {entry.technical}"
        lines.append(line)
    return "\n".join(lines)


class PromptDiagnostics(QWidget):
    """The dialog body; `update_runtime(...)` whenever the page's runtime changes."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("promptDiagnostics")
        self._available = False
        self._busy = False
        self._message = ""
        self._helper_message = ""
        self._listener = PromptListenerStatus()
        self._event_log: tuple[PromptEventLogEntry, ...] = ()
        self._background_message = ""

        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)
        self.helper_icon = StatusIcon("off", size=tokens.CHECK_SIZE_SMALL)
        self.helper_icon.setObjectName("promptDiagnosticsHelperIcon")
        self.helper_value = self._value("promptDiagnosticsHelper")
        self.helper_note = self._note("promptDiagnosticsHelperNote")
        box.addWidget(self._row("提示词助手", self.helper_value, self.helper_icon))
        box.addWidget(self.helper_note)
        box.addWidget(self._rule())
        self.storage_value = self._value("promptDiagnosticsStorage")
        self.storage_note = self._note("promptDiagnosticsStorageNote")
        box.addWidget(self._row("设备存储", self.storage_value))
        box.addWidget(self.storage_note)
        box.addWidget(self._rule())
        self.latest_value = self._value("promptDiagnosticsLatest")
        self.latest_value.setWordWrap(True)
        self.latest_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        box.addWidget(self._row("最近一次", self.latest_value, wide=True))

        box.addSpacing(16)
        box.addWidget(self._heading("操作记录"))
        box.addSpacing(8)
        self.log = QPlainTextEdit(objectName="promptDiagnosticsLog")
        self.log.setReadOnly(True)
        self.log.setFrameShape(QFrame.Shape.NoFrame)
        self.log.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.log.setProperty("boringI18nSkip_placeholderText", True)
        box.addWidget(self.log)
        box.addSpacing(16)
        box.addWidget(self._heading("粘贴说明"))
        box.addSpacing(8)
        paste = QLabel(objectName="promptDiagnosticsPasteHelp")
        paste.setProperty("diagRole", "body")
        paste.setWordWrap(True)
        set_translatable_text(paste, PASTE_HELP)
        box.addWidget(paste)
        self.paste_help = paste
        self._refresh()

    # -- data ----------------------------------------------------------------------

    def update_runtime(self, *, available, busy, message, helper_message, listener,
                       event_log, background_message) -> None:
        """Same arguments as `PromptLibraryEditor.update_runtime`."""
        self._available = bool(available)
        self._busy = bool(busy)
        self._message = message or ""
        self._helper_message = helper_message or ""
        self._listener = listener
        self._event_log = tuple(event_log)
        self._background_message = background_message or ""
        self._refresh()

    def diagnostics_text(self) -> str:
        """What 复制诊断信息 copies: plain, untranslated, technical details included."""
        listener = self._listener
        lines = [*diagnostics_header(), "", "[提示词]",
                 f"提示词助手：{listener.label}（{listener.state.value}）",
                 f"助手说明：{listener.message}"]
        if listener.technical:
            lines.append(f"技术信息：{listener.technical}")
        lines += [f"设备存储：{'可用' if self._available else '不可用'}",
                  f"设备说明：{self._message}",
                  f"设备忙：{'是' if self._busy else '否'}",
                  f"最近一次：{self._helper_message}",
                  f"后台：{self._background_message}",
                  "", "[操作记录]"]
        if not self._event_log:
            lines.append(NO_EVENTS)
        for entry in self._event_log:
            lines.append(f"{entry.sequence:02d} [{entry.level}] {entry.message}")
            if entry.technical:
                lines.append(f"     {entry.technical}")
        return "\n".join(lines)

    def copy(self) -> None:
        copy_diagnostics(self, self.diagnostics_text())

    # -- inside --------------------------------------------------------------------

    def _refresh(self) -> None:
        listener = self._listener
        word, shape = HELPER_WORDS[listener.state]
        self.helper_icon.set_state(shape, waiting_for="device")
        set_translatable_text(self.helper_value, word)
        self.helper_value.setToolTip(tr(self._background_message) if self._background_message else "")
        online = listener.state is PromptListenerState.READY
        self._set_note(self.helper_note, "" if online else listener.message, listener.technical)
        set_translatable_text(self.storage_value, "可用" if self._available else "不可用")
        self._set_note(self.storage_note, "" if self._available else self._message, "")
        set_translatable_text(self.latest_value, self._helper_message or "—")
        text = event_log_text(self._event_log)
        if self.log.toPlainText() != text:
            position = self.log.verticalScrollBar().value()
            self.log.setPlainText(text)
            self.log.verticalScrollBar().setValue(position)
        line = self.log.fontMetrics().lineSpacing()
        self.log.setFixedHeight(line * LOG_LINES + 24)

    @staticmethod
    def _set_note(label: QLabel, text: str, technical: str) -> None:
        if text:
            set_translatable_text(label, text)
        else:
            label.setProperty("boringI18nSource_text", None)
            label.setText("")
        label.setToolTip(technical)
        label.setProperty("boringI18nSkip_toolTip", True)
        label.setVisible(bool(text))

    @staticmethod
    def _value(name: str) -> QLabel:
        label = QLabel(objectName=name)
        label.setProperty("diagRole", "value")
        label.setTextFormat(Qt.TextFormat.PlainText)
        return label

    @staticmethod
    def _note(name: str) -> QLabel:
        label = QLabel(objectName=name)
        label.setProperty("diagRole", "note")
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.hide()
        return label

    @staticmethod
    def _heading(text: str) -> QLabel:
        label = QLabel()
        label.setProperty("diagRole", "heading")
        set_translatable_text(label, text)
        return label

    @staticmethod
    def _rule() -> QFrame:
        rule = QFrame(objectName="promptDiagnosticsRule")
        rule.setFixedHeight(1)
        return rule

    @staticmethod
    def _row(name: str, value: QLabel, icon: StatusIcon | None = None, *, wide: bool = False) -> QWidget:
        """Name on the left, value on the right; `wide` lets a long value wrap in the free width."""
        row = QWidget()
        row.setProperty("diagRow", True)
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 12, 0, 12)
        line.setSpacing(8)
        caption = QLabel()
        caption.setProperty("diagRole", "name")
        set_translatable_text(caption, name)
        line.addWidget(caption, 0, Qt.AlignmentFlag.AlignVCenter)
        if wide:
            line.addSpacing(16)
            line.addWidget(value, 1)
            return row
        line.addStretch(1)
        if icon is not None:
            line.addWidget(icon, 0, Qt.AlignmentFlag.AlignVCenter)
        line.addWidget(value, 0, Qt.AlignmentFlag.AlignVCenter)
        return row


def prompt_diagnostics_dialog(parent: QWidget | None, diagnostics: PromptDiagnostics) -> Dialog:
    """The shared dialog shell with `diagnostics` inside: title, round close, 复制诊断信息.

    The dialog takes the widget over (it is reparented into the dialog body);
    build a fresh PromptDiagnostics per dialog, or take it back afterwards.
    """
    dialog = Dialog(parent, TITLE, width=MEDIUM, object_name="promptDiagnosticsDialog")
    dialog.body_layout().addWidget(diagnostics)
    button = dialog.add_button(BUTTON_TEXT, "secondary", object_name="copyDiagnostics")
    button.clicked.connect(diagnostics.copy)
    return dialog
