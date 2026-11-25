"""Task entry points stay aligned with real configuration and update states."""
from copy import deepcopy
from dataclasses import replace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QLabel, QLineEdit, QMenu, QPushButton, QWidget

from controller_config.models import AppState
from controller_config.transactions import ConfigTransactionState
from controller_config.firmware_update import FirmwareUpdateState
from controller_config.views.action_editor import ActionEditor
from controller_config.views.mode_page import ModeStage
from test_primary_setup_flow import ready, device_writes
from test_session_recovery import session
from controller_config.views.ui import notice


def _stage(window):
    return next(stage for stage in window.findChildren(ModeStage) if stage.isVisible())


@pytest.mark.parametrize("mode", ["codex", "normal"])
def test_keyboard_flip_targets_configuration_without_switching_device(session, qtbot, mode):
    window, vm, gateway, snapshot, _ = session
    before = deepcopy(vm.draft.config)
    if mode == "normal":
        window._select_configuration_mode("codex")
    # Keyboard users flip screens with the arrow keys; the last screen is Codex.
    key = Qt.Key_Right if mode == "codex" else Qt.Key_Left
    for _ in range(len(window._mode_screens())):
        if window._configuration_mode == mode:
            break
        qtbot.keyClick(_stage(window), key)
    assert window._configuration_mode == mode
    assert _stage(window).state().word == mode.upper()
    assert vm.model.snapshot.status == snapshot.status
    assert vm.draft.config == before and not device_writes(gateway)


def test_device_choice_selects_same_control_as_model_without_saving(session, monkeypatch, tmp_path, qtbot):
    window, vm, gateway, _ = ready(session, monkeypatch, tmp_path)
    before = deepcopy(vm.draft.config)
    device = _stage(window).device
    assert device.labels()["key.9"]
    qtbot.mouseClick(device, Qt.LeftButton, pos=device.key_rect("key.9").center().toPoint())
    assert window._selected_control_id == "key.9"
    assert window._current_mapping_card().property("controlId") == "key.9"
    assert _stage(window).device.selected() == "key.9"
    window._select_physical_control("encoder.cw")
    assert window._current_mapping_card().property("controlId") == "encoder.cw"
    assert _stage(window).device.selected() is None   # the knob has no ring yet (batch 1)
    assert vm.draft.config == before and not device_writes(gateway)


def test_shortcut_entry_moves_forward_and_has_cancel(session, monkeypatch, tmp_path, qtbot):
    window, vm, gateway, _ = ready(session, monkeypatch, tmp_path)
    window._begin_shortcut_binding()
    assert window._shortcut_task_requested
    banner = window._banners.find("shortcutTask")
    assert banner is not None and "点一下设备上要设置的键" in banner.text()
    assert not any(b.isVisible() for b in window.findChildren(QPushButton, "startShortcutBinding"))
    _stage(window).device.choose("key.9")
    assert window._current_mapping_card().findChild(QLabel, "mappingTaskProgress") is not None
    _stage(window).blank_clicked.emit()   # closing the card ends the task
    assert not window._shortcut_task_requested
    assert not device_writes(gateway)


def test_shortcut_task_can_be_cancelled_before_a_key_is_chosen(session, monkeypatch, tmp_path, qtbot):
    # The old flow had a 取消 button (cancelShortcutTask) next to its first step.
    window, vm, gateway, _ = ready(session, monkeypatch, tmp_path)
    window._begin_shortcut_binding()
    window._banners.find("shortcutTask").dismiss()
    assert not window._shortcut_task_requested
    window._select_physical_control("key.9")
    assert window._current_mapping_card().findChild(QLabel, "mappingTaskProgress") is None
    assert not device_writes(gateway)


