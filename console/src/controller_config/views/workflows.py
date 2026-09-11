from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer
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
    QPushButton,
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
    trigger_prompt_id: int
    steps: tuple[WorkflowStep, ...]
    enabled: bool
    tested: bool
    review_required: bool


class WorkflowPage(QScrollArea):
    """No-code editor for BORING's small built-in host workflows."""

    def __init__(
        self,
        view_model: MainViewModel,
        *,
        prompt_names: dict[int, str] | None = None,
        prompt_controls: dict[int, tuple[str, ...]] | None = None,
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
        self._pending_test_id: str | None = None
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

        intro = _card("widget")
        intro_layout = QVBoxLayout(intro)
        intro_layout.setContentsMargins(24, 20, 24, 22)
        intro_layout.addWidget(QLabel("CREATE AUTOMATION", objectName="eyebrow"))
        intro_layout.addWidget(QLabel("创建自动化", objectName="inspectorTitle"))
        description = QLabel(
            "把一个实体提示词槽位绑定到 1–5 个执行步骤。任一步骤失败时立即停止，不自动重试，也不会自行按下 Enter。",
            objectName="muted",
        )
        description.setWordWrap(True)
        intro_layout.addWidget(description)
        layout.addWidget(intro)

        workspace = QBoxLayout(QBoxLayout.LeftToRight)
        self._workspace = workspace
        workspace.setSpacing(14)

        library = _card("primary")
        library_layout = QVBoxLayout(library)
        library_layout.setContentsMargins(20, 18, 20, 20)
        library_layout.addWidget(QLabel("自动化列表", objectName="inspectorTitle"))
        self._list = QListWidget(objectName="workflowList")
        self._list.currentItemChanged.connect(self._selection_changed)
        library_layout.addWidget(self._list, 1)
        library_buttons = QHBoxLayout()
        new_button = QPushButton("新建", objectName="secondary")
        new_button.clicked.connect(self._new_workflow)
        template_button = QPushButton("使用保存摘录模板", objectName="secondary")
        template_button.clicked.connect(self._new_reference_workflow)
        library_buttons.addWidget(new_button)
        library_buttons.addWidget(template_button)
        library_layout.addLayout(library_buttons)
        workspace.addWidget(library, 2)

        editor = _card("focus")
        editor_layout = QVBoxLayout(editor)
        editor_layout.setContentsMargins(24, 20, 24, 22)
        editor_layout.setSpacing(12)
        editor_layout.addWidget(QLabel("AUTOMATION", objectName="eyebrow"))
        editor_layout.addWidget(QLabel("自动化设置", objectName="inspectorTitle"))
        form = QFormLayout()
        form.setHorizontalSpacing(16)
        self._name = QLineEdit(objectName="workflowName")
        self._name.setPlaceholderText("例如：保存选中文字到项目笔记")
        self._name.textChanged.connect(self._set_dirty)
        form.addRow("名称", self._name)
        self._trigger = QComboBox(objectName="workflowTrigger")
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
        form.addRow("实体触发", self._trigger)
        self._enabled = QCheckBox("允许实体控件运行", objectName="workflowEnabled")
        self._enabled.toggled.connect(self._set_dirty)
        form.addRow("启用", self._enabled)
        editor_layout.addLayout(form)

        step_header = QHBoxLayout()
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
        editor_layout.addLayout(step_header)

        self._step_list = QListWidget(objectName="workflowStepList")
        self._step_list.currentRowChanged.connect(self._step_selected)
        self._step_list.setMinimumHeight(150)
        editor_layout.addWidget(self._step_list)
        order = QHBoxLayout()
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
        editor_layout.addLayout(order)

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
        parameter_form.addRow("追加间隔", self._separator)
        editor_layout.addWidget(self._parameter_box)

        self._status = QLabel(objectName="workflowStatus")
        self._status.setWordWrap(True)
        editor_layout.addWidget(self._status)
        configure_trigger = QPushButton("配置触发提示词", objectName="configureWorkflowTrigger")
        configure_trigger.clicked.connect(lambda: self._view_model.navigate("prompts"))
        editor_layout.addWidget(configure_trigger)
        buttons = QHBoxLayout()
        save = QPushButton("保存自动化", objectName="primary")
        save.clicked.connect(self._save)
        self._test_button = QPushButton("3 秒后测试", objectName="secondary")
        self._test_button.clicked.connect(self._schedule_test)
        duplicate = QPushButton("复制", objectName="secondary")
        duplicate.clicked.connect(self._duplicate)
        delete = QPushButton("删除", objectName="secondary")
        delete.clicked.connect(self._delete)
        buttons.addWidget(save)
        buttons.addWidget(self._test_button)
        buttons.addWidget(duplicate)
        buttons.addWidget(delete)
        buttons.addStretch(1)
        editor_layout.addLayout(buttons)

        files = QHBoxLayout()
        import_button = QPushButton("导入自动化", objectName="secondary")
        export_button = QPushButton("导出自动化", objectName="secondary")
        import_button.clicked.connect(self._import)
        export_button.clicked.connect(self._export)
        files.addWidget(import_button)
        files.addWidget(export_button)
        files.addStretch(1)
        editor_layout.addLayout(files)
        workspace.addWidget(editor, 4)
        layout.addLayout(workspace, 1)

        boundary = QLabel(
            "自动化只包含可检查的数据和 BORING 内置步骤；本地脚本与扩展在“工作流与脚本”中单独管理。",
            objectName="roleContext",
        )
        boundary.setWordWrap(True)
        layout.addWidget(boundary)
        layout.addStretch(1)
        self.setWidget(page)

        self._host.changed.connect(self.refresh)
        self._reload_list()
        if self._list.count() == 0:
            self._new_workflow()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if hasattr(self, "_workspace"):
            self._workspace.setDirection(QBoxLayout.TopToBottom if self.viewport().width() < 1000 else QBoxLayout.LeftToRight)

    def refresh(self) -> None:
        if self._dirty:
            self._update_status()
            return
        selected = self._selected_id
        self._reload_list(selected)
        self._update_status()

    def editing_state(self) -> WorkflowEditingState:
        return WorkflowEditingState(
            self._selected_id,
            self._name.text(),
            int(self._trigger.currentData() or 1),
            tuple(self._steps),
            self._enabled.isChecked(),
            self._tested,
            self._review_required,
        )

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
                f"{translate_ui_text(f'提示词槽位 {workflow.trigger_prompt_id}')} · {state}"
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
            self._load_workflow(workflow)

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
        self._steps = list(workflow.steps)
        self._enabled.setChecked(workflow.enabled)
        self._tested = workflow.tested
        self._review_required = workflow.review_required
        self._loading = False
        self._dirty = False
        self._render_steps()
        self._update_status()

    def _new_workflow(self) -> None:
        self._loading = True
        self._selected_id = None
        self._name.setText("")
        self._trigger.setCurrentIndex(0)
        self._steps = [WorkflowStep("read_clipboard", {})]
        self._enabled.setChecked(False)
        self._tested = False
        self._review_required = False
        self._loading = False
        self._dirty = True
        self._list.clearSelection()
        self._render_steps()
        self._update_status()

    def _new_reference_workflow(self) -> None:
        self._loading = True
        self._selected_id = None
        self._name.setText("保存选中文字到 Markdown")
        self._trigger.setCurrentIndex(0)
        self._steps = [
            WorkflowStep("capture_selection", {}),
            WorkflowStep("append_text_file", {"path": "", "separator": "\n\n"}),
            WorkflowStep("show_notification", {"message": "内容已保存"}),
        ]
        self._enabled.setChecked(False)
        self._tested = False
        self._review_required = False
        self._loading = False
        self._dirty = True
        self._list.clearSelection()
        self._render_steps()
        self._update_status()

    def _add_step(self) -> None:
        if len(self._steps) >= 5:
            self._show_error("一个自动化最多包含 5 个步骤")
            return
        action_id = str(self._action_choice.currentData())
        parameters: dict[str, object] = {}
        if action_id == "append_text_file":
            parameters = {"path": "", "separator": "\n\n"}
        elif action_id == "open_target":
            parameters = {"target": ""}
        elif action_id == "show_notification":
            parameters = {"message": "自动化已完成"}
        self._steps.append(WorkflowStep(action_id, parameters))
        self._mark_dirty()
        self._render_steps(select_row=len(self._steps) - 1)

    def _remove_step(self) -> None:
        row = self._step_list.currentRow()
        if row < 0 or len(self._steps) <= 1:
            self._show_error("自动化至少保留 1 个步骤")
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
            existing.trigger_prompt_id != int(self._trigger.currentData() or 1)
            or existing.steps != tuple(self._steps)
        ):
            tested = False
        workflow = LocalWorkflow(
            workflow_id,
            self._name.text().strip(),
            int(self._trigger.currentData() or 1),
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
        self._status.setText(translate_ui_text("自动化已保存"))
        return saved

    def _schedule_test(self) -> None:
        if self._test_timer.isActive():
            return
        self._enabled.setChecked(False)
        workflow = self._save()
        if workflow is None:
            return
        self._pending_test_id = workflow.workflow_id
        self._test_button.setEnabled(False)
        self._status.setText(
            translate_ui_text("3 秒后开始测试；请立即切回要获取内容的应用。")
        )
        self._test_timer.start(3000)

    def _run_scheduled_test(self) -> None:
        workflow_id = self._pending_test_id
        self._pending_test_id = None
        self._test_button.setEnabled(True)
        if workflow_id is None or self._workflow(workflow_id) is None:
            self._show_error("待测试的自动化已经不存在")
            return
        self._run_test_now(workflow_id)

    def _run_test_now(self, workflow_id: str) -> None:
        result = self._view_model.test_workflow(workflow_id)
        self._dirty = False
        self._reload_list(workflow_id)
        self._status.setText(translate_ui_text(result.message))

    def _duplicate(self) -> None:
        if self._selected_id is None:
            self._show_error("请先保存当前自动化")
            return
        duplicated = self._view_model.duplicate_workflow(self._selected_id)
        self._reload_list(duplicated.workflow_id)

    def _delete(self) -> None:
        if self._selected_id is None:
            self._new_workflow()
            return
        self._view_model.delete_workflow(self._selected_id)
        self._selected_id = None
        self._dirty = False
        self._reload_list()
        if self._list.count() == 0:
            self._new_workflow()

    def _import(self) -> None:
        path, _selected = QFileDialog.getOpenFileName(
            self, "导入 BORING 自动化", "", "BORING Automation (*.json)"
        )
        if not path:
            return
        try:
            workflow = import_workflow(Path(path))
            saved = self._view_model.save_workflow(workflow)
        except (WorkflowError, ValueError) as exc:
            self._show_error(str(exc))
            return
        self._reload_list(saved.workflow_id)
        self._status.setText(
            translate_ui_text("已导入但未启用；请检查本机路径和实体绑定。")
        )

    def _export(self) -> None:
        workflow = self._workflow(self._selected_id)
        if workflow is None:
            self._show_error("请先保存当前自动化")
            return
        path, _selected = QFileDialog.getSaveFileName(
            self,
            "导出 BORING 自动化",
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
        self._status.setText(translate_ui_text("自动化已导出"))

    def _update_status(self) -> None:
        if self._review_required:
            text = "导入内容尚未完成本机路径与绑定检查"
        elif self._dirty:
            text = "有未保存修改；修改后需要重新测试"
        elif self._enabled.isChecked():
            text = self._view_model.prompt_trigger_problem(int(self._trigger.currentData() or 1)) or "已启用 · 实体事件可以运行"
        elif self._tested:
            problem = self._view_model.prompt_trigger_problem(int(self._trigger.currentData() or 1))
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
