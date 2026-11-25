"""Task-oriented first use and editable, multiple common AI choices."""
from __future__ import annotations

import copy
import sys

from PySide6.QtCore import Qt, QTimer, QUrl, QProcess
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFrame, QGridLayout,
    QLabel, QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget,
)

from controller_config.ai_setup import (
    AI_APPLICATIONS, AI_BY_KEY, AI_SELECTION_KEY, AI_SETUP_COMPLETED_KEY,
    AI_SETUP_DEFERRED_KEY,
    installed_desktop_app, linked_profile, load_choices, load_device_choices,
    save_device_choices,
)
from controller_config.i18n import set_translatable_text, translate_ui_text as tr
from controller_config.models import AppState
from controller_config.onboarding.player import asset_root
from controller_config.style import tokens
from controller_config.transactions import ConfigTransactionState, configs_match_readback
from controller_config.views.device_silhouette import DeviceModelCanvas
from controller_config.views.connection_progress import AI_SETUP_CLIP, dialog_clip
from controller_config.views.ui.dialog import LARGE, TALL_MAX_HEIGHT, Dialog
from controller_config.views.voice_guide import VoiceSetupDialog
from controller_config.views.voice_practice import VoicePractice
from controller_config.views.voice_recovery import VoiceRecoveryPanel
from controller_config.voice_setup import (
    remove_voice_trial, save_confirmed_voice_choice, save_voice_trial,
    voice_provider_key,
)


# Choice cards: a selected card gets a cream ring, not orange (B v0.4: orange
# only on the primary button and the round close).
CHOICE_STYLE = tokens.stylesheet('''
    QCheckBox#aiChoice { padding: 16px; border: 1px solid ${BORDER_STRONG}; border-radius: ${RADIUS_LIST_ROW}px; }
    QCheckBox#aiChoice:checked { border-color: ${TEXT_PRIMARY}; background: ${SURFACE_3}; }
    QCheckBox#aiChoice::indicator { width: 20px; height: 20px; border: 1px solid ${BORDER_STRONG}; border-radius: 5px; background: ${SURFACE_2}; image: none; }
    QCheckBox#aiChoice::indicator:checked { background: ${TEXT_PRIMARY}; border-color: ${TEXT_PRIMARY}; image: url(CHECK_ICON); }
''')

PRACTICE_CONTEXT_CHANGED = (
    "这颗键的设备设置已变化。重新确认后，再完成一次练习。"
)


def _actions_match(first, second):
    def effective(action):
        if not isinstance(action, dict):
            return None
        return {
            key: value
            for key, value in action.items()
            if key == "type" or value not in (None, [], "")
        }

    return effective(first) == effective(second)


