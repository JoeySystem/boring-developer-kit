from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QMenu,
    QPushButton,
    QPlainTextEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from controller_config.i18n import translate_ui_text
from controller_config.views.v4_widgets import V4Card
from controller_config.workflow_files import export_workflow, import_workflow
from controller_config.workflows import (
    LocalWorkflow,
    WorkflowError,
    WorkflowStep,
)

if TYPE_CHECKING:
    from controller_config.viewmodels.main import MainViewModel


@dataclass(frozen=True)
class WorkflowEditingState:
    workflow_id: str | None
    name: str
    trigger_prompt_id: int | None
    steps: tuple[WorkflowStep, ...]
    enabled: bool
    tested: bool
    review_required: bool
    control_id: str | None = None
    dirty: bool = False
    selected_step: int = 0
    advanced_steps: bool = True
    action_choice: str | None = None
    scroll_position: int = 0


class WorkflowPage(QScrollArea):
    """No-code editor for BORING's small built-in host workflows."""

    def __init__(
        self,
        view_model: MainViewModel,
        *,
        prompt_names: dict[int, str] | None = None,
        prompt_controls: dict[int, tuple[str, ...]] | None = None,
        embedded: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent, objectName="workflowPage")
        self._view_model = view_model
        self._host = view_model.workflow_host
        self._prompt_names = prompt_names or {}
        self._prompt_controls = prompt_controls or {}
        self._selected_id: str | None = None
        self._steps: list[WorkflowStep] = []
        self._tested = False
        self._review_required = False
        self._loading = False
        self._dirty = False
        self._pending_test: LocalWorkflow | None = None
        self._template_kind: str | None = None
        self._test_timer = QTimer(self)
        self._test_timer.setSingleShot(True)
        self._test_timer.timeout.connect(self._run_scheduled_test)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setStyleSheet("QScrollArea { background: transparent; }")

        page = QWidget(objectName="workflowPageContents")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(14)

        self._empty_state = _card("primary")
        empty_layout = QVBoxLayout(self._empty_state)
        empty_layout.setContentsMargins(24, 24, 24, 26)
        empty_layout.setSpacing(12)
        empty_copy = QLabel("让一个按键连续完成多步操作。", objectName="muted")
        empty_copy.setWordWrap(True)
        empty_layout.addWidget(empty_copy)
        empty_actions = QHBoxLayout()
        create = QPushButton("创建自定义动作", objectName="createFirstWorkflow")
        create.setProperty("buttonRole", "primary")
        create.clicked.connect(self._new_workflow)
        empty_actions.addWidget(create)
        import_empty = QPushButton("导入自定义动作", objectName="importFirstWorkflow")
        import_empty.setProperty("buttonRole", "secondary")
        import_empty.clicked.connect(self._import)
        empty_actions.addWidget(import_empty)
        empty_actions.addStretch(1)
        empty_layout.addLayout(empty_actions)
        layout.addWidget(self._empty_state)

        self._workspace_shell = QWidget(objectName="workflowWorkspace")
        workspace = QBoxLayout(QBoxLayout.LeftToRight, self._workspace_shell)
        workspace.setContentsMargins(0, 0, 0, 0)
        self._workspace = workspace
        workspace.setSpacing(14)

        library = _card("primary")
        library_layout = QVBoxLayout(library)
        library_layout.setContentsMargins(20, 18, 20, 20)
        library_layout.addWidget(QLabel("自定义动作列表", objectName="inspectorTitle"))
        self._list = QListWidget(objectName="workflowList")
        self._list.setMaximumHeight(150)
        self._list.currentItemChanged.connect(self._selection_changed)
        library_layout.addWidget(self._list, 1)
        library_buttons = QHBoxLayout()
        new_button = QPushButton("新建", objectName="secondary")
        new_button.clicked.connect(self._new_workflow)
        template_button = QPushButton("保存复制的文字", objectName="secondary")
        template_button.clicked.connect(self._new_reference_workflow)
        library_buttons.addWidget(new_button)
        library_buttons.addWidget(template_button)
        library_layout.addLayout(library_buttons)
        workspace_template = QPushButton("打开工作环境", objectName="workspaceWorkflowTemplate")
        workspace_template.clicked.connect(self._new_workspace_workflow)
        library_layout.addWidget(workspace_template)
        workspace.addWidget(library, 2)
        library.setVisible(not embedded)

        editor = _card("focus")
        editor_layout = QVBoxLayout(editor)
        editor_layout.setContentsMargins(24, 20, 24, 22)
        editor_layout.setSpacing(12)
        form = QFormLayout()
        form.setHorizontalSpacing(16)
        self._name = QLineEdit(objectName="workflowName")
        self._name.setPlaceholderText("例如：保存复制的文字")
        self._name.textChanged.connect(self._set_dirty)
        form.addRow("名称", self._name)
        self._trigger = QComboBox(objectName="workflowTrigger")
        self._trigger.addItem("直接绑定设备键", None)
        for prompt_id in view_model.prompt_trigger_choices:
            prompt_name = self._prompt_names.get(prompt_id)
            prompt_label = (
                f"提示词 {prompt_id} · {prompt_name}"
                if prompt_name
                else f"提示词槽位 {prompt_id}"
            )
            controls = self._prompt_controls.get(prompt_id, ())
            label = (
                f"{prompt_label} · 当前 Profile：{'、'.join(controls)}"
                if controls
                else prompt_label
            )
            self._trigger.addItem(label, prompt_id)
        self._trigger.currentIndexChanged.connect(self._mark_dirty)
        self._trigger_label = QLabel("通过提示词触发")
        form.addRow(self._trigger_label, self._trigger)
        self._control = QComboBox(objectName="workflowPhysicalControl")
        self._control.currentIndexChanged.connect(self._set_dirty)
        self._control_label = QLabel("设备按键")
        form.addRow(self._control_label, self._control)
        self._enabled = QCheckBox("允许实体控件运行", objectName="workflowEnabled")
        self._enabled.toggled.connect(self._set_dirty)
        self._enabled_label = QLabel("启用")
        form.addRow(self._enabled_label, self._enabled)
        editor_layout.addLayout(form)

        self._template_preview = QLabel(objectName="workflowTemplatePreview")
        self._template_preview.setWordWrap(True)
        editor_layout.addWidget(self._template_preview)
        self._advanced_steps = QPushButton("自定义步骤", objectName="workflowAdvancedSteps")
        self._advanced_steps.setCheckable(True)
        self._advanced_steps.setChecked(True)
        self._step_header_widget = QWidget()
        step_header = QBoxLayout(QBoxLayout.LeftToRight, self._step_header_widget)
        step_header.setContentsMargins(0, 0, 0, 0)
        self._step_header = step_header
        step_header.addWidget(QLabel("执行步骤", objectName="inspectorTitle"))
        step_header.addStretch(1)
        self._action_choice = QComboBox(objectName="workflowActionChoice")
        for definition in self._host.registry.definitions:
            available, reason = self._host.registry.availability(definition.action_id)
            label = (
                definition.name
                if available
                else f"{definition.name} · 当前系统不可用"
            )
            self._action_choice.addItem(label, definition.action_id)
            if not available:
                self._action_choice.setItemData(
                    self._action_choice.count() - 1,
                    reason,
                    Qt.ItemDataRole.ToolTipRole,
                )
        step_header.addWidget(self._action_choice)
        add_step = QPushButton("添加步骤", objectName="secondary")
        add_step.clicked.connect(self._add_step)
        step_header.addWidget(add_step)
        editor_layout.addWidget(self._step_header_widget)

        self._step_list = QListWidget(objectName="workflowStepList")
        self._step_list.currentRowChanged.connect(self._step_selected)
        self._step_list.setMinimumHeight(150)
        self._step_list.setSpacing(6)
        self._step_list.setWordWrap(True)
        self._step_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._step_list.setStyleSheet(
            "QListWidget { background: transparent; border: none; }"
            "QListWidget::item { padding: 10px; border: 1px solid rgba(255,255,255,24); "
            "border-radius: 10px; background: rgba(255,255,255,8); }"
            "QListWidget::item:selected { border-color: #FF6A00; background: rgba(255,106,0,16); }"
        )
        editor_layout.addWidget(self._step_list)
        self._step_order = QWidget()
        order = QHBoxLayout(self._step_order)
        order.setContentsMargins(0, 0, 0, 0)
        up = QPushButton("上移", objectName="secondary")
        down = QPushButton("下移", objectName="secondary")
        remove = QPushButton("移除步骤", objectName="secondary")
        up.clicked.connect(lambda: self._move_step(-1))
        down.clicked.connect(lambda: self._move_step(1))
        remove.clicked.connect(self._remove_step)
        order.addWidget(up)
        order.addWidget(down)
        order.addWidget(remove)
        order.addStretch(1)
        editor_layout.addWidget(self._step_order)
        self._add_target = QPushButton("添加应用、文件夹或网址", objectName="workflowAddTarget")
        self._add_target.clicked.connect(self._add_workspace_target)
        editor_layout.addWidget(self._add_target)

        self._parameter_box = QFrame(objectName="workflowParameterBox")
        parameter_form = QFormLayout(self._parameter_box)
        self._parameter_help = QLabel(objectName="muted")
        self._parameter_help.setWordWrap(True)
        parameter_form.addRow("当前步骤", self._parameter_help)
        target_row = QHBoxLayout()
        self._parameter = QLineEdit(objectName="workflowStepParameter")
        self._parameter.textEdited.connect(self._parameter_changed)
        target_row.addWidget(self._parameter, 1)
        self._choose_file = QPushButton("选择文件", objectName="secondary")
        self._choose_file.clicked.connect(self._choose_parameter_file)
        self._choose_folder = QPushButton("选择目录", objectName="secondary")
        self._choose_folder.clicked.connect(self._choose_parameter_folder)
        target_row.addWidget(self._choose_file)
        target_row.addWidget(self._choose_folder)
        self._target_label = QLabel("参数")
        parameter_form.addRow(self._target_label, target_row)
        self._separator = QComboBox(objectName="workflowSeparator")
        self._separator.addItem("空一行", "\n\n")
        self._separator.addItem("换一行", "\n")
        self._separator.addItem("直接连接", "")
        self._separator.currentIndexChanged.connect(self._parameter_changed)
        self._separator_label = QLabel("追加间隔")
        parameter_form.addRow(self._separator_label, self._separator)
        editor_layout.addWidget(self._parameter_box)
        editor_layout.addWidget(self._advanced_steps)
        self._advanced_steps.toggled.connect(self._refresh_step_presentation)

        self.footer = QWidget(objectName="workflowFooter")
        footer_layout = QVBoxLayout(self.footer)
        footer_layout.setContentsMargins(8, 8, 8, 0)
        self._status = QLabel(objectName="workflowStatus")
        self._status.setWordWrap(True)
        footer_layout.addWidget(self._status)
        self._details_toggle = QPushButton("查看详情", objectName="workflowDetailsToggle")
        self._details_toggle.setCheckable(True)
        self._run_details = QPlainTextEdit(objectName="workflowRunDetails")
        self._run_details.setReadOnly(True)
        self._run_details.setMaximumHeight(140)
        self._run_details.hide()
        self._details_toggle.toggled.connect(self._run_details.setVisible)
        footer_layout.addWidget(self._details_toggle)
        footer_layout.addWidget(self._run_details)
        buttons = QBoxLayout(QBoxLayout.LeftToRight)
        self._footer_buttons = buttons
        self._configure_trigger = QPushButton("配置触发提示词", objectName="configureWorkflowTrigger")
        self._configure_trigger.clicked.connect(lambda: self._view_model.navigate("prompts"))
        buttons.addWidget(self._configure_trigger)
        more = QPushButton("更多操作", objectName="workflowMore")
        menu = QMenu(more)
        menu.addAction("复制", self._duplicate)
        menu.addAction("导出自定义动作", self._export)
        menu.addSeparator()
        menu.addAction("删除", self._delete)
        more.setMenu(menu)
        buttons.addWidget(more)
        buttons.addStretch(1)
        self._test_button = QPushButton("试运行", objectName="secondary")
        self._test_button.clicked.connect(self._schedule_test)
        buttons.addWidget(self._test_button)
        self._stop_button = QPushButton("停止运行", objectName="workflowStopRun")
        self._stop_button.clicked.connect(lambda: self._view_model.host_tasks.cancel_run())
        buttons.addWidget(self._stop_button)
        self._save_button = QPushButton("保存自定义动作", objectName="primary")
        self._save_button.clicked.connect(self._apply_or_save)
        buttons.addWidget(self._save_button)
        self._trial_button = QPushButton("开始实体试用", objectName="workflowTrial")
        self._trial_button.clicked.connect(self._arm_trial)
        buttons.addWidget(self._trial_button)
        self._open_output = QPushButton("打开文件", objectName="workflowOpenOutput")
        self._open_output.hide()
        self._open_output.clicked.connect(self._open_result_file)
        self._output_path = ""
        buttons.addWidget(self._open_output)
        footer_layout.addLayout(buttons)
        if not embedded:
            editor_layout.addWidget(self.footer)
        workspace.addWidget(editor, 4)
        layout.addWidget(self._workspace_shell, 1)

        layout.addStretch(1)
        self.setWidget(page)

        self._host.changed.connect(self.refresh)
        self._view_model.host_tasks.changed.connect(self._update_status)
        self._reload_list()
        if self._list.count() == 0:
            self._show_empty_state()
        else:
            self._show_editor()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if hasattr(self, "_workspace"):
            self._workspace.setDirection(QBoxLayout.TopToBottom if self.viewport().width() < 1000 else QBoxLayout.LeftToRight)
            compact = self.viewport().width() < 650
            self._step_header.setDirection(QBoxLayout.TopToBottom if compact else QBoxLayout.LeftToRight)
            self._footer_buttons.setDirection(QBoxLayout.TopToBottom if compact else QBoxLayout.LeftToRight)

    def refresh(self) -> None:
        if self._dirty:
            self._update_status()
            return
        inputs = (self._host.workflows, translate_ui_text("已启用"))
        if inputs != getattr(self, "_listed_workflows", None):
            self._reload_list(self._selected_id)
        self._update_status()

    def editing_state(self) -> WorkflowEditingState:
        return WorkflowEditingState(
            self._selected_id,
            self._name.text(),
            self._trigger.currentData(),
            tuple(self._steps),
            self._enabled.isChecked(),
            self._tested,
            self._review_required,
            self._control.currentData(),
            self._dirty,
            self._step_list.currentRow(),
            self._advanced_steps.isChecked(),
            self._action_choice.currentData(),
            self.verticalScrollBar().value(),
        )

    def restore_editing_state(self, state: WorkflowEditingState) -> None:
        """Restore local inputs only; never save, bind, enable or run a task."""
        self._loading = True
        try:
            self._selected_id = state.workflow_id
            self._name.setText(state.name)
            index = self._trigger.findData(state.trigger_prompt_id)
            if index < 0:
                self._trigger.addItem(translate_ui_text(
                    f"提示词槽位 {state.trigger_prompt_id} · 需重新配置"), state.trigger_prompt_id)
                index = self._trigger.count() - 1
            self._trigger.setCurrentIndex(index)
            self._refresh_controls(state.control_id)
            self._steps = [WorkflowStep(**step) if isinstance(step, dict) else step for step in state.steps]
            self._choose_template_presentation()
            self._advanced_steps.setChecked(state.advanced_steps)
            self._action_choice.setCurrentIndex(self._action_choice.findData(state.action_choice))
            self._enabled.setChecked(state.enabled)
            self._tested = state.tested
            self._review_required = state.review_required
            self._dirty = state.dirty
            self._render_steps(select_row=state.selected_step)
            if self._steps or state.dirty:
                self._show_editor()
        finally:
            self._loading = False
        self._update_status()
        self.verticalScrollBar().setValue(state.scroll_position)
        QTimer.singleShot(0, lambda: self.verticalScrollBar().setValue(state.scroll_position))

    def open_workflow(self, workflow_id: str) -> bool:
        if workflow_id == self._selected_id:
            self._show_editor()
            return True
        workflow = self._workflow(workflow_id)
        if workflow is None or not self._confirm_replace():
            return False
        self._load_workflow(workflow)
        self._show_editor()
        return True

    def _confirm_replace(self) -> bool:
        if not self._dirty:
            return True
        choice = QMessageBox.question(
            self, translate_ui_text("未保存的自定义动作"),
            translate_ui_text("保存当前修改后再继续？"),
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if choice == QMessageBox.Save:
            return self._save() is not None
        return choice == QMessageBox.Discard

    def _mark_dirty(self, *_args) -> None:
        if self._loading:
            return
        self._dirty = True
        self._tested = False
        if self._enabled.isChecked():
            self._enabled.blockSignals(True)
            self._enabled.setChecked(False)
            self._enabled.blockSignals(False)
        self._update_status()

    def _set_dirty(self, *_args) -> None:
        if self._loading:
            return
        self._dirty = True
        self._update_status()

    def _reload_list(self, selected_id: str | None = None) -> None:
        self._listed_workflows = (self._host.workflows, translate_ui_text("已启用"))
        target = selected_id if selected_id is not None else self._selected_id
        self._list.blockSignals(True)
        self._list.clear()
        for workflow in sorted(self._host.workflows, key=lambda item: item.name.lower()):
            state = translate_ui_text(
                "已启用"
                if workflow.enabled
                else ("已测试" if workflow.tested else "待测试")
            )
            item = QListWidgetItem(
                f"{workflow.name}\n"
                f"{translate_ui_text(f'提示词槽位 {workflow.trigger_prompt_id}') if workflow.trigger_prompt_id is not None else translate_ui_text('保存在这台电脑')} · {state}"
            )
            item.setData(Qt.ItemDataRole.UserRole, workflow.workflow_id)
            self._list.addItem(item)
        self._list.blockSignals(False)
        for row in range(self._list.count()):
            item = self._list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == target:
                self._list.setCurrentRow(row)
                self._load_workflow(self._workflow(target))
                return
        if self._list.count():
            self._list.setCurrentRow(0)
            item = self._list.item(0)
            self._load_workflow(
                self._workflow(item.data(Qt.ItemDataRole.UserRole))
            )

    def _selection_changed(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        if current is None:
            return
        workflow = self._workflow(current.data(Qt.ItemDataRole.UserRole))
        if workflow is not None:
            if not self.open_workflow(workflow.workflow_id):
                self._list.blockSignals(True)
                self._list.setCurrentItem(_previous)
                self._list.blockSignals(False)

    def _load_workflow(self, workflow: LocalWorkflow | None) -> None:
        if workflow is None:
            return
        self._loading = True
        self._selected_id = workflow.workflow_id
        self._name.setText(workflow.name)
        index = self._trigger.findData(workflow.trigger_prompt_id)
        if index < 0:
            self._trigger.addItem(f"提示词槽位 {workflow.trigger_prompt_id} · 需重新配置", workflow.trigger_prompt_id)
            index = self._trigger.count() - 1
        if index >= 0:
            self._trigger.setCurrentIndex(index)
        binding = self._view_model.host_tasks.binding_for("workflow", workflow.workflow_id)
        self._refresh_controls(binding.control_id if binding is not None else None)
        self._steps = list(workflow.steps)
        self._choose_template_presentation()
        self._enabled.setChecked(workflow.enabled)
        self._tested = workflow.tested
        self._review_required = workflow.review_required
        self._loading = False
        self._dirty = False
        self._render_steps(select_row=1 if self._template_kind == "clipboard" else 0)
        self._update_status()

    def _new_workflow(self) -> bool:
        if not self._confirm_replace():
            return False
        self._show_editor()
        self._loading = True
        self._selected_id = None
        self._name.setText("")
        self._trigger.setCurrentIndex(0)
        self._refresh_controls(getattr(self._view_model.host_tasks, "preferred_control", None))
        self._steps = [WorkflowStep("read_clipboard", {})]
        self._template_kind = None
        self._advanced_steps.setChecked(True)
        self._enabled.setChecked(False)
        self._tested = False
        self._review_required = False
        self._loading = False
        self._dirty = True
        self._list.clearSelection()
        self._render_steps()
        self._update_status()

        return True

    def _new_reference_workflow(self) -> bool:
        return self._new_template(
            "保存复制的文字",
            [WorkflowStep("read_clipboard", {}),
             WorkflowStep("append_text_file", {"path": "", "separator": "\n\n"})],
            select_row=1,
        )

    def _new_workspace_workflow(self) -> bool:
        return self._new_template(
            "打开工作环境", [WorkflowStep("open_target", {"target": ""})]
        )

    def _new_template(
        self, name: str, steps: list[WorkflowStep], *, select_row: int = 0
    ) -> bool:
        if not self._new_workflow():
            return False
        self._name.setText(translate_ui_text(name))
        self._steps = steps
        self._choose_template_presentation()
        self._render_steps(select_row=select_row)
        self._action_choice.setCurrentIndex(
            self._action_choice.findData("open_target" if steps[0].action_id == "open_target" else "append_text_file")
        )
        return True

    def _choose_template_presentation(self) -> None:
        actions = [step.action_id for step in self._steps]
        self._template_kind = (
            "clipboard" if actions == ["read_clipboard", "append_text_file"]
            else "workspace" if actions and all(action == "open_target" for action in actions)
            else None
        )
        self._advanced_steps.setChecked(self._template_kind is None)

    def _refresh_step_presentation(self, *_args) -> None:
        actions = [step.action_id for step in self._steps]
        if (self._template_kind == "clipboard" and actions != ["read_clipboard", "append_text_file"]
                or self._template_kind == "workspace" and not all(action == "open_target" for action in actions)):
            self._template_kind = None
        expanded = self._advanced_steps.isChecked() or self._template_kind is None
        if not expanded and self._template_kind == "clipboard" and self._step_list.currentRow() != 1:
            self._step_list.setCurrentRow(1)
        workspace = self._template_kind == "workspace"
        self._step_header_widget.setVisible(expanded)
        self._step_order.setVisible(expanded or workspace)
        self._step_list.setVisible(expanded or workspace)
        self._add_target.setVisible(workspace and not expanded)
        self._advanced_steps.setVisible(self._template_kind is not None)
        self._template_preview.setVisible(not expanded)
        if self._template_kind == "clipboard" and len(self._steps) > 1:
            target = str(self._steps[1].parameters.get("path", ""))
            self._template_preview.setText(translate_ui_text("剪贴板文字 → ") + (Path(target).name if target else translate_ui_text("选择保存文件")))
        elif workspace:
            self._template_preview.setText(translate_ui_text("按顺序打开下面的项目"))
        self._parameter_box.layout().setRowVisible(self._parameter_help, expanded)
        row = self._step_list.currentRow()
        append = 0 <= row < len(self._steps) and self._steps[row].action_id == "append_text_file"
        self._separator.setVisible(expanded and append)
        self._separator_label.setVisible(expanded and append)

    def _add_workspace_target(self) -> None:
        self._action_choice.setCurrentIndex(self._action_choice.findData("open_target"))
        self._add_step()

    def _add_step(self) -> None:
        if len(self._steps) >= 5:
            self._show_error("一个自定义动作最多包含 5 个步骤")
            return
        action_id = str(self._action_choice.currentData())
        parameters: dict[str, object] = {}
        if action_id == "append_text_file":
            parameters = {"path": "", "separator": "\n\n"}
        elif action_id == "open_target":
            parameters = {"target": ""}
        elif action_id == "show_notification":
            parameters = {"message": "自定义动作已完成"}
        self._steps.append(WorkflowStep(action_id, parameters))
        self._mark_dirty()
        self._render_steps(select_row=len(self._steps) - 1)

    def _remove_step(self) -> None:
        row = self._step_list.currentRow()
        if row < 0 or len(self._steps) <= 1:
            self._show_error("自定义动作至少保留 1 个步骤")
            return
        self._steps.pop(row)
        self._mark_dirty()
        self._render_steps(select_row=min(row, len(self._steps) - 1))

    def _move_step(self, offset: int) -> None:
        row = self._step_list.currentRow()
        target = row + offset
        if row < 0 or not 0 <= target < len(self._steps):
            return
        self._steps[row], self._steps[target] = self._steps[target], self._steps[row]
        self._mark_dirty()
        self._render_steps(select_row=target)

    def _render_steps(self, *, select_row: int | None = None) -> None:
        current = self._step_list.currentRow() if select_row is None else select_row
        self._step_list.blockSignals(True)
        self._step_list.clear()
        for index, step in enumerate(self._steps, start=1):
            definition = self._host.registry.definition(step.action_id)
            detail = self._step_detail(step)
            self._step_list.addItem(
                f"{index}. {translate_ui_text(definition.name)}"
                + (f"\n{translate_ui_text(detail)}" if detail else "")
            )
        self._step_list.blockSignals(False)
        self._step_list.setFixedHeight(max(64, min(360, len(self._steps) * 70)))
        if self._steps:
            self._step_list.setCurrentRow(max(0, min(current, len(self._steps) - 1)))
            self._step_selected(self._step_list.currentRow())

    def _step_selected(self, row: int) -> None:
        self._loading = True
        if not 0 <= row < len(self._steps):
            self._parameter_box.setVisible(False)
            self._loading = False
            return
        step = self._steps[row]
        definition = self._host.registry.definition(step.action_id)
        self._parameter_help.setText(translate_ui_text(definition.description))
        has_parameter = step.action_id in {
            "append_text_file",
            "open_target",
            "show_notification",
        }
        self._parameter.setVisible(has_parameter)
        self._target_label.setVisible(has_parameter)
        self._choose_file.setVisible(
            step.action_id in {"append_text_file", "open_target"}
        )
        self._choose_folder.setVisible(step.action_id == "open_target")
        self._separator.setVisible(step.action_id == "append_text_file")
        self._separator_label.setVisible(step.action_id == "append_text_file")
        if step.action_id == "append_text_file":
            self._target_label.setText(translate_ui_text("目标文件"))
            self._parameter.setText(str(step.parameters.get("path", "")))
            separator_index = self._separator.findData(step.parameters.get("separator", "\n\n"))
            self._separator.setCurrentIndex(max(0, separator_index))
        elif step.action_id == "open_target":
            self._target_label.setText(translate_ui_text("目标"))
            self._parameter.setText(str(step.parameters.get("target", "")))
        elif step.action_id == "show_notification":
            self._target_label.setText(translate_ui_text("通知内容"))
            self._parameter.setText(str(step.parameters.get("message", "")))
        else:
            self._parameter.setText("")
        self._parameter_box.setVisible(True)
        self._loading = False
        self._refresh_step_presentation()

    def _parameter_changed(self, *_args) -> None:
        if self._loading:
            return
        row = self._step_list.currentRow()
        if not 0 <= row < len(self._steps):
            return
        step = self._steps[row]
        if step.action_id == "append_text_file":
            parameters = {
                "path": self._parameter.text(),
                "separator": str(self._separator.currentData()),
            }
        elif step.action_id == "open_target":
            parameters = {"target": self._parameter.text()}
        elif step.action_id == "show_notification":
            parameters = {"message": self._parameter.text()}
        else:
            return
        self._steps[row] = WorkflowStep(step.action_id, parameters)
        self._mark_dirty()
        self._update_step_item(row)
        self._refresh_step_presentation()

    def _choose_parameter_file(self) -> None:
        row = self._step_list.currentRow()
        if not 0 <= row < len(self._steps):
            return
        step = self._steps[row]
        if step.action_id == "append_text_file":
            path, _selected = QFileDialog.getSaveFileName(
                self, "选择目标", "", "Markdown / Text (*.md *.txt)"
            )
        else:
            path, _selected = QFileDialog.getOpenFileName(
                self, "选择目标", "", "All files (*)"
            )
        if path:
            self._parameter.setText(path)
            self._parameter_changed()

    def _choose_parameter_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择目录")
        if path:
            self._parameter.setText(path)
            self._parameter_changed()

    def _save(self) -> LocalWorkflow | None:
        existing = self._workflow(self._selected_id)
        workflow_id = self._selected_id or uuid.uuid4().hex
        tested = self._tested
        if existing is not None and (
            existing.trigger_prompt_id != self._trigger.currentData()
            or existing.steps != tuple(self._steps)
        ):
            tested = False
        workflow = LocalWorkflow(
            workflow_id,
            self._name.text().strip(),
            self._trigger.currentData(),
            tuple(self._steps),
            self._enabled.isChecked(),
            tested,
            False,
        )
        try:
            saved = self._view_model.save_workflow(workflow)
        except (WorkflowError, ValueError) as exc:
            self._show_error(str(exc))
            return None
        self._selected_id = saved.workflow_id
        self._tested = saved.tested
        self._review_required = saved.review_required
        self._dirty = False
        self._reload_list(saved.workflow_id)
        self._update_status()
        return saved

    def _definition_snapshot(self) -> LocalWorkflow:
        return LocalWorkflow(
            self._selected_id or uuid.uuid4().hex,
            self._name.text().strip(), self._trigger.currentData(),
            tuple(self._steps), False, self._tested, False,
        )

    def _apply_or_save(self) -> None:
        if self._trigger.currentData() is not None:
            self._save()
            return
        definition = self._definition_snapshot()
        self._selected_id = definition.workflow_id
        try:
            definition.validate()
            self._view_model.host_tasks.apply("workflow", definition, self._control.currentData())
        except (WorkflowError, ValueError, OSError) as exc:
            self._show_error(str(exc))
            return
        self._selected_id = definition.workflow_id
        self._dirty = False
        self._update_status()

    def _arm_trial(self) -> None:
        binding = self._view_model.host_tasks.binding_for("workflow", self._selected_id)
        if binding is not None:
            try:
                self._view_model.host_tasks.arm_trial(binding.action_id)
            except ValueError as exc:
                self._show_error(str(exc))

    def _refresh_controls(self, control_id: str | None = None) -> None:
        target = control_id or self._control.currentData() or getattr(self._view_model.host_tasks, "preferred_control", None)
        self._control.blockSignals(True)
        self._control.clear()
        for label, value in self._view_model.host_tasks.controls:
            self._control.addItem(label, value)
        index = self._control.findData(target)
        self._control.setCurrentIndex(max(0, index))
        self._control.blockSignals(False)

    def _schedule_test(self) -> None:
        if self._test_timer.isActive():
            self._test_timer.stop()
            self._pending_test = None
            self._test_button.setText(translate_ui_text("试运行"))
            self._update_status()
            return
        definition = self._definition_snapshot()
        try:
            definition.validate()
        except WorkflowError as exc:
            self._show_error(str(exc))
            return
        if any(step.action_id == "capture_selection" for step in definition.steps):
            self._pending_test = definition
            self._test_button.setText(translate_ui_text("取消试运行"))
            self._status.setText(translate_ui_text("3 秒后开始测试；请立即切回要获取内容的应用。"))
            self._test_timer.start(3000)
        else:
            self._run_definition_test(definition)

    def _run_scheduled_test(self) -> None:
        definition = self._pending_test
        self._pending_test = None
        self._test_button.setText(translate_ui_text("试运行"))
        if definition is not None:
            self._run_definition_test(definition)

    def _run_definition_test(self, definition: LocalWorkflow) -> None:
        if self._view_model.host_tasks.running:
            self._status.setText(translate_ui_text("已有电脑任务正在运行，请等待完成。"))
            return
        self._open_output.hide()
        self._test_button.setEnabled(False)
        def finished(result) -> None:
            self._test_button.setEnabled(True)
            self._status.setText(translate_ui_text(result.message))
            paths = [str(step.parameters["path"]) for step in definition.steps if step.action_id == "append_text_file"]
            self._output_path = paths[-1] if result.succeeded and paths else ""
            self._open_output.setVisible(bool(self._output_path))
        try:
            self._host.run_definition(definition, completed=finished)
        except (WorkflowError, ValueError) as exc:
            self._test_button.setEnabled(True)
            self._show_error(str(exc))

    def _open_result_file(self) -> None:
        if self._output_path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._output_path))

    def _run_test_now(self, workflow_id: str) -> None:
        self._view_model.test_workflow(workflow_id, completed=self._legacy_test_finished)

    def _legacy_test_finished(self, result) -> None:
        if not self._dirty:
            self._reload_list(self._selected_id)
        self._status.setText(translate_ui_text(result.message))

    def hideEvent(self, event) -> None:  # noqa: N802
        self._test_timer.stop()
        self._pending_test = None
        self._test_button.setText(translate_ui_text("试运行"))
        self._view_model.host_tasks.cancel_trial()
        super().hideEvent(event)

    def _duplicate(self) -> None:
        if self._selected_id is None:
            self._show_error("请先保存当前自定义动作")
            return
        if not self._confirm_replace():
            return
        duplicated = self._view_model.duplicate_workflow(self._selected_id)
        self._dirty = False
        self._reload_list(duplicated.workflow_id)

    def _delete(self) -> None:
        if self._selected_id is None:
            self._new_workflow()
            return
        if QMessageBox.question(
            self, translate_ui_text("删除自定义动作"),
            translate_ui_text("删除后将不再响应设备操作。确定删除？"),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        try:
            self._view_model.delete_workflow(self._selected_id)
        except (WorkflowError, ValueError, OSError) as exc:
            self._show_error(str(exc))
            return
        self._selected_id = None
        self._dirty = False
        self._reload_list()
        if self._list.count() == 0:
            self._show_empty_state()

    def _import(self) -> bool:
        path, _selected = QFileDialog.getOpenFileName(
            self, "导入 BORING 自定义动作", "", "BORING Automation (*.json)"
        )
        if not path or not self._confirm_replace():
            return False
        try:
            workflow = import_workflow(Path(path))
            saved = self._view_model.save_workflow(workflow)
        except (WorkflowError, ValueError) as exc:
            self._show_error(str(exc))
            return False
        self._show_editor()
        self._dirty = False
        self._reload_list(saved.workflow_id)
        self._status.setText(
            translate_ui_text("已导入但未启用；请检查本机路径和实体绑定。")
        )

        return True

    def _show_empty_state(self) -> None:
        self.footer.hide()
        self._empty_state.show()
        self._workspace_shell.hide()

    def _show_editor(self) -> None:
        self.footer.show()
        self._empty_state.hide()
        self._workspace_shell.show()

    def _export(self) -> None:
        workflow = self._workflow(self._selected_id)
        if workflow is None:
            self._show_error("请先保存当前自定义动作")
            return
        path, _selected = QFileDialog.getSaveFileName(
            self,
            "导出 BORING 自定义动作",
            f"{workflow.name}.json",
            "BORING Automation (*.json)",
        )
        if not path:
            return
        try:
            export_workflow(Path(path), workflow)
        except WorkflowError as exc:
            self._show_error(str(exc))
            return
        self._status.setText(translate_ui_text("自定义动作已导出"))

    def _update_status(self) -> None:
        self._stop_button.setVisible(self._view_model.host_tasks.running)
        logs = tuple(entry for entry in self._host.logs if self._selected_id is None or entry.workflow_id == self._selected_id)
        detail_text = "\n".join(entry.message + ("\n" + entry.technical if entry.technical else "") for entry in logs)
        if self._run_details.toPlainText() != detail_text:
            self._run_details.setPlainText(detail_text)
        self._details_toggle.setVisible(bool(logs))
        self._details_toggle.setText(translate_ui_text("查看失败详情" if logs and logs[-1].level == "error" else "查看详情"))
        local_task = self._trigger.currentData() is None
        self._trigger.setVisible(not local_task)
        self._trigger_label.setVisible(not local_task)
        self._control.setVisible(local_task)
        self._control_label.setVisible(local_task)
        self._enabled.setVisible(not local_task)
        self._enabled_label.setVisible(not local_task)
        self._save_button.setText(translate_ui_text("应用到设备" if local_task else "保存自定义动作"))
        binding = self._view_model.host_tasks.binding_for("workflow", self._selected_id)
        self._trial_button.setVisible(local_task and binding is not None)
        self._trial_button.setEnabled(not self._dirty and binding is not None and binding.applied)
        if binding is not None and binding.tested:
            applied = self._workflow(binding.target_id)
            paths = [str(step.parameters["path"]) for step in applied.steps if step.action_id == "append_text_file"] if applied else []
            if paths:
                self._output_path = paths[-1]
                self._open_output.setVisible(Path(self._output_path).is_file())
        if local_task:
            self._refresh_controls()
            problem = self._view_model.host_tasks.problem
            self._configure_trigger.hide()
            self._save_button.setEnabled(not bool(problem))
            self._status.setText(translate_ui_text("正在运行…" if self._host.running else problem or self._view_model.host_tasks.status or "选择设备按键，应用后即可试用。"))
            return
        self._save_button.setEnabled(True)
        problem = self._view_model.prompt_trigger_problem(self._trigger.currentData())
        self._configure_trigger.setVisible(bool(problem))
        if self._review_required:
            text = "导入内容尚未完成本机路径与绑定检查"
        elif self._dirty:
            text = "有未保存修改；修改后需要重新测试"
        elif self._enabled.isChecked():
            text = self._view_model.prompt_trigger_problem(self._trigger.currentData()) or "已启用 · 实体事件可以运行"
        elif self._tested:
            problem = self._view_model.prompt_trigger_problem(self._trigger.currentData())
            text = f"步骤测试通过；{problem}" if problem else "测试通过 · 可以启用实体触发"
        else:
            text = "尚未测试 · 不会响应实体事件"
        self._status.setText(translate_ui_text(text))

    def _workflow(self, workflow_id: str | None) -> LocalWorkflow | None:
        if workflow_id is None:
            return None
        return next(
            (
                workflow
                for workflow in self._host.workflows
                if workflow.workflow_id == workflow_id
            ),
            None,
        )

    def _step_detail(self, step: WorkflowStep) -> str:
        if step.action_id == "append_text_file":
            return str(step.parameters.get("path") or "尚未选择目标文件")
        if step.action_id == "open_target":
            return str(step.parameters.get("target") or "尚未选择目标")
        if step.action_id == "show_notification":
            return str(step.parameters.get("message") or "尚未填写通知")
        return ""

    def _update_step_item(self, row: int) -> None:
        if not 0 <= row < len(self._steps):
            return
        item = self._step_list.item(row)
        if item is None:
            return
        step = self._steps[row]
        definition = self._host.registry.definition(step.action_id)
        detail = self._step_detail(step)
        item.setText(
            f"{row + 1}. {translate_ui_text(definition.name)}"
            + (f"\n{translate_ui_text(detail)}" if detail else "")
        )

    def _show_error(self, message: str) -> None:
        self._status.setText(translate_ui_text(message))
        QMessageBox.warning(self, "无法完成操作", message)


def _card(role: str = "secondary") -> QFrame:
    return V4Card(role=role, dots=role == "primary")
