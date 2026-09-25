"""Voice setup with an optional visual demo and a local, real-input practice."""
import copy
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal, QProcess, QUrl
from PySide6.QtGui import QColor, QIcon, QPainter, QDesktopServices
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QProgressBar,
    QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget,
)

from controller_config.accessibility import system_reduces_motion
from controller_config.actions import describe_action
from controller_config.ai_setup import AI_APPLICATIONS, AI_BY_KEY, installed_desktop_app, load_choices
from controller_config.i18n import set_translatable_text
from controller_config.models import AppState
from controller_config.transactions import ConfigTransactionState, configs_match_readback
from controller_config.voice_setup import (voice_provider_key, voice_provider, adapted_voice_action,
    recommended_voice_action,
    voice_draft_is_scoped, confirmed_voice_choice,
    confirmed_voice_choice_for_provider, _gesture)
from controller_config.views.voice_practice import VoicePractice

_ICONS = Path(__file__).resolve().parents[1] / "assets" / "onboarding" / "icons"

_PROVIDER_HELP_TEXTS = {
    "typeless": "打开 Typeless 设置 → 快捷键 → 语音输入，在快捷键框中按一下设备语音键；看到 F13 后返回这里确认。",
    "doubao": "从 Mac 顶部输入法菜单切换到豆包，打开豆包设置 → 免按模式，将快捷键设为右 Control；也可以开启全局唤起。",
    "qianwen": "从 Mac 顶部输入法菜单切换到千问，打开千问设置 → 语音输入，将快捷键设为右 Option，并开启“短按也唤起语音输入”。",
    "other": "先在语音输入软件中找到开始／结束快捷键，再回到这里录制同一个电脑快捷键。仅支持按住说话的软件暂不适用。",
}

_BINDING_CONFIRMATION_TEXTS = {
    "typeless": ("我已在 Typeless 中看到 F13", "✓ Typeless 快捷键已确认"),
    "doubao": ("我已在豆包中设为右 Control", "✓ 豆包快捷键已确认"),
    "qianwen": ("我已在千问中设为右 Option 并开启短按", "✓ 千问快捷键已确认"),
    "other": ("我已在语音软件中完成快捷键设置", "✓ 输入软件快捷键已确认"),
}


def _guide_icon(name: str, size: int):
    pixmap = QIcon(str(_ICONS / f"{name}.svg")).pixmap(size, size)
    painter = QPainter(pixmap)
    painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
    painter.fillRect(pixmap.rect(), QColor("#FF6A00"))
    painter.end()
    return pixmap


class VoiceDemo(QFrame):
    key_pressed = Signal()
    # The second double-click pulse uses a real double-click interval.
    _frames = (
        ("按一下 · 开始", "", "hand-tap", 900, True),
        ("说话", "", "mic", 1800, False),
        ("再按一下 · 结束", "", "hand-tap", 900, True),
        ("等文字出现", "", "wait", 1400, False),
        ("文字已出现", "这是一条语音输入", "text", 1200, False),
        ("双击 · 发送", "这是一条语音输入", "hand-tap", 170, True),
        ("双击 · 发送", "这是一条语音输入", "hand-tap", 700, True),
        ("演示结束", "", "check", 1000, False),
    )

    def __init__(self, parent=None):
        super().__init__(parent, objectName="voiceDemo")
        self.setProperty("cardRole", "widget")
        self._reduced_motion = system_reduces_motion()
        self._index = 0
        self._has_played = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._advance)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        header = QHBoxLayout()
        header.addWidget(QLabel("操作演示", objectName="muted"))
        header.addStretch(1)
        self._play = QPushButton("播放演示", objectName="voiceDemoReplay")
        self._play.setProperty("buttonRole", "ghost")
        self._play.clicked.connect(self._toggle)
        header.addWidget(self._play)
        layout.addLayout(header)
        self._icon = QLabel()
        self._icon.setAlignment(Qt.AlignCenter)
        self._icon.setFixedHeight(38)
        layout.addWidget(self._icon)
        self._caption = QLabel(objectName="voiceDemoCaption")
        self._caption.setAlignment(Qt.AlignCenter)
        self._caption.setWordWrap(True)
        layout.addWidget(self._caption)
        self._text = QLineEdit(objectName="voiceDemoText")
        self._text.setReadOnly(True)
        self._text.setFocusPolicy(Qt.NoFocus)
        self._text.setPlaceholderText("输入框示意")
        self._text.setAccessibleName("演示输入框，不会发送内容")
        layout.addWidget(self._text)
        self._activity = QProgressBar(objectName="voiceDemoActivity")
        self._activity.setTextVisible(False)
        self._activity.setFixedHeight(4)
        layout.addWidget(self._activity)
        self._show_frame()

    def _show_frame(self):
        caption, text, icon, _, press = self._frames[self._index]
        set_translatable_text(self._caption, caption)
        set_translatable_text(self._text, text)
        if icon == "mic":
            # Reuse the app's existing native-symbol loader.
            from controller_config.views.main_window import _navigation_icon
            symbol = _navigation_icon("mic.fill", QIcon(_guide_icon("hand-tap", 32)), white=.95)
            pixmap = symbol.pixmap(32, 32)
        else:
            pixmap = _guide_icon({
                "wait": "arrows-clockwise", "text": "check",
            }.get(icon, icon), 32)
        self._icon.setPixmap(pixmap)
        busy = icon in {"mic", "wait"} and not self._reduced_motion
        self._activity.setRange(0, 0 if busy else 8)
        if not busy:
            self._activity.setValue(self._index + 1)
        if press:
            self.key_pressed.emit()

    def _toggle(self):
        if self._reduced_motion:
            self._index = (self._index + 1) % len(self._frames)
            # Skip the second double-click frame in static step-by-step mode.
            if self._index == 6:
                self._index = 7
            self._show_frame()
            return
        if self._timer.isActive():
            self._timer.stop()
            set_translatable_text(self._play, "继续演示")
        else:
            if self._index == len(self._frames) - 1:
                self._index = 0
            self._show_frame()
            self._timer.start(self._frames[self._index][3])
            set_translatable_text(self._play, "暂停")

    def _advance(self):
        if self._index < len(self._frames) - 1:
            self._index += 1
            self._show_frame()
            self._timer.start(self._frames[self._index][3])
        else:
            set_translatable_text(self._play, "重播")

    def hideEvent(self, event):  # noqa: N802
        self._timer.stop()
        set_translatable_text(self._play, "播放演示")
        super().hideEvent(event)

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        if self._reduced_motion:
            set_translatable_text(self._play, "下一步演示")
        elif not self._has_played:
            self._has_played = True
            self._toggle()


