from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QBoxLayout,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from controller_config.automation import AutomationError, LocalScriptAutomation
from controller_config.i18n import translate_ui_text
from controller_config.views.v4_widgets import V4Card

if TYPE_CHECKING:
    from controller_config.viewmodels.main import MainViewModel


@dataclass(frozen=True)
class AutomationEditingState:
    automation_id: str | None
    name: str
    trigger_prompt_id: int | None
    script_path: str
    enabled: bool
    timeout_ms: int = 60_000


class AutomationPage(QScrollArea):
    """Local script automation editor backed by the shared host event bus."""

    def __init__(
        self,
        view_model: MainViewModel,
        *,
        prompt_names: dict[int, str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent, objectName="automationPage")
        self._view_model = view_model
        self._host = view_model.automation_host
        self._device_serial = self._host.serial
        self._prompt_names = prompt_names or {}
        self._selected_id: str | None = None
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self.refresh_runtime)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setStyleSheet("QScrollArea { background: transparent; }")

        page = QWidget(objectName="automationPageContents")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(14)

        summary = _card("widget")
        summary_layout = QHBoxLayout(summary)
        summary_layout.setContentsMargins(24, 20, 24, 22)
        heading = QVBoxLayout()
        heading.addWidget(QLabel("PYTHON", objectName="eyebrow"))
        heading.addWidget(QLabel("脚本导入", objectName="inspectorTitle"))
        intro = QLabel(
            "导入已有的 .py 小工具，按一下设备键在这台电脑上运行。BORING 后台需保持运行。",
            objectName="muted",
        )
        intro.setWordWrap(True)
        heading.addWidget(intro)
        summary_layout.addLayout(heading, 1)
        self._listener = QLabel(objectName="statusWarn")
        self._listener.setAlignment(Qt.AlignCenter)
        summary_layout.addWidget(self._listener, 0, Qt.AlignTop)
        layout.addWidget(summary)

        workspace = QBoxLayout(QBoxLayout.LeftToRight)
        self._workspace = workspace
        workspace.setSpacing(14)

        definitions = _card("primary")
        definitions_layout = QVBoxLayout(definitions)
        definitions_layout.setContentsMargins(20, 18, 20, 20)
        definitions_layout.addWidget(QLabel("脚本列表", objectName="inspectorTitle"))
        self._list = QListWidget(objectName="automationList")
        self._list.setMaximumHeight(140)
        self._list.currentItemChanged.connect(self._selection_changed)
        definitions_layout.addWidget(self._list, 1)
        new_button = QPushButton("添加 Python 脚本", objectName="secondary")
        new_button.clicked.connect(self._new_definition)
        definitions_layout.addWidget(new_button)
        workspace.addWidget(definitions, 2)

        editor = _card("focus")
        editor_layout = QVBoxLayout(editor)
        editor_layout.setContentsMargins(24, 20, 24, 22)
        editor_layout.setSpacing(12)
        editor_layout.addWidget(QLabel("本地脚本设置", objectName="eyebrow"))
        editor_layout.addWidget(QLabel("运行一个 Python 文件", objectName="inspectorTitle"))
        form = QFormLayout()
        form.setHorizontalSpacing(16)
        self._name = QLineEdit(objectName="automationName")
        self._name.setPlaceholderText("例如：收集当前项目信息")
        form.addRow("名称", self._name)
        self._trigger = QComboBox(objectName="automationTrigger")
        self._trigger.addItem("直接绑定设备键", None)
        for prompt_id in view_model.prompt_trigger_choices:
            prompt_name = self._prompt_names.get(prompt_id)
            label = (
                f"提示词 {prompt_id} · {prompt_name}"
                if prompt_name
                else f"提示词槽位 {prompt_id}"
            )
            self._trigger.addItem(label, prompt_id)
        self._trigger_label = QLabel("通过提示词触发")
        form.addRow(self._trigger_label, self._trigger)
        self._control = QComboBox(objectName="automationPhysicalControl")
        self._control_label = QLabel("设备按键")
        form.addRow(self._control_label, self._control)

        script_row = QHBoxLayout()
        self._script_path = QLineEdit(objectName="automationScriptPath")
        self._script_path.setPlaceholderText("选择一个本地 .py 文件")
        script_row.addWidget(self._script_path, 1)
        choose = QPushButton("选择脚本", objectName="secondary")
        choose.clicked.connect(self._choose_script)
        script_row.addWidget(choose)
        form.addRow("Python 脚本", script_row)
        self._enabled = QCheckBox("允许实体提示词事件触发此脚本", objectName="automationEnabled")
        self._enabled_label = QLabel("实体运行")
        form.addRow(self._enabled_label, self._enabled)
        editor_layout.addLayout(form)

        behavior = QLabel(
            "Console 自带 Python 运行器，无需另装 Python；不会自动安装第三方依赖。导入前请向作者确认所需依赖和输出位置。",
            objectName="roleHuman",
        )
        behavior.setWordWrap(True)
        editor_layout.addWidget(behavior)
        script_help = QPushButton("脚本接口说明", objectName="scriptInterfaceHelp")
        script_help.setCheckable(True)
        script_detail = QLabel("脚本从标准输入收到 UTF-8 JSON 事件，不通过 shell 启动。旧提示词绑定启用后不再自动粘贴该提示词。退出码 0 表示脚本正常退出，实际产物请按作者说明检查。", objectName="muted")
        script_detail.setWordWrap(True)
        script_detail.hide()
        script_help.toggled.connect(script_detail.setVisible)
        editor_layout.addWidget(script_help)
        editor_layout.addWidget(script_detail)
        timeout_row = QWidget(objectName="automationTimeoutRow")
        timeout_layout = QHBoxLayout(timeout_row)
        timeout_layout.setContentsMargins(0, 0, 0, 0)
        timeout_layout.addWidget(QLabel("最长运行时间"))
        self._timeout = QSpinBox(objectName="automationTimeout")
        self._timeout.setRange(1, 3600)
        self._timeout.setValue(60)
        self._timeout.setSuffix(" s")
        timeout_layout.addWidget(self._timeout)
        timeout_row.hide()
        script_help.toggled.connect(timeout_row.setVisible)
        editor_layout.addWidget(timeout_row)

        buttons = QHBoxLayout()
        self._save_button = QPushButton("保存本地脚本", objectName="primary")
        self._save_button.clicked.connect(self._apply_or_save)
        buttons.addWidget(self._save_button)
        self._trial = QPushButton("开始实体试用", objectName="automationTrial")
        self._trial.clicked.connect(self._arm_trial)
        buttons.addWidget(self._trial)
        self._test = QPushButton("手动测试运行", objectName="secondary")
        self._test.clicked.connect(self._run_test)
        buttons.addWidget(self._test)
        self._stop = QPushButton("停止运行", objectName="stopAutomationRun")
        self._stop.clicked.connect(self._host.cancel_run)
        buttons.addWidget(self._stop)
        self._delete = QPushButton("删除", objectName="secondary")
        self._delete.clicked.connect(self._delete_selected)
        buttons.addWidget(self._delete)
        buttons.addStretch(1)
        editor_layout.addLayout(buttons)
        self._form_status = QLabel("", objectName="muted")
        self._form_status.setWordWrap(True)
        editor_layout.addWidget(self._form_status)
        workspace.addWidget(editor, 3)
        layout.addLayout(workspace, 1)

        runtime = _card("widget")
        runtime_layout = QVBoxLayout(runtime)
        runtime_layout.setContentsMargins(24, 20, 24, 22)
        runtime_layout.setSpacing(10)
        runtime_header = QHBoxLayout()
        self._runtime_status = QLabel("尚未运行", objectName="automationRuntimeStatus")
        self._runtime_status.setWordWrap(True)
        runtime_header.addWidget(self._runtime_status, 1)
        self._details = QPushButton("查看详情", objectName="automationLogToggle")
        self._details.setCheckable(True)
        runtime_header.addWidget(self._details)
        runtime_header.addStretch(1)
        clear = QPushButton("清空记录", objectName="secondary")
        clear.clicked.connect(self._host.clear_logs)
        runtime_header.addWidget(clear)
        runtime_layout.addLayout(runtime_header)
        self._latest_event = QLabel(objectName="automationLatestEvent")
        self._latest_event.setWordWrap(True)
        runtime_layout.addWidget(self._latest_event)
        self._logs = QPlainTextEdit(objectName="automationRunLog")
        self._logs.setReadOnly(True)
        self._logs.setMaximumBlockCount(100)
        self._logs.setMinimumHeight(130)
        runtime_layout.addWidget(self._logs)
        self._logs.hide()
        self._latest_event.hide()
        self._details.toggled.connect(self._logs.setVisible)
        self._details.toggled.connect(self._latest_event.setVisible)
        layout.addWidget(runtime)

        boundary = QLabel(
            "本地脚本只执行用户在本机明确选择并启用的 Python 文件；不下载网络代码，也不修改设备协议或固件。",
            objectName="roleContext",
        )
        boundary.setWordWrap(True)
        layout.addWidget(boundary)
        layout.addStretch(1)

        self.setWidget(page)
        for field in (self._name, self._script_path):
            field.textEdited.connect(self._update_button_state)
        self._timeout.valueChanged.connect(self._update_button_state)
        self._trigger.currentIndexChanged.connect(self._update_button_state)
        self._host.changed.connect(self._schedule_runtime_refresh)
        self._view_model.host_tasks.changed.connect(self._update_button_state)
        self._view_model.event_bus.event_received.connect(self._event_received)
        self._reload_definitions()
        self.refresh_runtime()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if hasattr(self, "_workspace"):
            self._workspace.setDirection(
                QBoxLayout.TopToBottom if self.viewport().width() < 1000 else QBoxLayout.LeftToRight
            )

    @property
    def device_serial(self) -> str:
        return self._device_serial

    def _schedule_runtime_refresh(self) -> None:
        if not self._refresh_timer.isActive():
            self._refresh_timer.start(0)

    def _event_received(self, _event: object) -> None:
        self._schedule_runtime_refresh()

    def editing_state(self) -> AutomationEditingState:
        return AutomationEditingState(
            automation_id=self._selected_id,
            name=self._name.text(),
            trigger_prompt_id=self._trigger.currentData(),
            script_path=self._script_path.text(),
            enabled=self._enabled.isChecked(),
            timeout_ms=self._timeout.value() * 1000,
        )

    def restore_editing_state(self, state: AutomationEditingState) -> None:
        self._selected_id = state.automation_id
        self._name.setText(state.name)
        index = self._trigger.findData(state.trigger_prompt_id)
        if index < 0:
            self._trigger.addItem(f"提示词槽位 {state.trigger_prompt_id} · 需重新配置", state.trigger_prompt_id)
            index = self._trigger.count() - 1
        if index >= 0:
            self._trigger.setCurrentIndex(index)
        self._script_path.setText(state.script_path)
        self._enabled.setChecked(state.enabled)
        self._timeout.setValue(state.timeout_ms // 1000)
        self._select_list_item(state.automation_id)
        self._update_button_state()

    def refresh_runtime(self) -> None:
        if self._host.definitions != getattr(self, "_listed_definitions", None):
            draft = self.editing_state()
            control = self._control.currentData()
            self._reload_definitions(self._selected_id)
            self.restore_editing_state(draft)
            self._refresh_controls(control)
        listener = self._view_model.prompt_device.listener_status
        self._listener.setText(translate_ui_text(listener.label))
        self._set_listener_style(listener.online)
        events = tuple(
            event
            for event in self._view_model.event_bus.events
            if event.device_serial == self._host.serial
        )
        if events:
            event = events[-1]
            prompt_id = event.payload.get("prompt_id", "—")
            self._latest_event.setText(
                translate_ui_text("最近事件")
                + f"：{event.kind} · prompt_id={prompt_id} · event_id={event.event_id}"
            )
        else:
            self._latest_event.setText(translate_ui_text("尚未收到设备语义事件"))
        text = "\n".join(
            f"[{entry.timestamp}] {entry.level.upper()}  "
            f"{translate_ui_text(entry.message)}"
            + (f"\n  {entry.technical}" if entry.technical else "")
            for entry in self._host.logs
        )
        if self._logs.toPlainText() != text:
            self._logs.setPlainText(text)
            bar = self._logs.verticalScrollBar()
            bar.setValue(bar.maximum())
        if self._host.running:
            self._runtime_status.setText(translate_ui_text("正在运行…"))
        elif self._host.logs:
            latest = self._host.logs[-1]
            self._runtime_status.setText(translate_ui_text(latest.message))
            self._details.setText(translate_ui_text("查看失败详情" if latest.level == "error" else "查看详情"))
        else:
            self._runtime_status.setText(translate_ui_text("尚未运行"))
        self._update_button_state()

    def _reload_definitions(self, selected_id: str | None = None) -> None:
        self._listed_definitions = self._host.definitions
        target = selected_id if selected_id is not None else self._selected_id
        self._list.blockSignals(True)
        self._list.clear()
        for definition in sorted(self._host.definitions, key=lambda item: item.name.lower()):
            state = translate_ui_text("已启用" if definition.enabled else "未启用")
            item = QListWidgetItem(
                f"{definition.name}\n"
                f"{translate_ui_text('保存在这台电脑') if definition.trigger_prompt_id is None else translate_ui_text('提示词槽位') + str(definition.trigger_prompt_id)} · {state}"
            )
            item.setData(Qt.ItemDataRole.UserRole, definition.automation_id)
            self._list.addItem(item)
        self._list.blockSignals(False)
        if not self._select_list_item(target) and self._list.count():
            self._list.setCurrentRow(0)
        elif self._list.count() == 0:
            self._new_definition()

    def select_automation(self, automation_id: str) -> bool:
        if automation_id == self._selected_id:
            return True
        definition = self._definition(automation_id)
        if definition is None:
            return False
        previous = self._definition(self._selected_id)
        current = self.editing_state()
        expected = AutomationEditingState(
            previous.automation_id, previous.name, previous.trigger_prompt_id,
            previous.script_path, previous.enabled, previous.timeout_ms,
        ) if previous is not None else AutomationEditingState(None, "", None, "", False)
        if current != expected:
            choice = QMessageBox.question(
                self, translate_ui_text("未保存的修改"),
                translate_ui_text("保存当前修改后再继续？"),
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Cancel,
            )
            if choice == QMessageBox.Cancel:
                return False
            if choice == QMessageBox.Save and not self._save():
                return False
        self._reload_definitions(automation_id)
        self._selection_changed(self._list.currentItem(), None)
        return True

    def _select_list_item(self, automation_id: str | None) -> bool:
        if automation_id is None:
            self._list.clearSelection()
            return False
        for row in range(self._list.count()):
            item = self._list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == automation_id:
                self._list.setCurrentRow(row)
                return True
        return False

    def _selection_changed(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        automation_id = (
            current.data(Qt.ItemDataRole.UserRole) if current is not None else None
        )
        definition = self._definition(automation_id)
        if definition is None:
            return
        self._selected_id = definition.automation_id
        self._name.setText(definition.name)
        trigger_index = self._trigger.findData(definition.trigger_prompt_id)
        if trigger_index < 0:
            self._trigger.addItem(f"提示词槽位 {definition.trigger_prompt_id} · 需重新配置", definition.trigger_prompt_id)
            trigger_index = self._trigger.count() - 1
        if trigger_index >= 0:
            self._trigger.setCurrentIndex(trigger_index)
        binding = self._view_model.host_tasks.binding_for("script", definition.automation_id)
        self._refresh_controls(binding.control_id if binding else None)
        self._script_path.setText(definition.script_path)
        self._enabled.setChecked(definition.enabled)
        self._timeout.setValue(definition.timeout_ms // 1000)
        self._form_status.clear()
        self._update_button_state()

    def _new_definition(self) -> None:
        self._selected_id = None
        self._list.clearSelection()
        self._name.clear()
        self._trigger.setCurrentIndex(0)
        self._refresh_controls(getattr(self._view_model.host_tasks, "preferred_control", None))
        self._script_path.clear()
        self._timeout.setValue(60)
        self._enabled.setChecked(False)
        self._form_status.setText(
            translate_ui_text("选择脚本和设备按键，应用后进行首次试用。")
        )
        self._update_button_state()

    def _choose_script(self) -> None:
        file_name, _selected_filter = QFileDialog.getOpenFileName(
            self,
            translate_ui_text("选择本地 Python 脚本"),
            str(Path.home()),
            translate_ui_text("Python 脚本 (*.py);;所有文件 (*)"),
        )
        if file_name:
            self._script_path.setText(file_name)
            if not self._name.text().strip():
                self._name.setText(Path(file_name).stem)

    def _refresh_controls(self, control_id: str | None = None) -> None:
        target = control_id or self._control.currentData() or getattr(self._view_model.host_tasks, "preferred_control", None)
        self._control.clear()
        for label, value in self._view_model.host_tasks.controls:
            self._control.addItem(label, value)
        self._control.setCurrentIndex(max(0, self._control.findData(target)))

    def _apply_or_save(self) -> None:
        if self._trigger.currentData() is not None:
            self._save()
            return
        definition = LocalScriptAutomation(
            self._selected_id or uuid.uuid4().hex, self._name.text().strip(), None,
            self._script_path.text().strip(), False,
            self._timeout.value() * 1000,
        )
        self._selected_id = definition.automation_id
        try:
            definition.validate()
            self._view_model.host_tasks.apply("script", definition, self._control.currentData())
        except (AutomationError, ValueError, OSError) as exc:
            QMessageBox.warning(self, translate_ui_text("无法保存本地脚本"), str(exc))
            return
        self._selected_id = definition.automation_id
        self._update_button_state()

    def _arm_trial(self) -> None:
        binding = self._view_model.host_tasks.binding_for("script", self._selected_id)
        if binding is not None:
            try:
                self._view_model.host_tasks.arm_trial(binding.action_id)
            except ValueError as exc:
                self._form_status.setText(translate_ui_text(str(exc)))

    def hideEvent(self, event) -> None:  # noqa: N802
        self._view_model.host_tasks.cancel_trial()
        super().hideEvent(event)

    def _save(self) -> bool:
        try:
            definition = self._view_model.save_automation(
                automation_id=self._selected_id,
                name=self._name.text(),
                trigger_prompt_id=self._trigger.currentData(),
                script_path=self._script_path.text(),
                enabled=self._enabled.isChecked(),
                timeout_ms=self._timeout.value() * 1000,
            )
        except (AutomationError, OSError) as exc:
            QMessageBox.warning(
                self,
                translate_ui_text("无法保存本地脚本"),
                translate_ui_text(str(exc)),
            )
            return False
        self._selected_id = definition.automation_id
        self._form_status.setText(translate_ui_text("本地脚本已保存"))
        self._reload_definitions(definition.automation_id)
        return True

    def _run_test(self) -> None:
        if self._selected_id is None:
            return
        try:
            self._view_model.run_automation_test(self._selected_id)
        except AutomationError as exc:
            QMessageBox.warning(
                self,
                translate_ui_text("无法运行本地脚本"),
                translate_ui_text(str(exc)),
            )

    def _delete_selected(self) -> None:
        if self._selected_id is None:
            return
        answer = QMessageBox.question(
            self,
            translate_ui_text("删除本地脚本"),
            translate_ui_text("删除后，对应提示词事件将恢复默认处理。"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self._view_model.delete_automation(self._selected_id)
        except (AutomationError, ValueError, OSError) as exc:
            QMessageBox.warning(
                self,
                translate_ui_text("无法删除本地脚本"),
                translate_ui_text(str(exc)),
            )
            return
        self._selected_id = None
        self._reload_definitions()

    def _definition(self, automation_id: object) -> LocalScriptAutomation | None:
        return next(
            (
                definition
                for definition in self._host.definitions
                if definition.automation_id == automation_id
            ),
            None,
        )

    def _update_button_state(self) -> None:
        local_task = self._trigger.currentData() is None
        self._trigger.setVisible(not local_task)
        self._trigger_label.setVisible(not local_task)
        self._control.setVisible(local_task)
        self._control_label.setVisible(local_task)
        self._enabled.setVisible(not local_task)
        self._enabled_label.setVisible(not local_task)
        self._refresh_controls()
        binding = self._view_model.host_tasks.binding_for("script", self._selected_id)
        self._trial.setVisible(local_task and binding is not None)
        definition = self._definition(self._selected_id)
        matches_saved = definition is not None and (
            self._name.text().strip() == definition.name
            and self._script_path.text().strip() == definition.script_path
            and self._timeout.value() * 1000 == definition.timeout_ms
            and (binding is None or self._control.currentData() == binding.control_id)
        )
        self._trial.setEnabled(binding is not None and binding.applied and matches_saved)
        self._save_button.setText(translate_ui_text("应用到设备" if local_task else "保存本地脚本"))
        self._save_button.setEnabled(not local_task or not self._view_model.host_tasks.problem)
        if local_task:
            self._form_status.setText(translate_ui_text(self._view_model.host_tasks.problem or self._view_model.host_tasks.status or "选择脚本和设备按键，应用后进行首次试用。"))
        selected = self._selected_id is not None
        self._test.setEnabled(selected and matches_saved and not self._host.running)
        self._stop.setEnabled(self._host.running)
        self._delete.setEnabled(selected)
        if self._host.load_error:
            self._form_status.setText(self._host.load_error)
        elif not self._host.serial:
            self._form_status.setText(
                translate_ui_text("连接设备后，本地脚本会按 USB 序列号分别保存。")
            )

    def _set_listener_style(self, online: bool) -> None:
        object_name = "statusReady" if online else "statusWarn"
        if self._listener.objectName() == object_name:
            return
        self._listener.setObjectName(object_name)
        self._listener.style().unpolish(self._listener)
        self._listener.style().polish(self._listener)


def _card(role: str = "secondary") -> QFrame:
    return V4Card(role=role, dots=role == "primary")
