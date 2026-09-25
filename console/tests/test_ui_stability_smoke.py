from PySide6.QtWidgets import QMessageBox, QPushButton
from shiboken6 import isValid

from controller_config.automation import AutomationStore
from controller_config.prompt_library import PromptLibraryStore
from controller_config.transport.demo import DemoGateway
from controller_config.viewmodels.main import MainViewModel
from controller_config.views.main_window import MainWindow
from controller_config.workflows import WorkflowStore


def test_top_navigation_marks_destination_and_animates_new_page(
    qtbot, contract, tmp_path
):
    gateway = DemoGateway(contract, "ready")
    vm = MainViewModel(
        gateway,
        contract,
        prompt_library_store=PromptLibraryStore(tmp_path / "prompts"),
        workflow_store=WorkflowStore(tmp_path / "workflows"),
        automation_store=AutomationStore(tmp_path / "automations"),
    )
    window = MainWindow(vm)
    qtbot.addWidget(window, before_close_func=lambda _: vm.shutdown())
    window.show()
    vm.start()
    qtbot.waitUntil(lambda: vm.draft is not None, timeout=2000)

    window._nav_buttons["prompts"].click()

    assert vm.page == "prompts"
    assert window._nav_buttons["prompts"].property("active") is True
    animation = window._page_transition_animation
    assert animation is not None
    assert animation.duration() == 120
    animated_targets = window._page_transition_targets
    assert animated_targets
    qtbot.waitUntil(
        lambda: window._page_transition_animation is None,
        timeout=1000,
    )
    assert all(
        not isValid(target) or target.graphicsEffect() is None
        for target in animated_targets
    )
    prompt_page = window._content_layout.itemAt(
        window._content_layout.count() - 1
    ).widget()
    window._nav_buttons["prompts"].click()
    assert window._content_layout.itemAt(
        window._content_layout.count() - 1
    ).widget() is prompt_page
    assert window._page_transition_animation is None

    window._reduce_motion = True
    window._select_settings_section("system")
    window._content.findChild(QPushButton, "openAdvancedCustomization").click()
    assert vm.page == "actions"
    assert window._page_transition_animation is None


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
            previous_host = window._content
            if page == "actions":
                window._select_settings_section("system")
                window._content.findChild(QPushButton, "openAdvancedCustomization").click()
            else:
                window._nav_buttons[page].click()
            qtbot.waitUntil(lambda: vm.page == page, timeout=2000)
            window.resize(1100 if cycle % 2 else 1280, 720 if cycle % 2 else 800)
            qtbot.wait(50)
            # A finite set of page hosts is retained; inactive pages stay hidden.
            assert isValid(previous_host)
            assert not previous_host.isVisible()
            assert len(window._page_hosts) <= 5
            assert not unexpected_dialogs
        snapshot = vm.model.snapshot
        gateway.disconnected.emit("stability test disconnect")
        gateway.snapshot_ready.emit(snapshot)
        qtbot.waitUntil(lambda: vm.draft is not None, timeout=2000)
        assert not vm.draft.is_dirty
    assert window.isVisible()
    assert window.minimumWidth() == min(
        1100, window.screen().availableGeometry().width()
    )
