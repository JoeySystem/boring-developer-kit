from PySide6.QtWidgets import QApplication, QMessageBox
from shiboken6 import isValid

from controller_config.automation import AutomationStore
from controller_config.prompt_library import PromptLibraryStore
from controller_config.transport.demo import DemoGateway
from controller_config.viewmodels.main import MainViewModel
from controller_config.views.main_window import MainWindow
from controller_config.workflows import WorkflowStore


def test_repeated_navigation_resize_and_reconnect(qtbot, contract, tmp_path, monkeypatch):
    gateway = DemoGateway(contract, "ready")
    vm = MainViewModel(
        gateway, contract,
        prompt_library_store=PromptLibraryStore(tmp_path / "prompts"),
        workflow_store=WorkflowStore(tmp_path / "workflows"),
        automation_store=AutomationStore(tmp_path / "automations"),
    )
    window = MainWindow(vm)
    qtbot.addWidget(window, before_close_func=lambda _: vm.shutdown())
    unexpected_dialogs = []
    def warning(*args):
        unexpected_dialogs.append(args[2])
        return QMessageBox.Cancel
    monkeypatch.setattr(QMessageBox, "warning", warning)
    window.show()
    vm.start()
    qtbot.waitUntil(lambda: vm.draft is not None, timeout=2000)
    for cycle in range(6):
        for page in ("prompts", "actions", "lighting", "settings", "overview"):
            # DemoGateway doesn't implement image transport. Navigation must
            # remain usable even while its optional image read is pending.
            previous = window._content_layout.itemAt(window._content_layout.count() - 1).widget()
            window._nav_buttons[page].click()
            qtbot.waitUntil(lambda: vm.page == page, timeout=2000)
            window.resize(1100 if cycle % 2 else 1280, 720 if cycle % 2 else 800)
            qtbot.wait(50)
            # Old pages must be released, not retained on every navigation.
            qtbot.waitUntil(lambda: not isValid(previous), timeout=2000)
            assert not unexpected_dialogs
        snapshot = vm.model.snapshot
        gateway.disconnected.emit("stability test disconnect")
        gateway.snapshot_ready.emit(snapshot)
        qtbot.waitUntil(lambda: vm.draft is not None, timeout=2000)
        assert not vm.draft.is_dirty
    assert window.isVisible()
    available_width = QApplication.primaryScreen().availableGeometry().width()
    assert window.minimumWidth() == min(1100, available_width)
