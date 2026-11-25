"""Switching Mode changes the page content without rebuilding the stage or the device."""
from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QScrollArea

from controller_config.views.mode_page import ModeStage
from test_session_recovery import session
from test_primary_setup_flow import ready, current_editor, device_writes


class ModelEvents(QObject):
    def __init__(self, parent):
        super().__init__(parent)
        self.events = []

    def eventFilter(self, watched, event):
        if event.type() in {QEvent.Hide, QEvent.ParentChange, QEvent.Resize}:
            self.events.append(event.type())
        return False


def _stage(window) -> ModeStage:
    return next(stage for stage in window.findChildren(ModeStage) if stage.isVisible())


def test_mode_switch_keeps_page_stage_and_device(session, monkeypatch, tmp_path, qtbot):
    window, vm, gateway, _ = ready(session, monkeypatch, tmp_path)
    window.resize(1280, 820)
    qtbot.wait(350)
    scroll = window._content.findChild(QScrollArea, "overviewScroll")
    stage = _stage(window)
    device = stage.device
    geometry = device.geometry()
    observer = ModelEvents(device)
    device.installEventFilter(observer)

    for mode in ("codex", "normal") * 2:
        window._select_configuration_mode(mode)
        assert scroll.widget() is stage, "Changing Mode rebuilt the whole configuration page"
        assert _stage(window) is stage and stage.device is device
        assert stage.state().word == ("CODEX" if mode == "codex" else "NORMAL")
        assert device.geometry() == geometry
        qtbot.wait(150)
        assert device.geometry() == geometry
        assert device.selected() is None
        assert vm.model.snapshot.status["operating_mode"] == "normal"
    assert observer.events == [], "Mode switching hid, reparented, or resized the device"
    assert not device_writes(gateway)


def test_pending_mapping_becomes_draft_without_replacing_device(session, monkeypatch, tmp_path, qtbot):
    window, vm, gateway, _ = ready(session, monkeypatch, tmp_path)
    window._select_physical_control("key.8")
    device = _stage(window).device
    action = {"type": "key", "usage": 14, "modifiers": [227]}
    current_editor(window).set_action(action)
    window._select_configuration_mode("codex")
    assert _stage(window).device is device, "Saving the local draft replaced the device"
    assert vm.draft.mapping(0, "key.8")["action"] == action
    window._select_configuration_mode("normal")
    assert _stage(window).device is device
    assert current_editor(window).action() == action
    assert device.selected() == "key.8"
    assert window._selected_control_id == "key.8"
    assert not device_writes(gateway)


def test_retained_device_labels_follow_configuration_mode(session, monkeypatch, tmp_path, qtbot):
    window, _, _, _ = ready(session, monkeypatch, tmp_path)
    device = _stage(window).device
    normal_labels = device.labels()
    # design decision: the Claude Code tab is gone; Codex has dedicated labels too.
    # The labels are also the device's tooltips (ModeDeviceView.event).
    window._select_configuration_mode("codex")
    codex_labels = device.labels()
    assert codex_labels["key.3"] != normal_labels["key.3"]
    assert codex_labels["key.3"] == "Fork"
    window._select_configuration_mode("normal")
    assert _stage(window).device is device
    assert device.labels() == normal_labels


def test_rapid_mode_switch_after_draft_commit_uses_current_context(session, monkeypatch, tmp_path):
    window, vm, gateway, _ = ready(session, monkeypatch, tmp_path)
    window._select_physical_control("key.8")
    current_editor(window).set_action({"type": "key", "usage": 14, "modifiers": [227]})
    for mode, word in (("codex", "CODEX"), ("normal", "NORMAL")):
        window._select_configuration_mode(mode)
        stages = [stage for stage in window._content.findChildren(ModeStage) if stage.isVisible()]
        assert len(stages) == 1, "A pending-deletion stage remains in the active workspace"
        assert stages[0].state().word == word
        # The device keeps running normal mode; only the screen on show changed.
        assert stages[0].state().active == window._mode_screens().index(("normal", vm.draft.config["active_profile"]))
        assert stages[0].bar.state().profile_name == ("Codex" if mode == "codex" else
                                                     vm.draft.profile(vm.draft.config["active_profile"])["name"])
    assert vm.draft.is_dirty
    assert not device_writes(gateway)
