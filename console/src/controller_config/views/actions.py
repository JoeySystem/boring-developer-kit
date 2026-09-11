from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
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
    """One ordinary-language entry for built-in and extended automation."""

    OFFICIAL_ACTIONS = 0
    MY_ACTIONS = 1
    DEVICE_BINDINGS = 2
    RUN_HISTORY = 3
    DEVELOP_ACTIONS = 4

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

        summary = V4Card(role="widget")
        summary_layout = QHBoxLayout(summary)
        summary_layout.setContentsMargins(24, 20, 24, 22)
        summary_layout.setSpacing(18)
        heading = QVBoxLayout()
        heading.addWidget(QLabel("PLAYGROUND", objectName="eyebrow"))
        heading.addWidget(QLabel("工作流与脚本", objectName="inspectorTitle"))
        intro = QLabel(
            "让一个实体控件替你完成一项或多项电脑操作；需要更多能力时，再导入由 Codex 或其他 AI 帮你开发的扩展。",
            objectName="muted",
        )
        intro.setWordWrap(True)
        heading.addWidget(intro)
        summary_layout.addLayout(heading, 1)
        self._catalog_status = QLabel(objectName="actionCatalogStatus")
        self._catalog_status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        summary_layout.addWidget(self._catalog_status)
        layout.addWidget(summary)

        tabs = QFrame(objectName="actionTabs")
        tabs_layout = QHBoxLayout(tabs)
        tabs_layout.setContentsMargins(4, 4, 4, 4)
        tabs_layout.setSpacing(4)
        self._tab_group = QButtonGroup(self)
        self._tab_group.setExclusive(True)
        for label, section in (
            ("创建自动化", self.OFFICIAL_ACTIONS),
            ("我的自动化", self.MY_ACTIONS),
            ("控件绑定", self.DEVICE_BINDINGS),
            ("运行记录", self.RUN_HISTORY),
            ("工作流与脚本", self.DEVELOP_ACTIONS),
        ):
            tabs_layout.addWidget(self._tab_button(label, section))
        tabs_layout.addStretch(1)
        layout.addWidget(tabs)

        self._stack = QStackedWidget(objectName="actionSectionStack")
        self._workflow_page = WorkflowPage(
            view_model,
            prompt_names=prompt_names,
            prompt_controls=prompt_controls,
        )
        self._my_actions = _MyActionsPage(view_model)
        self._bindings = _DeviceBindingsPage(view_model)
        self._history = _RunHistoryPage(view_model)
        self._developer = _DevelopActionsPage(
            view_model,
            prompt_names=prompt_names,
        )
        library_tools = QFrame(objectName="playgroundLibraryTools")
        tools_layout = QHBoxLayout(library_tools)
        tools_layout.setContentsMargins(0, 0, 0, 0)
        tools_layout.setSpacing(8)
        tools_layout.addWidget(QLabel("LIBRARY", objectName="eyebrow"))
        for label, name, callback in (
            ("新建", "playgroundNew", self._new_workflow),
            ("导入 JSON", "playgroundImportJson", self._import_workflow),
            ("导入脚本", "playgroundImportScript", self._import_script),
        ):
            button = QPushButton(label, objectName=name)
            button.setProperty("playgroundTool", True)
            button.setProperty("buttonRole", "secondary")
            button.clicked.connect(callback)
            tools_layout.addWidget(button)
        tools_layout.addStretch(1)
        layout.addWidget(library_tools)
        for page in (
            self._workflow_page,
            self._my_actions,
            self._bindings,
            self._history,
            self._developer,
        ):
            self._stack.addWidget(page)
        layout.addWidget(self._stack, 1)

        first = self._tab_group.button(self.OFFICIAL_ACTIONS)
        if first is not None:
            first.setChecked(True)
        self._stack.setCurrentIndex(self.OFFICIAL_ACTIONS)
        view_model.workflow_host.changed.connect(self.refresh_catalog)
        view_model.automation_host.changed.connect(self.refresh_catalog)
        platform = view_model.extension_platform
        if platform is not None:
            platform.changed.connect(self.refresh_catalog)
            platform.log_added.connect(lambda _log: self._history.refresh())
        self.refresh_catalog()

    @property
    def device_serial(self) -> str:
        return self._device_serial

    def _new_workflow(self) -> None:
        self.show_section(self.OFFICIAL_ACTIONS)
        self._workflow_page._new_workflow()

    def _import_workflow(self) -> None:
        self.show_section(self.OFFICIAL_ACTIONS)
        self._workflow_page._import()

    def _import_script(self) -> None:
        self.show_developer_lane(_DevelopActionsPage.LOCAL_SCRIPT)
        self._developer._automation_page._choose_script()

    def show_section(self, section: int) -> None:
        if section not in {
            self.OFFICIAL_ACTIONS,
            self.MY_ACTIONS,
            self.DEVICE_BINDINGS,
            self.RUN_HISTORY,
            self.DEVELOP_ACTIONS,
        }:
            raise ValueError(f"未知自动化分区 {section}")
        self._stack.setCurrentIndex(section)
        button = self._tab_group.button(section)
        if button is not None:
            button.setChecked(True)
        self._refresh_section(section)

    def show_developer_lane(self, lane: int) -> None:
        self.show_section(self.DEVELOP_ACTIONS)
        self._developer.show_lane(lane)

    def refresh_catalog(self) -> None:
        self._workflow_page._update_status()
        actions = self._view_model.host_actions
        available = sum(action.available for action in actions)
        bound = sum(action.physically_bound for action in actions)
        set_translatable_text(
            self._catalog_status,
            f"自动化总数 {len(actions)}  ·  当前可用 {available}  ·  "
            f"已绑定控件 {bound}",
        )
        self._my_actions.refresh()
        self._bindings.refresh()
        self._history.refresh()

    def _refresh_section(self, section: int) -> None:
        if section == self.OFFICIAL_ACTIONS:
            self._workflow_page.refresh()
        elif section == self.MY_ACTIONS:
            self._my_actions.refresh()
        elif section == self.DEVICE_BINDINGS:
            self._bindings.refresh()
        elif section == self.RUN_HISTORY:
            self._history.refresh()
        elif section == self.DEVELOP_ACTIONS:
            self._developer.refresh()

    def _tab_button(self, label: str, section: int) -> QPushButton:
        button = QPushButton(label, objectName="actionTab")
        button.setCheckable(True)
        button.setProperty("actionSection", section)
        button.clicked.connect(
            lambda _checked=False, target=section: self.show_section(target)
        )
        self._tab_group.addButton(button, section)
        return button

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
        card = V4Card(role="primary", dots=True)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(24, 20, 24, 22)
        card_layout.addWidget(QLabel("MY AUTOMATION", objectName="eyebrow"))
        card_layout.addWidget(QLabel("我的自动化", objectName="inspectorTitle"))
        copy = QLabel(
            "这里汇总内置自动化、本地脚本和已导入扩展。普通使用时只需要关注名称、可用状态和控件绑定。",
            objectName="muted",
        )
        copy.setWordWrap(True)
        card_layout.addWidget(copy)
        self._list = QListWidget(objectName="myActionList")
        card_layout.addWidget(self._list, 1)
        layout.addWidget(card, 1)
        self.refresh()

    def refresh(self) -> None:
        self._list.clear()
        actions = self._view_model.host_actions
        if not actions:
            self._list.addItem(translate_ui_text("尚未创建自动化"))
            return
        provider_names = {
            HostActionProvider.OFFICIAL: "内置自动化",
            HostActionProvider.LOCAL_SCRIPT: "本地脚本",
            HostActionProvider.EXTENSION: "扩展",
        }
        for action in actions:
            state = translate_ui_text(
                "可运行"
                if action.available
                else ("已启用但未就绪" if action.enabled else "未启用")
            )
            binding = (
                "、".join(
                    f"{translate_ui_text('提示词')} {prompt_id}"
                    for prompt_id in action.prompt_ids
                )
                if action.prompt_ids
                else translate_ui_text("未绑定实体控件")
            )
            self._list.addItem(
                f"{action.name}\n{translate_ui_text(provider_names[action.provider])} · "
                f"{state} · {binding}"
                + (f"\n{translate_ui_text(action.availability_reason)}" if action.availability_reason else "")
            )


