"""Shared progressive disclosure for observable voice-input failures."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget

from controller_config.i18n import set_translatable_accessible_name


RECOVERY_CHOICES = (
    ("no_voice", "完全没反应"),
    ("no_text", "已经唤起，但没有文字"),
    ("cant_stop", "已经唤起，但无法结束"),
    ("conflict", "同时唤起了其他语音软件"),
    ("send", "文字出现了，但没有发送"),
)


class VoiceRecoveryPanel(QWidget):
    """Keep failure labels, expanded state and keyboard focus consistent."""

    outcome_selected = Signal(str)

    def __init__(self, *, toggle_name, options_name, button_prefix, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.toggle = QPushButton("没有成功？", objectName=toggle_name)
        self.toggle.setCheckable(True)
        self.toggle.setProperty("buttonRole", "ghost")
        self.toggle.setProperty("controls", options_name)
        self.toggle.setProperty("expanded", False)
        set_translatable_accessible_name(self.toggle, "展开失败结果")
        layout.addWidget(self.toggle)
        self.options = QWidget(objectName=options_name)
        options_layout = QVBoxLayout(self.options)
        options_layout.setContentsMargins(0, 0, 0, 0)
        self.buttons = {}
        for reason, label in RECOVERY_CHOICES:
            button = QPushButton(label, objectName=button_prefix + reason)
            button.setProperty("buttonRole", "ghost")
            button.clicked.connect(
                lambda _=False, key=reason: self.outcome_selected.emit(key)
            )
            options_layout.addWidget(button)
            self.buttons[reason] = button
        self.options.hide()
        self.toggle.toggled.connect(self._toggle)
        layout.addWidget(self.options)

    def _toggle(self, checked):
        self.options.setVisible(checked)
        self.toggle.setProperty("expanded", checked)
        set_translatable_accessible_name(
            self.toggle, "收起失败结果" if checked else "展开失败结果",
        )
        if checked:
            next(iter(self.buttons.values())).setFocus()
        else:
            self.toggle.setFocus()
