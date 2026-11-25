"""How the Console tells the user something: a banner or the one dialog shell.

Replaces every QMessageBox (the designer, design decision and 弹窗改横幅盘点 v0–v0.2):

    notify(parent, title, text, state="error")
        A one-line notice: a banner on the right under the navigation, stacking
        at most three. For failures the user will not mistake for success
        (local drafts, computer-side features, inputs that do not fit, checks
        and previews), "请先……" reminders (state="wait") and done notices
        (state="ok"). Errors stay until closed; the others hide by themselves.
        Called from inside a dialog, or before the main window exists, it falls
        back to alert(): what happens in a dialog is said in that dialog.

    alert(parent, title, text, kind="error")
        A read-only message in the dialog shell, for risky failures the user
        must not miss (a write to or delete from the device did not happen,
        firmware maintenance, cannot quit yet, calibration not saved, half
        saved, export failed, start-up failures) and long text.

    ask(parent, title, text, buttons, default, danger=False)
        A choice in the dialog shell. Takes and returns QMessageBox standard
        buttons so call sites keep their logic. danger=True follows the danger
        rule: no orange, the cancelling button is cream and the default.

    choose(parent, title, text, options, default=..., escape=..., danger=False)
        The same with named options, for questions whose buttons are not the
        standard ones. Returns the chosen key.

Which call is which was judged by one question (the designer's rule): would a user
who missed it think the thing was done, or leave the device in a wrong state?
Tests replace `alert`, `ask` and `choose` here; all modal paths go through
`_run`, so a test can also patch that one.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QMessageBox, QWidget

from controller_config.i18n import translate_ui_text as tr
from controller_config.views.ui.dialog import BUTTON_GAP, LARGE, MEDIUM, PADDING, SMALL, XLARGE, Dialog
from controller_config.views.ui.status_icon import StatusIcon

Std = QMessageBox.StandardButton

# Labels in the interface language; the order is the left-to-right order in the
# button row, so a cancelling button always comes before the action.
_LABELS = {
    Std.Cancel: "取消", Std.Close: "关闭", Std.Abort: "中止", Std.No: "否",
    Std.Discard: "放弃", Std.Ignore: "忽略", Std.Retry: "重试", Std.Open: "打开",
    Std.Ok: "好", Std.Yes: "是", Std.Save: "保存",
}
_ESCAPES = (Std.Cancel, Std.No, Std.Close, Std.Abort)
_TEXT_WIDTH_LIMIT = 90   # characters; longer goes to MEDIUM width


def _run(dialog: QDialog) -> int:
    """Every modal message passes here (one place for tests to replace)."""
    return dialog.exec()


def _banner_host(parent: QWidget | None):
    """The main window's banner stack, or None inside a dialog / before start-up."""
    if parent is None:
        return None
    window = parent.window()
    if isinstance(window, QDialog) or not window.isVisible():
        return None
    return getattr(window, "_banners", None)


def _width_for(text: str) -> int:
    return MEDIUM if len(text) > _TEXT_WIDTH_LIMIT or text.count("\n") > 3 else SMALL


def _message_row(text: str, mark: QWidget | None) -> QWidget:
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(10)
    if mark is not None:
        row.addWidget(mark, 0, Qt.AlignmentFlag.AlignTop)
    label = QLabel(tr(text), objectName="boringNoticeText")
    label.setWordWrap(True)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    row.addWidget(label, 1)
    holder = QWidget()
    holder.setLayout(row)
    return holder


def _widen_for_buttons(dialog: Dialog) -> None:
    """Buttons size to their text; take the next width tier when the row does not fit."""
    buttons = [b for b in dialog.findChildren(QWidget) if b.objectName().startswith("choice_")]
    needed = sum(b.sizeHint().width() for b in buttons) + BUTTON_GAP * max(0, len(buttons) - 1) + 2 * PADDING
    for tier in (SMALL, MEDIUM, LARGE, XLARGE):
        if tier >= dialog._width and tier >= needed:
            dialog._width = tier
            return
    dialog._width = XLARGE


def notify(parent: QWidget | None, title: str, text: str = "", *, state: str = "error",
           key: str | None = None) -> None:
    """One line on the right; see the module docstring for when."""
    host = _banner_host(parent)
    if host is None:
        alert(parent, title, text, kind="info" if state == "ok" else "error")
        return
    if not text:
        sentence = tr(title)
    elif not title or state == "wait":
        sentence = tr(text)  # a reminder says what to do; its title only repeats where it came from
    else:
        sentence = f"{tr(title)}：{tr(text)}"
    stays = state == "error"
    banner = host.notify(sentence, state=state, key=key,
                         timeout_ms=None if stays else 5000, closable=True)
    window = parent.window()
    banner.limit_text_width(max(200, window.width() // 2))


def alert(parent: QWidget | None, title: str, text: str, *, kind: str = "error",
          object_name: str = "boringAlert") -> None:
    """Read-only message in the dialog shell; closes with the round button, Esc or Enter."""
    dialog = Dialog(parent, title, width=_width_for(text), object_name=object_name)
    mark = StatusIcon("error", size=20) if kind == "error" else None
    dialog.body_layout().addWidget(_message_row(text, mark))
    if dialog.close_button is not None:
        dialog.close_button.setDefault(True)
        dialog.close_button.setFocus()
    _run(dialog)


def choose(parent: QWidget | None, title: str, text: str, options: list[tuple[str, str]], *,
           default: str, escape: str, danger: bool = False, object_name: str = "boringChoice",
           width: int | None = None) -> str:
    """Options as (key, label) left to right; the last one is the primary
    action, unless danger, where the escape option moves last and turns cream."""
    dialog = Dialog(parent, title, width=width or _width_for(text), close="none",
                    object_name=object_name)
    dialog.body_layout().addWidget(_message_row(text, StatusIcon("error", size=20) if danger else None))
    chosen = {"key": escape}
    ordered = list(options)
    if danger:
        ordered.sort(key=lambda option: option[0] == escape)
    for index, (option_key, label) in enumerate(ordered):
        last = index == len(ordered) - 1
        kind = ("cream" if danger else "primary") if last else "secondary"
        button = dialog.add_button(label, kind, object_name=f"choice_{option_key}")

        def pick(_checked=False, picked=option_key) -> None:
            chosen["key"] = picked
            dialog.accept()

        button.clicked.connect(pick)
        if option_key == default:
            button.setDefault(True)
            button.setFocus()
    _widen_for_buttons(dialog)
    if _run(dialog) != QDialog.DialogCode.Accepted:
        return escape
    return chosen["key"]


def ask(parent: QWidget | None, title: str, text: str,
        buttons=Std.Yes | Std.No, default=Std.NoButton, *, danger: bool = False,
        labels: dict | None = None) -> Std:
    """QMessageBox.question in the dialog shell; returns the standard button chosen."""
    present = [button for button in _LABELS if buttons & button]
    escape = next((button for button in _ESCAPES if button in present), present[0])
    if default == Std.NoButton:
        default = present[-1]
    names = {**_LABELS, **(labels or {})}
    options = [(str(int(button.value)), tr(names[button])) for button in present]
    picked = choose(parent, title, text, options, default=str(int(default.value)),
                    escape=str(int(escape.value)), danger=danger, object_name="boringAsk")
    return Std(int(picked))
