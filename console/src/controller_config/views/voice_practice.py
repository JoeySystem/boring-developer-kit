"""Local voice practice: receive text and a configured shortcut, never send HID."""
import sys

from PySide6.QtCore import QEasingCurve, QEvent, QPropertyAnimation, Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QPlainTextEdit,
    QPushButton, QVBoxLayout, QWidget,
)

from controller_config.accessibility import system_reduces_motion
from controller_config.i18n import set_translatable_text


class VoicePracticeInput(QPlainTextEdit):
    send_requested = Signal()

    def __init__(self, action: dict, parent=None):
        super().__init__(parent)
        self.setObjectName("voicePracticeInput")
        self.setAccessibleName("语音练习输入框")
        self.setPlaceholderText("说的话会出现在这里")
        self._usage = action.get("double_usage", 40)
        self._modifiers = sorted(action.get("double_modifiers", []))
        self._platform = "macos" if sys.platform == "darwin" else "windows"
        self._pressed_modifiers: set[int] = set()
        self._chord_used = False
        self._modifier_send_armed = False
        self._composing = False

    def set_action(self, action):
        self._usage = action.get("double_usage", 40)
        self._modifiers = sorted(action.get("double_modifiers", []))
        self._pressed_modifiers.clear()
        self._chord_used = False
        self._modifier_send_armed = False

    def _shortcut(self, event, *, released=False):
        # Reuse the recorder's platform/HID translation without an import cycle.
        from controller_config.views.action_editor import (
            _effective_modifier_usages, _key_usage, _modifier_key_usage,
        )
        modifier = _modifier_key_usage(
            event.key(), platform=self._platform,
            native_virtual_key=event.nativeVirtualKey(),
        )
        usage, shift = (modifier, False) if modifier is not None else _key_usage(event.key(), event.modifiers())
        modifiers = _effective_modifier_usages(
            event.modifiers(), platform=self._platform,
            pressed_usages=self._pressed_modifiers,
            released_usage=modifier if released else None,
        )
        if shift and not any((value - 224) % 4 == 1 for value in modifiers):
            modifiers.append(225)
        return usage, sorted(value for value in modifiers if value != usage), modifier

    def event(self, event):
        if event.type() == QEvent.Type.ShortcutOverride:
            usage, modifiers, _ = self._shortcut(event)
            if usage == self._usage and modifiers == self._modifiers:
                event.accept()
                return True
        return super().event(event)

    def keyPressEvent(self, event):  # noqa: N802
        usage, modifiers, modifier = self._shortcut(event)
        if modifier is not None:
            self._pressed_modifiers.add(modifier)
            self._modifier_send_armed = (
                224 <= self._usage <= 231
                and self._pressed_modifiers == {self._usage, *self._modifiers}
                and not self._chord_used and not self._composing
            )
        else:
            self._chord_used = True
            self._modifier_send_armed = False
        if modifier is None and usage == self._usage and modifiers == self._modifiers and not self._composing:
            event.accept()
            if not event.isAutoRepeat():
                self.send_requested.emit()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):  # noqa: N802
        if event.isAutoRepeat():
            super().keyReleaseEvent(event)
            return
        usage, modifiers, modifier = self._shortcut(event, released=True)
        if modifier is not None:
            self._pressed_modifiers.discard(modifier)
        if not self._pressed_modifiers:
            if self._modifier_send_armed and not self._chord_used and not self._composing:
                self.send_requested.emit()
            self._modifier_send_armed = False
            self._chord_used = False
        super().keyReleaseEvent(event)

    def inputMethodEvent(self, event):  # noqa: N802
        self._composing = bool(event.preeditString())
        super().inputMethodEvent(event)

    def focusOutEvent(self, event):  # noqa: N802
        self._pressed_modifiers.clear()
        self._chord_used = False
        self._modifier_send_armed = False
        super().focusOutEvent(event)


