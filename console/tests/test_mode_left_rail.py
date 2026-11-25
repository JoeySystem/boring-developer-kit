"""The Mode page's entries (set chip, Bluetooth, AI quota) fit without scrolling; the old left
rail and its "查看全部控件" dialog are gone (Mode页 定稿 v1): controls are chosen on the device."""
from copy import deepcopy
from dataclasses import replace

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtWidgets import QDialog, QLineEdit, QListWidget, QScrollArea

from controller_config.i18n import ENGLISH, JAPANESE, SIMPLIFIED_CHINESE
from controller_config.views.mode_page import ModeStage
from controller_config.transactions import ConfigTransactionState
from test_primary_setup_flow import ready, device_writes
from test_session_recovery import session


def _stage(window):
    return next(stage for stage in window.findChildren(ModeStage) if stage.isVisible())


def _inside(widget, viewport):
    return widget.isVisible() and viewport.rect().contains(QRect(widget.mapTo(viewport, QPoint()), widget.size()))


def _entries(stage):
    return {"modeProfileChip": stage.bar.profile, "modeBarBluetooth": stage.bar.ble, "modeQuota": stage.quota}


@pytest.mark.parametrize("language", [SIMPLIFIED_CHINESE, ENGLISH, JAPANESE])
@pytest.mark.parametrize("size", [(1280, 800), (1100, 700)])
def test_desktop_mode_page_entries_need_no_scroll_in_all_modes(session, qtbot, language, size):
    window, vm, gateway, *_ = session
    window._language_manager.set_language(language)
    window.resize(*size)
    before = deepcopy(vm.draft.config)
    for mode in ("normal", "codex"):
        window._select_configuration_mode(mode)
        scroll = window.findChild(QScrollArea, "overviewScroll")
        qtbot.waitUntil(lambda: scroll.verticalScrollBar().maximum() == 0, timeout=1500)
        stage = _stage(window)
        assert stage.findChild(QListWidget) is None
        for name, entry in _entries(stage).items():
            assert entry.objectName() == name
            assert _inside(entry, scroll.viewport()), (mode, name)
        bar = stage.bar
        assert bar.profile_label.text() and bar.profile.width() >= bar.profile_label.width()
        assert not bar.profile.geometry().intersects(bar.ble.geometry())
    assert vm.draft.config == before and not device_writes(gateway)


@pytest.mark.parametrize("mode", ["normal", "codex"])
def test_control_picker_uses_configuration_not_runtime_mode(session, monkeypatch, tmp_path, qtbot, mode):
    window, vm, gateway, snapshot = ready(session, monkeypatch, tmp_path)
    window._select_configuration_mode(mode)
    before = deepcopy(vm.draft.config)
    device = _stage(window).device
    qtbot.mouseClick(device, Qt.LeftButton, pos=device.key_rect("key.3").center().toPoint())
    assert window._current_mapping_card().property("controlId") == "key.3"
    assert _stage(window).card() is window._current_mapping_card()
    assert vm.model.snapshot.status == snapshot.status
    assert vm.draft.config == before and not device_writes(gateway)


def test_small_screen_mode_page_entries_remain_reachable(session, qtbot):
    window, vm, gateway, *_ = session
    window._fit_window_to_available_area(QRect(0, 0, 960, 640))
    window.resize(960, 640)
    qtbot.wait(350)
    scroll = window.findChild(QScrollArea, "overviewScroll")
    for name, entry in _entries(_stage(window)).items():
        scroll.ensureWidgetVisible(entry)
        qtbot.waitUntil(lambda: _inside(entry, scroll.viewport()), timeout=1500)
    assert not device_writes(gateway)


def test_opening_the_quota_does_not_change_pending_input(session, qtbot):
    # AI 额度 grows in place now (Mode页 定稿 v1, 07); the old quota window is gone.
    window, vm, gateway, *_ = session
    window._select_physical_control("key.9")
    name = window.findChild(QLineEdit, "mappingShortNameEditor")
    name.setText("保留编辑")
    before = deepcopy(vm.draft.config)
    stage = next(s for s in window.findChildren(ModeStage) if s.isVisible())
    stage.quota.set_open(True, animate=False)
    assert stage.quota.is_open() and window.findChild(QDialog, "codexUsageDialog") is None
    stage.quota.set_open(False, animate=False)
    assert window.findChild(QLineEdit, "mappingShortNameEditor") is name
    assert name.text() == "保留编辑"
    assert vm.draft.config == before and not device_writes(gateway)


def test_device_labels_track_draft_and_choosing_waits_for_transaction(session, qtbot):
    window, vm, gateway, *_ = session
    vm.navigate("overview")
    vm.set_mapping(0, "key.9", "更新后的功能", {"type": "key", "usage": 14})
    assert _stage(window).device.labels()["key.9"] == "更新后的功能"
    vm._write_transaction = replace(vm.write_transaction, state=ConfigTransactionState.VALIDATING)
    vm.changed.emit(vm.model)
    _stage(window).device.choose("key.9")
    assert window._selected_control_id is None and _stage(window).card() is None
    vm._write_transaction = replace(vm.write_transaction, state=ConfigTransactionState.IDLE)
    vm.changed.emit(vm.model)
    _stage(window).device.choose("key.9")
    assert window._current_mapping_card().property("controlId") == "key.9"
    vm.set_mapping(0, "key.9", "关闭后继续编辑", {"type": "key", "usage": 15})
    assert vm.draft.mapping(0, "key.9")["short_name"] == "关闭后继续编辑"
    assert not device_writes(gateway)
