from __future__ import annotations

from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QToolButton, QWidget

from controller_config.automation import AutomationStore
from controller_config.transport.demo import DemoGateway
from controller_config.viewmodels.main import MainViewModel
from controller_config.views.actions import ActionsPage
from controller_config.views.v4_widgets import V4Card
from controller_config.workflows import WorkflowStore


def _page(qtbot, contract, tmp_path):
    vm = MainViewModel(
        DemoGateway(contract, "ready"), contract,
        automation_store=AutomationStore(tmp_path / "scripts"),
        workflow_store=WorkflowStore(tmp_path / "workflows"),
    )
    page = ActionsPage(vm)
    qtbot.addWidget(page)
    return vm, page


def test_automation_empty_state_defers_editor_and_keeps_specialized_imports(qtbot, contract, tmp_path):
    vm, page = _page(qtbot, contract, tmp_path)
    for name in ("playgroundNew", "playgroundImportJson", "playgroundImportScript"):
        assert page.findChild(QPushButton, name) is None
    assert page._workflow_page.findChild(QWidget, "workflowWorkspace").isHidden()
    page.findChild(QPushButton, "createAction").click()
    assert not page._workflow_page.findChild(QWidget, "workflowWorkspace").isHidden()
    page.show_section(ActionsPage.DEVELOP_ACTIONS)
    assert page._stack.currentIndex() == ActionsPage.DEVELOP_ACTIONS
    assert page._developer._stack.currentIndex() == page._developer.LOCAL_SCRIPT
    assert page._developer._group.button(page._developer.LOCAL_SCRIPT).text() == "脚本导入"
    assert page._developer._group.button(page._developer.EXTENSION).text() == "扩展"
    vm.shutdown()


def test_codex_workflow_guide_is_instructional_and_preserves_proposal_review(qtbot, contract, tmp_path):
    vm, page = _page(qtbot, contract, tmp_path)
    page.show_developer_lane(1)
    extensions = page._developer._extensions_page
    steps = extensions.findChildren(QLabel, "workflowGuideStep")
    assert [label.text() for label in steps] == [f"{number:02}" for number in range(1, 8)]
    assert all(label.property("completed") is None for label in steps)
    assert len(extensions.findChildren(QPushButton, "importExtensionPackage")) == 1
    assert len(extensions.findChildren(QPushButton, "copyExtensionDevelopmentPrompt")) == 1
    for name in ("extensionToggle", "extensionBindAction", "extensionApproveProposal", "extensionRejectProposal"):
        assert extensions.findChild(QPushButton, name) is not None
    disclosure = extensions.findChild(QToolButton, "extensionProposalDisclosure")
    contents = extensions.findChild(QWidget, "extensionProposalContents")
    assert contents.isHidden()
    disclosure.click()
    assert not contents.isHidden()
    assert len([card for card in extensions.findChildren(V4Card) if card.property("v4Role") == "focus"]) == 1
    vm.shutdown()


def test_codex_workflow_copy_button_keeps_real_development_prompt(qtbot, contract, tmp_path):
    vm, page = _page(qtbot, contract, tmp_path)
    page.show_developer_lane(1)
    extensions = page._developer._extensions_page
    old_clipboard = QApplication.clipboard().text()
    try:
        extensions.findChild(QPushButton, "copyExtensionDevelopmentPrompt").click()
        copied = QApplication.clipboard().text()
        assert "BORING" in copied
        assert "manifest" in copied
        assert len(copied) > 100
    finally:
        QApplication.clipboard().setText(old_clipboard)
        vm.shutdown()
