import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QPushButton, QScrollArea, QWidget

from controller_config.views.device_silhouette import KeycapButton
from controller_config.views.preferences_editor import PreferencesEditor, RgbButton
from test_session_recovery import session


@pytest.fixture(autouse=True)
def restore_language(session):
    yield
    session[0]._language_manager.set_language("zh_CN")


@pytest.mark.parametrize("width,language", [(1440, "zh_CN"), (1280, "zh_CN"), (1280, "en_US")])
def test_lighting_workspace_layout_preview_and_controls(session, qtbot, tmp_path, width, language):
    window, vm, gateway, _snapshot, _store = session
    window._language_manager.set_language(language)
    window.resize(width, 940 if width == 1440 else 820)
    window.show()
    vm.navigate("lighting")
    qtbot.wait(150)
    editor = window.findChild(PreferencesEditor)
    nav = window._nav_buttons["lighting"]
    expected_name = "Appearance & Feedback" if language == "en_US" else "外观与反馈"
    assert nav.text() == nav.accessibleName() == expected_name
    assert nav.width() >= nav.fontMetrics().horizontalAdvance(expected_name) + nav.iconSize().width() + 24
    assert nav.toolTip() == (
        "Appearance & Feedback · Lighting, Haptics, Display"
        if language == "en_US" else "外观与反馈 · 灯光、震动、屏幕"
    )
    for object_name, chinese, english in (
        ("lightingModuleTitle", "灯光", "Lighting"),
        ("hapticModuleTitle", "震动", "Haptics"),
        ("displayModuleTitle", "屏幕", "Display"),
    ):
        assert editor.findChild(QLabel, object_name).text() == (
            english if language == "en_US" else chinese
        )
    stage = window.findChild(QWidget, "lightingDevicePreview")
    left = window.findChild(QScrollArea, "lightingLeftScroll")
    right = window.findChild(QScrollArea, "lightingRightScroll")
    assert not editor._compact_cards
    assert left.geometry().right() < stage.geometry().left()
    assert stage.geometry().right() < right.geometry().left()
    assert left.widget().width() <= left.viewport().width()
    assert right.widget().width() <= right.viewport().width()
    assert window.findChild(QPushButton, "savePreferencesToDevice").isVisible()
    assert window.findChild(QPushButton, "expandScreenIcons").isVisible()
    assert not window.findChild(QWidget, "screenIconOptions").isVisible()
    key = next(k for k in stage.findChildren(KeycapButton) if k.property("controlId") == "key.3")
    swatch = next(b for b in editor.findChildren(RgbButton) if b.property("controlId") == "key.3")
    editor._lighting_brightness.setValue(4)
    swatch._set_rgb((230, 120, 40), emit=True)
    assert key.property("lightingColor").red() == 255
    assert key.property("lightingColor").alpha() == round(235 * (230 / 255) ** .45)
    assert window.findChild(QLabel, "preferencesSyncSummary").text() == (
        "Settings Not Applied" if language == "en_US" else "设置尚未应用到设备"
    )
    before = len(gateway.commands)
    qtbot.mouseClick(key, Qt.LeftButton)
    assert swatch._dialog is not None
    swatch._dialog.reject()
    assert len(gateway.commands) == before  # Local preview does not write hardware.
    editor._haptic_strength.setCurrentIndex(3)
    for segment in editor.findChildren(QPushButton, "lightingLevelSegment"):
        assert editor._light_card.rect().contains(segment.mapTo(editor._light_card, segment.rect().topRight()))
    assert window.grab().save(str(tmp_path / "lighting-workspace-wide.png"))
    values = editor.values()
    window.resize(1020, 800)
    qtbot.wait(100)
    assert editor._compact_cards
    assert editor.values() == values
    assert window.grab().save(str(tmp_path / "lighting-workspace-compact.png"))
    window.findChild(QPushButton, "expandScreenIcons").click()
    assert window.findChild(QWidget, "screenIconOptions").isVisible()


