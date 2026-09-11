from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from PySide6.QtCore import QTimer, Qt, QSettings
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QPushButton, QMessageBox
from shiboken6 import isValid

from controller_config.background_helper import PromptBackgroundController
from controller_config.protocol.bootstrap import StatusSession
from controller_config.protocol.framing import Frame, RESPONSE_FLAG
from controller_config.transport.qt_serial import SerialWorker

from controller_config.transport.demo import DemoGateway
from controller_config.viewmodels.main import MainViewModel
from controller_config.views.main_window import MainWindow


def test_profile_menu_survives_codex_traffic_counter_update(qtbot, contract):
    model = MainViewModel(DemoGateway(contract, "ready"), contract)
    window = MainWindow(model)
    qtbot.addWidget(window)
    window.show()
    model.start()
    qtbot.waitUntil(lambda: model.draft is not None)
    qtbot.wait(400)
    button = window.findChild(QPushButton, "profilePill")
    menu = button.menu()
    observed = []

    def update_traffic():
        status = deepcopy(model.model.snapshot.status)
        status.setdefault("codex_micro", {})["rx_output_reports"] = 109
        model._on_status_updated(status)

    def inspect_menu():
        observed.append(isValid(menu) and menu.isVisible())
        if isValid(menu):
            menu.close()

    QTimer.singleShot(50, update_traffic)
    QTimer.singleShot(150, inspect_menu)
    qtbot.mouseClick(button, Qt.LeftButton)
    qtbot.waitUntil(lambda: bool(observed))
    assert observed == [True]
    assert model.model.snapshot.status["codex_micro"]["rx_output_reports"] == 109


def test_native_quit_action_uses_draft_cancel_and_discard(qtbot, qapp, contract, tmp_path, monkeypatch):
    model = MainViewModel(DemoGateway(contract, "ready"), contract)
    background = PromptBackgroundController(qapp, shutdown=model.shutdown,
        settings=QSettings(str(tmp_path / "ui.ini"), QSettings.IniFormat), platform="linux")
    window = MainWindow(model, background_controller=background)
    background.attach_window(window)
    qtbot.addWidget(window)
    window.show()
    model.start()
    qtbot.waitUntil(lambda: model.draft is not None)
    model.create_profile()
    quits = []
    prompts = []
    monkeypatch.setattr(qapp, "quit", lambda: quits.append(True))
    decision = [QMessageBox.RejectRole]
    real_exec = QMessageBox.exec

    def answer(dialog):
        prompts.append(dialog.windowTitle())
        chosen = next(b for b in dialog.buttons() if dialog.buttonRole(b) == decision[0])
        QTimer.singleShot(50, chosen.click)
        return real_exec(dialog)

    monkeypatch.setattr(QMessageBox, "exec", answer)
    action = window.findChild(QAction, "quitApplicationAction")
    try:
        assert action is not None
        assert action.menuRole() == QAction.QuitRole
        assert action.shortcut() == QKeySequence(QKeySequence.Quit)
        action.trigger()
        assert prompts and model.draft.is_dirty
        assert not quits and not background.quitting
        decision[0] = QMessageBox.DestructiveRole
        action.trigger()
        assert quits == [True]
        assert background.quitting
        assert not model.draft.is_dirty
    finally:
        model.discard_draft()


def test_status_timeout_retries_once_then_accepts_reply(contract, load_fixture):
    sent, done, errors = [], [], []
    session = StatusSession(contract=contract, send=lambda cmd, rid: sent.append((cmd, rid)),
        completed=done.append, failed=errors.append, request_id=7)
    session.start()
    session.timeout()
    assert not errors and not session.finished
    assert len(sent) == 2
    payload = load_fixture("status-matrix12-power-v2-v1.json")
    session.accept(Frame(1, 0, 0x7E, RESPONSE_FLAG, 7, json.dumps(payload).encode()))
    assert len(done) == 1 and not errors
    session.timeout()
    assert len(sent) == 2


def test_status_timeout_does_not_retry_forever(contract):
    sent, errors = [], []
    session = StatusSession(contract=contract, send=lambda cmd, rid: sent.append(rid),
        completed=lambda _: None, failed=errors.append, request_id=8)
    session.start()
    session.timeout()
    assert not errors
    session.timeout()
    assert session.finished and len(errors) == 1 and sent == [8, 8]


@pytest.mark.parametrize("field,value", [("active_slot", 2), ("ble_connected", True), ("slots", [{"slot": 1, "paired": True}])])
def test_visible_bluetooth_changes_still_refresh_page(qtbot, contract, field, value):
    model = MainViewModel(DemoGateway(contract, "ready"), contract)
    model.start()
    qtbot.waitUntil(lambda: model.draft is not None)
    updates = []
    model.changed.connect(updates.append)
    status = deepcopy(model.model.snapshot.status)
    status.setdefault("codex_micro", {})[field] = value
    model._on_status_updated(status)
    assert len(updates) == 1


def test_status_retry_keeps_port_and_queued_write_until_reply(qtbot, contract, load_fixture, monkeypatch):
    from controller_config.protocol.bootstrap import Command
    worker = SerialWorker(contract)
    worker._serial = SimpleNamespace(isOpen=lambda: True)
    sent, failures, closed = [], [], []
    worker.failure.connect(lambda *args: failures.append(args))
    monkeypatch.setattr(worker, "close", lambda: closed.append(True))
    monkeypatch.setattr(worker, "_send_command", lambda cmd, rid: sent.append((cmd, rid)))
    worker._poll_status()
    rid = sent[0][1]
    worker.execute_command(Command("SET_CONFIG", 0x12, {}))
    worker._on_request_timeout()
    assert not failures and not closed
    assert [cmd.name for cmd, _ in sent] == ["GET_STATUS", "GET_STATUS"]
    payload = load_fixture("status-matrix12-power-v2-v1.json")
    worker._session.accept(Frame(1, 0, 0x7E, RESPONSE_FLAG, rid, json.dumps(payload).encode()))
    assert sent[-1][0].name == "SET_CONFIG"
    assert not failures and not closed


def test_status_invalid_reply_is_not_retried(contract):
    sent, errors = [], []
    session = StatusSession(contract=contract, send=lambda cmd, rid: sent.append(rid),
        completed=lambda _: None, failed=errors.append, request_id=9)
    session.start()
    session.accept(Frame(1, 0, 0x7E, RESPONSE_FLAG, 9, b'{}'))
    assert errors and session.finished
    session.timeout()
    assert sent == [9]
