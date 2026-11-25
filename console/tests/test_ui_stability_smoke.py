from PySide6.QtWidgets import QMessageBox, QPushButton, QWidget
from shiboken6 import isValid

from controller_config.automation import AutomationStore
from controller_config.style import tokens
from controller_config.prompt_library import PromptLibraryStore
from controller_config.transport.demo import DemoGateway
from controller_config.viewmodels.main import MainViewModel
from controller_config.views.main_window import MainWindow
from controller_config.workflows import WorkflowStore
from notice_helpers import answer_warnings


def test_top_navigation_marks_destination_and_animates_new_page(
    qtbot, contract, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "controller_config.views.main_window.system_reduces_motion",
        lambda: False,
    )
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
    qtbot.waitUntil(
        lambda: window._connection_transition_model is None
        and not window._connection_progress.isVisible(),
        timeout=4000,  # the found clip's ending (about 1.6 s) plays before the Mode page
    )

    window._nav_buttons["prompts"].click()

    assert vm.page == "prompts"
    assert window._nav_buttons["prompts"].property("active") is True
    # 叠丙 (design decision): an overlay draws the change, then goes away.
    arrival = window._page_arrival
    assert arrival is not None and arrival.running()
    qtbot.waitUntil(lambda: bool(arrival._blocks), timeout=1000)
    assert len(arrival._blocks) == 3   # status bar and the two tiles (spec item 5)
    # the designer 11-20 真屏验收: the real page stays unseen under the change (no ghost).
    veil = window._content.graphicsEffect()
    assert veil is not None and veil.opacity() == 0
    starts = sorted(block.start_ms for block in arrival._blocks)
    assert starts[0] == 0 and starts[-1] == tokens.PAGE_STAGGER_MS
    qtbot.waitUntil(lambda: not arrival.running(), timeout=1500)
    assert window._content.graphicsEffect() is None
    assert all(widget.graphicsEffect() is None for widget in window._content.findChildren(QWidget)
               if widget.objectName() in {"promptStatusBar", "promptChoicesCard", "promptFocusCard"})
    prompt_page = window._content_layout.itemAt(
        window._content_layout.count() - 1
    ).widget()
    window._nav_buttons["prompts"].click()
    assert window._content_layout.itemAt(
        window._content_layout.count() - 1
    ).widget() is prompt_page
    assert not arrival.running()

    window._reduce_motion = True
    window._select_settings_section("system")
    window._content.findChild(QPushButton, "openAdvancedCustomization").click()
    assert vm.page == "actions"
    assert not arrival.running()


def test_repeated_navigation_resize_and_reconnect(qtbot, contract, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "controller_config.views.main_window.system_reduces_motion",
        lambda: True,
    )
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
    answer_warnings(monkeypatch, warning)
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
            elif page == "lighting":   # 灯光与反馈 is a Settings group now, not a top tab
                window._select_settings_section("appearance")
            else:
                window._nav_buttons[page].click()
            qtbot.waitUntil(lambda: vm.page == page, timeout=2000)
            window.resize(1100 if cycle % 2 else 1280, 720 if cycle % 2 else 800)
            qtbot.wait(50)
            # A finite set of page hosts is retained; inactive pages stay hidden.
            assert isValid(previous_host)
            assert not previous_host.isVisible()
            # Each Settings group keeps its own host (settings:<group>).
            assert set(window._page_hosts) <= {
                "prompts", "actions", "lighting", "settings:system", "settings:device", "overview",
            }
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
