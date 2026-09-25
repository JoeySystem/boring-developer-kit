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


def test_legacy_workflow_can_still_be_tested_and_enabled(
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
    window._select_settings_section("system")
    qtbot.waitUntil(lambda: window._content.findChild(QPushButton, "openAdvancedCustomization") is not None)
    window._content.findChild(QPushButton, "openAdvancedCustomization").click()

    qtbot.waitUntil(lambda: window.findChild(WorkflowPage, "workflowPage") is not None)
    page = window.findChild(WorkflowPage, "workflowPage")
    assert page is not None
    actions_page = window.findChild(ActionsPage, "actionsPage")
    actions_page._new_reference_workflow()
    steps = window.findChild(QListWidget, "workflowStepList")
    assert steps is not None and steps.count() == 2
    page._trigger.setCurrentIndex(page._trigger.findData(1))
    steps.setCurrentRow(1)
    parameter = window.findChild(QLineEdit, "workflowStepParameter")
    assert parameter is not None
    parameter.setText(str(tmp_path / "摘录.md"))
    page._parameter_changed()

    _button(window, "保存自定义动作").click()

    assert len(view_model.workflow_host.workflows) == 1
    workflow = view_model.workflow_host.workflows[0]
    assert workflow.enabled is False and workflow.tested is False

    page._run_test_now(workflow.workflow_id)
    qtbot.waitUntil(lambda: view_model.workflow_host.workflows[0].tested)
    enabled = window.findChild(QCheckBox, "workflowEnabled")
    assert enabled is not None
    enabled.setChecked(True)
    _button(window, "保存自定义动作").click()

    workflow = view_model.workflow_host.workflows[0]
    assert workflow.enabled and workflow.tested
    assert services.calls == [
        "clipboard",
        ("append", str(tmp_path / "摘录.md"), "clipboard", "\n\n"),
    ]
    actions_page.show_section(ActionsPage.MY_ACTIONS)
    my_actions = window.findChild(QListWidget, "myActionList")
    assert my_actions is not None
    assert "保存复制的文字" in my_actions.item(0).text()
    assert "提示词槽位 1" in my_actions.item(0).text()
    assert "可运行" in my_actions.item(0).text()
    window.findChild(QPushButton, "editSelectedAction").click()
    assert actions_page._stack.currentIndex() == ActionsPage.EDIT_ACTION
    assert page._selected_id == workflow.workflow_id
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
    qtbot.waitUntil(lambda: window.findChild(WorkflowPage, "workflowPage") is not None)
    page = window.findChild(WorkflowPage, "workflowPage")
    assert page is not None
    page._new_workflow()

    for _ in range(4):
        page._add_step()
    assert len(page._steps) == 5
    page._add_step()
    assert messages[-1] == "一个自定义动作最多包含 5 个步骤"
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
    qtbot.waitUntil(lambda: window.findChild(WorkflowPage, "workflowPage") is not None)
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

    qtbot.waitUntil(lambda: window.findChild(QComboBox, "workflowTrigger") is not None)
    trigger = window.findChild(QComboBox, "workflowTrigger")
    assert trigger is not None
    prompt_index = trigger.findData(1)
    assert prompt_index >= 0
    assert "当前 Profile：摇杆向上" in trigger.itemText(prompt_index)
    view_model.discard_draft()
    view_model.shutdown()


def test_new_templates_use_local_task_and_supported_steps(qtbot, contract, tmp_path):
    services = FakeHostServices()
    services.platform = "win32"
    view_model = MainViewModel(
        DemoGateway(contract, "ready"), contract,
        workflow_store=WorkflowStore(tmp_path),
        host_action_registry=HostActionRegistry(services),
    )
    view_model.start()
    qtbot.waitUntil(lambda: view_model.draft is not None)
    page = WorkflowPage(view_model)
    qtbot.addWidget(page)
    assert page._new_reference_workflow()
    assert page.editing_state().trigger_prompt_id is None
    assert [step.action_id for step in page._steps] == ["read_clipboard", "append_text_file"]
    assert page._step_list.currentRow() == 1
    assert page._step_list.isHidden()
    assert page._step_header_widget.isHidden()
    assert not page._parameter_box.isHidden()
    page._advanced_steps.click()
    assert not page._step_header_widget.isHidden()
    assert page._trigger.isHidden()
    assert page._save_button.text() == "应用到设备"
    page._dirty = False
    assert page._new_workspace_workflow()
    assert [step.action_id for step in page._steps] == ["open_target"]
    assert page._action_choice.currentData() == "open_target"
    view_model.shutdown()


def test_draft_trial_preserves_saved_enabled_workflow(qtbot, contract, tmp_path):
    from controller_config.workflows import LocalWorkflow, WorkflowStep

    services = FakeHostServices()
    view_model = MainViewModel(
        DemoGateway(contract, "ready"), contract,
        workflow_store=WorkflowStore(tmp_path),
        host_action_registry=HostActionRegistry(services),
    )
    view_model.start()
    qtbot.waitUntil(lambda: view_model.draft is not None)
    saved = LocalWorkflow("saved", "已应用", 1, (WorkflowStep("read_clipboard", {}),), True, True)
    view_model.workflow_host.save_workflow(saved)
    page = WorkflowPage(view_model)
    qtbot.addWidget(page)
    page._name.setText("未应用的修改")
    page._schedule_test()
    qtbot.waitUntil(lambda: bool(services.calls))
    qtbot.waitUntil(lambda: not view_model.workflow_host.running)
    assert view_model.workflow_host.workflows == (saved,)
    assert page._name.text() == "未应用的修改"
    assert page._dirty
    view_model.shutdown()


def test_selection_trial_can_be_cancelled_before_it_runs(qtbot, contract, tmp_path):
    from controller_config.workflows import WorkflowStep

    services = FakeHostServices()
    view_model = MainViewModel(
        DemoGateway(contract, "ready"), contract,
        workflow_store=WorkflowStore(tmp_path),
        host_action_registry=HostActionRegistry(services),
    )
    view_model.start()
    qtbot.waitUntil(lambda: view_model.draft is not None)
    page = WorkflowPage(view_model)
    qtbot.addWidget(page)
    page._new_workflow()
    page._name.setText("选区")
    page._steps = [WorkflowStep("capture_selection", {})]
    page._schedule_test()
    assert page._test_timer.isActive()
    page._schedule_test()
    assert not page._test_timer.isActive()
    assert page._pending_test is None
    assert not services.calls
    assert not view_model.workflow_host.workflows
    view_model.shutdown()


def test_applying_again_after_failure_keeps_the_same_local_task(qtbot, contract, tmp_path, monkeypatch):
    view_model = MainViewModel(
        DemoGateway(contract, "ready"), contract,
        workflow_store=WorkflowStore(tmp_path),
        host_action_registry=HostActionRegistry(FakeHostServices()),
    )
    view_model.start()
    qtbot.waitUntil(lambda: view_model.draft is not None)
    page = WorkflowPage(view_model)
    qtbot.addWidget(page)
    page._new_reference_workflow()
    page._parameter.setText(str(tmp_path / "notes.txt"))
    page._parameter_changed()
    attempted = []
    def fail(_provider, definition, _control):
        attempted.append(definition.workflow_id)
        raise ValueError("写入失败")
    monkeypatch.setattr(view_model.host_tasks, "apply", fail)
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: QMessageBox.Ok)
    page._apply_or_save()
    page._apply_or_save()
    assert len(attempted) == 2 and attempted[0] == attempted[1]
    assert page._dirty
    view_model.shutdown()