class VoiceSetupDialog(QDialog):
    """Bind and practise locally, with an optional target-app verification."""

    action_applied = Signal(object)

    def __init__(self, parent=None, *, action=None, settings=None,
                 include_target=True, target_key=None, view_model=None, control_id="key.8", mode="normal",
                 candidate_action=None, short_name=None):
        super().__init__(parent)
        self.setObjectName("voiceSetupDialog")
        self.setStyleSheet(
            "QDialog#voiceSetupDialog { background: #211F1A; }"
            "QPushButton#voiceBindingConfirmed:checked {"
            " background: #EFEAE0; color: #1C1B19; border-color: #EFEAE0; font-weight: 600; }"
        )
        self.setWindowTitle("设置语音输入")
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.resize(480, 620)
        self._include_target = include_target
        self._context_ready = True
        self._settings = settings
        self.action = copy.deepcopy(action if action is not None else
                                    {"type": "key_gesture", "usage": 104, "double_usage": 40})
        self._requested_action = copy.deepcopy(candidate_action or self.action)
        self._short_name = short_name
        self._syncing_shortcuts = False
        self._custom_shortcut_chosen = self.action.get("type") == "key_gesture"
        self._vm = view_model
        self._control_id = control_id
        self._mode = mode
        snapshot = view_model.model.snapshot if view_model is not None else None
        self._name_needs_apply = (snapshot is not None and mode == "normal" and short_name is not None
                                  and short_name != snapshot.mappings.get(control_id, {}).get("short_name", ""))
        self._serial = snapshot.identity.get("serial") if snapshot else None
        self._profile_id = snapshot.active_profile_id if snapshot else None
        self._provider_key = voice_provider_key(self._serial, self._profile_id, mode)
        if settings is not None and mode == "codex":
            saved = confirmed_voice_choice(settings, self._serial, self._profile_id, mode=mode)
            if saved is not None and _gesture(saved["action"]) == _gesture(self._requested_action):
                self._custom_shortcut_chosen = True
        self._adapt_candidate = None
        self._adapt_expected = None
        self._adapting = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        header = QHBoxLayout()
        self._step_number = QLabel(objectName="inspectorTitle")
        header.addWidget(self._step_number, 0, Qt.AlignTop)
        self._heading = QLabel(objectName="inspectorTitle")
        self._heading.setWordWrap(True)
        header.addWidget(self._heading, 1)
        layout.addLayout(header)
        self._mode_context = QLabel(objectName="voiceModeContext")
        self._mode_context.setWordWrap(True)
        set_translatable_text(
            self._mode_context,
            "正在设置：CODEX 模式语音键。普通模式与 CODEX 模式分别保存。"
            if mode == "codex" else
            "正在设置：普通模式语音键。普通模式与 CODEX 模式分别保存。",
        )
        layout.addWidget(self._mode_context)
        self._context_notice = QLabel("请连接原设备，切回 CODEX 模式，并应用当前语音键设置。" if mode == "codex" else "请连接原设备，切回普通模式，并应用当前语音键设置。", objectName="voiceContextNotice")
        self._context_notice.setWordWrap(True)
        self._context_notice.hide()
        self._issue_notice = QLabel(objectName="voiceIssueNotice")
        self._issue_notice.setWordWrap(True)
        self._issue_notice.hide()
        layout.addWidget(self._context_notice)
        layout.addWidget(self._issue_notice)
        self._pages = QStackedWidget()

        binding = QWidget()
        body = QVBoxLayout(binding)
        body.setContentsMargins(0, 0, 0, 0)
        body.addWidget(QLabel("你用哪个软件把说话变成文字？"))
        self.provider = QComboBox(objectName="voiceSoftware")
        self.provider.addItem("请选择语音输入软件", "")
        self.provider.addItem("Typeless", "typeless")
        self.provider.addItem("豆包输入法", "doubao")
        self.provider.addItem("千问输入法", "qianwen")
        self.provider.addItem("其他语音输入软件", "other")
        preferred = voice_provider(
            settings, self._serial, self._profile_id, mode=mode, default=""
        )
        confirmed = confirmed_voice_choice(settings, self._serial, self._profile_id, mode=mode)
        if (confirmed is not None and confirmed.get("provider")
                and _gesture(confirmed["action"]) == _gesture(self._requested_action)):
            preferred = confirmed["provider"]
        self.provider.setCurrentIndex(max(0, self.provider.findData(preferred)))
        self._last_provider = self.provider.currentData()
        body.addWidget(self.provider)
        self._binding_hint = QLabel(objectName="voiceBindingHint")
        self._binding_hint.setWordWrap(True)
        body.addWidget(self._binding_hint)
        self._recommended_button = QPushButton("使用推荐快捷键", objectName="voiceUseRecommended")
        self._recommended_button.setProperty("buttonRole", "secondary")
        self._recommended_button.clicked.connect(self._use_recommended_shortcut)
        body.addWidget(self._recommended_button)
        self._open_voice_button = QPushButton("打开 Typeless", objectName="openVoiceSoftware")
        self._open_voice_button.clicked.connect(self._open_voice)
        body.addWidget(self._open_voice_button)
        self._shortcut_panel = QWidget(objectName="voiceSetupShortcuts")
        shortcuts = QVBoxLayout(self._shortcut_panel)
        shortcuts.setContentsMargins(0, 0, 0, 0)
        self._shortcut_hint = QLabel(objectName="voiceShortcutRecordingHint")
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
            platform = "macos" if sys.platform == "darwin" else "windows"
            for prefix, label in (("", "单击 · 说话／结束"), ("double_", "双击 · 发送文字")):
                destination = send_layout if prefix else shortcuts
                destination.addWidget(QLabel(label))
                editor = ActionEditor(definitions, {"type": "key", "usage": self._requested_action[prefix + "usage"],
                                      "modifiers": self._requested_action.get(prefix + "modifiers", [])},
                                      platform=platform, capture_platform=platform)
                editor.setObjectName("voiceShortcutEditor" if not prefix else "voiceSendShortcutEditor")
                editor._type_selector.hide()  # There is only one supported type here.
                set_translatable_text(editor._shortcut_recorder._capture, "录制电脑快捷键")
                set_translatable_text(editor._manual_toggle, "手动选择快捷键")
                editor.action_changed.connect(lambda p=prefix, e=editor: self._shortcut_changed(p, e))
                destination.addWidget(editor)
                self._shortcut_editors.append((prefix, editor))
        self._shortcut_toggle = QPushButton("使用其他快捷键…", objectName="voiceShortcutCustomize")
        self._shortcut_toggle.setCheckable(True)
        self._shortcut_toggle.setProperty("buttonRole", "secondary")
        self._shortcut_toggle.toggled.connect(self._shortcut_panel.setVisible)
        self._custom_toggle = QPushButton("调整双击发送（可选）…", objectName="voiceCustomShortcuts")
        self._custom_toggle.setCheckable(True)
        self._custom_toggle.setProperty("buttonRole", "secondary")
        self._custom_toggle.toggled.connect(self._send_panel.setVisible)
        self._toggle_supported = QPushButton("我已完成输入软件中的设置", objectName="voiceBindingConfirmed")
        self._toggle_supported.setCheckable(True)
        self._toggle_supported.setProperty("buttonRole", "secondary")
        self._adapt_notice = QLabel("确认快捷键后，一次保存到设备。", objectName="voiceAdaptNotice")
        self._adapt_notice.setWordWrap(True)
        self._compatibility_notice = QLabel(objectName="voiceShortcutCompatibility")
        self._compatibility_notice.setWordWrap(True)
        self._shortcut_summary = QLabel(objectName="voiceShortcutSummary")
        self._shortcut_summary.setWordWrap(True)
        self._adapt_button = QPushButton("应用适配快捷键", objectName="voiceAdaptApply")
        self._adapt_button.setProperty("buttonRole", "primary")
        self._adapt_button.clicked.connect(self._apply_adaptation)
        self._adapt_error = QLabel(objectName="voiceAdaptError")
        self._adapt_error.setWordWrap(True)
        help_button = self._help_button = QPushButton("找不到设置？查看步骤…", objectName="voiceBindingHelpToggle")
        help_button.setProperty("buttonRole", "secondary")
        help_button.setCheckable(True)
        help_text = self._binding_help = QLabel("保留原来的快捷键。请选择可添加外部键盘快捷键、且支持按一下启停的软件；仅支持按住说话的方式暂不能用于这个语音键。")
        help_text.setWordWrap(True)
        help_text.hide()
        help_button.toggled.connect(help_text.setVisible)
        self._more_toggle = QPushButton("更多设置与帮助…", objectName="voiceMoreOptions")
        self._more_toggle.setProperty("buttonRole", "secondary")
        self._more_toggle.setCheckable(True)
        body.addWidget(self._more_toggle)
        self._more_panel = QWidget(objectName="voiceMoreOptionsPanel")
        more_layout = QVBoxLayout(self._more_panel)
        more_layout.setContentsMargins(0, 0, 0, 0)
        more_layout.setSpacing(8)
        more_layout.addWidget(self._shortcut_toggle)
        more_layout.addWidget(self._shortcut_panel)
        more_layout.addWidget(self._custom_toggle)
        more_layout.addWidget(self._send_panel)
        more_layout.addWidget(help_button)
        more_layout.addWidget(help_text)
        self._more_panel.hide()
        self._more_toggle.toggled.connect(self._more_options_toggled)
        body.addWidget(self._more_panel)
        self._send_panel.hide()
        body.addStretch(1)
        self._pages.addWidget(binding)

        trial = QWidget()
        trial_layout = QVBoxLayout(trial)
        trial_layout.setContentsMargins(0, 0, 0, 0)
        self._trial_hint = QLabel("点输入框，试说一句话。", objectName="voicePracticeHint")
        self._trial_hint.setWordWrap(True)
        trial_layout.addWidget(self._trial_hint)
        self.practice = VoicePractice(action or {"double_usage": 40})
        trial_layout.addWidget(self.practice)
        self._add_repair_actions(trial_layout, "practice")
        demo_toggle = QPushButton("不会操作？看演示", objectName="voicePracticeDemoToggle")
        demo_toggle.setProperty("buttonRole", "ghost")
        demo_toggle.setCheckable(True)
        trial_layout.addWidget(demo_toggle)
        self.demo = VoiceDemo()
        self.demo.hide()
        demo_toggle.toggled.connect(self.demo.setVisible)
        trial_layout.addWidget(self.demo)
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
            target_note = QLabel(objectName="voiceTargetOptionalNote")
            set_translatable_text(
                target_note,
                "这一步是可选验证，不会修改设备设置。你可以在常用 AI 中再试一次，也可以直接完成。",
            )
            target_note.setWordWrap(True)
            target_layout.addWidget(target_note)
            target_layout.addWidget(QLabel("这次先在哪个应用里试用？"))
            target_layout.addWidget(self._target)
            target_layout.addWidget(self._open_target_button)
            target_layout.addWidget(self._target_hint)
            target_layout.addWidget(self._target_error)
            self._add_repair_actions(target_layout, "target")
            target_layout.addStretch(1)
            self._pages.addWidget(target)
        else:
            # These controls stay owned by the dialog without appearing in the
            # nested guide; the common-AI guide owns the target-app trial.
            for widget in (self._target, self._open_target_button, self._target_hint, self._target_error):
                widget.setParent(self)
                widget.hide()
        self._body = QScrollArea(objectName="voiceSetupBody")
        self._body.setWidgetResizable(True)
        self._body.setFrameShape(QFrame.NoFrame)
        self._body.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._body.setWidget(self._pages)
        layout.addWidget(self._body, 1)
        # The prerequisite belongs beside the fixed Continue button, not below
        # optional editors in a scroll area where users cannot see why it is off.
        self._binding_footer = QWidget(objectName="voiceBindingFooter")
        footer = QVBoxLayout(self._binding_footer)
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(6)
        self._device_stage_status = QLabel(objectName="voiceDeviceStageStatus")
        self._software_stage_status = QLabel(objectName="voiceSoftwareStageStatus")
        footer.addWidget(self._device_stage_status)
        footer.addWidget(self._software_stage_status)
        footer.addWidget(self._shortcut_summary)
        footer.addWidget(self._compatibility_notice)
        footer.addWidget(self._toggle_supported)
        footer.addWidget(self._adapt_notice)
        layout.addWidget(self._binding_footer)
        layout.addWidget(self._adapt_error)
        buttons = QHBoxLayout()
        self._back = QPushButton("返回", objectName="voiceSetupBack")
        self._back.clicked.connect(lambda: self._step(max(0, self._pages.currentIndex() - 1)))
        buttons.addWidget(self._back)
        self._later = QPushButton("稍后设置", objectName="voiceSetupLater")
        self._later.clicked.connect(self.reject)
        buttons.addWidget(self._later)
        self._target_trial = QPushButton("到常用 AI 试用（可选）", objectName="voiceTryTarget")
        self._target_trial.setProperty("buttonRole", "secondary")
        set_translatable_text(self._target_trial, "到常用 AI 试用（可选）")
        self._target_trial.clicked.connect(self._open_optional_target)
        self._target_trial.hide()
        buttons.addWidget(self._target_trial)
        self._next = QPushButton(objectName="voiceSetupNext")
        self._next.setProperty("buttonRole", "primary")
        self._next.clicked.connect(self._next_step)
        buttons.addWidget(self._adapt_button)
        buttons.addWidget(self._next)
        layout.addLayout(buttons)
        self.practice.completed_changed.connect(self._practice_completed)
        self._toggle_supported.toggled.connect(self._refresh)
        self.provider.currentIndexChanged.connect(self._provider_changed)
        self._target.currentIndexChanged.connect(self._target_changed)
        self._provider_changed()
        self._target_changed()
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
        self._step(0)
        if self._vm is not None:
            self._vm.changed.connect(self._device_changed)
            self.finished.connect(lambda: self._vm.changed.disconnect(self._device_changed))

    def _add_repair_actions(self, layout, scope):
        toggle = QPushButton("遇到问题？", objectName=f"voiceRepairToggle_{scope}")
        toggle.setProperty("buttonRole", "secondary")
        toggle.setCheckable(True)
        layout.addWidget(toggle)
        panel = QWidget(objectName=f"voiceRepairPanel_{scope}")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(8)
        for reason, label in (("no_voice", "按键没反应"), ("conflict", "同时唤起其他软件"), ("send", "双击没有正确发送")):
            button = QPushButton(label, objectName=f"voiceRepair_{scope}_{reason}")
            button.setProperty("buttonRole", "secondary")
            button.clicked.connect(lambda _=False, key=reason: self._repair(key))
            panel_layout.addWidget(button)
        panel.hide()
        toggle.toggled.connect(panel.setVisible)
        layout.addWidget(panel)

    def _more_options_toggled(self, visible):
        self._more_panel.setVisible(visible)
        if visible:
            return
        self._custom_toggle.setChecked(False)
        self._help_button.setChecked(False)
        known_provider = sys.platform == "darwin" and self.provider.currentData() in {
            "typeless", "doubao", "qianwen",
        }
        if known_provider:
            self._shortcut_toggle.setChecked(False)

    def _repair(self, reason):
        messages = {
            "no_voice": "先打开输入法设置，核对启停快捷键及短按启停方式；豆包还需要切为当前输入法或开启全局唤起。确认后重新录制并试用。",
            "conflict": "多个软件响应同一快捷键。请在输入法中选择未被其他语音软件使用的快捷键，再回到这里重新录制。不会自动修改或退出其他软件。",
            "send": "查看目标应用使用 Enter 还是组合键发送，在下方只调整“双击 · 发送文字”，再用实体键试一次。",
        }
        set_translatable_text(self._issue_notice, messages[reason])
        self._issue_notice.show()
        if reason != "send":
            self._toggle_supported.setChecked(False)
        self.practice.reset()
        self._step(0)
        self._custom_toggle.setChecked(reason == "send")

    def _recommended_action(self):
        return recommended_voice_action(
            self._requested_action,
            self.provider.currentData(),
            sys.platform,
        )

    def _set_voice_shortcut(self, action):
        self._requested_action["usage"] = action["usage"]
        modifiers = action.get("modifiers", [])
        self._requested_action["modifiers"] = list(modifiers)
        self._custom_shortcut_chosen = True
        if self._shortcut_editors:
            self._syncing_shortcuts = True
            editor = self._shortcut_editors[0][1]
            editor.set_action({
                "type": "key",
                "usage": self._requested_action["usage"],
                "modifiers": self._requested_action.get("modifiers", []),
            })
            editor._shortcut_recorder.set_action(editor.action())
            self._syncing_shortcuts = False

    def _use_recommended_shortcut(self):
        self._set_voice_shortcut(self._recommended_action())
        self._toggle_supported.setChecked(False)
        self._adapt_error.clear()
        self.practice.reset()
        self._refresh()

    def _common_shortcut_conflict(self):
        if sys.platform != "darwin":
            return ""
        usage = self._requested_action.get("usage")
        modifiers = set(self._requested_action.get("modifiers", []))
        if modifiers.intersection({227, 231}) and usage in {4, 6, 25, 27, 29}:
            return "这个组合是 macOS 常用编辑快捷键，会与复制、粘贴或撤销等操作冲突。请使用推荐快捷键或重新录制。"
        return ""

    def _shortcut_problem(self):
        if not self.provider.currentData() or not self._custom_shortcut_chosen:
            return ""
        conflict = self._common_shortcut_conflict()
        if conflict:
            return conflict
        if adapted_voice_action(self._requested_action, self.provider.currentData(), sys.platform) != self._requested_action:
            return ("豆包不接受这个快捷键。请录制它当前使用的 Control、Option、Command 单键或组合键。"
                    if self.provider.currentData() == "doubao" else
                    "请录制千问当前使用的右 Option 或右 Command。")
        return ""

    @property
    def target_key(self):
        return self._target.currentData()

    def _voice_app_path(self):
        provider = self.provider.currentData()
        if provider == "typeless":
            return installed_desktop_app("Typeless")
        # QianwenIMEService is a background helper, not a settings launcher.
        # Its app bundle can open successfully without displaying any window.
        paths = {
            "doubao": "/Library/Input Methods/DoubaoIme.app/Contents/Applications/DoubaoImeSettings.app",
        }
        path = Path(paths[provider]) if sys.platform == "darwin" and provider in paths else None
        return path if path is not None and path.exists() else None

    def _open_voice(self):
        path = self._voice_app_path()
        if not path or not QProcess.startDetached("/usr/bin/open", [str(path)])[0]:
            set_translatable_text(self._binding_hint, "未能打开，请手动打开语音软件。")

    def _provider_changed(self):
        provider = self.provider.currentData()
        changed_provider = provider != self._last_provider
        known_provider = sys.platform == "darwin" and provider in {"typeless", "doubao", "qianwen"}
        restored = (confirmed_voice_choice_for_provider(
            self._settings, self._serial, self._profile_id, provider, mode=self._mode)
            if changed_provider else None)
        if restored is not None:
            # The send gesture belongs to the target app.  Restore only the
            # provider's voice shortcut and keep the current double-click send.
            self._set_voice_shortcut(restored["action"])
        elif changed_provider and known_provider:
            # Known macOS providers have tested starter shortcuts.  Selecting
            # one prepares that mapping without making the user discover and
            # retype it; nothing is written until they press Save.
            self._set_voice_shortcut(self._recommended_action())
        elif changed_provider and provider and self._shortcut_editors:
            # A shortcut read from the device cannot be attributed to a newly
            # selected app.  Require one recording instead of relabelling a
            # Qianwen/Doubao key as Typeless (or vice versa).
            self._custom_shortcut_chosen = False
        self._last_provider = provider
        self._syncing_shortcuts = True
        for prefix, editor in self._shortcut_editors:
            editor.set_action({"type": "key", "usage": self._requested_action[prefix + "usage"],
                               "modifiers": self._requested_action.get(prefix + "modifiers", [])})
            if not prefix and not self._custom_shortcut_chosen:
                editor._field_widgets["usage"].setCurrentIndex(-1)
                editor._shortcut_recorder.set_action({"type": "none"})
        self._syncing_shortcuts = False
        self._custom_toggle.setVisible(bool(provider) and bool(self._shortcut_editors))
        self._custom_toggle.setChecked(False)
        self._help_button.setChecked(False)
        self._more_toggle.blockSignals(True)
        self._more_toggle.setChecked(False)
        self._more_toggle.blockSignals(False)
        self._more_toggle.setVisible(bool(provider) and known_provider)
        self._more_panel.setVisible(bool(provider) and not known_provider)
        self._shortcut_toggle.blockSignals(True)
        self._shortcut_toggle.setChecked(False)
        self._shortcut_toggle.blockSignals(False)
        self._shortcut_toggle.setVisible(known_provider and bool(self._shortcut_editors))
        self._shortcut_panel.setVisible(
            bool(provider) and bool(self._shortcut_editors) and not known_provider
        )
        typeless = provider == "typeless"
        native = sys.platform == "darwin"
        hints = {
            "typeless": ("设备语音键在 Typeless 中会显示为 F13，不用在电脑键盘上寻找。"
                         "设备保存完成后，打开 Typeless，在“语音输入”快捷键框中按一下设备语音键；"
                         "显示 F13 即绑定成功。"),
            "doubao": "推荐使用右 Control。设备保存完成后，在豆包“免按模式”中设为右 Control，并切换到豆包输入法或开启全局唤起。",
            "qianwen": "推荐使用右 Option。设备保存完成后，在千问“语音输入”中设为右 Option，并打开“短按也唤起语音输入”。",
        }
        hint = hints.get(provider) if native or typeless else None
        set_translatable_text(self._binding_hint, hint or "下面会把语音软件的启停快捷键保存到设备。")
        set_translatable_text(
            self._shortcut_hint,
            ("如果你修改过语音软件的快捷键，才需要这里。录制不会修改语音软件；"
             "点击录制后按电脑键盘上的快捷键，不要按设备语音键。")
            if known_provider else
            ("先在语音输入软件中确认开始／结束快捷键。录制不会修改语音软件；"
             "回到这里点击录制，再按电脑键盘上的同一快捷键。不要按设备语音键。")
        )
        set_translatable_text(self._shortcut_toggle, "使用其他快捷键…")
        set_translatable_text(self._recommended_button, "使用推荐快捷键")
        set_translatable_text(self._open_voice_button, "打开 Typeless" if typeless else "打开输入法设置")
        self._open_voice_button.setVisible(self._voice_app_path() is not None)
        self._toggle_supported.setVisible(bool(provider))
        self._toggle_supported.setChecked(False)
        set_translatable_text(
            self._binding_help,
            _PROVIDER_HELP_TEXTS.get(provider, "先选择语音输入软件，再查看对应设置步骤。"),
        )
        self._adapt_error.clear()
        self.practice.reset()
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
        self._refresh()

    def _adaptation_needed(self):
        return _gesture(self._requested_action) != _gesture(self.action) or self._name_needs_apply

    def _device_available(self):
        if self._vm is None:
            return False
        vm = self._vm
        snapshot = vm.model.snapshot
        return (vm.model.state is AppState.READY and snapshot is not None
                and snapshot.identity.get("serial") == self._serial
                and snapshot.active_profile_id == self._profile_id
                and snapshot.status.get("operating_mode", "normal") == self._mode
                and snapshot.compatibility.get("write") is True
                and not vm.write_transaction.blocks_editing
                and not vm.firmware_update.blocks_editing
                and not vm.calibration.blocks_editing)

    def _apply_adaptation(self):
        if not self._context_ready or not self._device_available() or not self._adaptation_needed():
            return
        vm = self._vm
        provider = self.provider.currentData()
        if not provider or not self._custom_shortcut_chosen:
            return
        expected = copy.deepcopy(self._requested_action)
        try:
            if adapted_voice_action(expected, provider, sys.platform) != expected:
                raise ValueError("这个快捷键不适用于所选输入法，请重新选择。")
            if self._name_needs_apply and _gesture(expected) == _gesture(self.action):
                # A name-only save must not rewrite an equivalent shortcut just
                # because the recorder normalized an absent modifier list to [].
                expected = copy.deepcopy(self.action)
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
                self._custom_toggle.setChecked(False)
                self.practice.input.set_action(self.action)
                self.practice.reset()
                self.action_applied.emit(copy.deepcopy(self.action))
                if self._pages.currentIndex() == 0 and self._can_continue():
                    self._issue_notice.hide()
                    self._step(1)
            elif transaction.state in {ConfigTransactionState.FAILED, ConfigTransactionState.CONFLICT}:
                self._adapting = False
                set_translatable_text(self._adapt_error, transaction.message)
        self._refresh()

    def reject(self):
        if not self._adapting or (self._vm is not None and not self._vm.write_transaction.is_busy):
            super().reject()

    def _target_changed(self):
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
            set_translatable_text(self._target_error, "未能打开，请手动切换到你的 AI 应用。")

    def _open_optional_target(self):
        if self._pages.currentIndex() == 1 and self._can_continue() and self._include_target:
            self._issue_notice.hide()
            self._step(2)

    def set_context_ready(self, ready):
        # Temporary disconnects must not erase what the user is practising.
        # The caller checks device, profile and mapping before resuming.
        self._context_ready = bool(ready)
        self._context_notice.setVisible(not self._context_ready and not self._adapting)
        self._refresh()

    def _can_continue(self):
        if not self.provider.currentData() or not self._context_ready or self._adapting or self._adaptation_needed():
            return False
        binding_ready = self._toggle_supported.isChecked() and self._custom_shortcut_chosen and not self._shortcut_problem()
        index = self._pages.currentIndex()
        if index == 0:
            return binding_ready
        return binding_ready and self.practice.completed

    def _refresh(self, *_):
        adapting = self._adapting
        needs = self._adaptation_needed()
        provider = self.provider.currentData()
        index = self._pages.currentIndex()
        if not self._context_ready and not adapting:
            mode_name = "CODEX" if self._mode == "codex" else "NORMAL"
            set_translatable_text(
                self._context_notice,
                f"请连接原设备并切回 {mode_name} 模式。已完成的步骤会保留，连接恢复后可继续。",
            )
        self.provider.setEnabled(not adapting)
        self._shortcut_panel.setEnabled(not adapting)
        self._binding_footer.setVisible(index == 0)
        if not provider:
            notice = "先选择你使用的语音输入软件。"
        elif not self._custom_shortcut_chosen:
            notice = "先录制输入法当前使用的启停快捷键。"
        elif self._shortcut_problem() or not self._context_ready:
            notice = ""  # The specific compatibility/context message is visible.
        elif adapting:
            notice = "正在保存并核对设备，请稍候。"
        elif needs and not self._device_available():
            notice = "等待设备就绪后继续。"
        elif needs:
            notice = "先保存到设备，再到语音软件中完成上方绑定。"
        elif not self._toggle_supported.isChecked():
            notice = "完成上方绑定后勾选确认，再开始实体练习。"
        else:
            notice = "设置已匹配设备，可以开始实体练习。"
        set_translatable_text(self._adapt_notice, notice)
        self._adapt_notice.setVisible(bool(notice))
        self._adapt_button.setVisible(needs and bool(provider) and index == 0)
        self._adapt_button.setEnabled(not adapting and bool(provider) and self._context_ready and self._device_available()
                                      and self._custom_shortcut_chosen
                                      and not self._shortcut_problem())
        unknown = adapting and self._vm.write_transaction.state is ConfigTransactionState.UNKNOWN
        failed = (
            self._vm is not None
            and self._vm.write_transaction.state in {ConfigTransactionState.FAILED, ConfigTransactionState.CONFLICT}
        )
        adapt_label = (
            "等待连接确认" if unknown else
            "正在应用…" if adapting else
            "等待设备连接" if needs and not self._device_available() else
            "重试保存到设备" if failed else
            "保存到设备"
        )
        set_translatable_text(self._adapt_button, adapt_label)
        self._binding_hint.setVisible(bool(provider))
        known_provider = sys.platform == "darwin" and provider in {"typeless", "doubao", "qianwen"}
        recommended_differs = (
            known_provider
            and _gesture(self._recommended_action()) != _gesture(self._requested_action)
        )
        self._recommended_button.setVisible(
            index == 0 and recommended_differs
        )
        # Future-step controls should not look actionable before the device has
        # accepted and read back the shortcut they are meant to bind.
        voice_app_available = self._voice_app_path() is not None
        self._open_voice_button.setVisible(voice_app_available and not adapting and not needs)
        self._open_voice_button.setEnabled(not adapting and not needs)
        self._help_button.setVisible(bool(provider))
        self._toggle_supported.setVisible(bool(provider) and not adapting and not needs)
        self._toggle_supported.setEnabled(not adapting and not needs
                                          and self._custom_shortcut_chosen
                                          and not self._shortcut_problem())
        confirmation = _BINDING_CONFIRMATION_TEXTS.get(
            provider, ("我已完成输入软件中的设置", "✓ 输入软件快捷键已确认")
        )
        set_translatable_text(
            self._toggle_supported,
            confirmation[1] if self._toggle_supported.isChecked() else confirmation[0],
        )
        self._context_notice.setVisible(not self._context_ready and not adapting)
        set_translatable_text(self._compatibility_notice, self._shortcut_problem())
        self._compatibility_notice.setVisible(bool(provider) and bool(self._shortcut_problem()))
        self._shortcut_summary.setVisible(bool(provider) and self._custom_shortcut_chosen)
        if self._custom_shortcut_chosen:
            keys = [describe_action({"type": "key", "usage": self._requested_action[prefix + "usage"],
                                     "modifiers": self._requested_action.get(prefix + "modifiers", [])},
                                    platform="macos" if sys.platform == "darwin" else "windows")
                    for prefix in ("", "double_")]
            set_translatable_text(self._shortcut_summary, f"单击 {keys[0]} / 双击 {keys[1]}")
        if not provider:
            device_stage = "1  设备快捷键 · 选择输入软件后显示"
            software_stage = "2  输入软件绑定 · 等待选择"
        elif adapting:
            device_stage = "1  设备快捷键 · 正在保存并读回"
            software_stage = "2  输入软件绑定 · 保存完成后继续"
        elif needs:
            device_stage = "1  设备快捷键 · 待保存"
            software_stage = "2  输入软件绑定 · 保存设备后继续"
        else:
            device_stage = "✓ 设备快捷键 · 已保存并读回"
            software_stage = (
                "✓ 输入软件绑定 · 已确认"
                if self._toggle_supported.isChecked()
                else "2  输入软件绑定 · 待完成"
            )
        set_translatable_text(self._device_stage_status, device_stage)
        set_translatable_text(self._software_stage_status, software_stage)
        self._target_trial.setVisible(
            self._include_target and index == 1 and self.practice.completed
        )
        self._target_trial.setEnabled(self._can_continue())
        self._later.setVisible(index == 0 or (index == 1 and not self.practice.completed))
        self._next.setEnabled(self._can_continue())
        self._next.setVisible(not (needs and provider and index == 0))

    def _step(self, index):
        self._pages.setCurrentIndex(index)
        self._back.setVisible(index > 0)
        if index < 2:
            self._step_number.setText(("1/2 ·", "2/2 ·")[index])
        else:
            set_translatable_text(self._step_number, "可选 ·")
        set_translatable_text(self._heading, ("连接语音输入软件", "亲手练习", "在常用 AI 中再试一次")[index])
        set_translatable_text(self._later, "稍后设置" if index == 0 else "退出练习")
        label = (
            "开始实体练习" if index == 0 else
            "完成练习后继续" if index == 1 and not self.practice.completed else
            "完成设置" if self._include_target else
            "完成练习"
        )
        set_translatable_text(self._next, label)
        self._refresh()
        if index == 1 and not self.practice.completed:
            self.practice.input.setFocus()

    def _practice_completed(self, completed):
        set_translatable_text(
            self._trial_hint,
            "练习已完成。可以直接完成设置，或到常用 AI 中再试一次。"
            if completed else "点输入框，试说一句话。",
        )
        if self._pages.currentIndex() == 1:
            set_translatable_text(
                self._next,
                ("完成设置" if self._include_target else "完成练习")
                if completed else "完成练习后继续",
            )
        self._refresh()

    def accept(self):
        # Binding cannot finish before a successful local practice. Target-app
        # verification is intentionally optional.
        if self._pages.currentIndex() > 0 and self._can_continue():
            if self._settings is not None:
                self._settings.setValue(self._provider_key, self.provider.currentData())
                self._settings.sync()
            super().accept()

    def _next_step(self):
        if not self._can_continue():
            return
        if self._pages.currentIndex() == 0:
            self._issue_notice.hide()
            self._step(1)
        else:
            self.accept()