class _DeviceBindingsPage(QWidget):
    def __init__(self, view_model: MainViewModel) -> None:
        super().__init__(objectName="actionBindingsPage")
        self._view_model = view_model
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        card = V4Card(role="widget")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(24, 20, 24, 22)
        card_layout.addWidget(QLabel("CONTROL BINDINGS", objectName="eyebrow"))
        card_layout.addWidget(QLabel("控件绑定", objectName="inspectorTitle"))
        explanation = QLabel(
            "一个提示词槽位只能绑定一个自动化来源：内置自动化、本地脚本或扩展。这里不会静默替换已有绑定。",
            objectName="muted",
        )
        explanation.setWordWrap(True)
        card_layout.addWidget(explanation)
        self._list = QListWidget(objectName="actionBindingList")
        card_layout.addWidget(self._list, 1)
        layout.addWidget(card, 1)
        self.refresh()

    def refresh(self) -> None:
        self._list.clear()
        bound = [
            (prompt_id, action)
            for action in self._view_model.host_actions
            for prompt_id in action.prompt_ids
        ]
        if not bound:
            self._list.addItem(translate_ui_text("尚未建立实体绑定"))
            return
        for prompt_id, action in sorted(bound, key=lambda item: item[0]):
            state = translate_ui_text(
                "会响应实体事件" if action.available and prompt_id in action.ready_prompt_ids
                else self._view_model.prompt_trigger_problem(prompt_id) or "当前不会运行"
            )
            self._list.addItem(
                f"{translate_ui_text(f'提示词槽位 {prompt_id}')}\n{action.name} · "
                f"{translate_ui_text(action.provider_name)} · {state}"
            )


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
        for label, index in (("脚本导入", self.LOCAL_SCRIPT), ("Codex 工作流", self.EXTENSION)):
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
        self._automation_page = AutomationPage(view_model, prompt_names=prompt_names)
        self._extensions_page = ExtensionsPage(view_model)
        self._stack.addWidget(self._automation_page)
        self._stack.addWidget(self._extensions_page)
        layout.addWidget(self._stack, 1)
        first = self._group.button(self.LOCAL_SCRIPT)
        if first is not None:
            first.setChecked(True)

    def show_lane(self, lane: int) -> None:
        self._stack.setCurrentIndex(lane)
        button = self._group.button(lane)
        if button is not None:
            button.setChecked(True)
        self.refresh()

    def refresh(self) -> None:
        if self._stack.currentIndex() == self.LOCAL_SCRIPT:
            self._automation_page.refresh_runtime()
        else:
            self._extensions_page.refresh()
