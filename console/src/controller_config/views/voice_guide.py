"""Voice setup with an optional visual demo and a local, real-input practice."""
import copy
import math
import sys
from pathlib import Path  # noqa: F401 - tests patch voice_guide.Path

from PySide6.QtCore import Qt, QTimer, Signal, QProcess, QUrl
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QColor, QDesktopServices, QFont, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget,
)

from controller_config import voice_presets
from controller_config.voice_presets import DEFAULT_VOICE_ACTION, DEFAULT_VOICE_USAGE
from controller_config.accessibility import system_reduces_motion
from controller_config.actions import describe_action
from controller_config.ai_setup import AI_APPLICATIONS, AI_BY_KEY, installed_desktop_app, load_choices
from controller_config.i18n import set_translatable_accessible_name, set_translatable_text, translate_ui_text
from controller_config.models import AppState
from controller_config.transactions import ConfigTransactionState, configs_match_readback
from controller_config.voice_setup import (
    voice_provider_key, adapted_voice_action, voice_draft_is_scoped,
    confirmed_voice_choice, remove_voice_trial, _gesture,
)
from controller_config.style import tokens
from controller_config.views.ui.button import Button
from controller_config.views.ui.dialog import MEDIUM, Dialog
from controller_config.views.ui.status_icon import StatusIcon
from controller_config.views.voice_practice import VoicePractice
from controller_config.views.voice_recovery import VoiceRecoveryPanel



def _activation(action):
    """The input-method binding, independent from the double-click send key."""
    return {
        "type": action.get("type"),
        "usage": action.get("usage"),
        "modifiers": sorted(action.get("modifiers", [])),
    }


def _warning_box(label: QLabel) -> QFrame:
    """The dialog draft's orange-triangle notice round an existing message label."""
    from controller_config.views.ui.dialog import _WarningMark
    box = QFrame(objectName="voiceWarningBox")
    row = QHBoxLayout(box)
    row.setContentsMargins(14, 10, 14, 10)
    row.setSpacing(10)
    row.addWidget(_WarningMark(), 0, Qt.AlignTop)
    label.setParent(box)
    row.addWidget(label, 1)
    box.hide()
    return box


class _DemoStage(QWidget):
    """The white stage of the demo (Mode页 两个弹窗 v0, 10): key 8 on the left with a
    microphone on it, the input box on the right, the step and seven dots on top."""

    HEIGHT = 168

    def __init__(self, demo: "VoiceDemo") -> None:
        super().__init__(demo)
        self._demo = demo
        self.setFixedHeight(self.HEIGHT)
        self.setObjectName("voiceDemoStage")

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt virtual method
        demo = self._demo
        t = demo.time()
        index = demo.stage_at(t)
        caption = translate_ui_text(demo._frames[index][0])
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        body = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(tokens.VOICE_STAGE))
        painter.drawRoundedRect(body, 18, 18)
        ink, muted = QColor(tokens.VOICE_STAGE_INK), QColor(tokens.VOICE_STAGE_MUTED)
        # The step: a black number in a circle, what happens now, seven dots of progress.
        painter.setBrush(ink)
        painter.drawEllipse(QRectF(16, 14, 18, 18))
        painter.setPen(QColor(tokens.VOICE_STAGE))
        painter.setFont(_demo_font(11, bold=True))
        painter.drawText(QRectF(16, 14, 18, 18), Qt.AlignmentFlag.AlignCenter, str(index + 1))
        painter.setPen(ink)
        painter.setFont(_demo_font(13, bold=True))
        painter.drawText(QRectF(42, 12, body.width() - 140, 22), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         caption)
        painter.setPen(Qt.PenStyle.NoPen)
        for dot in range(len(demo._frames)):
            painter.setBrush(ink if dot <= index else QColor(tokens.VOICE_STAGE_EDGE))
            painter.drawEllipse(QPointF(body.right() - 16 - (len(demo._frames) - 1 - dot) * 9, 23), 2.6, 2.6)
        # Key 8: sinks, rings orange and fills the microphone while it is pressed.
        pressed = demo.pressed_at(t)
        key = QRectF(16, 64 + (3 if pressed else 0), 62, 62)
        if pressed:
            painter.setPen(QPen(QColor(tokens.ACCENT), 2.4))
        painter.setBrush(QColor(tokens.VOICE_KEY))
        painter.drawRoundedRect(key, 14, 14)
        _paint_mic(painter, key.center(), filled=pressed)
        painter.setPen(muted)
        painter.setFont(_demo_font(10.5))
        painter.drawText(QRectF(8, 130, 78, 18), Qt.AlignmentFlag.AlignCenter, demo.key_label)
        # The input box and what is in it.
        field = QRectF(92, 64, body.width() - 108, 62)
        painter.setPen(QPen(QColor(tokens.VOICE_STAGE_EDGE), 1))
        painter.setBrush(QColor(tokens.VOICE_STAGE_FIELD))
        painter.drawRoundedRect(field, 12, 12)
        text = translate_ui_text(DEMO_TEXT)
        inner = field.adjusted(14, 0, -14, 0)
        painter.setFont(_demo_font(13))
        if index == 1:   # 说话: a row of orange dots that follows the voice; 在听 at the top right
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(tokens.ACCENT))
            x, n = inner.left(), 0
            while x < inner.right() - 6:
                level = 0.5 + 0.5 * math.sin(n * 1.7 + t * 9) * math.sin(n * 0.6 + t * 4)
                radius = 1.4 + 3.2 * abs(level)
                painter.drawEllipse(QPointF(x + 3, field.center().y() + 4), radius, radius)
                x += 12
                n += 1
            painter.drawEllipse(QPointF(field.right() - 46, field.top() + 12), 3, 3)
            painter.setPen(QColor(tokens.ACCENT))
            painter.setFont(_demo_font(10.5))
            painter.drawText(QRectF(field.right() - 40, field.top() + 4, 36, 16),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, translate_ui_text("在听"))
        elif index == 3:   # 等文字出现
            painter.setPen(QPen(muted, 1.6))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawArc(QRectF(inner.left(), field.center().y() - 6, 12, 12), round(-t * 360 * 16) % 5760, 270 * 16)
            painter.drawText(QRectF(inner.left() + 20, field.top(), inner.width() - 20, field.height()),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, translate_ui_text("正在变成文字…"))
        elif index in (4, 5) and not demo.sent_at(t):   # the words come one by one, then wait for the send
            shown = text if index == 5 else text[:max(1, round(len(text) * min(1.0, (t - 3.2) / 0.9)))]
            painter.setPen(ink)
            metrics = QFontMetricsF(painter.font())
            painter.drawText(inner, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, shown)
            caret_x = inner.left() + metrics.horizontalAdvance(shown) + 2
            painter.setPen(QPen(QColor(tokens.ACCENT), 1.4))
            painter.drawLine(QPointF(caret_x, field.center().y() - 9), QPointF(caret_x, field.center().y() + 9))
        else:
            painter.setPen(muted)
            painter.drawText(inner, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             translate_ui_text("说的话会出现在这里"))
        if demo.sent_at(t):   # sent: the words became a bubble above the box
            font = _demo_font(12)
            painter.setFont(font)
            width = min(field.width() - 40, QFontMetricsF(font).horizontalAdvance(text) + 24)
            bubble = QRectF(field.right() - width, 36, width, 24)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(ink)
            painter.drawRoundedRect(bubble, 12, 12)
            painter.setPen(QColor(tokens.VOICE_STAGE))
            painter.drawText(bubble, Qt.AlignmentFlag.AlignCenter,
                             QFontMetricsF(font).elidedText(text, Qt.TextElideMode.ElideRight, width - 20))
            painter.setPen(muted)
            painter.setFont(_demo_font(10.5))
            painter.drawText(QRectF(bubble.left() - 52, 36, 46, 24),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, translate_ui_text("已发出"))
        painter.end()


DEMO_TEXT = "明天上午十点开会，记得带上周报"


