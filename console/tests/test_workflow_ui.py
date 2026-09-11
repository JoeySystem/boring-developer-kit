from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
)

from controller_config.host_actions import HostActionRegistry
from controller_config.prompt_library import PromptEntry, PromptLibraryStore
from controller_config.transport.demo import DemoGateway
from controller_config.viewmodels.main import MainViewModel
from controller_config.views.actions import ActionsPage
from controller_config.views.main_window import MainWindow
from controller_config.views.workflows import WorkflowPage
from controller_config.workflows import WorkflowStore


class FakeHostServices:
    platform = "darwin"

    def __init__(self) -> None:
        self.calls = []

    def capture_selection(self) -> str:
        self.calls.append("capture")
        return "第一行中文\nSecond line"

    def read_clipboard(self) -> str:
        self.calls.append("clipboard")
        return "clipboard"

    def append_text_file(self, path: str, text: str, separator: str) -> None:
        self.calls.append(("append", path, text, separator))

    def open_target(self, target: str) -> None:
        self.calls.append(("open", target))

    def show_notification(self, message: str) -> None:
        self.calls.append(("notify", message))


def _button(window: MainWindow, text: str) -> QPushButton:
    return next(button for button in window.findChildren(QPushButton) if button.text() == text)


def test_ordinary_user_builds_tests_and_enables_reference_workflow(
    qtbot, contract, tmp_path, monkeypatch
) -> None:
    services = FakeHostServices()
    view_model = MainViewModel(
        DemoGateway(contract, "ready"),
        contract,
        workflow_store=WorkflowStore(tmp_path / "workflows"),
        host_action_registry=HostActionRegistry(services),
        prompt_library_store=PromptLibraryStore(tmp_path / "prompts"),
    )
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args, **_kwargs: QMessageBox.Ok)
    window.show()
    view_model.start()
    qtbot.waitUntil(lambda: view_model.draft is not None, timeout=1_000)
    view_model.prompt_device._read_entries = [PromptEntry(1, "Trigger", "Test trigger")]
    view_model.prompt_device._finish_full_read()
    qtbot.mouseClick(window._nav_buttons["actions"], Qt.LeftButton)

    page = window.findChild(WorkflowPage, "workflowPage")
    assert page is not None
    _button(window, "使用保存摘录模板").click()
    steps = window.findChild(QListWidget, "workflowStepList")
    assert steps is not None and steps.count() == 3
    steps.setCurrentRow(1)
    parameter = window.findChild(QLineEdit, "workflowStepParameter")
    assert parameter is not None
    parameter.setText(str(tmp_path / "摘录.md"))
    page._parameter_changed()

    _button(window, "保存自动化").click()

    assert len(view_model.workflow_host.workflows) == 1
    workflow = view_model.workflow_host.workflows[0]
    assert workflow.enabled is False and workflow.tested is False

    page._run_test_now(workflow.workflow_id)
    enabled = window.findChild(QCheckBox, "workflowEnabled")
    assert enabled is not None
    enabled.setChecked(True)
    _button(window, "保存自动化").click()

    workflow = view_model.workflow_host.workflows[0]
    assert workflow.enabled and workflow.tested
    assert services.calls == [
        "capture",
        ("append", str(tmp_path / "摘录.md"), "第一行中文\nSecond line", "\n\n"),
        ("notify", "内容已保存"),
    ]
    status = window.findChild(QLabel, "actionCatalogStatus")
    assert status is not None
    assert "自动化总数 1" in status.text()
    assert "当前可用 1" in status.text()

    actions_page = window.findChild(ActionsPage, "actionsPage")
    assert actions_page is not None
    actions_page.show_section(ActionsPage.MY_ACTIONS)
    my_actions = window.findChild(QListWidget, "myActionList")
    assert my_actions is not None
    assert "保存选中文字到 Markdown" in my_actions.item(0).text()
    actions_page.show_section(ActionsPage.DEVICE_BINDINGS)
    bindings = window.findChild(QListWidget, "actionBindingList")
    assert bindings is not None
    assert "提示词槽位 1" in bindings.item(0).text()
    view_model.shutdown()


def test_workflow_editor_supports_reorder_and_limits_to_five_steps(
    qtbot, contract, tmp_path, monkeypatch
) -> None:
    view_model = MainViewModel(
        DemoGateway(contract, "ready"),
        contract,
        workflow_store=WorkflowStore(tmp_path),
        host_action_registry=HostActionRegistry(FakeHostServices()),
    )
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    messages = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, _title, message: messages.append(message) or QMessageBox.Ok,
    )
    window.show()
    view_model.start()
    qtbot.waitUntil(lambda: view_model.draft is not None, timeout=1_000)
    view_model.navigate("actions")
    page = window.findChild(WorkflowPage, "workflowPage")
    assert page is not None

    for _ in range(4):
        page._add_step()
    assert len(page._steps) == 5
    page._add_step()
    assert messages[-1] == "一个自动化最多包含 5 个步骤"
    page._step_list.setCurrentRow(4)
    last_action = page._steps[4].action_id
    page._move_step(-1)
    assert page._steps[3].action_id == last_action
    view_model.shutdown()


def test_editing_step_parameter_does_not_reset_cursor_or_close_editor(
    qtbot, contract, tmp_path
) -> None:
    view_model = MainViewModel(
        DemoGateway(contract, "ready"),
        contract,
        workflow_store=WorkflowStore(tmp_path),
        host_action_registry=HostActionRegistry(FakeHostServices()),
    )
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.show()
    view_model.start()
    qtbot.waitUntil(lambda: view_model.draft is not None, timeout=1_000)
    view_model.navigate("actions")
    page = window.findChild(WorkflowPage, "workflowPage")
    assert page is not None
    page._new_reference_workflow()
    page._step_list.setCurrentRow(1)
    parameter = window.findChild(QLineEdit, "workflowStepParameter")
    assert parameter is not None
    parameter.setText("/tmp/ac.md")
    parameter.setCursorPosition(5)
    parameter.setFocus()

    qtbot.keyClicks(parameter, "XY")

    assert parameter.text() == "/tmp/XYac.md"
    assert page._step_list.currentRow() == 1
    view_model.shutdown()


def test_workflow_trigger_names_the_current_profile_control(
    qtbot, contract, tmp_path
) -> None:
    view_model = MainViewModel(
        DemoGateway(contract, "ready"),
        contract,
        workflow_store=WorkflowStore(tmp_path),
        host_action_registry=HostActionRegistry(FakeHostServices()),
    )
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.show()
    view_model.start()
    qtbot.waitUntil(lambda: view_model.draft is not None, timeout=1_000)
    assert view_model.draft is not None
    active_profile = view_model.draft.config["active_profile"]
    view_model.set_mapping(
        active_profile,
        "joystick.up",
        "向上提示词",
        {"type": "prompt", "prompt_id": 1},
    )

    view_model.navigate("actions")

    trigger = window.findChild(QComboBox, "workflowTrigger")
    assert trigger is not None
    prompt_index = trigger.findData(1)
    assert prompt_index >= 0
    assert "当前 Profile：摇杆向上" in trigger.itemText(prompt_index)
    view_model.discard_draft()
    view_model.shutdown()
