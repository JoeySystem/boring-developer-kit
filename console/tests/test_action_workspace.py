from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox, QPushButton

from controller_config.automation import AutomationStore
from controller_config.host_actions import HostActionRegistry
from controller_config.prompt_library import PromptLibraryStore
from controller_config.transport.demo import DemoGateway
from controller_config.viewmodels.main import MainViewModel
from controller_config.views.actions import ActionsPage
from controller_config.workflow_runtime import WorkflowRunLog
from controller_config.workflows import LocalWorkflow, WorkflowStep, WorkflowStore
from test_workflow_ui import FakeHostServices


def workspace(qtbot, contract, tmp_path):
    vm = MainViewModel(
        DemoGateway(contract, "ready"), contract,
        workflow_store=WorkflowStore(tmp_path / "workflows"),
        automation_store=AutomationStore(tmp_path / "scripts"),
        prompt_library_store=PromptLibraryStore(tmp_path / "prompts"),
        host_action_registry=HostActionRegistry(FakeHostServices()),
    )
    vm.start()
    qtbot.waitUntil(lambda: vm.draft is not None)
    page = ActionsPage(vm)
    qtbot.addWidget(page)
    page.resize(900, 680)
    page.show()
    return vm, page


def test_empty_library_creates_and_saves_in_same_workspace(qtbot, contract, tmp_path):
    vm, page = workspace(qtbot, contract, tmp_path)
    assert not page.findChildren(QPushButton, "actionTab")
    assert page._my_actions._empty.isVisible()
    assert not page._failure.isVisible()
    assert page._create.isHidden() and page._tools.isHidden()
    next(button for button in page._choices.findChildren(QPushButton) if button.text() == "组合电脑操作").click()
    next(button for button in page._templates.findChildren(QPushButton) if button.text() == "自定义步骤").click()
    editor = page._workflow_page
    assert page._stack.currentIndex() == ActionsPage.EDIT_ACTION
    editor._name.setText("办公室摘录")
    saved = editor._save()
    assert saved is not None and saved.name == "办公室摘录"
    page._back.click()
    assert page._my_actions._list.count() == 1
    assert "办公室摘录" in page._my_actions._list.item(0).text()
    page._my_actions._edit.click()
    assert editor._selected_id == saved.workflow_id
    assert editor._trigger.currentData() == saved.trigger_prompt_id
    assert not saved.enabled
    vm.shutdown()


def test_navigation_and_background_refresh_keep_new_draft(qtbot, contract, tmp_path):
    vm, page = workspace(qtbot, contract, tmp_path)
    page._new_reference_workflow()
    editor = page._workflow_page
    editor._name.setText("未完成的摘录")
    editor._step_list.setCurrentRow(1)
    editor._parameter.setText("/tmp/my notes.md")
    editor._parameter_changed()
    editor._parameter.setCursorPosition(8)
    before = editor.editing_state()
    page.show_section(ActionsPage.RUN_HISTORY)
    vm.workflow_host.changed.emit()
    page._back.click()
    assert page._resume.isVisible()
    page._resume.click()
    assert editor.editing_state() == before
    assert editor._step_list.currentRow() == 1
    assert editor._parameter.cursorPosition() == 8
    vm.shutdown()


def test_cancel_switching_action_retains_unsaved_fields(qtbot, contract, tmp_path, monkeypatch):
    vm, page = workspace(qtbot, contract, tmp_path)
    saved = vm.save_workflow(LocalWorkflow("first", "已有动作", 1, (WorkflowStep("read_clipboard", {}),)))
    page._new_workflow()
    editor = page._workflow_page
    editor._name.setText("正在编辑")
    page._back.click()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.Cancel)
    page._my_actions._edit.click()
    assert page._stack.currentIndex() == ActionsPage.MY_ACTIONS
    assert editor._name.text() == "正在编辑"
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.Discard)
    page._my_actions._edit.click()
    assert page._stack.currentIndex() == ActionsPage.EDIT_ACTION
    assert editor._selected_id == saved.workflow_id
    vm.shutdown()


