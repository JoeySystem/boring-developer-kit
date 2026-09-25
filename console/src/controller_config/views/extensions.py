from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QBoxLayout,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from controller_config.extensions.development_prompt import (
    build_extension_development_prompt,
)
from controller_config.extensions.manager import ExtensionManagerError
from controller_config.extensions.platform import (
    ExtensionPlatformController,
    ExtensionPlatformLog,
)
from controller_config.extensions.proposals import ProposalState
from controller_config.extensions.runtime import ExtensionRuntimeState
from controller_config.i18n import translate_ui_text
from controller_config.views.v4_widgets import V4Card

if TYPE_CHECKING:
    from controller_config.viewmodels.main import MainViewModel


@dataclass(frozen=True)
class ExtensionsEditingState:
    extension_id: str | None
    prompt_id: int
    action_id: str | None
    proposal_id: str | None


class ExtensionsPage(QScrollArea):
    """Manage installed extensions, action bindings and mapping proposals."""

    def __init__(
        self, view_model: MainViewModel, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent, objectName="extensionsPage")
        self._view_model = view_model
        platform = view_model.extension_platform
        self._platform = (
            platform if isinstance(platform, ExtensionPlatformController) else None
        )
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setStyleSheet("QScrollArea { background: transparent; }")

        page = QWidget(objectName="extensionsPageContents")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(14)

        summary = _card("focus")
        summary_layout = QHBoxLayout(summary)
        summary_layout.setContentsMargins(24, 20, 24, 22)
        heading = QVBoxLayout()
        heading.addWidget(QLabel("EXTENSIONS", objectName="eyebrow"))
        heading.addWidget(QLabel("安装扩展包", objectName="inspectorTitle"))
        intro = QLabel(
            "导入开发者提供的目录或 ZIP，使用包里的具名动作。扩展保存在这台电脑，运行时需保持 BORING 后台开启。",
            objectName="muted",
        )
        intro.setWordWrap(True)
        heading.addWidget(intro)
        guide = QFrame(objectName="codexWorkflowGuide")
        guide_layout = QVBoxLayout(guide)
        guide_layout.setContentsMargins(0, 8, 0, 0)
        guide_layout.setSpacing(6)
        for number, text in enumerate((
            "复制 AI 开发提示词",
            "粘贴给 Codex 并描述需求",
            "Codex 生成 ZIP 扩展包",
            "导入 ZIP",
            "控制台校验扩展",
            "启用、检查运行结果并绑定控件",
            "按键触发运行",
        ), 1):
            row = QHBoxLayout()
            row.setSpacing(10)
            marker = QLabel(f"{number:02}", objectName="workflowGuideStep")
            marker.setFixedWidth(26)
            label = QLabel(text, objectName="workflowGuideText")
            label.setWordWrap(True)
            row.addWidget(marker)
            row.addWidget(label, 1)
            guide_layout.addLayout(row)
        guide.hide()
        guide_toggle = QPushButton("自己开发扩展", objectName="extensionDevelopmentGuide")
        guide_toggle.setCheckable(True)
        guide_toggle.toggled.connect(guide.setVisible)
        heading.addWidget(guide_toggle)
        heading.addWidget(guide)
        note = QLabel(
            "Console 自带 Python 运行器；不会自动安装第三方依赖。请按作者说明准备依赖，导入后启用并检查实际输出。",
            objectName="muted",
        )
        note.setWordWrap(True)
        heading.addWidget(note)
        summary_layout.addLayout(heading, 1)
        actions = QVBoxLayout()
        self._platform_status = QLabel(objectName="extensionPlatformStatus")
        self._platform_status.setProperty("extensionPageStatus", True)
        self._platform_status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        actions.addWidget(self._platform_status)
        copy_prompt = QPushButton(
            "复制 AI 开发提示词", objectName="copyExtensionDevelopmentPrompt"
        )
        copy_prompt.clicked.connect(self._copy_development_prompt)
        actions.addWidget(copy_prompt)
        self._import_zip_button = QPushButton(
            "导入 ZIP", objectName="importExtensionPackage"
        )
        self._import_zip_button.setProperty("buttonRole", "primary")
        self._import_zip_button.clicked.connect(self._import_zip)
        actions.addWidget(self._import_zip_button)
        actions.addStretch(1)
        summary_layout.addLayout(actions, 0)
        layout.addWidget(summary)

        workspace = QBoxLayout(QBoxLayout.LeftToRight)
        self._workspace = workspace
        workspace.setSpacing(14)

        catalog = _card("primary")
        catalog_layout = QVBoxLayout(catalog)
        catalog_layout.setContentsMargins(20, 18, 20, 20)
        catalog_layout.setSpacing(10)
        catalog_layout.addWidget(QLabel("已安装扩展", objectName="inspectorTitle"))
        self._extensions = QListWidget(objectName="extensionList")
        self._extensions.setMaximumHeight(140)
        self._extensions.currentItemChanged.connect(self._extension_selected)
        catalog_layout.addWidget(self._extensions, 1)
        import_row = QHBoxLayout()
        self._import_directory_button = QPushButton(
            "导入扩展目录", objectName="importExtensionDirectory"
        )
        self._import_directory_button.clicked.connect(self._import_directory)
        import_row.addWidget(self._import_directory_button)
        catalog_layout.addLayout(import_row)
        workspace.addWidget(catalog, 2)

        details = _card()
        details_layout = QVBoxLayout(details)
        details_layout.setContentsMargins(24, 20, 24, 22)
        details_layout.setSpacing(11)
        details_layout.addWidget(QLabel("扩展详情", objectName="eyebrow"))
        self._title = QLabel("请选择扩展", objectName="inspectorTitle")
        details_layout.addWidget(self._title)
        self._description = QLabel(objectName="muted")
        self._description.setWordWrap(True)
        details_layout.addWidget(self._description)
        buttons = QHBoxLayout()
        self._toggle = QPushButton("启用", objectName="extensionToggle")
        self._toggle.clicked.connect(self._toggle_selected)
        buttons.addWidget(self._toggle)
        self._restart = QPushButton("重启扩展", objectName="extensionRestart")
        self._restart.clicked.connect(self._restart_selected)
        buttons.addWidget(self._restart)
        self._remove = QPushButton("移除", objectName="extensionRemove")
        self._remove.clicked.connect(self._remove_selected)
        buttons.addWidget(self._remove)
        buttons.addStretch(1)
        details_layout.addLayout(buttons)

        details_layout.addWidget(QLabel("选择要运行的动作", objectName="inspectorTitle"))
        self._action = QComboBox(objectName="extensionAction")
        details_layout.addWidget(self._action)
        task_row = QHBoxLayout()
        self._task_control = QComboBox(objectName="extensionPhysicalControl")
        task_row.addWidget(self._task_control, 1)
        self._apply_task = QPushButton("应用到设备", objectName="extensionApplyTask")
        self._apply_task.clicked.connect(self._apply_host_task)
        task_row.addWidget(self._apply_task)
        self._trial_task = QPushButton("开始实体试用", objectName="extensionTrial")
        self._trial_task.clicked.connect(self._arm_task_trial)
        task_row.addWidget(self._trial_task)
        details_layout.addLayout(task_row)
        run_settings = QPushButton("运行设置", objectName="extensionRunSettingsToggle")
        run_settings.setCheckable(True)
        details_layout.addWidget(run_settings)
        self._task_timeout = QSpinBox(objectName="extensionTaskTimeout")
        self._task_timeout.setRange(1, 3600)
        self._task_timeout.setValue(60)
        self._task_timeout.setPrefix(translate_ui_text("最长运行 "))
        self._task_timeout.setSuffix(" s")
        self._task_timeout.hide()
        run_settings.toggled.connect(self._task_timeout.setVisible)
        details_layout.addWidget(self._task_timeout)
        self._task_status = QLabel(objectName="extensionTaskStatus")
        self._task_status.setWordWrap(True)
        details_layout.addWidget(self._task_status)
        legacy_toggle = QPushButton("旧提示词绑定", objectName="extensionLegacyBindingToggle")
        legacy_toggle.setCheckable(True)
        details_layout.addWidget(legacy_toggle)
        self._legacy_binding = QWidget(objectName="extensionLegacyBinding")
        binding_row = QHBoxLayout(self._legacy_binding)
        binding_row.setContentsMargins(0, 0, 0, 0)
        self._legacy_binding.hide()
        legacy_toggle.toggled.connect(self._legacy_binding.setVisible)
        self._prompt_slot = QComboBox(objectName="extensionPromptSlot")
        bound_slots = (
            {binding.prompt_id for binding in platform.bindings
             if binding.device_serial == view_model.automation_host.serial}
            if platform is not None else set()
        )
        for prompt_id in sorted(set(view_model.prompt_trigger_choices) | bound_slots):
            self._prompt_slot.addItem(f"提示词槽位 {prompt_id}", prompt_id)
        self._prompt_slot.currentIndexChanged.connect(self._refresh_binding)
        binding_row.addWidget(self._prompt_slot)
        self._bind = QPushButton("绑定", objectName="extensionBindAction")
        self._bind.clicked.connect(self._bind_action)
        binding_row.addWidget(self._bind)
        self._unbind = QPushButton("解除绑定", objectName="extensionUnbindAction")
        self._unbind.clicked.connect(self._unbind_action)
        binding_row.addWidget(self._unbind)
        details_layout.addWidget(self._legacy_binding)
        self._binding_status = QLabel(objectName="muted")
        self._binding_status.setWordWrap(True)
        details_layout.addWidget(self._binding_status)
        self._binding_status.hide()
        legacy_toggle.toggled.connect(self._binding_status.setVisible)
        self._action.currentIndexChanged.connect(self._refresh_task_binding)
        workspace.addWidget(details, 3)
        layout.addLayout(workspace, 1)

        proposal_card = _card("widget")
        proposal_layout = QVBoxLayout(proposal_card)
        proposal_layout.setContentsMargins(24, 20, 24, 22)
        proposal_layout.setSpacing(10)
        self._proposal_toggle = QToolButton(objectName="extensionProposalDisclosure")
        self._proposal_toggle.setText("配置变更提案审阅")
        self._proposal_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._proposal_toggle.setArrowType(Qt.RightArrow)
        self._proposal_toggle.setCheckable(True)
        proposal_heading = QHBoxLayout()
        proposal_heading.addWidget(self._proposal_toggle)
        self._proposal_count = QLabel("0", objectName="extensionProposalCount")
        proposal_heading.addWidget(self._proposal_count)
        proposal_heading.addStretch(1)
        proposal_layout.addLayout(proposal_heading)
        proposal_contents = QWidget(objectName="extensionProposalContents")
        proposal_contents.setVisible(False)
        proposal_contents_layout = QVBoxLayout(proposal_contents)
        proposal_contents_layout.setContentsMargins(0, 8, 0, 0)
        self._proposal_toggle.toggled.connect(proposal_contents.setVisible)
        self._proposal_toggle.toggled.connect(
            lambda expanded: self._proposal_toggle.setArrowType(
                Qt.DownArrow if expanded else Qt.RightArrow
            )
        )
        proposal_workspace = QHBoxLayout()
        self._proposal_list = QListWidget(objectName="extensionProposalList")
        self._proposal_list.currentItemChanged.connect(self._proposal_selected)
        proposal_workspace.addWidget(self._proposal_list, 2)
        proposal_detail = QVBoxLayout()
        self._proposal_detail = QLabel("尚无待审阅提案", objectName="muted")
        self._proposal_detail.setWordWrap(True)
        proposal_detail.addWidget(self._proposal_detail)
        proposal_buttons = QHBoxLayout()
        self._approve = QPushButton(
            "批准到本地草稿", objectName="extensionApproveProposal"
        )
        self._approve.clicked.connect(self._approve_proposal)
        proposal_buttons.addWidget(self._approve)
        self._reject = QPushButton("拒绝", objectName="extensionRejectProposal")
        self._reject.clicked.connect(self._reject_proposal)
        proposal_buttons.addWidget(self._reject)
        self._remove_proposal = QPushButton(
            "删除记录", objectName="extensionRemoveProposal"
        )
        self._remove_proposal.clicked.connect(self._remove_proposal_record)
        proposal_buttons.addWidget(self._remove_proposal)
        proposal_buttons.addStretch(1)
        proposal_detail.addLayout(proposal_buttons)
        proposal_workspace.addLayout(proposal_detail, 3)
        proposal_contents_layout.addLayout(proposal_workspace)
        proposal_layout.addWidget(proposal_contents)
        layout.addWidget(proposal_card)

        log_card = _card("widget")
        log_layout = QVBoxLayout(log_card)
        log_layout.setContentsMargins(24, 20, 24, 22)
        self._run_status = QLabel("尚未运行", objectName="extensionRunStatus")
        self._run_status.setWordWrap(True)
        log_layout.addWidget(self._run_status)
        self._log_toggle = QPushButton("查看详情", objectName="extensionLogToggle")
        self._log_toggle.setCheckable(True)
        log_layout.addWidget(self._log_toggle)
        self._logs = QPlainTextEdit(objectName="extensionRunLog")
        self._logs.setReadOnly(True)
        self._logs.setMaximumBlockCount(200)
        self._logs.setMinimumHeight(120)
        log_layout.addWidget(self._logs)
        self._logs.hide()
        self._log_toggle.toggled.connect(self._logs.setVisible)
        layout.addWidget(log_card)

        boundary = QLabel(
            "扩展只通过 BORING 本地 API 读取上下文、观察事件、承接显式绑定的 action 或提交配置提案；不能持有串口、发送原始命令或绕过用户确认写设备。",
            objectName="roleContext",
        )
        boundary.setProperty("extensionBoundary", True)
        boundary.setWordWrap(True)
        layout.addWidget(boundary)
        layout.addStretch(1)

        self.setWidget(page)
        if self._platform is not None:
            self._platform.changed.connect(self.refresh)
            self._platform.log_added.connect(self._append_log)
        self._view_model.host_tasks.changed.connect(self._refresh_task_binding)
        self.refresh()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if hasattr(self, "_workspace"):
            self._workspace.setDirection(
                QBoxLayout.TopToBottom if self.viewport().width() < 1000 else QBoxLayout.LeftToRight
            )

    def editing_state(self) -> ExtensionsEditingState:
        return ExtensionsEditingState(
            extension_id=self._selected_extension_id(),
            prompt_id=int(self._prompt_slot.currentData() or 1),
            action_id=(
                str(self._action.currentData())
                if self._action.currentData() is not None
                else None
            ),
            proposal_id=self._selected_proposal_id(),
        )

    def restore_editing_state(self, state: ExtensionsEditingState) -> None:
        self._select_item(self._extensions, state.extension_id)
        prompt_index = self._prompt_slot.findData(state.prompt_id)
        if prompt_index >= 0:
            self._prompt_slot.setCurrentIndex(prompt_index)
        action_index = self._action.findData(state.action_id)
        if action_index >= 0:
            self._action.setCurrentIndex(action_index)
        self._select_item(self._proposal_list, state.proposal_id)

    def select_action(self, extension_id: str, action_id: str, prompt_id: int | None = None) -> None:
        self._select_item(self._extensions, extension_id)
        action_index = self._action.findData(action_id)
        if action_index >= 0:
            self._action.setCurrentIndex(action_index)
        if prompt_id is not None:
            prompt_index = self._prompt_slot.findData(prompt_id)
            if prompt_index >= 0:
                self._prompt_slot.setCurrentIndex(prompt_index)

    def refresh(self) -> None:
        state = self.editing_state()
        self._extensions.clear()
        if self._platform is None:
            self._import_directory_button.setEnabled(False)
            self._import_zip_button.setEnabled(False)
            self._platform_status.setText(translate_ui_text("扩展平台未启动"))
            self._title.setText(
                translate_ui_text("当前运行方式不包含扩展平台")
            )
            self._description.setText(
                translate_ui_text(
                    "请从 BORING 控制台正式入口启动；演示和独立 UI 测试不会执行第三方代码。"
                )
            )
            self._set_detail_enabled(False)
            self._proposal_list.clear()
            self._proposal_count.setText("0")
            self._proposal_detail.setText(
                translate_ui_text("扩展平台启动后可审阅配置提案。")
            )
            self._approve.setEnabled(False)
            self._reject.setEnabled(False)
            self._remove_proposal.setEnabled(False)
            self._logs.setPlainText("")
            self._refresh_binding()
            return
        self._import_directory_button.setEnabled(True)
        self._import_zip_button.setEnabled(True)
        for extension in self._platform.extensions:
            extension_id = extension.manifest.extension_id
            runtime = self._platform.runtime_state(extension_id)
            item = QListWidgetItem(
                f"{extension.manifest.name}  ·  {extension.manifest.version}\n"
                f"{_runtime_label(runtime)}"
            )
            item.setData(Qt.UserRole, extension_id)
            self._extensions.addItem(item)
        self._select_item(self._extensions, state.extension_id)
        if self._extensions.currentItem() is None and self._extensions.count():
            self._extensions.setCurrentRow(0)

        self._proposal_list.clear()
        self._proposal_count.setText(str(len(self._platform.proposals.records)))
        for record in self._platform.proposals.records:
            item = QListWidgetItem(
                f"{record.extension_name} · {record.proposal.control_id}\n"
                f"{_proposal_state_label(record.state)}"
            )
            item.setData(Qt.UserRole, record.proposal.proposal_id)
            self._proposal_list.addItem(item)
        self._select_item(self._proposal_list, state.proposal_id)
        if self._proposal_list.currentItem() is None and self._proposal_list.count():
            self._proposal_list.setCurrentRow(0)

        ready = sum(
            self._platform.extension_is_ready(item.manifest.extension_id)
            for item in self._platform.extensions
        )
        extension_count = len(self._platform.extensions)
        self._platform_status.setText(
            translate_ui_text(
                "扩展平台启动失败：" + self._view_model.extension_platform_error
                if self._view_model.extension_platform_error
                else "等待设备认证后启动"
            )
            if not self._platform.is_started
            else (
                translate_ui_text("尚未安装扩展")
                if extension_count == 0
                else translate_ui_text(
                    f"{extension_count} 个已安装 · {ready} 个运行中"
                )
            )
        )
        self._logs.setPlainText(
            "\n".join(
                _format_log(item)
                for item in self._platform.logs
            )
        )
        if self._platform.logs:
            self._update_run_status(self._platform.logs[-1])
        self._extension_selected(self._extensions.currentItem(), None)
        self._proposal_selected(self._proposal_list.currentItem(), None)

    def _append_log(self, record: ExtensionPlatformLog) -> None:
        self._logs.appendPlainText(_format_log(record))
        self._update_run_status(record)

    def _update_run_status(self, record: ExtensionPlatformLog) -> None:
        self._run_status.setText(translate_ui_text(record.message))
        self._log_toggle.setText(translate_ui_text("查看失败详情" if record.level == "error" else "查看详情"))

    def _import_directory(self) -> None:
        value = QFileDialog.getExistingDirectory(self, "选择 BORING 扩展目录")
        if value:
            self._import_path(Path(value))

    def _copy_development_prompt(self) -> None:
        QApplication.clipboard().setText(
            build_extension_development_prompt(self._view_model.extension_context())
        )
        self._platform_status.setText(translate_ui_text("开发提示词已复制"))

    def _import_zip(self) -> None:
        value, _filter = QFileDialog.getOpenFileName(
            self, "选择 BORING 扩展包", "", "BORING Extension (*.zip)"
        )
        if value:
            self._import_path(Path(value))

    def _import_path(self, path: Path) -> None:
        if self._platform is None:
            QMessageBox.warning(self, "无法导入扩展", "扩展平台尚未启动")
            return
        try:
            extension = self._platform.import_package(path)
        except (ExtensionManagerError, OSError, ValueError) as exc:
            QMessageBox.warning(self, "无法导入扩展", str(exc))
            return
        self.refresh()
        self._select_item(self._extensions, extension.manifest.extension_id)

    def _extension_selected(self, current, _previous) -> None:
        extension_id = current.data(Qt.UserRole) if current is not None else None
        if not isinstance(extension_id, str) or self._platform is None:
            self._title.setText(translate_ui_text("尚未导入扩展"))
            self._description.setText(
                translate_ui_text(
                    "可以导入包含 boring-extension.json 的目录或 ZIP。"
                )
            )
            self._action.clear()
            self._set_detail_enabled(False)
            self._refresh_binding()
            return
        extension = self._platform.manager.get(extension_id)
        runtime = self._platform.runtime_state(extension_id)
        self._title.setText(extension.manifest.name)
        capabilities = [
            *(f"observer: {item}" for item in extension.manifest.observer_events),
            *(f"action: {item.action_id}" for item in extension.manifest.actions),
        ]
        self._description.setText(
            f"ID {extension_id}\n{translate_ui_text('版本')} "
            f"{extension.manifest.version} · API "
            f"{extension.manifest.api_version.major}.{extension.manifest.api_version.minor}"
            f"\n{translate_ui_text('运行状态')}：{_runtime_label(runtime)}"
            f"\n{translate_ui_text('请求能力')}："
            f"{', '.join(capabilities) if capabilities else translate_ui_text('只读上下文')}"
        )
        self._toggle.setText(
            translate_ui_text("停用" if extension.enabled else "启用")
        )
        self._set_detail_enabled(True)
        self._restart.setEnabled(extension.enabled)
        self._action.clear()
        for action in extension.manifest.actions:
            self._action.addItem(action.name, action.action_id)
        self._refresh_binding()

    def _set_detail_enabled(self, enabled: bool) -> None:
        for widget in (self._toggle, self._restart, self._remove):
            widget.setEnabled(enabled)
        self._bind.setEnabled(enabled and self._action.count() > 0)

    def _toggle_selected(self) -> None:
        extension_id = self._selected_extension_id()
        if extension_id is None or self._platform is None:
            return
        try:
            extension = self._platform.manager.get(extension_id)
            if extension.enabled:
                self._platform.disable(extension_id)
            else:
                self._platform.enable(extension_id)
        except (ExtensionManagerError, OSError, ValueError) as exc:
            QMessageBox.warning(self, "无法修改扩展状态", str(exc))

    def _restart_selected(self) -> None:
        extension_id = self._selected_extension_id()
        if extension_id is None or self._platform is None:
            return
        if not self._platform.restart(extension_id):
            QMessageBox.warning(
                self, "扩展未启动", "请检查扩展入口和私有 Runner。"
            )

    def _remove_selected(self) -> None:
        extension_id = self._selected_extension_id()
        if extension_id is None or self._platform is None:
            return
        answer = QMessageBox.question(
            self,
            "移除扩展",
            "移除后将删除控制台的受管扩展副本和实体 action 绑定。继续？",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if answer == QMessageBox.Yes:
            self._platform.remove(extension_id)

    def _bind_action(self) -> None:
        extension_id = self._selected_extension_id()
        action_id = self._action.currentData()
        serial = self._device_serial()
        if (
            self._platform is None
            or extension_id is None
            or not isinstance(action_id, str)
            or serial is None
        ):
            QMessageBox.warning(
                self, "无法绑定", "请连接设备并选择一个扩展 action。"
            )
            return
        try:
            self._platform.bind_action(
                device_serial=serial,
                prompt_id=int(self._prompt_slot.currentData()),
                extension_id=extension_id,
                action_id=action_id,
            )
        except ValueError as exc:
            QMessageBox.warning(self, "无法绑定", str(exc))

    def _unbind_action(self) -> None:
        serial = self._device_serial()
        if serial is not None and self._platform is not None:
            self._platform.unbind_action(
                serial, int(self._prompt_slot.currentData())
            )

    def _refresh_binding(self) -> None:
        self._refresh_task_binding()
        serial = self._device_serial()
        prompt_id = int(self._prompt_slot.currentData() or 1)
        binding = next(
            (
                item
                for item in (
                    self._platform.bindings if self._platform is not None else ()
                )
                if item.device_serial == serial and item.prompt_id == prompt_id
            ),
            None,
        )
        self._binding_status.setText(
            f"{translate_ui_text('当前绑定')}："
            f"{binding.extension_id} / {binding.action_id}"
            if binding is not None
            else translate_ui_text(
                "当前槽位没有扩展 action 绑定；未就绪时使用 Unicode 粘贴回退。"
            )
        )
        self._unbind.setEnabled(binding is not None)
        extension_id = self._selected_extension_id()
        self._bind.setEnabled(
            extension_id is not None
            and self._action.count() > 0
            and serial is not None
        )

    def _task_descriptor(self) -> dict | None:
        extension_id = self._selected_extension_id()
        action_id = self._action.currentData()
        if extension_id is None or action_id is None:
            return None
        return {"target_id": f"{extension_id}:{action_id}",
                "extension_id": extension_id, "action_id": action_id,
                "name": self._action.currentText(), "timeout_ms": self._task_timeout.value() * 1000}

    def _refresh_task_binding(self) -> None:
        tasks = self._view_model.host_tasks
        descriptor = self._task_descriptor()
        binding = tasks.binding_for("extension", descriptor["target_id"]) if descriptor else None
        target_id = descriptor["target_id"] if descriptor else None
        changed_target = target_id != getattr(self, "_task_target_selected", None)
        self._task_target_selected = target_id
        target = ((binding.control_id if binding else getattr(tasks, "preferred_control", None))
                  if changed_target else self._task_control.currentData())
        if changed_target:
            self._task_timeout.setValue(binding.pending.get("timeout_ms", 60_000) // 1000 if binding and binding.pending else 60)
        self._task_control.clear()
        for label, value in tasks.controls:
            self._task_control.addItem(label, value)
        self._task_control.setCurrentIndex(max(0, self._task_control.findData(target)))
        problem = tasks.problem
        if not problem and descriptor and self._platform is not None:
            extension = self._platform.manager.get(descriptor["extension_id"])
            if extension.manifest.api_version.minor < 1:
                problem = "请向作者索取支持电脑任务的新版扩展；旧提示词绑定仍可使用。"
            elif not self._platform.extension_is_ready(descriptor["extension_id"]):
                problem = "请先启用扩展并等待启动完成。"
        self._apply_task.setEnabled(descriptor is not None and not problem)
        self._trial_task.setVisible(binding is not None)
        self._trial_task.setEnabled(binding is not None and binding.applied and not problem)
        self._task_status.setText(translate_ui_text(problem or tasks.status or "选择动作和设备按键，应用后进行首次试用。"))

    def _apply_host_task(self) -> None:
        descriptor = self._task_descriptor()
        if descriptor is None:
            return
        try:
            self._view_model.host_tasks.apply("extension", descriptor, self._task_control.currentData())
        except (ValueError, OSError) as exc:
            self._task_status.setText(translate_ui_text(str(exc)))

    def _arm_task_trial(self) -> None:
        descriptor = self._task_descriptor()
        binding = self._view_model.host_tasks.binding_for("extension", descriptor["target_id"]) if descriptor else None
        if binding is not None:
            try:
                self._view_model.host_tasks.arm_trial(binding.action_id)
            except ValueError as exc:
                self._task_status.setText(translate_ui_text(str(exc)))

    def hideEvent(self, event) -> None:  # noqa: N802
        self._view_model.host_tasks.cancel_trial()
        super().hideEvent(event)

    def _proposal_selected(self, current, _previous) -> None:
        proposal_id = current.data(Qt.UserRole) if current is not None else None
        if not isinstance(proposal_id, str) or self._platform is None:
            self._proposal_detail.setText(translate_ui_text("尚无待审阅提案。"))
            self._approve.setEnabled(False)
            self._reject.setEnabled(False)
            self._remove_proposal.setEnabled(False)
            return
        record = self._platform.proposals.record(proposal_id)
        proposal = record.proposal
        self._proposal_detail.setText(
            f"{translate_ui_text('来源')}：{record.extension_name}\n"
            f"{translate_ui_text('目标')}：{proposal.device_serial} / "
            f"Profile {proposal.profile_id} / {proposal.control_id}\n"
            f"{translate_ui_text('状态')}：{_proposal_state_label(record.state)}"
            f" · {translate_ui_text(record.message)}\n"
            f"{translate_ui_text('当前')}：{record.before_mapping}\n"
            f"{translate_ui_text('建议')}：{record.after_mapping}\n"
            f"{translate_ui_text('差异')}：{len(record.changes)} "
            f"{translate_ui_text('项')}"
        )
        self._approve.setEnabled(record.state is ProposalState.REVIEWABLE)
        self._reject.setEnabled(
            record.state
            in {
                ProposalState.REVIEWABLE,
                ProposalState.BLOCKED,
                ProposalState.STALE,
            }
        )
        self._remove_proposal.setEnabled(
            record.state
            in {
                ProposalState.REJECTED,
                ProposalState.STALE,
                ProposalState.VERIFIED,
                ProposalState.FAILED,
            }
        )

    def _approve_proposal(self) -> None:
        proposal_id = self._selected_proposal_id()
        if proposal_id is None or self._platform is None:
            return
        try:
            self._platform.approve_proposal(proposal_id)
        except ValueError as exc:
            QMessageBox.warning(self, "无法批准提案", str(exc))

    def _reject_proposal(self) -> None:
        proposal_id = self._selected_proposal_id()
        if proposal_id is not None and self._platform is not None:
            self._platform.reject_proposal(proposal_id)

    def _remove_proposal_record(self) -> None:
        proposal_id = self._selected_proposal_id()
        if proposal_id is not None and self._platform is not None:
            self._platform.remove_proposal_record(proposal_id)

    def _selected_extension_id(self) -> str | None:
        item = self._extensions.currentItem()
        value = item.data(Qt.UserRole) if item is not None else None
        return value if isinstance(value, str) else None

    def _selected_proposal_id(self) -> str | None:
        item = self._proposal_list.currentItem()
        value = item.data(Qt.UserRole) if item is not None else None
        return value if isinstance(value, str) else None

    def _device_serial(self) -> str | None:
        snapshot = self._view_model.model.snapshot
        value = snapshot.identity.get("serial") if snapshot is not None else None
        return value if isinstance(value, str) and value else None

    @staticmethod
    def _select_item(widget: QListWidget, identity: str | None) -> None:
        if identity is None:
            return
        for row in range(widget.count()):
            item = widget.item(row)
            if item.data(Qt.UserRole) == identity:
                widget.setCurrentRow(row)
                return


def _card(role: str = "secondary") -> QFrame:
    return V4Card(role=role, dots=role == "primary")


def _format_log(record: ExtensionPlatformLog) -> str:
    return (
        f"[{record.timestamp}] {record.level.upper()} "
        f"{record.extension_id} {record.message}"
    ).strip()


def _runtime_label(state: ExtensionRuntimeState) -> str:
    return translate_ui_text(
        {
            ExtensionRuntimeState.INSTALLED_DISABLED: "已安装 · 未启用",
            ExtensionRuntimeState.STARTING: "启动中",
            ExtensionRuntimeState.RUNNING: "运行中",
            ExtensionRuntimeState.STOPPED: "已停止",
            ExtensionRuntimeState.FAILED: "启动失败",
            ExtensionRuntimeState.MISSING_ENTRY: "入口缺失",
        }[state]
    )


def _proposal_state_label(state: ProposalState) -> str:
    return translate_ui_text(
        {
            ProposalState.REVIEWABLE: "待审阅",
            ProposalState.BLOCKED: "已阻塞",
            ProposalState.STALE: "已失效",
            ProposalState.APPROVED_TO_DRAFT: "已批准到本地草稿",
            ProposalState.REJECTED: "已拒绝",
            ProposalState.VERIFIED: "已读回确认",
            ProposalState.FAILED: "失败",
        }[state]
    )
