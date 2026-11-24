"""Power V2's explicitly selected, device-wide voice key.

Platform-specific software, presets and copy come from ``voice_presets`` (B-01,
B-05); this dialog never hard-codes macOS or Windows behaviour.
"""

import copy

from PySide6.QtCore import QProcess, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from controller_config import voice_presets
from controller_config.actions import describe_action, modifier_choice_label
from controller_config.models import AppState
from controller_config.transactions import ConfigTransactionState
from controller_config.views.action_editor import ShortcutRecorder
from controller_config.views.ui.dialog import MEDIUM, Dialog
from controller_config.views.voice_practice import VoicePractice
from controller_config.voice_setup import adapted_voice_action, voice_provider_key


def legacy_voice_choices(config):
    """Show every prior Key 8 meaning; never nominate one for the user."""
    choices = []
    for profile in config.get("profiles", ()):
        name = profile.get("name", f"Profile {profile.get('id')}")
        mapping = next((item for item in profile.get("mappings", ())
                        if item.get("control_id") == "key.8"), None)
        if mapping is not None:
            choices.append((f"{name} · Normal", copy.deepcopy(mapping["action"])))
        choices.append((f"{name} · Codex", copy.deepcopy(
            profile.get("codex_voice", {"type": "none"}))))
    return choices


def send_method_text(trigger: str) -> str:
    """B-03: exactly one description of the send method that is in effect."""
    if trigger == "double":
        return ("发送方式：双击语音键发送 Enter。单击需要等待双击判定，开始／结束听写会有短暂延迟；"
                "不想要延迟时，可在“自己设置快捷键”中改为“单击启停”。")
    return ("发送方式：由 11 号键发送。Normal 下 11 号键发送 Enter，Codex 沿用 11 号键的发送功能；"
            "启用后，Normal 各 Profile 的 11 号键原映射不再触发。")


def _shortcut_label(action: dict, platform: str) -> str:
    """Side-specific label: Doubao distinguishes the right Alt/Control."""
    usage = action.get("usage", 0)
    modifiers = sorted(action.get("modifiers") or [])
    if 224 <= usage <= 231 and not modifiers:
        return modifier_choice_label(usage, platform)
    key = "空格" if usage == voice_presets.SPACE else describe_action(
        {"type": "key", "usage": usage, "modifiers": []}, platform=platform)
    return "+".join([modifier_choice_label(m, platform) for m in modifiers] + [key])


