from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, Qt, Signal, QSize
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from controller_config.host_actions import HostActionProvider
from controller_config.i18n import set_translatable_text, translate_ui_text
from controller_config.views.automation import AutomationPage
from controller_config.views.extensions import ExtensionsPage
from controller_config.views.workflows import WorkflowPage
from controller_config.views.v4_widgets import V4Card

if TYPE_CHECKING:
    from controller_config.viewmodels.main import MainViewModel


class ActionsPage(QWidget):
    """A single actionable library, with authoring and diagnostics on demand."""

    section_created = Signal(object)
    macros_requested = Signal()

    MY_ACTIONS = 0
    EDIT_ACTION = 1
    RUN_HISTORY = 2
    DEVELOP_ACTIONS = 3

    def __init__(
        self,
        view_model: MainViewModel,
        *,
        prompt_names: dict[int, str] | None = None,
        prompt_controls: dict[int, tuple[str, ...]] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent, objectName="actionsPage")
        self._view_model = view_model
        self._device_serial = view_model.automation_host.serial
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        header = QHBoxLayout()
        self._back = QPushButton("返回我的自定义动作", objectName="actionBack")
        self._back.clicked.connect(lambda: self.show_section(self.MY_ACTIONS))
        header.addWidget(self._back)
        self._title = QLabel("我的自定义动作", objectName="inspectorTitle")
        header.addWidget(self._title)
        header.addStretch(1)
        self._create = QPushButton("创建自定义动作", objectName="createAction")
        self._create.setProperty("buttonRole", "primary")
        self._create.clicked.connect(self._new_workflow)
        header.addWidget(self._create)
        self._resume = QPushButton("继续编辑", objectName="resumeActionDraft")
        self._resume.setProperty("buttonRole", "primary")
        self._resume.clicked.connect(lambda: self.show_section(self.EDIT_ACTION))
        header.addWidget(self._resume)
        more = QPushButton("更多", objectName="actionMore")
        menu = QMenu(more)
        menu.addAction("导入自定义动作", self._import_workflow)
        menu.addAction("使用保存摘录模板", self._new_reference_workflow)
        menu.addSeparator()
        history_action = menu.addAction("运行记录", lambda: self.show_section(self.RUN_HISTORY))
        history_action.setObjectName("actionHistory")
        self._tools = QPushButton("脚本与扩展", objectName="openActionTools")
        self._tools.setProperty("buttonRole", "secondary")
        self._tools.clicked.connect(lambda: self.show_developer_lane(_DevelopActionsPage.LOCAL_SCRIPT))
        header.addWidget(self._tools)
        more.setMenu(menu)
        header.addWidget(more)
        layout.addLayout(header)

        self._task_notice = QWidget(objectName="actionTaskNotice")
        task_layout = QHBoxLayout(self._task_notice)
        task_layout.setContentsMargins(0, 0, 0, 0)
        self._task_status = QLabel(objectName="actionTaskStatus")
        self._task_status.setWordWrap(True)
        task_layout.addWidget(self._task_status, 1)
        self._task_stop = QPushButton("停止运行", objectName="actionStopTask")
        self._task_stop.clicked.connect(lambda: view_model.host_tasks.cancel_run())
        task_layout.addWidget(self._task_stop)
        layout.addWidget(self._task_notice)

        self._failure = QWidget(objectName="actionFailureNotice")
        failure_layout = QHBoxLayout(self._failure)
        failure_layout.setContentsMargins(0, 0, 0, 0)
        self._failure_text = QLabel(objectName="statusWarn")
        self._failure_text.setWordWrap(True)
        failure_layout.addWidget(self._failure_text, 1)
        details = QPushButton("查看运行记录", objectName="actionFailureDetails")
        details.clicked.connect(lambda: self.show_section(self.RUN_HISTORY))
        failure_layout.addWidget(details)
        layout.addWidget(self._failure)

        self._stack = QStackedWidget(objectName="actionSectionStack")
        self._my_actions = _MyActionsPage(view_model)
        self._my_actions._edit.clicked.connect(self._open_selected_action)
        self._my_actions._list.itemActivated.connect(lambda _item: self._open_selected_action())
        self._workflow_page = WorkflowPage(
            view_model, prompt_names=prompt_names, prompt_controls=prompt_controls,
            embedded=True,
        )
        self._editor = QWidget(objectName="actionEditorWorkspace")
        editor_layout = QVBoxLayout(self._editor)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.addWidget(self._workflow_page, 1)
        editor_layout.addWidget(self._workflow_page.footer)
        self._history = _RunHistoryPage(view_model)
        self._prompt_names = prompt_names or {}
        self._developer = None
        for page in (self._my_actions, self._editor, self._history, QWidget()):
            self._stack.addWidget(page)
        self._choices = QWidget(objectName="customTaskChoices")
        choices_layout = QHBoxLayout(self._choices)
        choices_layout.setContentsMargins(0, 0, 0, 0)
        from controller_config.views.main_window import _navigation_icon
        for symbol, fallback, title, example, callback in (
            ("keyboard", QStyle.SP_ComputerIcon, "连续按键", "依次按快捷键、输入英文、等待\n保存在设备", self.macros_requested.emit),
            ("folder", QStyle.SP_DirIcon, "组合电脑操作", "保存复制的文字、打开工作环境\n保存在这台电脑", lambda: self._templates.setVisible(not self._templates.isVisible())),
            ("curlybraces", QStyle.SP_FileDialogDetailedView, "运行脚本或扩展", "使用自己或开发者提供的工具\n保存在这台电脑", lambda: self.show_developer_lane(_DevelopActionsPage.LOCAL_SCRIPT)),
        ):
            card = V4Card(role="widget")
            box = QVBoxLayout(card)
            button = QPushButton(title)
            button.setIcon(_navigation_icon(symbol, self.style().standardIcon(fallback), white=.95))
            button.setIconSize(QSize(24, 24))
            button.setProperty("buttonRole", "secondary")
            button.clicked.connect(callback)
            box.addWidget(button)
            caption = QLabel(example)
            caption.setWordWrap(True)
            box.addWidget(caption)
            choices_layout.addWidget(card, 1)
        self._templates = QWidget(objectName="computerTaskTemplates")
        template_layout = QHBoxLayout(self._templates)
        template_layout.setContentsMargins(0, 0, 0, 0)
        for title, callback in (("保存复制的文字", self._new_reference_workflow),
                                ("打开工作环境", self._new_workspace_workflow),
                                ("自定义步骤", self._new_workflow)):
            button = QPushButton(title)
            button.clicked.connect(callback)
            template_layout.addWidget(button)
        self._templates.hide()
        layout.addWidget(self._choices)
        layout.addWidget(self._templates)
        layout.addWidget(self._stack, 1)
        view_model.host_tasks.changed.connect(self.refresh_catalog)

        view_model.workflow_host.changed.connect(self.refresh_catalog)
        view_model.automation_host.changed.connect(self.refresh_catalog)
        platform = view_model.extension_platform
        if platform is not None:
            platform.changed.connect(self.refresh_catalog)
            platform.log_added.connect(lambda _log: self.refresh_catalog())
        self.refresh_catalog()
        self.show_section(self.MY_ACTIONS)

    @property
    def device_serial(self) -> str:
        return self._device_serial

    def _new_workflow(self) -> None:
        if self._workflow_page._new_workflow():
            self.show_section(self.EDIT_ACTION)

    def _new_reference_workflow(self) -> None:
        if self._workflow_page._new_reference_workflow():
            self.show_section(self.EDIT_ACTION)

    def _new_workspace_workflow(self):
        if self._workflow_page._new_workspace_workflow():
            self.show_section(self.EDIT_ACTION)

    def _import_workflow(self) -> None:
        if self._workflow_page._import():
            self.show_section(self.EDIT_ACTION)

    def _open_selected_action(self) -> None:
        item = self._my_actions._list.currentItem()
        if item is None:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        action = next((action for action in self._view_model.host_actions if action.action_key == key), None)
        if action is None:
            return
        if action.provider == HostActionProvider.OFFICIAL:
            if self._workflow_page.open_workflow(key.removeprefix("official:")):
                self.show_section(self.EDIT_ACTION)
        elif action.provider == HostActionProvider.LOCAL_SCRIPT:
            self._ensure_developer().show_lane(_DevelopActionsPage.LOCAL_SCRIPT)
            if self._developer._automation_page.select_automation(key.removeprefix("local-script:")):
                self.show_developer_lane(_DevelopActionsPage.LOCAL_SCRIPT)
        else:
            _, extension_id, action_id = key.split(":", 2)
            self.show_developer_lane(_DevelopActionsPage.EXTENSION)
            self._developer._extensions_page.select_action(
                extension_id, action_id,
                action.prompt_ids[0] if action.prompt_ids else None,
            )

    def show_section(self, section: int) -> None:
        titles = {
            self.MY_ACTIONS: "我的自定义动作", self.EDIT_ACTION: "编辑自定义动作",
            self.RUN_HISTORY: "运行记录", self.DEVELOP_ACTIONS: "脚本与扩展",
        }
        if section not in titles:
            raise ValueError(f"未知自定义动作分区 {section}")
        if section == self.DEVELOP_ACTIONS:
            developer = self._ensure_developer()
            developer.show_lane(developer._stack.currentIndex())
        self._choices.setVisible(section == self.MY_ACTIONS)
        if section != self.MY_ACTIONS:
            self._templates.hide()
        self._stack.setCurrentIndex(section)
        set_translatable_text(self._title, titles[section])
        self._back.setVisible(section != self.MY_ACTIONS)
        self._create.setVisible(section == self.MY_ACTIONS and bool(self._view_model.host_actions))
        self._tools.setVisible(section == self.MY_ACTIONS and bool(self._view_model.host_actions))
        self._resume.setVisible(section == self.MY_ACTIONS and self._workflow_page._dirty)
        if section == self.MY_ACTIONS:
            self.refresh_catalog()
        elif section == self.RUN_HISTORY:
            self._history.refresh()
        elif section == self.DEVELOP_ACTIONS:
            self._ensure_developer().refresh()

    def _ensure_developer(self):
        if self._developer is None:
            placeholder = self._stack.widget(self.DEVELOP_ACTIONS)
            self._stack.removeWidget(placeholder)
            placeholder.deleteLater()
            self._developer = _DevelopActionsPage(self._view_model, prompt_names=self._prompt_names)
            self._stack.insertWidget(self.DEVELOP_ACTIONS, self._developer)
            self._developer.section_created.connect(self.section_created.emit)
            self.section_created.emit(self._developer)
        return self._developer

    def show_developer_lane(self, lane: int) -> None:
        self._ensure_developer().show_lane(lane)
        self.show_section(self.DEVELOP_ACTIONS)

    def update_prompt_context(self, names, controls) -> None:
        self._prompt_names = names
        editors = [(self._workflow_page, controls)]
        if self._developer is not None:
            self._developer._prompt_names = names
            if self._developer._automation_page is not None:
                editors.append((self._developer._automation_page, {}))
        for editor, bindings in editors:
            trigger = editor._trigger
            choices = tuple(self._view_model.prompt_trigger_choices)
            inputs = (names, bindings, choices, translate_ui_text("提示词槽位 1"))
            if inputs == getattr(editor, "_trigger_context", None):
                continue
            editor._trigger_context = inputs
            selected = trigger.currentData()
            trigger.blockSignals(True)
            trigger.clear()
            trigger.addItem(translate_ui_text("直接绑定设备键"), None)
            for prompt_id in choices:
                name = names.get(prompt_id)
                label = f"提示词 {prompt_id} · {name}" if name else f"提示词槽位 {prompt_id}"
                if bindings.get(prompt_id):
                    label += f" · 当前 Profile：{'、'.join(bindings[prompt_id])}"
                trigger.addItem(translate_ui_text(label), prompt_id)
            if selected is not None and trigger.findData(selected) < 0:
                trigger.addItem(translate_ui_text(f"提示词槽位 {selected} · 需重新配置"), selected)
            if selected is not None:
                trigger.setCurrentIndex(trigger.findData(selected))
            trigger.blockSignals(False)

    def refresh_catalog(self) -> None:
        tasks = self._view_model.host_tasks
        message = tasks.status or ("正在运行…" if tasks.running else "")
        self._task_notice.setVisible(bool(message))
        set_translatable_text(self._task_status, message)
        self._task_stop.setVisible(tasks.running)
        self._workflow_page._update_status()
        self._my_actions.refresh()
        has_actions = bool(self._view_model.host_actions)
        show_shortcuts = self._stack.currentIndex() == self.MY_ACTIONS and has_actions
        self._create.setVisible(show_shortcuts)
        self._tools.setVisible(show_shortcuts)
        primary = not self._view_model.host_actions and not self._workflow_page._dirty
        role = "primary" if primary else "secondary"
        if self._create.property("buttonRole") != role:
            self._create.setProperty("buttonRole", role)
            self._create.style().unpolish(self._create)
            self._create.style().polish(self._create)
        if self._stack.currentIndex() == self.RUN_HISTORY:
            self._history.refresh()
        # Only surface a source's latest failure. Its next success clears it;
        # history remains available from More without a permanent log panel.
        sources = [self._view_model.workflow_host.logs, self._view_model.automation_host.logs]
        platform = self._view_model.extension_platform
        if platform is not None:
            sources.append(platform.logs)
        failures = [logs[-1] for logs in sources if logs and logs[-1].level in {"error", "warning"}]
        latest = max(failures, key=lambda entry: entry.timestamp, default=None)
        self._failure.setVisible(latest is not None)
        if latest is not None:
            set_translatable_text(self._failure_text, latest.message)

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt API
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.refresh_catalog()


