import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QPushButton, QScrollArea, QWidget

from controller_config.views.device_silhouette import DeviceModelCanvas, KeycapButton
from controller_config.views.preferences_editor import PreferencesEditor, RgbButton
from controller_config.views.v4_widgets import UsageRings
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
    window.resize(width, 940 if width == 1440 else 820)
    vm.navigate("lighting")
    qtbot.wait(150)
    editor = window.findChild(PreferencesEditor)
    nav = window._nav_buttons["lighting"]
    expected_name = "Appearance & Feedback" if language == "en_US" else "外观与反馈"
    assert nav.accessibleName() == expected_name
    if nav.property("compactNavigation"):
        assert nav.text() == "" and nav.width() >= nav.iconSize().width() + 24
    else:
        assert nav.text() == expected_name
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
    qtbot.waitUntil(lambda: (
        stage.geometry().bottom() < left.geometry().top()
        if editor._compact_cards else
        left.geometry().right() < stage.geometry().left() < right.geometry().left()
    ), timeout=2000)
    assert editor._compact_cards == (editor.width() < 1080)
    if editor._compact_cards:
        assert stage.geometry().bottom() < left.geometry().top()
        assert left.geometry().right() < right.geometry().left()
    else:
        assert left.geometry().right() < stage.geometry().left()
        assert stage.geometry().right() < right.geometry().left()
    assert left.widget().width() <= left.viewport().width()
    assert right.widget().width() <= right.viewport().width()
    assert window.findChild(QPushButton, "savePreferencesToDevice").isVisible()
    assert window.findChild(QPushButton, "expandScreenIcons").isVisible()
    assert not window.findChild(QWidget, "screenIconOptions").isVisible()
    canvas = stage.findChild(DeviceModelCanvas, "deviceModelCanvas")
    assert canvas is not None
    assert canvas._preset == "lighting"
    key = next(
        item
        for item in stage.findChildren(KeycapButton)
        if item.property("controlId") == "key.3"
    )
    swatch = next(
        item
        for item in editor.findChildren(RgbButton)
        if item.property("controlId") == "key.3"
    )
    assert swatch.isHidden()
    assert canvas._selected_control_id == "key.3"
    for control_id in ("key.3", "key.8", "key.12"):
        assert canvas.can_activate_control(control_id)
    for control_id in (
        "key.1", "encoder.cw", "encoder.press", "joystick.up", "joystick.press",
    ):
        assert not canvas.can_activate_control(control_id)
    canvas.activateControl("key.1")
    canvas.activateControl("encoder.cw")
    canvas.activateControl("joystick.up")
    assert canvas._springs["key.1"].target == 0.0
    assert canvas._springs["encoder.rotation"].target == 0.0
    assert canvas._springs["joystick.x"].target == 0.0
    assert canvas._springs["joystick.y"].target == 0.0
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
    selected = canvas.key_visual_states()["key.3"]
    assert selected["cap_light_color"] == selected["diffuser_light_color"]
    assert selected["cap_light_color"] == selected["spill_light_color"]
    assert selected["light_source"] == "lighting_preview"
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
    left_scroll = window.findChild(QScrollArea, "lightingLeftScroll")
    qtbot.waitUntil(lambda: left_scroll.verticalScrollBar().value() > 0)
    assert window.findChild(QPushButton, "screenIconImport").isVisible()
    glyph_options = window.findChild(QWidget, "screenGlyphOptions")
    glyph_toggle = window.findChild(QPushButton, "expandScreenGlyphs")
    assert glyph_toggle.isVisible()
    assert not glyph_options.isVisible()
    glyph_toggle.click()
    assert glyph_options.isVisible()


def test_level_segments_select_real_firmware_values(session):
    window, vm, _gateway, _snapshot, _store = session
    vm.navigate("lighting")
    editor = window.findChild(PreferencesEditor)
    segments = editor.findChildren(QPushButton, "lightingLevelSegment")
    gauge = editor.findChild(UsageRings, "lightingLevelGauge")
    original_brightness = editor.lighting_value()["brightness"]
    for segment, brightness, displayed_percentage in zip(
        segments[1:],
        (10, 20, 40, 80),
        (25, 50, 75, 100),
        strict=True,
    ):
        segment.click()
        assert editor.lighting_value()["brightness"] == brightness
        assert editor.lighting_value()["enabled"] is True
        assert gauge.seven_day == displayed_percentage
        assert segment.isChecked()
    segments[0].click()
    assert editor.lighting_value()["enabled"] is False
    assert editor.lighting_value()["brightness"] == original_brightness
    assert gauge.seven_day == 0


def test_zero_lighting_preview_does_not_invent_agent_state(session, qtbot):
    window, vm, _gateway, _snapshot, _store = session
    vm.navigate("lighting")
    editor = window.findChild(PreferencesEditor)
    stage = window.findChild(QWidget, "lightingDevicePreview")
    editor._lighting_brightness.setValue(0)
    for key in stage.findChildren(KeycapButton):
        assert key.property("lightingColor").alpha() == 0


def test_preview_preserves_configured_color_values_and_level_order(session, qtbot, tmp_path):
    window, vm, gateway, _snapshot, _store = session
    window.resize(1440, 940)
    window.show()
    vm.navigate("lighting")
    qtbot.wait(100)
    editor = window.findChild(PreferencesEditor)
    key = next(k for k in window._content.findChildren(KeycapButton) if k.property("controlId") == "key.3")
    original_rgb = editor.lighting_value()["under_key"][2]
    before = len(gateway.commands)
    alphas = []
    for level in range(5):
        editor._lighting_brightness.setValue(level)
        alphas.append(key.property("lightingColor").alpha())
        assert editor.lighting_value()["under_key"][2] == original_rgb
    assert alphas[0] == 0
    if max(original_rgb.values()):
        assert all(a < b for a, b in zip(alphas, alphas[1:]))
        assert alphas[-1] >= 75  # Dark saved colors must remain visible at the top level.
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