class AISetupDialog(Dialog):
    """Five steps in the shared dialog shell; "稍后继续" bottom-left defers."""

    max_height = TALL_MAX_HEIGHT   # design decision fixed 640; step 3 does not fit 560 yet (design decision)

    def __init__(self, view_model, settings, parent=None, *, open_guide=None):
        super().__init__(parent, "选择常用 AI", step="设置常用 AI · 1 / 5", width=LARGE,
                         close="later", later_text="稍后继续", full_height=True,
                         object_name="aiSetupDialog")
        self.setWindowTitle(tr("设置常用 AI"))
        # Appended: without a parent window the shell carries the app sheet itself.
        self.setStyleSheet(self.styleSheet() + CHOICE_STYLE.replace(
            "CHECK_ICON", (asset_root() / "icons/check.svg").as_posix()))
        self.vm, self.settings = view_model, settings
        self._serial = None
        self._candidate = None
        self._entries = {}
        self._applied = False
        self._practice = None
        self._voice_dialog = None
        self._applying = False
        self._retrying = False
        self._model_canvas = None
        self._error_text = ""
        self._preference_serial = None
        self._voice_edited = False
        self._voice_provider = None
        self._voice_use_mode = "global"
        self._voice_recovery_reason = None
        self._target_result_confirmed = False
        root = self.body_layout()
        self._title = self.title_label
        self._connection = QLabel(objectName="muted")
        self._connection.setWordWrap(True)
        root.addWidget(self._connection)
        self._pages = QStackedWidget()
        root.addWidget(self._pages, 1)
        # Steps 3-5 are filled in when they are reached (keys, practice,
        # recovery), so the tallest one cannot be measured on opening: the
        # dialog keeps its max_height (full_height) and never jumps.
        self._choices = {}
        chosen = load_choices(settings)
        page, layout = self._page()
        layout.addWidget(self._label("你平时用哪些 AI 工作？可以多选，以后也能调整。"))
        grid = QGridLayout()
        for index, app in enumerate(AI_APPLICATIONS):
            checkbox = QCheckBox(app.name, objectName="aiChoice")
            checkbox.setProperty("aiKey", app.key)
            checkbox.setChecked(app.key in chosen)
            checkbox.toggled.connect(self._selection_changed)
            self._choices[app.key] = checkbox
            grid.addWidget(checkbox, index // 2, index % 2)
        layout.addLayout(grid)
        layout.addStretch()
        self._selection_note = self._label("各自保存设置。取消勾选不会删除已有配置。")
        layout.addWidget(self._selection_note)
        page, layout = self._page()
        layout.addWidget(self._label("先用哪一个试一次？"))
        self._current = QComboBox(objectName="aiFirstApplication")
        self._current.currentIndexChanged.connect(self._current_changed)
        layout.addWidget(self._current)
        layout.addWidget(self._label("在这个 AI 中，你习惯怎样输入？"))
        self._voice = QComboBox(objectName="aiVoiceSource")
        for label, value in (("键盘输入，暂不设置语音", "none"),
                             ("我自己的语音输入软件", "external"),
                             ("AI 自带语音", "native")):
            self._voice.addItem(tr(label), value)
        self._voice.currentIndexChanged.connect(self._voice_changed)
        self._voice.activated.connect(lambda *_: setattr(self, "_voice_edited", True))
        layout.addWidget(self._voice)
        self._voice_note = self._label("")
        layout.addWidget(self._voice_note)
        layout.addStretch()
        layout.addWidget(self._label("其他已选 AI 会一起保存，语音方式可以分别设置。"))
        page, self._review = self._page()
        self._summary = self._label("")
        self._review.addWidget(self._summary)
        self._review_model = QVBoxLayout()
        self._review.addLayout(self._review_model)
        self._changes = self._label("")
        self._review.addWidget(self._changes)
        self._review.addStretch()
        page, self._trial_layout = self._page()
        self._trial_note = self._label("")
        self._trial_layout.addWidget(self._trial_note)
        self._bind = QPushButton("设置语音软件并练习", objectName="aiBindVoice")
        self._bind.clicked.connect(self._open_binding)
        self._trial_layout.addWidget(self._bind)
        self._trial_layout.addStretch()
        page, layout = self._page()
        self._target_note = self._label("")
        layout.addWidget(self._target_note)
        self._open_app = QPushButton("打开 AI", objectName="aiOpenApp")
        self._open_app.clicked.connect(self._open_target)
        layout.addWidget(self._open_app)
        layout.addWidget(self._label("使用桌面应用时，直接切换到已打开的 AI 输入框。"))
        self._target_check = QCheckBox("我已在这个 AI 中输入并发送一次", objectName="aiTargetConfirmed")
        self._target_check.toggled.connect(self._refresh)
        layout.addWidget(self._target_check)
        recovery = VoiceRecoveryPanel(
            toggle_name="aiTargetRecoveryToggle",
            options_name="aiTargetRecovery",
            button_prefix="aiTargetRepair_",
        )
        recovery.outcome_selected.connect(self._target_repair)
        layout.addWidget(recovery)
        self._target_recovery_toggle = recovery.toggle
        self._target_recovery = recovery.options
        layout.addStretch()
        layout.addWidget(self._label("下次从首页的“常用 AI”切换，无需重新设置。"))
        self._error = self._label("")
        self._error.setObjectName("aiSetupError")
        root.addWidget(self._error)
        # "稍后继续" remembers the deferral, so it does more than close.
        later = self.close_button
        later.setObjectName("aiSetupLater")
        later.clicked.disconnect()
        later.clicked.connect(self._defer)
        if open_guide:
            guide = self.add_button("设备操作说明", "text")
            guide.clicked.connect(open_guide)
        self._back = self.add_button("返回", "secondary", object_name="aiSetupBack")
        self._back.clicked.connect(self._go_back)
        self._next = self.add_button("", "primary", object_name="aiSetupNext")
        self._next.clicked.connect(self._advance)
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
        self.vm.changed.connect(self._refresh)
        self.finished.connect(self._finished)
        self._selection_changed()
        self._step(0)

    @staticmethod
    def _label(text):
        label = QLabel(text)
        label.setWordWrap(True)
        return label

    def _page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(14)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(page)
        self._pages.addWidget(scroll)
        return page, layout

    def selected(self):
        return [key for key, box in self._choices.items() if box.isChecked()]

    def _selection_changed(self, *_):
        if not hasattr(self, "_current"):
            return
        previous = self._current.currentData()
        if previous is None and self.vm.draft:
            saved = load_device_choices(self.settings, self.vm.draft.serial)
            previous = next((key for key in self.selected()
                             if saved.get(key, {}).get("profile_id") == self.vm.draft.config.get("active_profile")), None)
        self._current.blockSignals(True)
        self._current.clear()
        for key in self.selected():
            self._current.addItem(AI_BY_KEY[key].name, key)
        index = self._current.findData(previous)
        if index >= 0:
            self._current.setCurrentIndex(index)
        self._current.blockSignals(False)
        if self.settings is not None:
            self.settings.setValue(AI_SELECTION_KEY, self.selected())
        self._current_changed()
        self._refresh()

    def _current_changed(self, *_):
        if not hasattr(self, "_voice"):
            return
        draft = self.vm.draft
        saved = load_device_choices(self.settings, draft.serial) if draft else {}
        entry = saved.get(self._current.currentData(), {})
        source = entry.get("voice", "none")
        profile = linked_profile(draft, entry) if draft else None
        if profile:
            action = draft.mapping(profile["id"], "key.8")["action"]
            if action.get("type") == "key_gesture":
                source = "external"
            elif source == "external":
                source = "none"
        self._preference_serial = draft.serial if draft else None
        self._voice.setCurrentIndex(max(0, self._voice.findData(source)))
        self._voice_changed()

    def _voice_changed(self, *_):
        if not hasattr(self, "_voice_note"):
            return
        source = self._voice.currentData()
        text = {
            "none": "先配好发送和换行，语音以后再加。",
            "external": "用设备键单击说话／结束，文字出现后双击发送。首次需要在语音软件中绑定一次。",
            "native": "在 AI 内开启语音。各家语音入口不同，本次不改动设备的官方语音控件。",
        }[source]
        set_translatable_text(self._voice_note, text)

    def _step(self, index):
        self._pages.setCurrentIndex(index)
        self._error_text = ""
        if index == 2:
            self._prepare_preview()
        elif index == 3:
            self._prepare_practice()
        elif index == 4:
            app = AI_BY_KEY[self._current.currentData()]
            voice = self._voice.currentData()
            instruction = "单击说话，再单击结束；文字出现后双击同一颗键发送。" if voice == "external" else (
                "在 AI 中开启语音，确认文字后用设备发送键发送。" if voice == "native" else "输入一句测试文字，再按设备发送键。")
            self._target_note.setText(f'{tr("到常用 AI 中完成一次")} · {app.name}\n\n{tr(instruction)}')
            set_translatable_text(self._open_app, "打开桌面应用" if self._installed_app() else "打开官网")
            self._target_result_confirmed = False
            self._target_check.setChecked(False)
        self._refresh()

    def _uses_doubao_macos(self):
        return (sys.platform == "darwin" and self._voice.currentData() == "external"
                and self._voice_provider == "doubao")

    def _target_repair(self, reason):
        messages = {
            "no_voice": "重新打开语音设置，先确认豆包的使用方式。",
            "no_text": "确认麦克风可用后，重新完成按键练习。",
            "cant_stop": "重新打开语音设置，确认豆包使用免按启停。",
            "conflict": "暂时退出同时响应的其他语音软件，然后重试。",
            "send": "重新打开语音设置，只检查双击发送键。",
        }
        key = self._current.currentData()
        entry = self._entries.get(key, {})
        profile_id = entry.get("profile_id")
        profile = next(
            (item for item in (self._candidate or {}).get("profiles", [])
             if item.get("id") == profile_id),
            None,
        )
        mapping = next(
            (item for item in (profile or {}).get("mappings", [])
             if item.get("control_id") == "key.8"),
            None,
        )
        if profile_id is not None and mapping is not None:
            remove_voice_trial(
                self.settings, self._serial, profile_id, "key.8",
                mapping.get("action", {}), self._voice_provider,
            )
        if key in self._entries:
            self._entries[key]["tried"] = False
            save_device_choices(self.settings, self._serial, self._entries)
        self._voice_recovery_reason = reason
        if reason != "conflict" and self._practice is not None:
            self._practice.completed = False
        self._target_result_confirmed = False
        self._target_recovery_toggle.setChecked(False)
        set_translatable_text(self._trial_note, messages[reason])
        set_translatable_text(self._bind, "重新打开语音设置")
        self._step(3)

    def _installed_app(self):
        return installed_desktop_app(AI_BY_KEY[self._current.currentData()].name)

    def _open_target(self):
        path = self._installed_app()
        if path:
            success = QProcess.startDetached("/usr/bin/open", [str(path)])[0]
        else:
            success = QDesktopServices.openUrl(QUrl(AI_BY_KEY[self._current.currentData()].url))
        if not success:
            self._error_text = "未能打开，请手动切换到你的 AI 应用。"
            self._refresh()

    def _prepare_preview(self):
        self._candidate = None
        self._applied = False
        draft = self.vm.draft
        if draft is None:
            return
        saved = load_device_choices(self.settings, draft.serial)
        try:
            config, entries = self.vm.preview_common_ai(
                self.selected(), self._current.currentData(), self._voice.currentData(),
                saved, platform=sys.platform,
            )
        except ValueError as exc:
            self._error_text = str(exc)
            return
        self._serial = draft.serial
        app = AI_BY_KEY[self._current.currentData()]
        self._summary.setText(f'{tr("一起保存")}：{"、".join(AI_BY_KEY[k].name for k in self.selected())}\n{tr("先试用")}：{app.name}')
        if self._model_canvas:
            self._review_model.removeWidget(self._model_canvas)
            self._model_canvas.deleteLater()
        profile = next(p for p in config["profiles"] if p["id"] == config["active_profile"])
        mappings = {m["control_id"]: m for m in profile["mappings"]}
        # The pre-rendered small version (new model, key 8 marked) replaces the live 3D once
        # the designer renders it; it cannot carry the per-key short names, so those then show
        # only in the text below. Until the file exists the live model stays.
        self._model_canvas = dialog_clip(AI_SETUP_CLIP, 280, self)
        if self._model_canvas is None:
            self._model_canvas = DeviceModelCanvas(self.vm.model.snapshot, mappings, "key.8")
            self._model_canvas.setFixedSize(280, 280)
            self._model_canvas.set_control_labels({m["control_id"]: m["short_name"] for m in profile["mappings"]})
        self._review_model.addWidget(self._model_canvas, 0, Qt.AlignHCenter)
        external = self._voice.currentData() == "external"
        change = "高亮键：单击说话／结束 · 双击发送" if external else "高亮键：发送"
        self._changes.setText(tr(change) + "\n" + tr("先配置基础输入，应用后到目标 AI 试一次。"))
        self._candidate = copy.deepcopy(config)
        self._entries = entries
        transaction = self.vm.write_transaction
        if (draft.is_dirty and transaction.device_serial == draft.serial
                and transaction.candidate_config == config == draft.config):
            self._retrying = True
            self._applying = transaction.blocks_editing

    def _ready(self):
        snapshot = self.vm.model.snapshot
        return (snapshot is not None and self.vm.model.state is AppState.READY
                and snapshot.compatibility.get("write") is True
                and (self._serial is None or snapshot.identity.get("serial") == self._serial))

    def _normal(self):
        snapshot = self.vm.model.snapshot
        return snapshot is not None and snapshot.status.get("operating_mode", "normal") == "normal"

    def _practice_context_matches(self):
        snapshot = self.vm.model.snapshot
        candidate = self._candidate
        if snapshot is None or not isinstance(candidate, dict):
            return False
        profile_id = candidate.get("active_profile")
        if snapshot.active_profile_id != profile_id:
            return False
        profile = next(
            (item for item in candidate.get("profiles", [])
             if isinstance(item, dict) and item.get("id") == profile_id),
            None,
        )
        mapping = next(
            (item for item in (profile or {}).get("mappings", [])
             if isinstance(item, dict) and item.get("control_id") == "key.8"),
            None,
        )
        actual = snapshot.mappings.get("key.8")
        return (
            mapping is not None
            and actual is not None
            and _actions_match(mapping.get("action"), actual.get("action"))
        )

    def _refresh(self, *_):
        if not hasattr(self, "_next"):
            return
        index = self._pages.currentIndex()
        draft = self.vm.draft
        if (index <= 2 and draft is not None and self._preference_serial != draft.serial
                and not self._voice_edited and not self._applying):
            self._current_changed()
            if index == 2:
                self._candidate = None
        if index == 2 and self._candidate is None and not self._error_text and self._ready() and self.vm.draft:
            self._prepare_preview()
        transaction = self.vm.write_transaction
        busy = transaction.blocks_editing or self.vm.firmware_update.blocks_editing or self.vm.calibration.blocks_editing
        if self._ready():
            connection = ("设备已连接" if self._normal() else
                          "可以先保存普通模式的 AI 方案；实体试用前请双击旋钮切到普通模式。")
        else:
            connection = "连接设备后继续；已选的 AI 会保留。"
        set_translatable_text(self._connection, connection)
        self.set_step(f'{tr("设置常用 AI")} · {index + 1} / 5')
        set_translatable_text(self._title, ("选择常用 AI", "按你的习惯输入", "准备好这颗键", "亲手试一次", "用到工作中")[index])
        self._back.setVisible(index > 0 and not self._applied)
        self._back.setEnabled(not busy and not self._applying)
        enabled = bool(self.selected())
        label = "继续"
        if index == 2:
            enabled = self._candidate is not None and self._ready() and not busy
            label = "应用并试用"
            if self._retrying and transaction.state is ConfigTransactionState.FAILED:
                label = "重新应用"
            if self._applying:
                label = "正在应用…"
                enabled = False
                snapshot = self.vm.model.snapshot
                if (transaction.state is ConfigTransactionState.ACTIVE and self._ready()
                        and configs_match_readback(self._candidate, snapshot.config)):
                    self._applying = False
                    self._applied = True
                    save_device_choices(self.settings, self._serial, self._entries)
                    QTimer.singleShot(0, self, lambda: self._step(3))
                elif transaction.state in {ConfigTransactionState.FAILED, ConfigTransactionState.CONFLICT}:
                    self._applying = False
                    self._error_text = transaction.message
                    label = "重新应用"
                    enabled = self._ready()
                elif transaction.state is ConfigTransactionState.UNKNOWN:
                    label = "等待重连确认"
            elif self.vm.draft is not None and self.vm.draft.is_dirty and not self._retrying:
                enabled = False
                self._error_text = "你还有未应用的修改。返回首页处理后，再继续设置。"
        elif index == 3:
            label = "到 AI 中试用"
            enabled = self._ready() and self._normal() and self._applied and self._practice is not None and self._practice.completed
        elif index == 4:
            if self._uses_doubao_macos():
                label = "可以正常使用"
                enabled = self._ready() and self._normal()
            else:
                label = "完成设置"
                enabled = self._target_check.isChecked() and self._ready() and self._normal()
        practice_context_matches = self._practice_context_matches()
        if index >= 3 and self._ready() and not practice_context_matches:
            label = "重新确认设置"
            enabled = self._normal() and not busy
            self._error_text = PRACTICE_CONTEXT_CHANGED
        elif self._error_text == PRACTICE_CONTEXT_CHANGED:
            self._error_text = ""
        self._next.setEnabled(enabled and not busy)
        set_translatable_text(self._next, label)
        doubao_target = index == 4 and self._uses_doubao_macos()
        self._target_check.setVisible(index == 4 and not doubao_target)
        self._target_recovery_toggle.setVisible(doubao_target)
        self._target_recovery.setVisible(doubao_target and self._target_recovery_toggle.isChecked())
        self._error.setText(tr(self._error_text))
        self._error.setVisible(bool(self._error_text))
        practice_ready = (self._ready() and self._normal() and not busy
                          and self._candidate is not None
                          and practice_context_matches)
        if self._practice is not None:
            self._practice.setEnabled(practice_ready)
        self._bind.setEnabled(practice_ready)
        if self._voice_dialog is not None:
            self._voice_dialog.set_context_ready(practice_ready)

    def _advance(self):
        index = self._pages.currentIndex()
        if not self._next.isEnabled():
            return
        if index >= 3 and not self._practice_context_matches():
            self._restart_after_practice_context_change()
            return
        if index == 0:
            if self.settings is not None:
                self.settings.setValue(AI_SELECTION_KEY, self.selected())
                self.settings.sync()
            self._step(1)
        elif index == 2:
            self._apply()
        elif index == 4:
            if self._uses_doubao_macos():
                self._target_result_confirmed = True
            self.accept()
        else:
            self._step(index + 1)

    def _restart_after_practice_context_change(self):
        if self._practice is not None:
            self._trial_layout.removeWidget(self._practice)
            self._practice.deleteLater()
            self._practice = None
        self._candidate = None
        self._applied = False
        self._retrying = False
        self._target_result_confirmed = False
        self._target_check.setChecked(False)
        self._step(2)

    def _apply(self):
        try:
            if not self._retrying:
                self._entries = self.vm.prepare_common_ai(
                    self.selected(), self._current.currentData(), self._voice.currentData(),
                    load_device_choices(self.settings, self._serial), platform=sys.platform,
                )
                self._candidate = copy.deepcopy(self.vm.draft.config)
                # Remember new profile identities for recovery, but do not call
                # a voice preference applied before the device confirms it.
                remembered = load_device_choices(self.settings, self._serial)
                for key, entry in self._entries.items():
                    remembered[key] = {**remembered.get(key, {}), "profile_id": entry["profile_id"], "profile_name": entry["profile_name"]}
                    if "before_voice" in entry:
                        remembered[key]["before_voice"] = entry["before_voice"]
                save_device_choices(self.settings, self._serial, remembered)
            elif self.vm.draft.config != self._candidate:
                raise ValueError("本地配置已变化，请返回并重新确认。")
            self._retrying = True
            if not self.vm.draft.is_dirty:
                self._applied = True
                save_device_choices(self.settings, self._serial, self._entries)
                self._step(3)
                return
            self._applying = True
            self._error_text = ""
            self.vm.prepare_device_write(confirm_after_validation=True)
        except ValueError as exc:
            self._applying = False
            self._error_text = str(exc)
        self._refresh()

    def _prepare_practice(self):
        if self._practice is not None:
            return
        external = self._voice.currentData() == "external"
        set_translatable_text(self._trial_note, "先绑定语音软件，再试说和发送。" if external else "在下方输入一句话，再按高亮的设备发送键。")
        self._bind.setVisible(external)
        action = self.vm.model.snapshot.mappings.get("key.8", {}).get("action", {})
        if action.get("type") == "key_gesture":
            send_action = action
        elif action.get("type") == "key":
            send_action = {"double_usage": action["usage"], "double_modifiers": action.get("modifiers", [])}
        else:
            self._error_text = "这颗键已被自定义，请在按键配置中检查发送动作。"
            return
        self._practice = VoicePractice(send_action, voice=external)
        self._practice.completed_changed.connect(self._refresh)
        self._trial_layout.insertWidget(1, self._practice)
        self._practice.setVisible(not external)
        if not external:
            self._practice.input.setFocus()

    def _open_binding(self):
        self._refresh()
        if not self._bind.isEnabled():
            return
        if self._voice_dialog:
            self._voice_dialog.raise_()
            return
        action = self.vm.model.snapshot.mappings["key.8"]["action"]
        self._practice.completed = False
        self._target_check.setChecked(False)
        self._target_result_confirmed = False
        dialog = VoiceSetupDialog(self, action=action, settings=self.settings, include_target=False,
                                  view_model=self.vm, control_id="key.8",
                                  initial_use_mode=self._voice_use_mode,
                                  recovery_reason=self._voice_recovery_reason)
        self._voice_dialog = dialog
        def adapted(_action):
            entry = self._entries[self._current.currentData()]
            entry["voice_action"] = copy.deepcopy(_action)
            entry["tried"] = False
            save_device_choices(self.settings, self._serial, self._entries)
            save_confirmed_voice_choice(
                self.settings, self._serial,
                self.vm.model.snapshot.active_profile_id,
                _action, dialog.provider.currentData(), mode="normal",
            )
            self._candidate = copy.deepcopy(self.vm.model.snapshot.config)
            self._error_text = ""
            self._target_check.setChecked(False)
            self._refresh()
        dialog.action_applied.connect(adapted)
        dialog.setModal(True)
        def finished(result):
            self._voice_dialog = None
            if result == QDialog.Accepted:
                self._practice.completed = True
                self._voice_provider = dialog.provider.currentData()
                self._voice_use_mode = dialog.doubao_use_mode
                self._voice_recovery_reason = None
                set_translatable_text(self._trial_note, "已完成语音和发送练习，现在到常用 AI 中试一次。")
                set_translatable_text(self._bind, "重新练习")
            self._refresh()
        dialog.finished.connect(finished)
        self._refresh()
        dialog.show()

    def _go_back(self):
        if (self._retrying and self.vm.draft is not None
                and self.vm.draft.config == self._candidate and not self.vm.write_transaction.blocks_editing):
            # Only undo the unconfirmed draft created by this guide.
            self.vm.discard_draft()
        self._retrying = False
        self._step(max(0, self._pages.currentIndex() - 1))

    def _defer(self):
        if self.settings is not None:
            self.settings.setValue(AI_SETUP_DEFERRED_KEY, True)
            self.settings.sync()
        self.reject()

    def accept(self):
        if (self._pages.currentIndex() != 4 or not self._next.isEnabled()
                or not self._practice_context_matches()):
            return
        if self._uses_doubao_macos() and not self._target_result_confirmed:
            return
        key = self._current.currentData()
        self._entries[key]["tried"] = True  # User confirmation in the target app, not automatic verification.
        save_device_choices(self.settings, self._serial, self._entries)
        if self._voice.currentData() == "external":
            snapshot = self.vm.model.snapshot
            if self.settings is not None and self._voice_provider:
                self.settings.setValue(
                    voice_provider_key(self._serial, snapshot.active_profile_id),
                    self._voice_provider,
                )
                self.settings.sync()
            save_voice_trial(self.settings, self._serial, snapshot.active_profile_id, "key.8",
                             snapshot.mappings["key.8"]["action"], self._voice_provider, key)
        if self.settings is not None:
            self.settings.setValue(AI_SETUP_COMPLETED_KEY, True)
            self.settings.setValue(AI_SETUP_DEFERRED_KEY, False)
            self.settings.sync()
        super().accept()

    def _finished(self, *_):
        if self._applying:
            # Keep observing a write after the user hides the guide. Reopening
            # resumes this same dialog; readback still owns preference updates.
            return
        self.vm.changed.disconnect(self._refresh)
        if self._voice_dialog:
            self._voice_dialog.close()
        self.deleteLater()