def _demo_font(size: float, *, bold: bool = False) -> QFont:
    font = QFont()
    font.setPixelSize(max(1, round(size)))
    font.setWeight(QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
    return font


def _paint_mic(painter: QPainter, centre: QPointF, *, filled: bool) -> None:
    """A line microphone (filled while the key is pressed), the designer 11-20: no digit 8."""
    colour = QColor(tokens.VOICE_STAGE)
    painter.save()
    painter.setPen(QPen(colour, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    painter.setBrush(colour if filled else Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(QRectF(centre.x() - 6, centre.y() - 15, 12, 20), 6, 6)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawArc(QRectF(centre.x() - 11, centre.y() - 6, 22, 18), 200 * 16, 140 * 16)
    painter.drawLine(QPointF(centre.x(), centre.y() + 12), QPointF(centre.x(), centre.y() + 17))
    painter.drawLine(QPointF(centre.x() - 6, centre.y() + 17), QPointF(centre.x() + 6, centre.y() + 17))
    painter.restore()


class VoiceDemo(QFrame):
    """操作演示 (Mode页 两个弹窗 v0, 10): one 6.4 s run in seven steps, stopping on
    "演示结束"; 暂停 / 继续, 重播, and "回去自己练 ›" under the stage. With reduced
    motion the steps are clicked through one at a time (下一步)."""

    key_pressed = Signal()
    back_requested = Signal()
    # (step, from, to, key presses in it). The double-click presses are a real double-click apart.
    _frames = (
        ("按一下 · 开始", 0.0, 0.7, (0.2,)),
        ("说话", 0.7, 2.1, ()),
        ("再按一下 · 结束", 2.1, 2.7, (2.2,)),
        ("等文字出现", 2.7, 3.2, ()),
        ("文字出现了", 3.2, 4.2, ()),
        ("双击 · 发送", 4.2, 5.2, (4.4, 4.64)),
        ("演示结束", 5.2, 6.4, ()),
    )
    LENGTH = 6.4
    PRESS = 0.22          # how long the key stays down
    SENT = 4.74           # the words fly up into the bubble
    TICK_MS = 33

    def __init__(self, parent=None, *, key_label: str = "8 号键"):
        super().__init__(parent, objectName="voiceDemo")
        self._reduced_motion = system_reduces_motion()
        self.key_label = translate_ui_text(key_label)
        self._index = 0
        self._t = 0.0
        self._has_played = False
        self._timer = QTimer(self)
        self._timer.setInterval(self.TICK_MS)
        self._timer.timeout.connect(self._advance)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self._stage = _DemoStage(self)
        layout.addWidget(self._stage)
        controls = QHBoxLayout()
        controls.setSpacing(8)
        self._pause = Button("暂停", "secondary", size="small", object_name="voiceDemoPause")
        self._pause.clicked.connect(self._toggle)
        self._play = Button("重播", "secondary", size="small", object_name="voiceDemoReplay")
        self._play.clicked.connect(self._replay)
        back = Button("回去自己练 ›", "text", object_name="voiceDemoBack")
        back.clicked.connect(self.back_requested)
        controls.addWidget(self._pause)
        controls.addWidget(self._play)
        controls.addStretch(1)
        controls.addWidget(back)
        layout.addLayout(controls)
        self._text = QLineEdit(objectName="voiceDemoText")   # kept for screen readers: what the box shows
        self._text.setReadOnly(True)
        self._text.hide()
        self._caption = QLabel(objectName="voiceDemoCaption")
        self._caption.hide()
        self._show_frame()

    # -- the timeline ------------------------------------------------------------------

    def time(self) -> float:
        return self._t

    def stage_at(self, t: float) -> int:
        return next((index for index, frame in enumerate(self._frames) if t < frame[2]), len(self._frames) - 1)

    def pressed_at(self, t: float) -> bool:
        return any(press <= t < press + self.PRESS for frame in self._frames for press in frame[3])

    def sent_at(self, t: float) -> bool:
        return t >= self.SENT

    def _set_time(self, t: float, *, emit: bool = True) -> None:
        before, self._t = self._t, t
        if emit:
            for frame in self._frames:
                for press in frame[3]:
                    if before < press <= t:
                        self.key_pressed.emit()
        index = self.stage_at(t)
        if index != self._index or not emit:
            self._index = index
            set_translatable_text(self._caption, self._frames[index][0])
            set_translatable_accessible_name(self, self._frames[index][0])
        self._stage.update()

    def _show_frame(self):
        """Jump to the start of step `_index` (step-by-step mode) and press the key as it does."""
        frame = self._frames[self._index]
        self._t = frame[1]
        set_translatable_text(self._caption, frame[0])
        set_translatable_accessible_name(self, frame[0])
        for _press in frame[3]:
            self.key_pressed.emit()
        if frame[3]:
            self._t = frame[3][-1]   # show the key pressed
        self._stage.update()

    def _advance(self):
        t = self._t + self.TICK_MS / 1000
        if t >= self.LENGTH:
            self._set_time(self.LENGTH - 0.001)
            self._timer.stop()
            set_translatable_text(self._pause, "暂停")
            self._pause.setEnabled(False)
            return
        self._set_time(t)

    def _toggle(self):
        if self._reduced_motion:
            self._step_once()
            return
        if self._timer.isActive():
            self._timer.stop()
            set_translatable_text(self._pause, "继续")
        else:
            self._timer.start()
            set_translatable_text(self._pause, "暂停")

    def _step_once(self):
        self._index = (self._index + 1) % len(self._frames)
        self._show_frame()

    def _replay(self):
        if self._reduced_motion:
            self._step_once()
            return
        self._t = -0.001
        self._index = -1
        self._set_time(0.0)
        self._pause.setEnabled(True)
        set_translatable_text(self._pause, "暂停")
        self._timer.start()

    def hideEvent(self, event):  # noqa: N802
        self._timer.stop()
        super().hideEvent(event)

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        if self._reduced_motion:
            set_translatable_text(self._play, "下一步")
            self._pause.hide()
        else:
            self._replay()


class VoiceSetupDialog(Dialog):
    """Bind, practise locally, then explicitly confirm use in the target app.

    HID input does not identify whether a shortcut came from the device or a
    computer keyboard. Physical use and the target-app result are user confirmed.
    """

    action_applied = Signal(object)

    def __init__(self, parent=None, *, action=None, settings=None,
                 include_target=True, target_key=None, view_model=None, control_id="key.8", mode="normal",
                 candidate_action=None, short_name=None, trial_record=None,
                 initial_use_mode="global", recovery_reason=None, platform=None, settings_lookup=None,
                 open_device_voice=None):
        # The shared shell; "稍后设置" bottom-left closes (reject), as before.
        super().__init__(parent, "确认现有快捷键", step="设置语音输入 · 1 / 3", width=MEDIUM,
                         close="later", object_name="voiceSetupDialog")
        # B-01: an explicit platform (tests, previews) or the live host.
        self._platform_override = platform
        self._settings_lookup = settings_lookup
        self.setWindowTitle("设置语音输入")
        self.setAttribute(Qt.WA_DeleteOnClose)
        self._include_target = include_target
        self._context_ready = True
        self._settings = settings
        self.action = copy.deepcopy(action if action is not None else
                                    copy.deepcopy(DEFAULT_VOICE_ACTION))
        self._requested_action = copy.deepcopy(candidate_action or self.action)
        self._short_name = short_name
        self._syncing_shortcuts = False
        self._custom_shortcut_chosen = self.action.get("type") == "key_gesture"
        self._vm = view_model
        self._control_id = control_id
        self._mode = mode
        self._trial_record = trial_record
        self._history_active = bool(trial_record)
        self._doubao_use_mode = (
            initial_use_mode if initial_use_mode in {"global", "current_input"} else "global"
        )
        self._doubao_prerequisite_confirmed = False
        self._doubao_physical_confirmed = False
        self._doubao_target_confirmed = False
        self._recovery_reason = None
        self._provider_commit_deferred = False
        snapshot = view_model.model.snapshot if view_model is not None else None
        self._name_needs_apply = (snapshot is not None and mode == "normal" and short_name is not None
                                  and short_name != snapshot.mappings.get(control_id, {}).get("short_name", ""))
        self._serial = snapshot.identity.get("serial") if snapshot else None
        self._profile_id = snapshot.active_profile_id if snapshot else None
        self._provider_key = voice_provider_key(self._serial, self._profile_id, mode)
        self._initial_provider_exists = settings.contains(self._provider_key) if settings is not None else False
        self._initial_provider = settings.value(self._provider_key, "") if settings is not None else ""
        self._confirmed_provider = None
        if settings is not None:
            saved = confirmed_voice_choice(settings, self._serial, self._profile_id, mode=mode)
            if saved is not None and _gesture(saved["action"]) == _gesture(self._requested_action):
                self._custom_shortcut_chosen = True
                self._confirmed_provider = saved.get("provider")
        self._adapt_candidate = None
        self._adapt_previous_config = None
        self._adapt_expected = None
        self._adapting = False
        layout = self.body_layout()
        self._heading = self.title_label
        self._context_notice = QLabel("请连接原设备，切回 CODEX 模式，并应用当前语音键设置。" if mode == "codex" else "请连接原设备，切回普通模式，并应用当前语音键设置。", objectName="voiceContextNotice")
        self._context_notice.setWordWrap(True)
        self._context_notice.hide()
        self._issue_notice = QLabel(objectName="voiceIssueNotice")
        self._issue_notice.setWordWrap(True)
        self._issue_notice.hide()
        layout.addWidget(self._context_notice)
        layout.addWidget(self._issue_notice)
        self._pages = QStackedWidget()
        self._repair_groups = []

        binding = QWidget()
        body = QVBoxLayout(binding)
        body.setContentsMargins(0, 0, 0, 0)
        self._history_panel = QWidget(objectName="voiceVerifiedSummary")
        history_layout = QVBoxLayout(self._history_panel)
        history_layout.setContentsMargins(0, 0, 0, 0)
        self._history_label = QLabel(objectName="voiceVerifiedStatus")
        self._history_label.setWordWrap(True)
        history_layout.addWidget(self._history_label)
        history_actions = QHBoxLayout()
        self._reverify_button = QPushButton("重新验证", objectName="voiceReverify")
        self._change_settings_button = QPushButton("更改设置", objectName="voiceChangeSettings")
        self._change_settings_button.setProperty("buttonRole", "ghost")
        self._reverify_button.clicked.connect(self._reverify)
        self._change_settings_button.clicked.connect(self._change_settings)
        history_actions.addWidget(self._reverify_button)
        history_actions.addWidget(self._change_settings_button)
        history_layout.addLayout(history_actions)
        body.addWidget(self._history_panel)
        # 状态那一行 (Mode页 两个弹窗 v0, 09): only when there is one, said with the status shapes.
        self._status_row = QFrame(objectName="voiceStatusRow")
        status_layout = QHBoxLayout(self._status_row)
        status_layout.setContentsMargins(14, 10, 14, 10)
        status_layout.setSpacing(10)
        self._status_icon = StatusIcon("ok", self._status_row, size=16)
        status_layout.addWidget(self._status_icon, 0, Qt.AlignTop)
        status_text = QVBoxLayout()
        status_text.setSpacing(2)
        self._status_title = QLabel(objectName="voiceStatusTitle")
        self._status_note = QLabel(objectName="voiceStatusNote")
        self._status_note.setProperty("tone", "secondary")
        self._status_note.setWordWrap(True)
        status_text.addWidget(self._status_title)
        status_text.addWidget(self._status_note)
        status_layout.addLayout(status_text, 1)
        self._status_row.hide()
        body.addWidget(self._status_row)
        self._provider_prompt = QLabel("你用哪个软件把说话变成文字？")
        body.addWidget(self._provider_prompt)
        self.provider = QComboBox(objectName="voiceSoftware")
        self.provider.addItem("请选择语音输入软件", "")
        # B-05: one software list for every voice entry point.
        for key, label in voice_presets.software_choices(self._host_platform()):
            self.provider.addItem(label, key)
        preferred = (self._confirmed_provider
                     or (settings.value(self._provider_key, "") if settings is not None else ""))
        if preferred and self.provider.findData(preferred) < 0:
            software = voice_presets.BY_KEY.get(preferred)
            self.provider.addItem(f"{software.label if software else preferred}（已有手动设置）", preferred)
        self.provider.setCurrentIndex(max(0, self.provider.findData(preferred)))
        body.addWidget(self.provider)
        self._binding_hint = QLabel(objectName="voiceBindingHint")
        self._binding_hint.setWordWrap(True)
        body.addWidget(self._binding_hint)
        self.preset_button = QPushButton(objectName="voicePresetButton")
        self.preset_button.clicked.connect(self._use_provider_preset)
        body.addWidget(self.preset_button)
        self._open_voice_button = QPushButton("打开 Typeless", objectName="openVoiceSoftware")
        self._open_voice_button.setProperty("buttonRole", "ghost")
        self._open_voice_button.clicked.connect(self._open_voice)
        body.addWidget(self._open_voice_button)
        self._other_use_toggle = QPushButton("其他使用方式", objectName="voiceOtherUseToggle")
        self._other_use_toggle.setProperty("buttonRole", "ghost")
        self._other_use_toggle.setCheckable(True)
        self._other_use_toggle.setProperty("controls", "voiceOtherUseOptions")
        self._other_use_toggle.setProperty("expanded", False)
        set_translatable_accessible_name(self._other_use_toggle, "展开其他使用方式")
        body.addWidget(self._other_use_toggle)
        self._other_use_panel = QWidget(objectName="voiceOtherUseOptions")
        other_use_layout = QVBoxLayout(self._other_use_panel)
        other_use_layout.setContentsMargins(0, 0, 0, 0)
        self._current_input_only = QCheckBox("我只在豆包作为当前输入法时使用", objectName="voiceCurrentInputOnly")
        other_use_layout.addWidget(self._current_input_only)
        self._other_use_panel.hide()
        self._other_use_toggle.toggled.connect(self._toggle_other_use)
        self._current_input_only.toggled.connect(self._use_mode_changed)
        body.addWidget(self._other_use_panel)
        self._shortcut_toggle = QPushButton("快捷键不一致？", objectName="voiceShortcutDisclosure")
        self._shortcut_toggle.setProperty("buttonRole", "ghost")
        self._shortcut_toggle.setCheckable(True)
        self._shortcut_toggle.setProperty("controls", "voiceSetupShortcuts")
        self._shortcut_toggle.setProperty("expanded", False)
        set_translatable_accessible_name(self._shortcut_toggle, "展开快捷键调整")
        self._shortcut_toggle.toggled.connect(self._toggle_shortcuts)
        body.addWidget(self._shortcut_toggle)
        self._shortcut_panel = QWidget(objectName="voiceSetupShortcuts")
        shortcuts = QVBoxLayout(self._shortcut_panel)
        shortcuts.setContentsMargins(0, 0, 0, 0)
        self._shortcut_hint = QLabel("先查看输入法现有的启停快捷键，再用电脑键盘在这里录制；不用改输入法的设置。")
        self._shortcut_hint.setWordWrap(True)
        shortcuts.addWidget(self._shortcut_hint)
        self._send_panel = QWidget(objectName="voiceSendOptions")
        send_layout = QVBoxLayout(self._send_panel)
        send_layout.setContentsMargins(0, 0, 0, 0)
        self._shortcut_editors = []
        if view_model is not None:
            # Reuse the existing recorder and named-key selectors. Import here
            # because ActionEditor also uses the optional VoiceDemo above.
            from controller_config.views.action_editor import ActionEditor
            definitions = tuple(d for d in view_model.action_definitions if d.action_type == "key")
            platform = self._host_platform()
            for prefix, label in (("", "单击 · 说话／结束"), ("double_", "双击 · 发送文字")):
                destination = send_layout if prefix else shortcuts
                destination.addWidget(QLabel(label))
                editor = ActionEditor(definitions, {"type": "key", "usage": self._requested_action[prefix + "usage"],
                                      "modifiers": self._requested_action.get(prefix + "modifiers", [])},
                                      platform=platform, capture_platform=platform)
                editor.setObjectName("voiceShortcutEditor" if not prefix else "voiceSendShortcutEditor")
                editor._type_selector.hide()  # There is only one supported type here.
                editor._shortcut_recorder.voice_key = not prefix  # Single press = the voice key itself.
                set_translatable_text(editor._manual_toggle, "手动选择快捷键")
                editor.action_changed.connect(lambda p=prefix, e=editor: self._shortcut_changed(p, e))
                destination.addWidget(editor)
                self._shortcut_editors.append((prefix, editor))
        self._custom_toggle = QPushButton("调整双击发送", objectName="voiceCustomShortcuts")
        self._custom_toggle.setCheckable(True)
        self._custom_toggle.setProperty("buttonRole", "ghost")
        self._custom_toggle.toggled.connect(self._send_panel.setVisible)
        body.addWidget(self._shortcut_panel)
        self._toggle_supported = QCheckBox("已确认快捷键和启停方式", objectName="voiceToggleSupported")
        self._adapt_notice = QLabel("确认快捷键后，一次保存到设备。", objectName="voiceAdaptNotice")
        self._adapt_notice.setWordWrap(True)
        self._compatibility_notice = QLabel(objectName="voiceShortcutCompatibility")
        self._compatibility_notice.setWordWrap(True)
        self._shortcut_summary = QLabel(objectName="voiceShortcutSummary")
        self._shortcut_summary.setWordWrap(True)
        body.addWidget(self._custom_toggle)
        body.addWidget(self._send_panel)
        self._send_panel.hide()
        # Shown in place of "开始实体练习" while an adaptation is needed.
        self._adapt_button = Button("应用适配快捷键", "primary", object_name="voiceAdaptApply")
        self._adapt_button.clicked.connect(self._apply_adaptation)
        self._adapt_error = QLabel(objectName="voiceAdaptError")
        self._adapt_error.setWordWrap(True)
        help_button = self._help_button = QPushButton("找不到设置？", objectName="voiceBindingHelpToggle")
        help_button.setProperty("buttonRole", "ghost")
        help_button.setCheckable(True)
        body.addWidget(help_button)
        # 找不到设置？ opens a box in place (Mode页 两个弹窗 v0, 09): what the software needs, how to
        # set it, and the two ways on (高级快捷键 ›, 设备级语音键 ›).
        self._help_box = QFrame(objectName="voiceHelpBox")
        help_layout = QVBoxLayout(self._help_box)
        help_layout.setContentsMargins(14, 12, 14, 12)
        help_layout.setSpacing(6)
        help_layout.addWidget(QLabel("语音软件要一直开着，并且支持用快捷键开始、结束。", objectName="voiceHelpLead"))
        help_text = self._binding_help = QLabel("保留原来的快捷键。请选择可添加外部键盘快捷键、且支持按一下启停的软件；仅支持按住说话的方式暂不能用于这个语音键。")
        help_text.setWordWrap(True)
        help_text.setProperty("tone", "secondary")
        help_layout.addWidget(help_text)
        links = QHBoxLayout()
        links.setSpacing(16)
        advanced = Button("高级快捷键 ›", "text", object_name="voiceHelpAdvanced")
        advanced.clicked.connect(lambda: self._custom_toggle.setChecked(not self._custom_toggle.isChecked()))
        links.addWidget(advanced)
        self._device_voice_link = Button("设备级语音键 ›", "text", object_name="voiceHelpDeviceVoice")
        self._device_voice_link.setVisible(open_device_voice is not None)
        if open_device_voice is not None:
            self._device_voice_link.clicked.connect(open_device_voice)
        links.addWidget(self._device_voice_link)
        links.addStretch(1)
        help_layout.addLayout(links)
        self._help_box.hide()
        help_button.toggled.connect(lambda _on: self._refresh())
        body.addWidget(self._help_box)
        body.addStretch(1)
        self._pages.addWidget(binding)

        trial = QWidget()
        trial_layout = QVBoxLayout(trial)
        trial_layout.setContentsMargins(0, 0, 0, 0)
        number = control_id.split(".")[1] if control_id.startswith("key.") else "8"
        trial_hint = QLabel(translate_ui_text("按设备上的 {number} 号键试一下").format(number=number),
                            objectName="voicePracticeHint")
        trial_hint.setWordWrap(True)
        trial_layout.addWidget(trial_hint)
        self.practice = VoicePractice(action or {"double_usage": 40})
        trial_layout.addWidget(self.practice)
        # 看演示: the white stage takes the practice box's place; the dialog keeps its height (10).
        self.demo = VoiceDemo(key_label=translate_ui_text("{number} 号键").format(number=number))
        self.demo.hide()
        trial_layout.addWidget(self.demo)
        demo_toggle = self._demo_toggle = Button("不会操作？看演示 ›", "text", object_name="voicePracticeDemoToggle")
        demo_toggle.setCheckable(True)
        demo_toggle.toggled.connect(self._show_demo)
        self.demo.back_requested.connect(lambda: demo_toggle.setChecked(False))
        trial_layout.addWidget(demo_toggle, 0, Qt.AlignLeft)
        self._add_repair_actions(trial_layout)
        self._physical_confirmed = QCheckBox("我已用设备键完成练习", objectName="voicePhysicalConfirmed")
        self._physical_confirmed.hide()
        trial_layout.addWidget(self._physical_confirmed)
        trial_layout.addStretch(1)
        self._pages.addWidget(trial)

        self._target = QComboBox(objectName="voiceTargetApp")
        selected = load_choices(settings)
        ordered = selected + [app.key for app in AI_APPLICATIONS if app.key not in selected]
        for key in ordered:
            self._target.addItem(AI_BY_KEY[key].name, key)
        self._target.addItem("其他应用", "other")
        preferred_target = target_key if target_key in (*AI_BY_KEY, "other") else (selected[0] if selected else "other")
        self._target.setCurrentIndex(self._target.findData(preferred_target))
        self._target_confirmed = QCheckBox("我已在这个应用完成输入和发送", objectName="voiceTargetConfirmed")
        self._open_target_button = QPushButton(objectName="voiceOpenTarget")
        self._open_target_button.clicked.connect(self._open_target)
        self._target_hint = QLabel(objectName="voiceTargetHint")
        self._target_hint.setWordWrap(True)
        self._target_error = QLabel(objectName="voiceTargetError")
        self._target_error.setWordWrap(True)
        if include_target:
            target = QWidget()
            target_layout = QVBoxLayout(target)
            target_layout.setContentsMargins(0, 0, 0, 0)
            target_layout.addWidget(QLabel("这次先在哪个应用里试用？"))
            target_layout.addWidget(self._target)
            target_layout.addWidget(self._open_target_button)
            target_layout.addWidget(self._target_hint)
            self._target_error_box = _warning_box(self._target_error)
            target_layout.addWidget(self._target_error_box)
            self._add_repair_actions(target_layout)
            target_layout.addStretch(1)
            target_layout.addWidget(self._target_confirmed)
            self._pages.addWidget(target)
        else:
            # These controls stay owned by the dialog without appearing in the
            # nested guide; the common-AI guide owns the target-app trial.
            for widget in (self._target, self._target_confirmed, self._open_target_button,
                           self._target_hint, self._target_error):
                widget.setParent(self)
                widget.hide()
        self._body = QScrollArea(objectName="voiceSetupBody")
        self._body.setWidgetResizable(True)
        self._body.setFrameShape(QFrame.NoFrame)
        self._body.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._body.setWidget(self._pages)
        layout.addWidget(self._body, 1)
        # 高取最高的那一步，换步不跳（弹窗定稿 B v0.4）。
        self.use_steps(self._pages)
        # The prerequisite belongs beside the fixed Continue button, not below
        # optional editors in a scroll area where users cannot see why it is off.
        self._binding_footer = QWidget(objectName="voiceBindingFooter")
        footer = QVBoxLayout(self._binding_footer)
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(6)
        footer.addWidget(self._shortcut_summary)
        footer.addWidget(self._compatibility_notice)
        footer.addWidget(self._toggle_supported)
        footer.addWidget(self._adapt_notice)
        layout.addWidget(self._binding_footer)
        self._adapt_error_box = _warning_box(self._adapt_error)
        layout.addWidget(self._adapt_error_box)
        self.close_button.setObjectName("voiceSetupLater")
        self._back = self.add_button("返回", "secondary", object_name="voiceSetupBack")
        self._back.clicked.connect(lambda: self._step(max(0, self._pages.currentIndex() - 1)))
        self.button_row().addWidget(self._adapt_button)
        self._next = self.add_button("", "primary", object_name="voiceSetupNext")
        self._next.clicked.connect(self._next_step)
        self.practice.completed_changed.connect(self._practice_completed)
        self._physical_confirmed.toggled.connect(self._refresh)
        self._target_confirmed.toggled.connect(self._refresh)
        self._toggle_supported.toggled.connect(self._refresh)
        self.provider.currentIndexChanged.connect(self._provider_changed)
        self._target.currentIndexChanged.connect(self._target_changed)
        self._provider_changed()
        self._target_changed()
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
        self._step(0)
        if self._is_doubao_macos() and self._doubao_use_mode == "current_input":
            self._current_input_only.setChecked(True)
        if recovery_reason is not None:
            if recovery_reason != "no_voice":
                self._doubao_prerequisite_confirmed = True
            self._repair(recovery_reason)
        if self._vm is not None:
            self._vm.changed.connect(self._device_changed)
            self.finished.connect(lambda: self._vm.changed.disconnect(self._device_changed))

    FIXED_HEIGHT = 585   # the fullest step (Mode页 两个弹窗 v0, 08): steps do not jump

    def fit(self) -> None:
        """All three steps are 585 high; what opens inside scrolls instead of growing."""
        super().fit()
        if not self.isWindow():
            return
        extra = self.FIXED_HEIGHT - self.height()
        if extra:
            self._shell_scroll.setFixedHeight(max(0, self._shell_scroll.height() + extra))
            self.setFixedSize(self.width(), self.FIXED_HEIGHT)
        # The steps' own scroll area takes the squeeze, so the footer under it stays in view.
        self._shell_body.setFixedHeight(self._shell_scroll.height())

    def _show_demo(self, shown: bool) -> None:
        self.practice.setVisible(not shown)
        self.demo.setVisible(shown)
        set_translatable_text(self._demo_toggle, "不会操作？看演示 ›")
        self._demo_toggle.setVisible(not shown)

    def _refresh_status(self, allowed: bool) -> None:
        """上次已在 {应用} 验证 (green tick), or 设置改过了 (orange wait) when it changed since."""
        record = self._trial_record
        if not allowed or not record:
            self._status_row.hide()
            return
        target = record.get("target", "other")
        app = AI_BY_KEY[target].name if target in AI_BY_KEY else translate_ui_text("目标应用")
        same = (record.get("provider") == self.provider.currentData()
                and _gesture(record.get("action", {})) == _gesture(self._requested_action))
        if same:
            self._status_icon.set_state("ok")
            self._status_title.setText(translate_ui_text("上次已在 {app} 验证").format(app=app))
            self._status_note.setText(translate_ui_text("这颗键可以直接用。要换软件或快捷键，再往下改。"))
        else:
            self._status_icon.set_state("wait")
            self._status_title.setText(translate_ui_text("设置改过了，要重新试用一次"))
            self._status_note.setText(translate_ui_text("上次在 {app} 验证过的是改之前的设置。").format(app=app))
        self._status_row.show()

    def _add_repair_actions(self, layout):
        panel = VoiceRecoveryPanel(
            toggle_name="voiceRecoveryToggle",
            options_name="voiceRecoveryOptions",
            button_prefix="voiceRepair_",
        )
        panel.outcome_selected.connect(self._repair)
        layout.addWidget(panel)
        self._repair_groups.append(
            (panel.toggle, panel.options, list(panel.buttons.values()))
        )

    def _toggle_other_use(self, checked):
        self._other_use_panel.setVisible(checked)
        self._other_use_toggle.setProperty("expanded", checked)
        set_translatable_accessible_name(self._other_use_toggle, "收起其他使用方式" if checked else "展开其他使用方式")
        if checked:
            self._current_input_only.setFocus()
        else:
            self._other_use_toggle.setFocus()

    def _toggle_shortcuts(self, checked):
        self._shortcut_panel.setVisible(checked)
        self._shortcut_toggle.setProperty("expanded", checked)
        set_translatable_accessible_name(self._shortcut_toggle, "收起快捷键调整" if checked else "展开快捷键调整")
        if checked and self._shortcut_editors:
            self._shortcut_editors[0][1].setFocus()
        elif not checked:
            self._shortcut_toggle.setFocus()

    def _use_mode_changed(self, current_only):
        self._doubao_use_mode = "current_input" if current_only else "global"
        self._doubao_prerequisite_confirmed = False
        self._step(self._pages.currentIndex())

    def _reverify(self):
        self._history_active = False
        self._recovery_reason = None
        # Reverification is about the mapping that is currently active on the
        # device.  A separate, unapplied editor candidate remains in the main
        # window and must not turn this historical action into a hidden block.
        self._requested_action = copy.deepcopy(self.action)
        self._name_needs_apply = False
        self._custom_shortcut_chosen = self.action.get("type") == "key_gesture"
        self._syncing_shortcuts = True
        for prefix, editor in self._shortcut_editors:
            editor.set_action({
                "type": "key",
                "usage": self._requested_action[prefix + "usage"],
                "modifiers": self._requested_action.get(prefix + "modifiers", []),
            })
        self._syncing_shortcuts = False
        self.practice.set_action(self.action)
        self._doubao_prerequisite_confirmed = True
        self._issue_notice.hide()
        self.practice.reset()
        self._step(1)

    def _change_settings(self):
        self._history_active = False
        self._recovery_reason = None
        self._doubao_prerequisite_confirmed = False
        self._refresh()

    def _repair(self, reason):
        messages = {
            "no_voice": "你现在是把豆包作为当前输入法，还是希望不切换输入法使用？根据下方的使用方式确认后再试一次。",
            "no_text": "豆包已经唤起。请先确认麦克风可用并说一句完整的话，然后重新练习。",
            "cant_stop": "请在豆包的语音设置中选择“按一次开始，再按一次结束”的免按方式，然后重试。",
            "conflict": "请暂时退出同时响应这个快捷键的其他语音软件，然后重试。Console 不会自动退出其他软件。",
            "send": "查看目标应用使用 Enter 还是组合键发送，只调整“双击 · 发送文字”，再用实体键试一次。",
        }
        if not self._is_doubao_macos():
            messages.update({
                "no_voice": "先打开输入法设置，核对启停快捷键及短按启停方式；豆包还需要切为当前输入法或开启全局唤起。确认后重新录制并试用。",
                "conflict": "多个软件响应同一快捷键。请在输入法中选择未被其他语音软件使用的快捷键，再回到这里重新录制。不会自动修改或退出其他软件。",
            })
        remove_voice_trial(
            self._settings, self._serial, self._profile_id, self._control_id,
            self.action, self.provider.currentData(), mode=self._mode,
        )
        self._history_active = False
        self._recovery_reason = reason
        set_translatable_text(self._issue_notice, messages[reason])
        self._issue_notice.show()
        if reason != "send":
            self._toggle_supported.setChecked(False)
        self.practice.reset()
        self._doubao_physical_confirmed = False
        self._doubao_target_confirmed = False
        self._target_confirmed.setChecked(False)
        self._step(1 if self._is_doubao_macos() and reason == "no_text" else 0)
        if self._is_doubao_macos() and reason == "no_voice":
            self._other_use_toggle.setChecked(True)
        self._custom_toggle.setChecked(reason == "send")
        if self._is_doubao_macos() and reason == "cant_stop":
            self._shortcut_toggle.setChecked(True)
        for toggle, _, _ in self._repair_groups:
            toggle.setChecked(False)

    def _shortcut_problem(self):
        if not self.provider.currentData() or not self._custom_shortcut_chosen:
            return ""
        if adapted_voice_action(self._requested_action, self.provider.currentData(), self._sys_platform()) != self._requested_action:
            return ("豆包不接受这个快捷键。请录制它当前使用的 Control、Option、Command 单键或组合键。"
                    if self.provider.currentData() == "doubao" else
                    "请录制千问当前使用的右 Option 或右 Command。")
        return ""

    def _is_doubao_macos(self):
        return self._is_mac() and self.provider.currentData() == "doubao"

    def _shortcut_was_confirmed(self):
        saved = (confirmed_voice_choice(
            self._settings, self._serial, self._profile_id, mode=self._mode,
        ) if self._settings is not None else None)
        if (saved and saved.get("provider") == self.provider.currentData()
                and _activation(saved["action"]) == _activation(self._requested_action)):
            return True
        return bool(
            self._trial_record
            and self._trial_record.get("provider") == self.provider.currentData()
            and _activation(self._trial_record.get("action", {}))
            == _activation(self._requested_action)
        )

    def _effective_shortcut_problem(self):
        problem = self._shortcut_problem()
        return "" if problem and self._shortcut_was_confirmed() else problem

    def _recommended_action(self):
        return adapted_voice_action(self._requested_action, self.provider.currentData(), self._sys_platform())

    def commit_provider_choice(self):
        """Commit the deferred macOS Doubao provider only after target success."""
        provider = self.provider.currentData()
        if self._settings is None or not provider:
            return
        self._settings.setValue(self._provider_key, provider)
        self._settings.sync()

    def _host_platform(self):
        return voice_presets.host_platform(self._platform_override)

    def _is_mac(self):
        return self._host_platform() == voice_presets.MACOS

    def _sys_platform(self):
        return voice_presets.sys_platform_of(self._host_platform())

    def _use_provider_preset(self):
        """Apply this platform's one-click preset for the chosen software."""
        provider = self.provider.currentData()
        action = voice_presets.preset_action(provider, self._host_platform())
        if action is None:
            return
        self._requested_action = action
        self._custom_shortcut_chosen = True
        self._syncing_shortcuts = True
        for prefix, editor in self._shortcut_editors:
            editor.set_action({
                "type": "key",
                "usage": self._requested_action[prefix + "usage"],
                "modifiers": self._requested_action.get(prefix + "modifiers", []),
            })
        self._syncing_shortcuts = False
        self._toggle_supported.setChecked(False)
        self._adapt_error.clear()
        self.practice.reset()
        self._target_confirmed.setChecked(False)
        self._refresh()

    @property
    def doubao_use_mode(self):
        return self._doubao_use_mode

    @property
    def target_key(self):
        return self._target.currentData()

    def _voice_app_path(self):
        provider = self.provider.currentData()
        if provider == "typeless":
            return installed_desktop_app("Typeless")
        # B-06: located by bundle identifier, never by a hard-coded path.
        # QianwenIMEService is a background helper, not a settings launcher.
        return voice_presets.settings_app(provider, self._host_platform(), lookup=self._settings_lookup)

    def _open_voice(self):
        path = self._voice_app_path()
        if not path or not QProcess.startDetached("/usr/bin/open", [str(path)])[0]:
            set_translatable_text(self._binding_hint, "未能打开，请手动打开语音软件。")

    def _provider_changed(self):
        provider = self.provider.currentData()
        self._recovery_reason = None
        entering_deferred = (self._is_mac()
                             and (provider == "doubao" or self._initial_provider == "doubao")
                             and not self._provider_commit_deferred)
        if entering_deferred:
            self._provider_commit_deferred = True
            # Earlier non-Doubao selections keep their legacy immediate-save
            # behavior, but once this becomes a Doubao draft, cancellation must
            # restore the value from before the dialog opened.
            if self._settings is not None:
                if self._initial_provider_exists:
                    self._settings.setValue(self._provider_key, self._initial_provider)
                else:
                    self._settings.remove(self._provider_key)
                self._settings.sync()
        if self._trial_record is not None and provider != self._trial_record.get("provider"):
            self._history_active = False
        self._doubao_prerequisite_confirmed = False
        self._doubao_physical_confirmed = False
        self._doubao_target_confirmed = False
        # A compatible preset is not evidence of the user's actual IME binding.
        # Keep the candidate visible for confirmation; never silently substitute a key.
        self._syncing_shortcuts = True
        for prefix, editor in self._shortcut_editors:
            editor.set_action({"type": "key", "usage": self._requested_action[prefix + "usage"],
                               "modifiers": self._requested_action.get(prefix + "modifiers", [])})
            if not prefix and not self._custom_shortcut_chosen:
                editor._field_widgets["usage"].setCurrentIndex(-1)
                editor._shortcut_recorder.set_action({"type": "none"})
        self._syncing_shortcuts = False
        if (self._is_doubao_macos() and self._requested_action.get("type") == "key_gesture"
                and not self._shortcut_problem()):
            self._custom_shortcut_chosen = True
        self._custom_toggle.setVisible(bool(provider) and bool(self._shortcut_editors))
        self._custom_toggle.setChecked(False)
        self._shortcut_panel.setVisible(bool(provider) and bool(self._shortcut_editors)
                                        and not self._is_doubao_macos())
        typeless = provider == "typeless"
        # B-05: the same per-platform hint text as the device-wide dialog.
        hint = voice_presets.hint(provider, self._host_platform())
        if (not hint and voice_presets.BY_KEY.get(provider) is not None
                and voice_presets.settings_app(provider, self._host_platform(), lookup=self._settings_lookup) is None
                and voice_presets.BY_KEY[provider].settings_bundle_ids):
            hint = voice_presets.settings_fallback_text(provider, self._host_platform())
        set_translatable_text(self._binding_hint, hint or "打开语音输入软件，确认这里的快捷键与它的启停快捷键一致。")
        set_translatable_text(self._open_voice_button, "打开 Typeless" if typeless else "打开输入法设置")
        self._open_voice_button.setVisible(self._voice_app_path() is not None)
        software = voice_presets.BY_KEY.get(provider)
        self._toggle_supported.setText(  # Mode页 两个弹窗 v0, 08: the software's name in it
            translate_ui_text("{software} 里的快捷键我已经设好了").format(software=software.label)
            if software is not None and provider != "other" else translate_ui_text("已确认快捷键和启停方式"))
        self._toggle_supported.setVisible(bool(provider) and not self._is_doubao_macos())
        self._toggle_supported.setChecked(False)
        set_translatable_text(self._binding_help,
            "仅支持按住说话的输入方式暂不适用。若多个语音软件同时响应，请为它们使用不同快捷键；修改输入法后，在这里重新录制。")
        self._adapt_error.clear()
        self.practice.reset()
        self._target_confirmed.setChecked(False)
        if self._settings is not None and provider and not self._provider_commit_deferred:
            self._settings.setValue(self._provider_key, provider)
        self._step(0)

    def _shortcut_changed(self, prefix, editor):
        if self._syncing_shortcuts:
            return
        shortcut = editor.action()
        if shortcut.get("usage") is None:
            return
        editor._shortcut_recorder.set_action(shortcut)
        self._requested_action[prefix + "usage"] = shortcut["usage"]
        self._requested_action[prefix + "modifiers"] = shortcut.get("modifiers", [])
        if not prefix:
            self._custom_shortcut_chosen = True
            self._toggle_supported.setChecked(False)
        self._adapt_error.clear()
        self.practice.reset()
        self._target_confirmed.setChecked(False)
        self._refresh()

    def _adaptation_needed(self):
        return (_gesture(self._requested_action) != _gesture(self.action)
                or self._name_needs_apply
                or (self._is_doubao_macos()
                    and bool(self._effective_shortcut_problem())))

    def _device_available(self):
        if self._vm is None:
            return False
        vm = self._vm
        snapshot = vm.model.snapshot
        return (vm.model.state is AppState.READY and snapshot is not None
                and snapshot.identity.get("serial") == self._serial
                and snapshot.active_profile_id == self._profile_id
                and snapshot.compatibility.get("write") is True
                and not vm.write_transaction.blocks_editing
                and not vm.firmware_update.blocks_editing
                and not vm.calibration.blocks_editing)

    def _runtime_ready(self):
        if self._vm is None:
            return True
        snapshot = self._vm.model.snapshot
        return (snapshot is not None
                and snapshot.status.get("operating_mode", "normal") == self._mode)

    def _apply_adaptation(self):
        if not self._context_ready or not self._device_available() or not self._adaptation_needed():
            return
        vm = self._vm
        provider = self.provider.currentData()
        doubao = self._is_doubao_macos()
        shortcut_problem = (
            self._effective_shortcut_problem() if doubao else self._shortcut_problem()
        )
        if not provider or (not doubao and (
                not self._custom_shortcut_chosen or not self._toggle_supported.isChecked())):
            return
        if doubao and shortcut_problem and not self._shortcut_toggle.isChecked():
            expected = self._recommended_action()
            self._requested_action = copy.deepcopy(expected)
            self._custom_shortcut_chosen = True
        else:
            expected = copy.deepcopy(self._requested_action)
        try:
            if (adapted_voice_action(expected, provider, self._sys_platform()) != expected
                    and not (doubao and self._shortcut_was_confirmed())):
                raise ValueError("这个快捷键不适用于所选输入法，请重新选择。")
            # A failed attempt may be retried, but must not absorb other edits.
            owns_draft = self._adapt_candidate is not None and vm.draft.config == self._adapt_candidate
            retry = owns_draft and expected == self._adapt_expected
            if (vm.draft.is_dirty and not owns_draft
                    and not voice_draft_is_scoped(vm.draft.config, vm.model.snapshot, self._control_id, self._mode)):
                raise ValueError("你还有未应用的修改。返回首页处理后，再继续设置。")
            self._adapt_error.clear()
            self._adapting = True
            self._adapt_expected = expected
            if not retry:
                if not owns_draft:
                    # A scoped edit may predate this guide. Retain that exact
                    # draft, not just the device baseline, across failed retries.
                    self._adapt_previous_config = copy.deepcopy(vm.draft.config)
                if self._mode == "codex":
                    vm.set_codex_voice(self._profile_id, expected)
                else:
                    mapping = vm.model.snapshot.mappings.get(self._control_id, {})
                    name = self._short_name if self._short_name is not None else mapping.get("short_name", "")
                    vm.set_mapping(self._profile_id, self._control_id, name, expected)
                self._adapt_candidate = copy.deepcopy(vm.draft.config)
            vm.prepare_device_write(confirm_after_validation=True)
        except ValueError as exc:
            self._adapting = False
            set_translatable_text(self._adapt_error, str(exc))
        self._refresh()

    def _device_changed(self, *_):
        if self._adapting:
            transaction = self._vm.write_transaction
            snapshot = self._vm.model.snapshot
            if (transaction.state is ConfigTransactionState.ACTIVE and self._device_available()
                    and self._adapt_candidate is not None
                    and configs_match_readback(self._adapt_candidate, snapshot.config)):
                self.action = copy.deepcopy(self._adapt_expected)
                self._name_needs_apply = False
                self._adapting = False
                self._adapt_candidate = None
                self._adapt_previous_config = None
                self._custom_toggle.setChecked(False)
                self.practice.set_action(self.action)
                self.practice.reset()
                self.action_applied.emit(copy.deepcopy(self.action))
                if self._is_doubao_macos() and self._recovery_reason == "send":
                    self._recovery_reason = None
                    self._issue_notice.hide()
                    self._step(1)
                elif (not self._is_doubao_macos()
                        and self._pages.currentIndex() == 0 and self._can_continue()):
                    self._issue_notice.hide()
                    self._step(1)
            elif transaction.state in {ConfigTransactionState.FAILED, ConfigTransactionState.CONFLICT}:
                self._adapting = False
                set_translatable_text(self._adapt_error, transaction.message)
        self._refresh()

    def reject(self):
        if self._adapting and (self._vm is None or self._vm.write_transaction.blocks_editing):
            return
        if (self._vm is not None and self._adapt_candidate is not None
                and self._vm.draft is not None
                and self._vm.draft.serial == self._serial
                and self._vm.draft.config == self._adapt_candidate
                and not self._vm.write_transaction.blocks_editing):
            self._vm._before_draft_change()
            self._vm.draft.config = copy.deepcopy(self._adapt_previous_config)
            self._vm._notify_draft_changed()
            self._adapt_candidate = None
            self._adapt_previous_config = None
        super().reject()

    def _target_changed(self):
        self._target_confirmed.setChecked(False)
        self._target_error.clear()
        app = AI_BY_KEY.get(self.target_key)
        self._open_target_button.setVisible(self._include_target and app is not None)
        if app:
            set_translatable_text(self._open_target_button, "打开桌面应用" if installed_desktop_app(app.name) else "打开官网")
        set_translatable_text(self._target_hint, "切到目标应用，点输入框：单击说话，再单击结束。等文字出现后，双击同一颗键发送。")
        self._refresh()

    def _open_target(self):
        app = AI_BY_KEY.get(self.target_key)
        if app is None:
            return
        path = installed_desktop_app(app.name)
        success = (QProcess.startDetached("/usr/bin/open", [str(path)])[0] if path
                   else QDesktopServices.openUrl(QUrl(app.url)))
        if not success:
            self._target_error.setText(  # Mode页 两个弹窗 v0, 09
                translate_ui_text("没打开 {app}：这台电脑上好像没装。点“打开官网”在网页里试也一样。").format(app=app.name)
                if path else translate_ui_text("未能打开，请手动切换到你的 AI 应用。"))
            self._refresh()

    def set_context_ready(self, ready):
        # Temporary disconnects must not erase what the user is practising.
        # The caller checks device, profile and mapping before resuming.
        self._context_ready = bool(ready)
        self._context_notice.setVisible(not self._context_ready and not self._adapting)
        self._refresh()

    def _can_continue(self):
        if not self.provider.currentData() or not self._context_ready or self._adapting or self._adaptation_needed():
            return False
        if not self._runtime_ready():
            return False
        if self._is_doubao_macos():
            if self._history_active:
                return False
            index = self._pages.currentIndex()
            if index == 0:
                return not self._effective_shortcut_problem()
            if index == 1:
                return self._doubao_prerequisite_confirmed and self.practice.completed
            return (self._doubao_prerequisite_confirmed
                    and self._doubao_physical_confirmed)
        binding_ready = self._toggle_supported.isChecked() and self._custom_shortcut_chosen and not self._shortcut_problem()
        index = self._pages.currentIndex()
        if index == 0:
            return binding_ready
        local_ready = binding_ready and self.practice.completed and self._physical_confirmed.isChecked()
        return local_ready and (index == 1 or self._target_confirmed.isChecked())

    def _refresh(self, *_):
        adapting = self._adapting
        needs = self._adaptation_needed()
        provider = self.provider.currentData()
        doubao = self._is_doubao_macos()
        shortcut_problem = (
            self._effective_shortcut_problem() if doubao else self._shortcut_problem()
        )
        history = doubao and self._history_active
        if doubao:
            hint = ("切换到豆包输入法后使用 BORING 语音键；这种方式不需要开启全局唤起。"
                    if self._doubao_use_mode == "current_input" else
                    "在豆包的“语音输入”中开启全局唤起，并完成 macOS 弹出的系统确认。已经设置好可直接继续。")
            set_translatable_text(self._binding_hint, hint)
        self.provider.setEnabled(not adapting)
        self._shortcut_panel.setEnabled(not adapting)
        self._history_panel.setVisible(history and self._pages.currentIndex() == 0)
        if history:
            target = self._trial_record.get("target", "other")
            target_name = AI_BY_KEY[target].name if target in AI_BY_KEY else "目标应用"
            set_translatable_text(self._history_label, f"上次已在 {target_name} 验证。这是上次成功的记录，不代表豆包的实时系统状态。")
        binding_visible = self._pages.currentIndex() == 0 and not history
        preset = voice_presets.preset(provider, self._host_platform())
        self.preset_button.setText(f"使用预设：{preset.summary}" if preset is not None else "")
        self.preset_button.setVisible(binding_visible and preset is not None
                                      and provider != "typeless" and not self._is_doubao_macos())
        self.preset_button.setEnabled(not adapting)
        send_recovery = binding_visible and doubao and self._recovery_reason == "send"
        self._provider_prompt.setVisible(binding_visible)
        self.provider.setVisible(binding_visible)
        self._binding_footer.setVisible(binding_visible)
        self._open_voice_button.setVisible(binding_visible and self._voice_app_path() is not None)
        self._other_use_toggle.setVisible(binding_visible and doubao)
        self._other_use_panel.setVisible(binding_visible and doubao and self._other_use_toggle.isChecked())
        self._shortcut_toggle.setVisible(
            binding_visible and doubao and bool(provider)
            and not shortcut_problem and not send_recovery
        )
        if doubao:
            self._shortcut_panel.setVisible(
                binding_visible and self._shortcut_toggle.isChecked() and not send_recovery
            )
            self._custom_toggle.setVisible(binding_visible and self._shortcut_toggle.isChecked()
                                           and not send_recovery
                                           and bool(self._shortcut_editors))
            self._send_panel.setVisible(
                send_recovery or (
                    self._custom_toggle.isVisible() and self._custom_toggle.isChecked()
                )
            )
            if not self._custom_toggle.isVisible() and not send_recovery:
                self._send_panel.hide()
        self._binding_hint.setVisible(binding_visible and bool(provider))
        self._help_button.setVisible(binding_visible and bool(provider) and not doubao)
        self._help_box.setVisible(self._help_button.isVisible() and self._help_button.isChecked())
        self._refresh_status(binding_visible and not doubao)
        self._adapt_error_box.setVisible(bool(self._adapt_error.text()))
        if hasattr(self, "_target_error_box"):
            self._target_error_box.setVisible(bool(self._target_error.text()))
        if not provider:
            notice = "先选择你使用的语音输入软件。"
        elif doubao and shortcut_problem:
            notice = "当前语音键不适用于豆包，需要先应用推荐快捷键。"
        elif not self._custom_shortcut_chosen:
            notice = "先录制输入法当前使用的启停快捷键。"
        elif shortcut_problem or not self._context_ready:
            notice = ""  # The specific compatibility/context message is visible.
        elif adapting:
            notice = "正在保存并核对设备，请稍候。"
        elif not doubao and not self._toggle_supported.isChecked():
            notice = "请确认上方的快捷键和启停方式，再继续。"
        elif needs and not self._device_available():
            notice = "等待设备就绪后继续。"
        elif needs:
            notice = "保存成功后，自动进入实体练习。"
        elif doubao and not self._shortcut_was_confirmed():
            notice = "请核对豆包当前显示的免按快捷键与上方摘要一致。"
        else:
            notice = "设置已匹配设备，可以开始实体练习。"
        set_translatable_text(self._adapt_notice, notice)
        self._adapt_notice.setVisible(bool(notice))
        self._adapt_button.setVisible(not history and needs and bool(provider) and self._pages.currentIndex() == 0)
        adapt_ready = (not adapting and bool(provider) and self._context_ready and self._device_available())
        if doubao:
            adapt_ready = adapt_ready and (
                (bool(shortcut_problem) and not self._shortcut_toggle.isChecked())
                or (self._custom_shortcut_chosen and not shortcut_problem)
            )
        else:
            adapt_ready = (adapt_ready and self._custom_shortcut_chosen
                           and self._toggle_supported.isChecked() and not shortcut_problem)
        self._adapt_button.setEnabled(adapt_ready)
        unknown = adapting and self._vm.write_transaction.state is ConfigTransactionState.UNKNOWN
        adapt_label = "应用豆包推荐快捷键" if doubao and shortcut_problem else "保存快捷键"
        set_translatable_text(self._adapt_button, "等待连接确认" if unknown else ("正在应用…" if adapting else adapt_label))
        self._toggle_supported.setVisible(binding_visible and bool(provider) and not doubao)
        self._toggle_supported.setEnabled(not adapting and self._custom_shortcut_chosen and not self._shortcut_problem())
        self._context_notice.setVisible((not self._context_ready or not self._runtime_ready()) and not adapting)
        if self._context_ready and not self._runtime_ready():
            set_translatable_text(self._context_notice,
                "可以先保存语音键设置；实体练习前请双击旋钮切换到 Codex 模式。" if self._mode == "codex" else
                "可以先保存语音键设置；实体练习前请双击旋钮切换到普通模式。")
        else:
            set_translatable_text(self._context_notice,
                "请连接原设备，切回 CODEX 模式，并应用当前语音键设置。" if self._mode == "codex" else
                "请连接原设备，切回普通模式，并应用当前语音键设置。")
        self.practice.setEnabled(self._context_ready and self._runtime_ready() and not adapting)
        set_translatable_text(self._compatibility_notice, shortcut_problem)
        self._compatibility_notice.setVisible(bool(provider) and bool(shortcut_problem))
        self._shortcut_summary.setVisible(bool(provider) and self._custom_shortcut_chosen)
        if self._custom_shortcut_chosen:
            keys = [describe_action({"type": "key", "usage": self._requested_action[prefix + "usage"],
                                     "modifiers": self._requested_action.get(prefix + "modifiers", [])},
                                    platform=self._host_platform())
                    for prefix in ("", "double_")]
            set_translatable_text(self._shortcut_summary, f"单击 {keys[0]} / 双击 {keys[1]}")
        for toggle, options, buttons in self._repair_groups:
            toggle.setVisible(doubao and not history)
            options.setVisible((toggle.isChecked() if doubao else True) and not history)
            for button in buttons:
                reason = button.objectName().removeprefix("voiceRepair_")
                button.setVisible(doubao or reason in {"no_voice", "conflict", "send"})
            if not doubao:
                labels = {"no_voice": "按键没反应", "conflict": "同时唤起其他软件", "send": "双击没有正确发送"}
                for button in buttons:
                    reason = button.objectName().removeprefix("voiceRepair_")
                    if reason in labels:
                        set_translatable_text(button, labels[reason])
        self._physical_confirmed.setVisible(
            not doubao and self.practice.completed and self._pages.currentIndex() == 1
        )
        self._target_confirmed.setVisible(not doubao and self._include_target)
        self._next.setEnabled(self._can_continue())
        self._next.setVisible(not history and not (needs and provider and self._pages.currentIndex() == 0))

    def _step(self, index):
        self._pages.setCurrentIndex(index)
        self._back.setVisible(index > 0)
        self.set_step(f"{translate_ui_text('设置语音输入')} · {index + 1} / {self._pages.count()}")
        if self._is_doubao_macos():
            headings = ("启用豆包", "用 BORING 键练习", "到常用 AI 试用")
            set_translatable_text(self._heading, headings[index])
            label = (("我已切换，开始按键练习" if self._doubao_use_mode == "current_input"
                      else "我已完成，开始按键练习") if index == 0 else
                     "我已用 BORING 键完成练习" if index == 1 else
                     "可以正常使用")
        else:
            set_translatable_text(self._heading, ("确认现有快捷键", "亲手练习", "到常用 AI 试用")[index])
            label = "开始实体练习" if index == 0 else (
                "去常用 AI 试用" if index == 1 and self._include_target else
                "完成设置" if self._include_target else "完成练习")
        set_translatable_text(self._next, label)
        self._refresh()
        if index == 1 and not self.practice.completed:
            self.practice.input.setFocus()

    def _practice_completed(self, completed):
        self._doubao_physical_confirmed = False
        self._physical_confirmed.setChecked(False)
        self._physical_confirmed.setVisible(completed and not self._is_doubao_macos())
        self._target_confirmed.setChecked(False)
        self._refresh()

    def accept(self):
        # Enter/default-dialog handling must not bypass a trial or confirmation.
        if self._is_doubao_macos():
            final_confirmed = (self._doubao_target_confirmed if self._include_target
                               else self._doubao_physical_confirmed)
            if self._pages.currentIndex() == self._pages.count() - 1 and final_confirmed:
                super().accept()
        elif self._pages.currentIndex() == self._pages.count() - 1 and self._can_continue():
            super().accept()

    def _next_step(self):
        if not self._can_continue():
            return
        if self._is_doubao_macos():
            index = self._pages.currentIndex()
            if index == 0:
                self._doubao_prerequisite_confirmed = True
                self._issue_notice.hide()
                self._step(1)
            elif index == 1:
                self._doubao_physical_confirmed = True
                if self._include_target:
                    self._issue_notice.hide()
                    self._step(2)
                else:
                    self.accept()
            else:
                self._doubao_target_confirmed = True
                self.accept()
            return
        if self._pages.currentIndex() < self._pages.count() - 1:
            self._issue_notice.hide()
            self._step(self._pages.currentIndex() + 1)
        else:
            self.accept()
