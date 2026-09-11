from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QFileDialog, QLabel, QMessageBox

from controller_config.background_helper import PromptBackgroundController
from controller_config.automation import AutomationStore
from controller_config.config_files import load_config_package
from controller_config.i18n import ENGLISH, SIMPLIFIED_CHINESE, LanguageManager, translate_ui_text
from controller_config.models import AppState
from controller_config.prompt_library import PromptEntry, PromptLibrary, PromptLibraryStore
from controller_config.protocol.bootstrap import BootstrapError, BootstrapKind
from controller_config.transactions import ConfigTransaction, ConfigTransactionState
from controller_config.transport.demo import _power_v2_snapshot
from controller_config.viewmodels.main import MainViewModel
from controller_config.views.main_window import MainWindow
from controller_config.views.prompt_library_editor import PromptLibraryEditor
from controller_config.workflows import WorkflowStore
from test_write_transaction import FakeWriteGateway, _ack


@pytest.fixture
def session(qtbot, qapp, contract, tmp_path):
    gateway = FakeWriteGateway()
    store = PromptLibraryStore(tmp_path / "prompts")
    view_model = MainViewModel(
        gateway, contract, prompt_library_store=store,
        workflow_store=WorkflowStore(tmp_path / "workflows"),
        automation_store=AutomationStore(tmp_path / "automations"),
    )
    snapshot = _power_v2_snapshot(contract, read_only=False)
    gateway.snapshot_ready.emit(snapshot)
    language = LanguageManager(
        qapp,
        settings=QSettings(str(tmp_path / "language.ini"), QSettings.Format.IniFormat),
        initial_language=SIMPLIFIED_CHINESE,
    )
    window = MainWindow(view_model, language_manager=language)
    # The application-wide translator and Show event filter must leave with
    # this test window, rather than accumulate on the session-scoped qapp.
    language.setParent(window)

    def cleanup_window(_window):
        # pytest-qt closes widgets before fixture finalizers run.
        view_model._write_transaction = ConfigTransaction()
        view_model.navigate("overview")
        # The fake gateway never answers automatic icon reads. Detach those
        # readers before clearing the draft during teardown.
        view_model.screen_icon.attach(None)
        view_model.screen_glyphs.attach(None)
        if view_model.draft is not None and view_model.draft.is_dirty:
            view_model.discard_draft()

    qtbot.addWidget(window, before_close_func=cleanup_window)
    yield window, view_model, gateway, snapshot, store
    view_model.shutdown()


def _editor(window):
    return window._content.findChild(PromptLibraryEditor)


def test_prompt_edits_do_not_follow_another_device(session):
    window, view_model, gateway, snapshot, store = session
    serial_b = "CP01-001122334455"
    assert serial_b != snapshot.identity["serial"]
    store.save(PromptLibrary(serial_b, draft=(PromptEntry(1, "B name", "B body"),)))
    view_model.navigate("prompts")
    editor_a = _editor(window)
    editor_a._select_direction(3)
    editor_a._name.setText("A unsaved")
    editor_a._body.setPlainText("A private text")

    gateway.disconnected.emit("device A unplugged")
    gateway.snapshot_ready.emit(replace(snapshot, identity={**snapshot.identity, "serial": serial_b}))

    editor_b = _editor(window)
    assert editor_b._selected_prompt_id() == 1
    assert editor_b._name.text() == "B name"
    assert editor_b._body.toPlainText() == "B body"
    # Follow the actual save callback: A's text must not be written into B's library.
    editor_b._save()
    assert store.load(serial_b).draft_entry(1) == PromptEntry(1, "B name", "B body")


def test_prompt_edits_survive_same_device_reconnection(session):
    window, view_model, gateway, snapshot, _store = session
    view_model.navigate("prompts")
    editor = _editor(window)
    editor._select_direction(3)
    editor._name.setText("Same device")
    editor._body.setPlainText("中文草稿\nEnglish text")

    gateway.disconnected.emit("unplugged")
    gateway.snapshot_ready.emit(snapshot)

    restored = _editor(window)
    assert restored._selected_prompt_id() == 3
    assert restored._name.text() == "Same device"
    assert restored._body.toPlainText() == "中文草稿\nEnglish text"


def _disconnect_during_write(view_model, gateway):
    view_model.rename_profile(0, "Unresolved candidate")
    view_model.prepare_device_write()
    gateway.command_completed.emit("VALIDATE_CONFIG", _ack("VALIDATE_CONFIG"))
    view_model.confirm_device_write()
    gateway.disconnected.emit("unplugged during SET_CONFIG")
    view_model._on_write_deadline()
    gateway.command_failed.emit(
        "GET_CONFIG", BootstrapError(BootstrapKind.READ_FAILED, "设备已离线", "no readback")
    )
    assert view_model.model.state is AppState.DISCONNECTED
    assert view_model.write_transaction.state is ConfigTransactionState.UNKNOWN