def test_fixed_mode_status_and_ai_profile_entry_are_not_editable_claims(session, monkeypatch):
    window, vm, gateway, *_ = session
    # design decision: the fixed Claude Code mode is no longer offered for editing.
    window._select_configuration_mode("claude_code")
    assert window._configuration_mode == "normal"
    bar = _stage(window).bar
    assert "固定功能" not in bar.status_text.text()
    # The set chip opens 全部方案 (Mode页 定稿 v1, 10); the AI entry is 新建一屏 › AI 模板.
    bar.profile.click()
    overview = _stage(window).overview
    assert _stage(window).overview_shown()
    new = overview.cards[-1]
    assert new.screen is None
    shown = []
    import controller_config.views.main_window as main_window_module

    class RecordingMenu(QMenu):   # PySide6 ignores QMenu.exec patched on the class itself
        def exec(self, *_args, **_kwargs):  # noqa: A003 - Qt's name
            shown.append(self)

    monkeypatch.setattr(main_window_module, "QMenu", RecordingMenu)
    overview.new_requested.emit(new)
    assert len(shown) == 1 and shown[0].objectName() == "modeNewScreenMenu"
    assert shown[0].findChild(QMenu, "modeAiTemplateMenu") is not None
    names = {action.objectName() for action in shown[0].findChildren(QAction)}
    assert "chooseCommonAI" in names or any(name.startswith("commonAI_") for name in names)
    assert not device_writes(gateway)


def test_updates_home_separates_software_and_firmware_and_does_not_install(session):
    window, vm, gateway, *_ = session
    commands = list(gateway.commands)
    # 从一级导航进来现在先落在第一组（没有更新提醒时）；这条测的是「更新」这一组本身。
    window._select_settings_section("updates")
    assert vm.page == "settings" and window._settings_section == "updates"
    assert window.findChild(QLabel, "consoleVersion") is not None
    assert window.findChild(QWidget, "firmwareOverviewCard") is not None
    assert gateway.commands == commands
    window.findChild(QPushButton, "openFirmwareUpdates").click()
    assert vm.page == "firmware"
    assert not vm.firmware_update.is_busy and not device_writes(gateway)


def test_updates_stay_available_offline_and_explain_last_firmware_read(session):
    window, vm, gateway, *_ = session
    gateway.disconnected.emit("unplugged")
    window._select_settings_section("updates")
    assert vm.model.state is AppState.DISCONNECTED
    assert window.findChild(QWidget, "firmwareOverviewCard") is not None
    notes = [label.text() for label in window._content.findChildren(QLabel)]
    assert any("上次读取" in text for text in notes)
    assert not device_writes(gateway)


@pytest.mark.parametrize("supported", [False, True])
def test_calibration_entry_respects_capability_without_starting_calibration(session, supported):
    window, vm, gateway, snapshot, _ = session
    gateway.snapshot_ready.emit(replace(snapshot, capabilities={**snapshot.capabilities,
        "features": {**snapshot.capabilities["features"], "joystick_calibration": supported}}))
    window._select_settings_section("device")
    button = window.findChild(QPushButton, "openJoystickCalibration")
    assert button is not None and button.isEnabled() is supported
    assert window.findChild(QLabel, "joystickCalibrationAvailability").text()
    assert not device_writes(gateway)


def test_connection_details_are_secondary_but_remain_available(session, monkeypatch):
    window, vm, gateway, *_ = session
    # Not on the Mode page any more; 设置 › 设备与连接 keeps the summary and the details.
    assert window._content.findChild(QLabel, "deviceLinkState") is None
    window._select_settings_section("device")
    assert window.findChild(QLabel, "deviceLinkState").text()
    shown = []
    monkeypatch.setattr(notice, "alert", lambda _parent, _title, text, **_kw: shown.append(text))
    window.findChild(QPushButton, "deviceDetailsToggle").click()
    assert len(shown) == 1 and shown[0]
    assert not device_writes(gateway)


def test_all_save_summary_names_modes_and_preserves_other_drafts(session, monkeypatch, tmp_path):
    window, vm, gateway, _ = ready(session, monkeypatch, tmp_path)
    vm.set_mapping(0, "key.9", "我的软件", {"type": "key", "usage": 14})
    vm.set_codex_voice(0, {"type": "key", "usage": 104})
    summary = "\n".join(window._draft_change_summary())
    assert "普通" in summary and "9" in summary and "Codex" in summary
    assert "我的软件" in summary
    assert "config.profiles" not in summary
    assert vm.draft.is_dirty and not device_writes(gateway)
    assert vm.write_transaction.state is ConfigTransactionState.IDLE