class _MyActionsPage(QWidget):
    def __init__(self, view_model: MainViewModel) -> None:
        super().__init__(objectName="myActionsPage")
        self._view_model = view_model
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setAlignment(Qt.AlignTop)
        card = V4Card(role="primary", dots=True)
        self._card = card
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(24, 20, 24, 22)
        self._empty = QLabel("组合保存文字、打开应用等电脑操作，由设备触发。", objectName="actionEmptyState")
        self._empty.setWordWrap(True)
        self._empty.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        card_layout.addWidget(self._empty)
        self._list = QListWidget(objectName="myActionList")
        self._list.setSpacing(6)
        self._list.setWordWrap(True)
        self._list.setStyleSheet("""
            QListWidget#myActionList { background: transparent; border: none; padding: 0; }
            QListWidget#myActionList::item { padding: 12px; border: 1px solid transparent; border-radius: 12px; }
            QListWidget#myActionList::item:selected { background: #302C27; color: #EFEAE0; border-color: #FF6A00; }
        """)
        self._list.currentItemChanged.connect(lambda *_: self._edit.setEnabled(self._list.currentItem() is not None))
        card_layout.addWidget(self._list, 1)
        row = QHBoxLayout()
        self._edit = QPushButton("编辑与绑定", objectName="editSelectedAction")
        self._edit.setProperty("buttonRole", "primary")
        row.addWidget(self._edit)
        row.addStretch(1)
        card_layout.addLayout(row)
        layout.addWidget(card, 1)
        self.refresh()

    def refresh(self) -> None:
        actions = self._view_model.host_actions
        inputs = (actions, self._view_model.host_tasks.bindings, self._view_model.host_tasks.status, translate_ui_text("可运行"))
        if inputs == getattr(self, "_rendered_actions", None):
            return
        self._rendered_actions = inputs
        current = self._list.currentItem()
        selected = current.data(Qt.ItemDataRole.UserRole) if current is not None else None
        scroll = self._list.verticalScrollBar().value()
        self._list.blockSignals(True)
        self._list.clear()
        for action in actions:
            state = translate_ui_text("可运行" if action.available else ("未就绪" if action.enabled else "未启用"))
            binding = "、".join(translate_ui_text(f"提示词槽位 {prompt_id}") for prompt_id in action.prompt_ids) or translate_ui_text("未绑定实体控件")
            prefix, _, target_id = action.action_key.partition(":")
            provider = {"official": "workflow", "local-script": "script", "extension": "extension"}.get(prefix, "extension")
            local = self._view_model.host_tasks.binding_for(provider, target_id)
            if local:
                binding = local.control_id.replace("key.", "功能键 ") + " · 保存在这台电脑"
                state = self._view_model.host_tasks.problem or ("可以使用" if local.applied and local.tested else "待实体试用" if local.applied else "待应用")
            item = QListWidgetItem(f"{action.name}\n{translate_ui_text(binding)} · {translate_ui_text(state)}")
            if action.availability_reason and local is None:
                item.setText(item.text() + "\n" + translate_ui_text(action.availability_reason))
            item.setData(Qt.ItemDataRole.UserRole, action.action_key)
            self._list.addItem(item)
            if action.action_key == selected:
                self._list.setCurrentItem(item)
        if self._list.currentItem() is None and self._list.count():
            self._list.setCurrentRow(0)
        self._list.blockSignals(False)
        self._list.verticalScrollBar().setValue(scroll)
        self._card.setMaximumHeight(16777215 if actions else 160)
        self._empty.setVisible(not actions)
        self._list.setVisible(bool(actions))
        self._edit.setVisible(bool(actions))
        self._edit.setEnabled(self._list.currentItem() is not None)