def _choose_export(monkeypatch):
    real_exec = QMessageBox.exec

    def choose(prompt):
        button = next(button for button in prompt.buttons() if "导出" in button.text())
        QTimer.singleShot(50, button.click)
        return real_exec(prompt)

    monkeypatch.setattr(QMessageBox, "exec", choose)
    monkeypatch.setattr(QMessageBox, "information", lambda *_args: QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: QMessageBox.Cancel)


def test_offline_unknown_write_can_export_and_exit_without_replaying(
    session, contract, tmp_path, monkeypatch
):
    window, view_model, gateway, snapshot, _store = session
    _disconnect_during_write(view_model, gateway)
    transaction = view_model.write_transaction
    path = tmp_path / "unresolved.boring-config.json"
    _choose_export(monkeypatch)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_args: (str(path), ""))
    shutdowns = []
    monkeypatch.setattr(gateway, "shutdown", lambda: shutdowns.append(True))
    commands = list(gateway.commands)

    event = QCloseEvent()
    window.closeEvent(event)

    assert event.isAccepted()
    assert shutdowns == [True]
    assert gateway.commands == commands
    assert view_model.write_transaction == transaction
    raw = json.loads(path.read_text())
    assert raw["kind"] == "draft"
    assert raw["unresolved_write"]["device_serial"] == snapshot.identity["serial"]
    assert raw["unresolved_write"]["state"] == "unknown"
    assert raw["unresolved_write"]["candidate_digest"] == transaction.candidate_digest
    assert raw["unresolved_write"]["base_generation"] == transaction.base_generation
    assert raw["unresolved_write"]["base_digest"] == transaction.base_digest
    assert raw["config"] == transaction.candidate_config
    # Keep using the existing draft import flow; exporting is not an automatic retry.
    package = load_config_package(path, contract, current_hardware_id=view_model.draft.hardware_id)
    assert package.kind == "draft"
    assert package.config == transaction.candidate_config


@pytest.mark.parametrize("export_result", ["cancel", "error"])
def test_unresolved_exit_stays_open_if_export_is_cancelled_or_fails(
    session, tmp_path, monkeypatch, export_result
):
    window, view_model, gateway, _snapshot, _store = session
    _disconnect_during_write(view_model, gateway)
    transaction = view_model.write_transaction
    _choose_export(monkeypatch)
    path = "" if export_result == "cancel" else str(tmp_path / "missing" / "draft.json")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_args: (path, ""))
    shutdowns = []
    monkeypatch.setattr(gateway, "shutdown", lambda: shutdowns.append(True))
    commands = list(gateway.commands)

    event = QCloseEvent()
    window.closeEvent(event)

    assert not event.isAccepted()
    assert not shutdowns
    assert view_model.write_transaction == transaction
    assert gateway.commands == commands


def test_unresolved_exit_cancel_keeps_transaction(session, monkeypatch):
    window, view_model, gateway, _snapshot, _store = session
    _disconnect_during_write(view_model, gateway)
    transaction = view_model.write_transaction
    _choose_export(monkeypatch)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda _prompt: None)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_args: pytest.fail("Must not export"))

    event = QCloseEvent()
    window.closeEvent(event)

    assert not event.isAccepted()
    assert view_model.write_transaction == transaction


def test_offline_unknown_can_hide_to_background_then_explicitly_quit(
    session, qapp, tmp_path, monkeypatch
):
    window, view_model, gateway, _snapshot, _store = session
    _disconnect_during_write(view_model, gateway)
    controller = PromptBackgroundController(
        qapp,
        shutdown=view_model.shutdown,
        settings=QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat),
        platform="linux",
    )
    controller._native_status_item = SimpleNamespace(hide=lambda: None, set_tooltip=lambda _text: None)
    controller.attach_window(window)
    window._background_controller = controller
    previous_quit_policy = qapp.quitOnLastWindowClosed()
    # Match the real tray setup without creating a native system status item.
    qapp.setQuitOnLastWindowClosed(False)
    window.show()
    _choose_export(monkeypatch)
    exports = []
    monkeypatch.setattr(window, "_export_configuration", lambda kind: exports.append(kind) or True)

    assert not window.close()  # Close-to-background ignores the close event.
    assert not window.isVisible()
    assert not exports
    assert view_model.write_transaction.state is ConfigTransactionState.UNKNOWN

    quits = []
    monkeypatch.setattr(qapp, "quit", lambda: quits.append(True))
    controller.quit_application()
    assert quits == [True]
    assert exports == ["draft"]
    assert controller.quitting
    window._background_controller = None
    qapp.setQuitOnLastWindowClosed(previous_quit_policy)