class VoicePractice(QFrame):
    completed_changed = Signal(bool)

    def __init__(self, action: dict, parent=None, *, voice=True):
        super().__init__(parent, objectName="voicePractice")
        self._voice = voice
        self.setProperty("cardRole", "widget")
        self.completed = False
        self._has_text = False
        self._animation = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        steps = QHBoxLayout()
        self._received = QLabel("1", objectName="voiceReceivedMark")
        self._sent = QLabel("2", objectName="voiceSentMark")
        for mark, text in ((self._received, "收到文字"), (self._sent, "练习发送")):
            mark.setStyleSheet("color: #FF6A00; font-weight: 700;")
            steps.addWidget(mark)
            steps.addWidget(QLabel(text))
            steps.addStretch(1)
        layout.addLayout(steps)
        self._hint = QLabel()
        self._hint.setWordWrap(True)
        layout.addWidget(self._hint)
        self.input = VoicePracticeInput(action)
        if not voice:
            self.input.setAccessibleName("发送练习输入框")
            self.input.setPlaceholderText("输入一句测试文字")
        self.input.setMinimumHeight(100)
        self.input.setMaximumHeight(160)
        self.input.textChanged.connect(self._text_changed)
        self.input.send_requested.connect(self._send)
        layout.addWidget(self.input)
        self._send_hint = QLabel("双击设备语音键发送" if voice else "按一下设备发送键", objectName="voicePracticeSendHint")
        self._send_hint.setAlignment(Qt.AlignCenter)
        self._send_hint.setWordWrap(True)
        self._send_hint.setStyleSheet("color: #FF6A00; background: #3A2B20; border-left: 3px solid #FF6A00; border-radius: 6px; padding: 10px;")
        layout.addWidget(self._send_hint)
        self._message = QPlainTextEdit(objectName="voicePracticeMessage")
        self._message.setReadOnly(True)
        self._message.setAccessibleName("练习消息")
        self._message.setMinimumHeight(90)
        self._message.setMaximumHeight(160)
        self._message.setStyleSheet("background: #3A2B20; border-radius: 14px; padding: 14px;")
        layout.addWidget(self._message)
        self._retry = QPushButton("再练一次", objectName="voicePracticeRetry")
        self._retry.setAutoDefault(False)
        self._retry.clicked.connect(self.reset)
        layout.addWidget(self._retry)
        note = QLabel("仅用于练习，不会发送到 AI。", objectName="muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.reset()

    def _reveal(self, widget: QWidget):
        widget.show()
        if self._animation is not None:
            self._animation.stop()
            self._animation.targetObject().setOpacity(1)
            self._animation.deleteLater()
            self._animation = None
        if system_reduces_motion():
            return
        effect = widget.graphicsEffect()
        if not isinstance(effect, QGraphicsOpacityEffect):
            effect = QGraphicsOpacityEffect(widget)
            widget.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b"opacity", self)
        animation.setDuration(160)
        animation.setStartValue(0.3)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.OutCubic)
        self._animation = animation
        animation.start()

    def _text_changed(self):
        if self.completed:
            return
        has_text = bool(self.input.toPlainText().strip())
        self._received.setText("✓" if has_text else "1")
        waiting = "按一下设备语音键，说一句话，再按一下结束。" if self._voice else "输入一句话，再用设备发送。"
        set_translatable_text(self._hint, "文字已出现，试着发送。" if has_text else waiting)
        if has_text and not self._has_text:
            self._reveal(self._send_hint)
        elif not has_text:
            self._send_hint.hide()
        self._has_text = has_text

    def _send(self):
        text = self.input.toPlainText().strip()
        if not text or self.completed:
            return
        self.completed = True
        self._message.setPlainText(text)
        self.input.hide()
        self._send_hint.hide()
        self._sent.setText("✓")
        set_translatable_text(self._hint, "练习完成，内容未发送到 AI。")
        self._reveal(self._message)
        self._retry.show()
        self.completed_changed.emit(True)

    def reset(self):
        self.completed = False
        self._has_text = False
        self.input.clear()
        self._message.clear()
        self._message.hide()
        self._retry.hide()
        self._send_hint.hide()
        self._received.setText("1")
        self._sent.setText("2")
        self.input.show()
        self._text_changed()
        self.input.setFocus()
        self.completed_changed.emit(False)

    def hideEvent(self, event):  # noqa: N802
        if self._animation is not None:
            self._animation.stop()
            self._animation.targetObject().setOpacity(1)
        super().hideEvent(event)