def test_summary_includes_current_editor_and_retained_draft_without_mutating_either(session, monkeypatch, tmp_path):
    window, vm, gateway, _ = ready(session, monkeypatch, tmp_path)
    vm.set_mapping(0, "key.9", "已保留", {"type": "key", "usage": 14})
    window._select_control("key.10")
    card = window._current_mapping_card()
    card.findChild(QLineEdit, "mappingShortNameEditor").setText("当前编辑")
    card.findChild(ActionEditor).set_action({"type": "key", "usage": 15})
    before = deepcopy(vm.draft.config)
    commands = list(gateway.commands)
    summary = "\n".join(window._draft_change_summary())
    assert "已保留" in summary and "当前编辑" in summary
    assert "9" in summary and "10" in summary
    assert vm.draft.config == before and gateway.commands == commands
    assert window._mapping_editor_has_uncommitted_changes()


def test_summary_reports_incomplete_editor_without_mutating_draft(session, monkeypatch, tmp_path):
    window, vm, gateway, _ = ready(session, monkeypatch, tmp_path)
    vm.set_mapping(0, "key.9", "已保留", {"type": "key", "usage": 14})
    window._select_control("key.10")
    editor = window._current_mapping_card().findChild(ActionEditor)
    monkeypatch.setattr(editor, "action", lambda: (_ for _ in ()).throw(ValueError("输入未完成")))
    before = deepcopy(vm.draft.config)
    assert "输入尚未完成" in "\n".join(window._draft_change_summary())
    assert vm.draft.config == before and not device_writes(gateway)


def test_summary_restoring_codex_default_does_not_claim_unmapped(session, monkeypatch, tmp_path):
    window, vm, gateway, _ = ready(session, monkeypatch, tmp_path, mode="codex",
                                    codex_action={"type": "key", "usage": 104})
    vm.set_codex_voice(0, {"type": "none"})
    summary = "\n".join(window._draft_change_summary())
    assert "Codex 自带语音" in summary and "未映射" not in summary
    assert not device_writes(gateway)


@pytest.mark.parametrize("state", [FirmwareUpdateState.WAITING_RECONNECT, FirmwareUpdateState.VERIFYING])
def test_firmware_overview_keeps_transaction_progress_during_disconnect(session, state, qtbot):
    window, vm, gateway, *_ = session
    gateway.disconnected.emit("updating")
    original = vm.firmware_update
    try:
        vm._firmware.set_transaction(replace(original, state=state, message="正在等待固件版本确认"))
        # Exercise the overview projection, not forbidden navigation while a
        # maintenance transaction owns the firmware page.
        page = window._updates_wall()
        qtbot.addWidget(page)
        assert not hasattr(window, "_updates_overview_page")  # 只给这条测试用的空壳已删
        assert page.findChild(QLabel, "firmwareOverviewMessage").text() == "正在等待固件版本确认"
        assert not device_writes(gateway)
    finally:
        vm._firmware.set_transaction(original)


def test_summary_button_and_menu_ignore_qt_checked_argument(session, monkeypatch, tmp_path):
    window, vm, gateway, _ = ready(session, monkeypatch, tmp_path)
    vm.set_mapping(0, "key.9", "已保留", {"type": "key", "usage": 14})
    window._select_control("key.10")
    editor = window._current_mapping_card().findChild(ActionEditor)
    monkeypatch.setattr(editor, "action", lambda: (_ for _ in ()).throw(ValueError("输入未完成")))
    shown = []
    monkeypatch.setattr(notice, "alert", lambda _parent, _title, text, **_kw: shown.append(text))
    pending = _stage(window).bar.pending
    assert pending.isVisible()
    pending.click()   # 查看本地更改 lives only on the bar's "N 处没保存 ›" now
    assert len(shown) == 1 and all("输入尚未完成" in text for text in shown)
    assert not device_writes(gateway)