@pytest.mark.parametrize("readback_finished", [False, True])
def test_reconnection_during_export_does_not_interrupt_readback(session, monkeypatch, readback_finished):
    window, view_model, gateway, _snapshot, _store = session
    _disconnect_during_write(view_model, gateway)
    _choose_export(monkeypatch)
    shutdowns = []
    monkeypatch.setattr(gateway, "shutdown", lambda: shutdowns.append(True))

    def export_during_readback(_kind):
        view_model._request_config_readback("设备已重连，正在读取配置")
        if readback_finished:
            view_model._set(replace(view_model.model, state=AppState.READY))
            view_model._finish_write(ConfigTransactionState.UNKNOWN, "读回结果仍未知")
        return True

    monkeypatch.setattr(window, "_export_configuration", export_during_readback)
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted()
    assert not shutdowns
    expected_state = ConfigTransactionState.UNKNOWN if readback_finished else ConfigTransactionState.VERIFYING
    assert view_model.write_transaction.state is expected_state
    assert [command.name for command in gateway.commands].count("SET_CONFIG") == 1


@pytest.mark.parametrize("already_applied", [False, True])
def test_reopen_and_import_unresolved_draft_never_replays_a_write(
    session, contract, tmp_path, already_applied
):
    _window, view_model, gateway, snapshot, _store = session
    _disconnect_during_write(view_model, gateway)
    transaction = view_model.write_transaction
    path = tmp_path / "recovery.boring-config.json"
    view_model.export_configuration(path, kind="draft")
    if already_applied:
        active = {"generation": transaction.base_generation + 1, "digest": transaction.candidate_digest}
        snapshot = replace(
            snapshot,
            config_result={**active, "config": transaction.candidate_config},
            status={**snapshot.status, "active": active, "pending": None},
        )
    fresh_gateway = FakeWriteGateway()
    fresh = MainViewModel(fresh_gateway, contract, prompt_library_store=PromptLibraryStore(tmp_path / "restart"))
    try:
        fresh_gateway.snapshot_ready.emit(snapshot)
        fresh.apply_configuration_import(fresh.read_configuration_file(path))
        assert fresh.draft.is_dirty is not already_applied
        assert fresh.write_transaction.state is ConfigTransactionState.IDLE
        assert "SET_CONFIG" not in [command.name for command in fresh_gateway.commands]
    finally:
        fresh.shutdown()


@pytest.mark.parametrize("b_has_candidate", [False, True])
def test_another_device_cannot_resolve_or_relabel_unresolved_write(
    session, tmp_path, monkeypatch, b_has_candidate
):
    window, view_model, gateway, snapshot, _store = session
    _disconnect_during_write(view_model, gateway)
    transaction = view_model.write_transaction
    serial_b = "CP01-001122334455"
    device_b = replace(snapshot, identity={**snapshot.identity, "serial": serial_b})
    if b_has_candidate:
        active = {"generation": transaction.base_generation + 1, "digest": transaction.candidate_digest}
        device_b = replace(
            device_b,
            status={**device_b.status, "active": active, "pending": None},
            config_result={**active, "config": transaction.candidate_config},
        )
    gateway.snapshot_ready.emit(device_b)
    gateway.status_updated.emit(device_b.status)
    assert view_model.write_transaction.state is ConfigTransactionState.UNKNOWN
    assert view_model.draft.serial == serial_b

    path = tmp_path / "original-device.boring-config.json"
    _choose_export(monkeypatch)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_args: (str(path), ""))
    event = QCloseEvent()
    window.closeEvent(event)

    assert event.isAccepted()
    raw = json.loads(path.read_text())
    assert raw["unresolved_write"]["device_serial"] == snapshot.identity["serial"]
    assert raw["config"] == transaction.candidate_config
    assert raw["base_generation"] == transaction.base_generation
    assert raw["base_digest"] == transaction.base_digest
    assert [command.name for command in gateway.commands].count("SET_CONFIG") == 1


@pytest.mark.parametrize("language", [SIMPLIFIED_CHINESE, ENGLISH])
def test_unresolved_exit_dialog_explains_unknown_result_in_both_languages(
    session, qapp, tmp_path, language
):
    window, view_model, gateway, _snapshot, _store = session
    _disconnect_during_write(view_model, gateway)
    window._language_manager.set_language(language)
    observed = []

    def inspect_dialog():
        dialog = qapp.activeModalWidget()
        try:
            assert isinstance(dialog, QMessageBox)
            label = dialog.findChild(QLabel, "qt_msgbox_label")
            observed.append((label.text(), [button.text() for button in dialog.buttons()]))
            for button in dialog.buttons():
                assert button.width() > button.fontMetrics().horizontalAdvance(button.text())
            assert dialog.grab().save(str(tmp_path / f"unresolved-exit-{language}.png"))
        finally:
            if isinstance(dialog, QMessageBox):
                dialog.reject()

    QTimer.singleShot(100, inspect_dialog)
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted()
    assert observed
    message, buttons = observed[0]
    # macOS deliberately omits QMessageBox window titles; verify the catalog too.
    title = translate_ui_text("原设备未连接，写入结果未知")
    if language == ENGLISH:
        assert "Write Outcome Unknown" in title
        assert "does not cancel" in message
        assert "Export and Quit" in buttons
    else:
        assert "写入结果未知" in title
        assert "退出不会取消" in message
        assert "导出未决草稿后退出" in buttons
    window._language_manager.set_language(SIMPLIFIED_CHINESE)
