from dataclasses import replace

import pytest
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QMessageBox

from test_session_recovery import session


def switch_with_dirty_a(vm, gateway, snapshot):
    vm.rename_profile(0, "A unsaved")
    draft_a = vm.draft
    gateway.disconnected.emit("A unplugged")
    gateway.snapshot_ready.emit(replace(snapshot, identity={**snapshot.identity, "serial": "CP01-001122334455"}))
    assert not vm.draft.is_dirty
    return draft_a


def test_exit_detects_dirty_previous_device(session, monkeypatch):
    window, vm, gateway, snapshot, _ = session
    draft_a = switch_with_dirty_a(vm, gateway, snapshot)
    prompts = []
    monkeypatch.setattr(QMessageBox, "exec", lambda dialog: prompts.append(dialog.text()) or 0)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda dialog: None)
    try:
        event = QCloseEvent()
        window.closeEvent(event)
        assert not event.isAccepted(), "A's unsaved workspace was silently discarded"
        assert prompts and draft_a.serial in prompts[0]
        assert draft_a.is_dirty
    finally:
        draft_a.discard()


@pytest.mark.parametrize("choice", [QMessageBox.AcceptRole, QMessageBox.DestructiveRole])
def test_exit_handles_previous_device_workspace(session, monkeypatch, tmp_path, choice):
    import json
    from PySide6.QtWidgets import QFileDialog
    window, vm, gateway, snapshot, _ = session
    draft_a = switch_with_dirty_a(vm, gateway, snapshot)
    selected = []
    monkeypatch.setattr(QMessageBox, "exec", lambda dialog: selected.append(next(b for b in dialog.buttons() if dialog.buttonRole(b) == choice)) or 0)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda dialog: selected[-1])
    target = tmp_path / "a.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(target), ""))
    monkeypatch.setattr(QMessageBox, "information", lambda *args: QMessageBox.Ok)
    try:
        assert vm.pending_dirty_workspaces() == (draft_a,)
        assert window.close()  # Native window close uses the same closeEvent path.
        if choice == QMessageBox.AcceptRole:
            assert json.loads(target.read_text())["config"]["profiles"][0]["name"] == "A unsaved"
        else:
            assert not vm.pending_dirty_workspaces()
        assert vm.draft.serial != draft_a.serial
    finally:
        draft_a.discard()