def test_existing_script_opens_exact_binding_from_library(qtbot, contract, tmp_path):
    vm, page = workspace(qtbot, contract, tmp_path)
    script = tmp_path / "notes.py"
    script.write_text("print('not executed')\n")
    for identity, slot in (("a", 1), ("b", 2)):
        vm.save_automation(automation_id=identity, name=identity, trigger_prompt_id=slot, script_path=str(script), enabled=False)
    items = page._my_actions._list
    items.setCurrentRow(next(row for row in range(items.count()) if items.item(row).data(Qt.UserRole) == "local-script:b"))
    page._my_actions._edit.click()
    script_page = page._developer._automation_page
    assert page._stack.currentIndex() == ActionsPage.DEVELOP_ACTIONS
    assert script_page._selected_id == "b"
    assert script_page._trigger.currentData() == 2
    assert not vm.automation_host.running
    vm.shutdown()


def test_failure_exposes_history_without_forcing_navigation(qtbot, contract, tmp_path):
    vm, page = workspace(qtbot, contract, tmp_path)
    vm.workflow_host._logs.append(WorkflowRunLog(1, "2026-09-18T12:00:00", "error", "a", "摘录", "无法写入目标文件"))
    vm.workflow_host.changed.emit()
    assert page._failure.isVisible()
    assert "无法写入目标文件" in page._failure_text.text()
    assert page._stack.currentIndex() == ActionsPage.MY_ACTIONS
    page.findChild(QPushButton, "actionFailureDetails").click()
    assert page._stack.currentIndex() == ActionsPage.RUN_HISTORY
    assert "无法写入目标文件" in page._history._log.toPlainText()
    vm.workflow_host.clear_logs()
    assert not page._failure.isVisible()
    vm.shutdown()


def test_script_new_draft_is_not_lost_when_opening_saved_script(qtbot, contract, tmp_path, monkeypatch):
    vm, page = workspace(qtbot, contract, tmp_path)
    script = tmp_path / "example.py"
    script.write_text("print('not executed')\n")
    vm.save_automation(automation_id="a", name="已保存脚本", trigger_prompt_id=1, script_path=str(script), enabled=False)
    page._my_actions._edit.click()
    editor = page._developer._automation_page
    editor._new_definition()
    editor._name.setText("未保存的新脚本")
    page._back.click()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.Cancel)
    page._my_actions._edit.click()
    assert editor._name.text() == "未保存的新脚本"
    assert page._stack.currentIndex() == ActionsPage.MY_ACTIONS
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.Discard)
    page._my_actions._edit.click()
    assert editor._selected_id == "a"
    assert editor._name.text() == "已保存脚本"
    vm.shutdown()


def test_editor_save_stays_visible_in_compact_workspace(qtbot, contract, tmp_path):
    vm, page = workspace(qtbot, contract, tmp_path)
    page.resize(700, 520)
    page._new_reference_workflow()
    qtbot.wait(30)
    footer = page._workflow_page.footer
    save = page._workflow_page._save_button
    assert save.text() == "应用到设备"
    assert footer.isVisible() and save.isVisible()
    assert page.rect().contains(save.mapTo(page, save.rect().bottomRight()))
    before = footer.geometry()
    scroll = page._workflow_page.verticalScrollBar()
    scroll.setValue(scroll.maximum())
    assert footer.geometry() == before
    vm.shutdown()


def test_task_status_and_stop_are_available_without_opening_an_editor(qtbot, contract, tmp_path, monkeypatch):
    vm, page = workspace(qtbot, contract, tmp_path)
    assert page._task_notice.isHidden()
    vm.host_tasks.status = "此电脑尚未设置这个任务"
    vm.host_tasks.changed.emit()
    assert page._task_notice.isVisible()
    assert page._task_status.text() == "此电脑尚未设置这个任务"
    assert page._task_stop.isHidden()
    stopped = []
    def cancel():
        stopped.append(True)
        vm.workflow_host.running = False
        vm.host_tasks.status = "已停止"
        vm.workflow_host.changed.emit()
    monkeypatch.setattr(vm.workflow_host, "cancel_run", cancel)
    vm.workflow_host.running = True
    vm.host_tasks.status = "正在运行：保存复制的文字"
    vm.workflow_host.changed.emit()
    assert page._task_stop.isVisible()
    page._task_stop.click()
    assert stopped == [True]
    assert page._task_stop.isHidden()
    assert page._stack.currentIndex() == ActionsPage.MY_ACTIONS
    vm.shutdown()