def test_level_segments_select_real_firmware_values(session):
    window, vm, _gateway, _snapshot, _store = session
    vm.navigate("lighting")
    editor = window.findChild(PreferencesEditor)
    segments = editor.findChildren(QPushButton, "lightingLevelSegment")
    original_brightness = editor.lighting_value()["brightness"]
    for segment, brightness in zip(segments[1:], (10, 20, 40, 80), strict=True):
        segment.click()
        assert editor.lighting_value()["brightness"] == brightness
        assert editor.lighting_value()["enabled"] is True
        assert segment.isChecked()
    segments[0].click()
    assert editor.lighting_value()["enabled"] is False
    assert editor.lighting_value()["brightness"] == original_brightness


def test_zero_lighting_preview_does_not_invent_agent_state(session, qtbot):
    window, vm, _gateway, _snapshot, _store = session
    vm.navigate("lighting")
    editor = window.findChild(PreferencesEditor)
    stage = window.findChild(QWidget, "lightingDevicePreview")
    editor._lighting_brightness.setValue(0)
    for key in stage.findChildren(KeycapButton):
        assert key.property("lightingColor").alpha() == 0


@pytest.mark.parametrize("rgb", [(0, 24, 48), (0, 48, 64), (255, 255, 255), (0, 0, 0)])
def test_preview_boost_preserves_color_values_and_level_order(session, qtbot, tmp_path, rgb):
    window, vm, gateway, _snapshot, _store = session
    window.resize(1440, 940)
    window.show()
    vm.navigate("lighting")
    qtbot.wait(100)
    editor = window.findChild(PreferencesEditor)
    key = next(k for k in window.findChildren(KeycapButton) if k.property("controlId") == "key.3")
    swatch = next(b for b in editor.findChildren(RgbButton) if b.property("controlId") == "key.3")
    before = len(gateway.commands)
    swatch._set_rgb(rgb, emit=True)
    alphas = []
    for level in range(5):
        editor._lighting_brightness.setValue(level)
        alphas.append(key.property("lightingColor").alpha())
        assert editor.lighting_value()["under_key"][2] == dict(zip(("r", "g", "b"), rgb))
    assert alphas[0] == 0
    if max(rgb):
        assert all(a < b for a, b in zip(alphas, alphas[1:]))
        assert alphas[-1] >= 100  # Dark shipped blue/cyan must be visible at the top level.
    else:
        assert alphas == [0] * 5
    assert len(gateway.commands) == before
    assert window.grab().save(str(tmp_path / "lighting-preview-brighter.png"))


def test_sync_actions_remain_clickable_when_settings_are_locked(session, monkeypatch):
    from dataclasses import replace
    from PySide6.QtWidgets import QMessageBox
    from controller_config.transactions import ConfigTransactionState
    from test_write_transaction import _ack
    window, vm, gateway, snapshot, _store = session
    vm.rename_profile(snapshot.active_profile_id, "Lighting QA")
    vm.navigate("lighting")
    vm.screen_icon.attach(None)
    vm.screen_glyphs.attach(None)
    vm.prepare_device_write()
    gateway.command_completed.emit("VALIDATE_CONFIG", _ack("VALIDATE_CONFIG"))
    confirm = next(b for b in window.findChild(QWidget, "lightingSyncCard").findChildren(QPushButton)
                   if b.text() == "确认保存到设备")
    assert confirm.isEnabled()
    monkeypatch.setattr(QMessageBox, "warning", lambda *_: QMessageBox.Yes)
    confirm.click()
    assert gateway.commands[-1].name == "SET_CONFIG"
    assert not window.findChild(PreferencesEditor)._light_card.isEnabled()
    vm._write_transaction = replace(vm.write_transaction, state=ConfigTransactionState.UNKNOWN)
    vm.changed.emit(vm.model)
    sync = window.findChild(QWidget, "lightingSyncCard")
    reconcile = next(b for b in sync.findChildren(QPushButton) if b.text() == "重新确认")
    assert reconcile.isEnabled()
    assert window.findChild(QLabel, "preferencesSyncSummary").text() != "已同步 · 没有本地变更"