class _RunHistoryPage(QWidget):
    def __init__(self, view_model: MainViewModel) -> None:
        super().__init__(objectName="actionRunHistoryPage")
        self._view_model = view_model
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        card = V4Card(role="widget")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(24, 20, 24, 22)
        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.addWidget(QLabel("RUN HISTORY", objectName="eyebrow"))
        titles.addWidget(QLabel("运行记录", objectName="inspectorTitle"))
        header.addLayout(titles)
        header.addStretch(1)
        clear = QPushButton("清空官方与本地记录", objectName="secondary")
        clear.clicked.connect(self._clear)
        header.addWidget(clear)
        card_layout.addLayout(header)
        self._log = QPlainTextEdit(objectName="unifiedActionRunLog")
        self._log.setReadOnly(True)
        card_layout.addWidget(self._log, 1)
        layout.addWidget(card, 1)
        self.refresh()

    def refresh(self) -> None:
        records: list[tuple[str, str]] = []
        for entry in self._view_model.workflow_host.logs:
            records.append(
                (
                    entry.timestamp,
                    f"[{entry.timestamp}] OFFICIAL {entry.level.upper()}  "
                    f"{translate_ui_text(entry.message)}"
                    + (f"\n  {entry.technical}" if entry.technical else ""),
                )
            )
        for entry in self._view_model.automation_host.logs:
            records.append(
                (
                    entry.timestamp,
                    f"[{entry.timestamp}] LOCAL {entry.level.upper()}  "
                    f"{translate_ui_text(entry.message)}"
                    + (f"\n  {entry.technical}" if entry.technical else ""),
                )
            )
        platform = self._view_model.extension_platform
        if platform is not None:
            for entry in platform.logs:
                records.append(
                    (
                        entry.timestamp,
                        f"[{entry.timestamp}] CUSTOM {entry.level.upper()}  "
                        f"{translate_ui_text(entry.message)}",
                    )
                )
        lines = [text for _timestamp, text in sorted(records, key=lambda item: item[0])]
        text = "\n".join(lines) or translate_ui_text("尚无运行记录")
        if self._log.toPlainText() != text:
            self._log.setPlainText(text)

    def _clear(self) -> None:
        self._view_model.workflow_host.clear_logs()
        self._view_model.automation_host.clear_logs()
        self.refresh()