class GlobalVoiceDialog(Dialog):
    # B-06: a fixed size (full_height); the content scrolls instead of the
    # dialog jumping in height when the post-apply guidance appears.

    def __init__(self, view_model, parent=None, *, settings=None, platform=None, settings_lookup=None):
        super().__init__(parent, "设备级语音键", width=MEDIUM, full_height=True,
                         object_name="globalVoiceDialog")
        self.setAttribute(Qt.WA_DeleteOnClose)
        self._vm = view_model
        self._settings = settings
        self._platform = voice_presets.host_platform(platform)
        self._settings_lookup = settings_lookup
        snapshot = view_model.model.snapshot
        self._serial = snapshot.identity.get("serial")
        self._expected = None
        self._write_started = False
        self._pending_provider = None
        self._applied_provider = None
        self._saved = snapshot.config.get("voice_action")
        self._recorded = None

        layout = self.body_layout()
        layout.setSpacing(10)

        intro = QLabel("选你常用的语音软件，设置这台设备的语音键。Normal 和 Codex 共用；"
                       "Claude Code 保留它自己的按住说话操作。")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.provider = QComboBox(objectName="globalVoiceProvider")
        self.provider.addItem("选择语音软件", "")
        for key, label in voice_presets.software_choices(self._platform):
            self.provider.addItem(label, key)
        prior = settings.value(voice_provider_key(self._serial, mode="global"), "") if settings is not None else ""
        self._prior_provider = prior
        if prior and self.provider.findData(prior) < 0:
            # A choice not offered on this platform stays visible, never guessed away.
            self.provider.addItem(f"{voice_presets.BY_KEY[prior].label if prior in voice_presets.BY_KEY else prior}"
                                  "（已有手动设置）", prior)
        self.provider.setCurrentIndex(max(0, self.provider.findData(prior)))
        layout.addWidget(self.provider)
        self.quick_summary = QLabel(objectName="globalVoiceQuickSummary")
        self.quick_summary.setWordWrap(True)
        layout.addWidget(self.quick_summary)
        self.advanced = QPushButton("自己设置快捷键", objectName="globalVoiceAdvanced")
        self.advanced.setCheckable(True)
        layout.addWidget(self.advanced)

        self.advanced_panel = QWidget(objectName="globalVoiceAdvancedPanel")
        advanced_layout = QVBoxLayout(self.advanced_panel)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        self.legacy = QComboBox(objectName="globalVoiceLegacyChoice")
        self.legacy.addItem("请选择旧设置，或重新录制", None)
        old_choices = legacy_voice_choices(snapshot.config)
        if self._saved is not None:
            self.legacy.addItem("设备当前设置 · " + describe_action(self._saved, platform=self._platform), self._saved)
            self.legacy.setCurrentIndex(1)
        else:
            for label, action in old_choices:
                description = describe_action(action, platform=self._platform, control_id="key.8")
                self.legacy.addItem(f"{label}：{description}", action)
        advanced_layout.addWidget(self.legacy)
        differing = any(action != old_choices[0][1] for _, action in old_choices[1:]) if old_choices else False
        note = ("设备已经有一套共享语音设置；修改后需重新保存并读回。" if self._saved is not None else
                "旧 Profile／模式的语音动作不一致。请选择要沿用的动作，或重新录制；不会自动采用某个旧值。" if differing else
                "请选择要沿用的旧动作，或重新录制，再明确保存到设备。")
        self.choice_note = QLabel(note)
        self.choice_note.setWordWrap(True)
        advanced_layout.addWidget(self.choice_note)
        self.recorder = ShortcutRecorder({"type": "none"}, platform=self._platform, capture_platform=self._platform)
        self.recorder.setObjectName("globalVoiceShortcutRecorder")
        self.recorder.voice_key = True
        self.recorder.shortcut_recorded.connect(self._new_shortcut)
        advanced_layout.addWidget(self.recorder)
        self.trigger = QComboBox(objectName="globalVoiceTrigger")
        self.trigger.addItem("单击启停，或按住说话／松开结束（由语音软件决定）", "direct")
        self.trigger.addItem("双击语音键发送（单击会等待双击判定）", "double")
        if isinstance(self._saved, dict) and self._saved.get("type") == "key_gesture":
            self.trigger.setCurrentIndex(1)
        advanced_layout.addWidget(self.trigger)
        layout.addWidget(self.advanced_panel)

        self.send_method = QLabel(objectName="globalVoiceSendMethod")
        self.send_method.setWordWrap(True)
        layout.addWidget(self.send_method)
        self.scope = QLabel(objectName="globalVoiceSaveScope")
        self.scope.setWordWrap(True)
        layout.addWidget(self.scope)
        self.inspect = None
        if parent is not None and callable(getattr(parent, "_show_draft_changes", None)):
            self.inspect = QPushButton("查看其他待保存修改", objectName="globalVoiceInspectChanges")
            self.inspect.clicked.connect(parent._show_draft_changes)
            layout.addWidget(self.inspect)
        self.confirmed = QCheckBox("同时保存上述其他修改", objectName="globalVoiceConfirmed")
        layout.addWidget(self.confirmed)
        self.status = QLabel("选择语音软件后，应用到设备。", objectName="globalVoiceStatus")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        # The one action: bottom-right in the shell's button row.
        self.save = self.add_button("应用到设备", "primary", object_name="globalVoiceSave")
        self.save.clicked.connect(self._save)
        self.setup_hint = QLabel(objectName="globalVoiceSetupHint")
        self.setup_hint.setWordWrap(True)
        layout.addWidget(self.setup_hint)
        self.open_settings = QPushButton(objectName="globalVoiceOpenSettings")
        self.open_settings.clicked.connect(self._open_settings)
        layout.addWidget(self.open_settings)
        self.practice = VoicePractice(voice_presets.DEFAULT_VOICE_ACTION)
        layout.addWidget(self.practice)
        layout.addStretch(1)
        # The shell's round close (bottom-left) replaces the old "关闭" button.
        self.close_button.setObjectName("globalVoiceClose")

        self.legacy.currentIndexChanged.connect(self._legacy_changed)
        self.trigger.currentIndexChanged.connect(self._refresh)
        self.confirmed.toggled.connect(self._refresh)
        self.provider.currentIndexChanged.connect(self._provider_changed)
        self.advanced.toggled.connect(self._refresh)
        self.trigger.currentIndexChanged.connect(lambda *_: self.confirmed.setChecked(False))
        self._vm.changed.connect(self._device_changed)
        self.finished.connect(lambda: self._vm.changed.disconnect(self._device_changed))
        self._provider_changed()

    @property
    def platform(self) -> str:
        return self._platform

    def _new_shortcut(self, action):
        self._recorded = copy.deepcopy(action)
        self.legacy.setCurrentIndex(0)
        self.confirmed.setChecked(False)
        self._refresh()

    def _provider_changed(self, *_):
        provider = self.provider.currentData()
        quick = voice_presets.preset(provider, self._platform)
        reuse_saved = (provider == self._prior_provider
                       and voice_presets.preset_matches(provider, self._saved, self._platform))
        self.confirmed.setChecked(False)
        if quick is not None:
            self._recorded = copy.deepcopy(self._saved if reuse_saved else quick.action)
            self.legacy.setCurrentIndex(0)
            self.trigger.setCurrentIndex(1 if self._recorded.get("type") == "key_gesture" else 0)
            self.advanced.setChecked(False)
        else:
            self._recorded = None
            self.advanced.setChecked(bool(provider))
        self._applied_provider = provider if reuse_saved else None
        self.status.setText("设备当前设置已读回；目标软件效果尚未验证。" if reuse_saved else
                            "选择语音软件后，应用到设备。" if not provider else
                            "设备尚未应用这次选择；应用后再设置语音软件的快捷键。")
        self._refresh()

    def _legacy_changed(self, *_):
        self.confirmed.setChecked(False)
        source = self.legacy.currentData()
        if isinstance(source, dict):
            self.trigger.setCurrentIndex(1 if source.get("type") == "key_gesture" else 0)
            if source.get("type") == "none":
                self.status.setText("这项旧设置使用 Codex 自带语音，不能直接用作跨模式语音快捷键；请重新录制。")
        self._refresh()

    def candidate(self):
        source = self._recorded if self.legacy.currentIndex() == 0 else self.legacy.currentData()
        if not isinstance(source, dict) or source.get("type") not in {"key", "key_gesture"}:
            return None
        action = copy.deepcopy(source)
        if self.trigger.currentData() == "double":
            if action.get("type") != "key_gesture":
                action = {"type": "key_gesture", "usage": action["usage"],
                          "modifiers": action.get("modifiers", []), "double_usage": voice_presets.ENTER}
        elif action.get("type") == "key_gesture":
            action = {"type": "key", "usage": action["usage"],
                      "modifiers": action.get("modifiers", [])}
        return action

    def settings_app(self):
        provider = self.provider.currentData()
        return voice_presets.settings_app(provider, self._platform, lookup=self._settings_lookup)

    def _refresh(self, *_):
        snapshot = self._vm.model.snapshot
        provider = self.provider.currentData()
        quick = voice_presets.preset(provider, self._platform)
        candidate = self.candidate()
        if quick is not None:
            if voice_presets.preset_matches(provider, candidate, self._platform) or candidate is None:
                summary = quick.summary
                if provider == "doubao" and candidate is not None:
                    summary = (f"准备让设备单击{_shortcut_label(candidate, self._platform)}启停豆包语音、"
                               "双击发送 Enter；先应用到设备，再让豆包的快捷键与之相同。")
            else:
                summary = "请核对录制的快捷键是否为所选语音软件当前使用的键。"
            self.quick_summary.setText(summary)
        else:
            self.quick_summary.clear()
        self.quick_summary.setVisible(quick is not None and not self.advanced.isChecked())
        self.advanced.setVisible(quick is not None)
        self.advanced_panel.setVisible(bool(provider) and (quick is None or self.advanced.isChecked()))
        self.send_method.setText(send_method_text(self.trigger.currentData()))
        self.send_method.setVisible(bool(provider))
        draft = self._vm.draft
        count = len(draft.changes) if draft is not None else 0
        self.scope.setText(f"当前另有 {count} 项本地修改；应用语音设置时会一并写入设备。")
        self.scope.setVisible(count > 0)
        if self.inspect is not None:
            self.inspect.setVisible(count > 0)
        self.confirmed.setVisible(count > 0)

        applied = self._applied_provider == provider and self._expected is None and bool(provider)
        quick_applied = applied and quick is not None and voice_presets.preset_matches(
            provider, self._saved, self._platform)
        if quick_applied:
            guide = quick.guide
            if "{shortcut}" in guide:
                guide = guide.format(shortcut=_shortcut_label(self._saved, self._platform))
            self.setup_hint.setText(f"设备已设置。接下来：{guide}{voice_presets._DOUBLE_CLICK_GUIDE_TAIL}")
        elif applied:
            self.setup_hint.setText("设备已设置。请让语音软件的启停快捷键与设备一致，"
                                    "再到常用输入框试说话和发送；设备读回不代表目标软件已经响应。")
        else:
            self.setup_hint.clear()
        self.setup_hint.setVisible(applied)
        app = self.settings_app() if applied else None
        software = voice_presets.BY_KEY.get(provider)
        if software is not None:
            self.open_settings.setText(f"打开{software.label}设置")
        self.open_settings.setVisible(app is not None)
        if applied and app is None and software is not None and software.settings_bundle_ids:
            self.setup_hint.setText(self.setup_hint.text() + "\n"
                                    + voice_presets.settings_fallback_text(provider, self._platform))
        self.practice.setVisible(quick_applied)
        self.practice.set_action(candidate or voice_presets.preset_action(provider, self._platform)
                                 or voice_presets.DEFAULT_VOICE_ACTION)

        ready = (self._vm.model.state is AppState.READY and snapshot is not None
                 and snapshot.identity.get("serial") == self._serial
                 and snapshot.compatibility.get("write") is True
                 and not self._vm.write_transaction.blocks_editing)
        self.save.setEnabled(ready and self._expected is None and (count == 0 or self.confirmed.isChecked())
                             and bool(provider) and candidate is not None)
        editing = self._expected is None and not self._vm.write_transaction.blocks_editing
        for widget in (self.provider, self.legacy, self.recorder, self.trigger, self.confirmed, self.advanced):
            widget.setEnabled(editing)

    def _open_settings(self):
        app = self.settings_app()
        if app is None:
            self.status.setText(voice_presets.settings_fallback_text(self.provider.currentData(), self._platform))
            return
        if self._platform == voice_presets.MACOS:
            opened = QProcess.startDetached("/usr/bin/open", [str(app)])[0]
        else:
            opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(app)))
        if not opened:
            self.status.setText(voice_presets.settings_fallback_text(self.provider.currentData(), self._platform))

    def _save(self):
        action = self.candidate()
        if action is None or not self.save.isEnabled():
            return
        provider = self.provider.currentData()
        if adapted_voice_action(action, provider, voice_presets.sys_platform_of(self._platform)) != action:
            self.status.setText("此快捷键不适用于所选语音工具，请录制该工具支持的启停快捷键。")
            return
        snapshot = self._vm.model.snapshot
        if (snapshot is not None and action == snapshot.config.get("voice_action")
                and self._vm.draft is not None and not self._vm.draft.is_dirty):
            if self._settings is not None:
                self._settings.setValue(voice_provider_key(self._serial, mode="global"), provider)
                self._settings.sync()
            self.status.setText("设备当前设置已读回；目标软件效果尚未验证。")
            self._applied_provider = provider
            self._prior_provider = provider
            self._refresh()
            return
        self._expected = copy.deepcopy(action)
        self._pending_provider = provider
        try:
            self._vm.set_global_voice(action)
            self._vm.prepare_device_write(confirm_after_validation=True)
            self._write_started = True
        except ValueError as exc:
            self._expected = None
            self._write_started = False
            self._pending_provider = None
            self.status.setText(str(exc))
        else:
            self.status.setText("正在写入并等待设备读回；目标软件效果未验证。")
        self._refresh()

    def _device_changed(self, *_):
        if self._expected is not None:
            transaction = self._vm.write_transaction
            snapshot = self._vm.model.snapshot
            if (self._write_started and transaction.state is ConfigTransactionState.ACTIVE
                    and snapshot is not None and transaction.candidate_config == snapshot.config
                    and snapshot.config.get("voice_action") == self._expected):
                self.status.setText("设备已写入并读回；请用实体 8 号键在目标软件试用，目标软件效果尚未验证。")
                self._saved = copy.deepcopy(self._expected)
                self._applied_provider = self._pending_provider
                self._prior_provider = self._pending_provider
                if self._settings is not None:
                    self._settings.setValue(voice_provider_key(self._serial, mode="global"),
                                            self._pending_provider)
                    self._settings.sync()
                self._expected = None
                self._write_started = False
                self._pending_provider = None
            elif transaction.state in {ConfigTransactionState.FAILED,
                                       ConfigTransactionState.CONFLICT,
                                       ConfigTransactionState.UNKNOWN}:
                self.status.setText(transaction.message)
                self._expected = None
                self._write_started = False
                self._pending_provider = None
        self._refresh()

    def reject(self):
        if self._vm.write_transaction.blocks_editing:
            return
        super().reject()
