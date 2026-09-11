from __future__ import annotations

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
    trigger_prompt_id: int
    script_path: str
    enabled: bool


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
        heading.addWidget(QLabel("HOST AUTOMATION", objectName="eyebrow"))
        heading.addWidget(QLabel("脚本导入", objectName="inspectorTitle"))
        intro = QLabel(
            "把已有的 USB 提示词事件交给控制台内部事件总线，再运行用户明确选择的本地 Python 脚本。",
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
        self._list.currentItemChanged.connect(self._selection_changed)
        definitions_layout.addWidget(self._list, 1)
        new_button = QPushButton("新建脚本", objectName="secondary")
        new_button.clicked.connect(self._new_definition)
        definitions_layout.addWidget(new_button)
        workspace.addWidget(definitions, 2)

        editor = _card("focus")
        editor_layout = QVBoxLayout(editor)
        editor_layout.setContentsMargins(24, 20, 24, 22)
        editor_layout.setSpacing(12)
        editor_layout.addWidget(QLabel("本地脚本设置", objectName="eyebrow"))
        editor_layout.addWidget(QLabel("提示词事件 → Python 脚本", objectName="inspectorTitle"))
        form = QFormLayout()
        form.setHorizontalSpacing(16)
        self._name = QLineEdit(objectName="automationName")
        self._name.setPlaceholderText("例如：收集当前项目信息")
        form.addRow("名称", self._name)
        self._trigger = QComboBox(objectName="automationTrigger")
        for prompt_id in view_model.prompt_trigger_choices:
            prompt_name = self._prompt_names.get(prompt_id)
            label = (
                f"提示词 {prompt_id} · {prompt_name}"
                if prompt_name
                else f"提示词槽位 {prompt_id}"
            )
            self._trigger.addItem(label, prompt_id)
        form.addRow("实体触发", self._trigger)

        script_row = QHBoxLayout()
        self._script_path = QLineEdit(objectName="automationScriptPath")
        self._script_path.setPlaceholderText("选择一个本地 .py 文件")
        script_row.addWidget(self._script_path, 1)
        choose = QPushButton("选择脚本", objectName="secondary")
        choose.clicked.connect(self._choose_script)
        script_row.addWidget(choose)
        form.addRow("Python 脚本", script_row)
        self._enabled = QCheckBox("允许实体提示词事件触发此脚本", objectName="automationEnabled")
        form.addRow("实体运行", self._enabled)
        editor_layout.addLayout(form)

        behavior = QLabel(
            "启用后，该提示词槽位的事件会优先运行脚本，不再自动粘贴提示词。脚本从标准输入收到 UTF-8 JSON 事件，不通过 shell 启动。",
            objectName="roleHuman",
        )
        behavior.setWordWrap(True)
        editor_layout.addWidget(behavior)

        buttons = QHBoxLayout()
        save = QPushButton("保存本地脚本", objectName="primary")
        save.clicked.connect(self._save)
        buttons.addWidget(save)
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
        runtime_header.addWidget(QLabel("运行记录", objectName="inspectorTitle"))
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
        layout.addWidget(runtime)

        boundary = QLabel(
            "本地脚本只执行用户在本机明确选择并启用的 Python 文件；不下载网络代码，也不修改设备协议或固件。",
            objectName="roleContext",
        )
        boundary.setWordWrap(True)
        layout.addWidget(boundary)
        layout.addStretch(1)

        self.setWidget(page)
        self._host.changed.connect(self._schedule_runtime_refresh)
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
            trigger_prompt_id=int(self._trigger.currentData() or 1),
            script_path=self._script_path.text(),
            enabled=self._enabled.isChecked(),
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
        self._select_list_item(state.automation_id)
        self._update_button_state()

    def refresh_runtime(self) -> None:
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
        self._update_button_state()

    def _reload_definitions(self, selected_id: str | None = None) -> None:
        target = selected_id if selected_id is not None else self._selected_id
        self._list.blockSignals(True)
        self._list.clear()
        for definition in sorted(self._host.definitions, key=lambda item: item.name.lower()):
            state = translate_ui_text("已启用" if definition.enabled else "未启用")
            item = QListWidgetItem(
                f"{definition.name}\n{translate_ui_text('提示词槽位')} "
                f"{definition.trigger_prompt_id} · {state}"
            )
            item.setData(Qt.ItemDataRole.UserRole, definition.automation_id)
            self._list.addItem(item)
        self._list.blockSignals(False)
        if not self._select_list_item(target) and self._list.count():
            self._list.setCurrentRow(0)
        elif self._list.count() == 0:
            self._new_definition()

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
        self._script_path.setText(definition.script_path)
        self._enabled.setChecked(definition.enabled)
        self._form_status.clear()
        self._update_button_state()

    def _new_definition(self) -> None:
        self._selected_id = None
        self._list.clearSelection()
        self._name.clear()
        self._trigger.setCurrentIndex(0)
        self._script_path.clear()
        self._enabled.setChecked(False)
        self._form_status.setText(
            translate_ui_text("新建本地脚本默认不允许实体触发；保存后可手动测试。")
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

    def _save(self) -> None:
        try:
            definition = self._view_model.save_automation(
                automation_id=self._selected_id,
                name=self._name.text(),
                trigger_prompt_id=int(self._trigger.currentData()),
                script_path=self._script_path.text(),
                enabled=self._enabled.isChecked(),
            )
        except (AutomationError, OSError) as exc:
            QMessageBox.warning(
                self,
                translate_ui_text("无法保存本地脚本"),
                translate_ui_text(str(exc)),
            )
            return
        self._selected_id = definition.automation_id
        self._form_status.setText(translate_ui_text("本地脚本已保存"))
        self._reload_definitions(definition.automation_id)

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
        except AutomationError as exc:
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
        selected = self._selected_id is not None
        self._test.setEnabled(selected and not self._host.running)
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