class _DevelopActionsPage(QWidget):
    section_created = Signal(object)

    LOCAL_SCRIPT = 0
    EXTENSION = 1

    def __init__(
        self,
        view_model: MainViewModel,
        *,
        prompt_names: dict[int, str] | None,
    ) -> None:
        super().__init__(objectName="developActionsPage")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        lanes = QFrame(objectName="actionTabs")
        lane_layout = QHBoxLayout(lanes)
        lane_layout.setContentsMargins(4, 4, 4, 4)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        for label, index in (("脚本导入", self.LOCAL_SCRIPT), ("扩展", self.EXTENSION)):
            button = QPushButton(label, objectName="developerLaneTab")
            button.setCheckable(True)
            button.setProperty("developerLane", index)
            button.clicked.connect(
                lambda _checked=False, target=index: self.show_lane(target)
            )
            self._group.addButton(button, index)
            lane_layout.addWidget(button)
        lane_layout.addStretch(1)
        layout.addWidget(lanes)
        self._stack = QStackedWidget(objectName="developerLaneStack")
        self._view_model = view_model
        self._prompt_names = prompt_names
        self._automation_page = None
        self._extensions_page = None
        self._stack.addWidget(QWidget())
        self._stack.addWidget(QWidget())
        layout.addWidget(self._stack, 1)
        first = self._group.button(self.LOCAL_SCRIPT)
        if first is not None:
            first.setChecked(True)

    def show_lane(self, lane: int) -> None:
        page = self._automation_page if lane == self.LOCAL_SCRIPT else self._extensions_page
        if page is None:
            placeholder = self._stack.widget(lane)
            self._stack.removeWidget(placeholder)
            placeholder.deleteLater()
            if lane == self.LOCAL_SCRIPT:
                page = self._automation_page = AutomationPage(self._view_model, prompt_names=self._prompt_names)
            else:
                page = self._extensions_page = ExtensionsPage(self._view_model)
            self._stack.insertWidget(lane, page)
            self.section_created.emit(page)
        self._stack.setCurrentIndex(lane)
        button = self._group.button(lane)
        if button is not None:
            button.setChecked(True)
        self.refresh()

    def refresh(self) -> None:
        if self._stack.currentIndex() == self.LOCAL_SCRIPT:
            if self._automation_page is not None:
                self._automation_page.refresh_runtime()
        elif self._extensions_page is not None:
            self._extensions_page.refresh()
