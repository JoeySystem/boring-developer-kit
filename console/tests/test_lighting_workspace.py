import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QDialog, QLabel, QPushButton, QWidget

from controller_config.views.preferences_editor import PreferencesEditor
from controller_config.views.ui import ColorPresetRow, DeviceTopView
from test_session_recovery import session


@pytest.fixture(autouse=True)
def restore_language(session):
    yield
    session[0]._language_manager.set_language("zh_CN")


@pytest.mark.parametrize("width,language", [(1440, "zh_CN"), (1280, "zh_CN"), (1100, "en_US")])
def test_lighting_wall_layout_preview_and_controls(session, qtbot, width, language):
    """灯光与反馈 is a 4 × 2 wall (the designer, 灯光页排法二 v0.4)."""
    window, vm, gateway, _snapshot, _store = session
    window._language_manager.set_language(language)
    window.resize(width, 800)
    window.show()
    vm.navigate("lighting")
    qtbot.wait(150)
    editor = window.findChild(PreferencesEditor)
    assert editor.findChild(QLabel, "settingsWallTitle").text() == (
        "Lighting & Feedback" if language == "en_US" else "灯光与反馈"
    )
    light, screen, haptic, icons = (
        editor._light_card, editor._screen_card, editor._haptic_card, editor._icon_card,
    )
    qtbot.waitUntil(lambda: light.width() > 0 and screen.isVisible())
    # 按键灯光 2 × 2 on the left; 屏幕 and 震动 top right; 屏幕图标 2 × 1 under them.
    assert light.span == (2, 2) and icons.span == (1, 2)
    assert light.geometry().right() < screen.geometry().left() < haptic.geometry().left()
    assert screen.geometry().bottom() < icons.geometry().top()
    assert icons.geometry().left() == screen.geometry().left()
    for tile in (light, screen, haptic, icons):
        assert editor.rect().contains(tile.geometry())

    stage = window.findChild(DeviceTopView, "lightingDevicePreview")
    assert light.isAncestorOf(stage)
    assert light.isAncestorOf(window.findChild(ColorPresetRow, "lightingColorRow"))
    assert light.isAncestorOf(window.findChild(QWidget, "lightingPreviewEnabled"))
    level = editor._lighting_brightness
    assert level.isVisible()
    assert light.rect().contains(level.mapTo(light, level.rect().topRight()))
    assert not window._save_banner.is_shown()  # nothing to save yet
    assert window.findChild(QPushButton, "expandScreenIcons").isVisible()

    # Pick key 9 on the device picture, give it a preset: the key shows it at once.
    editor._lighting_brightness.setValue(4)
    before = len(gateway.commands)
    qtbot.mouseClick(stage, Qt.LeftButton, pos=stage.key_rect("key.9").center().toPoint())
    assert stage.selected() == "key.9"
    row = window.findChild(ColorPresetRow, "lightingColorRow")
    assert row.label_text() == ("Key 9" if language == "en_US" else "键 9")
    row.color_chosen.emit((255, 59, 92))
    assert editor.lighting_value()["under_key"][8] == {"r": 255, "g": 59, "b": 92}
    assert stage.key_colors()["key.9"] == (255, 59, 92)
    assert len(gateway.commands) == before  # Local preview does not write hardware.
    assert window.findChild(QLabel, "preferencesSyncSummary").text() == (
        "Changes not saved to device yet" if language == "en_US" else "有修改，还没存进设备"
    )
    banner = window._save_banner
    qtbot.waitUntil(banner.isVisible)
    assert window.findChild(QPushButton, "savePreferencesToDevice").isVisible()
    tabs_bottom = window._nav_buttons["settings"].mapTo(window, window._nav_buttons["settings"].rect().bottomLeft()).y()
    assert banner.mapTo(window, banner.rect().topLeft()).y() > tabs_bottom
    assert banner.mapTo(window, banner.rect().bottomLeft()).y() < light.mapTo(window, light.rect().topLeft()).y()

    # 屏幕 opens two cells wide over 震动, and the other way round; one at a time.
    values = editor.values()
    from controller_config.views.ui.tile_morph import TileMorph

    def settled():   # the covered tile stays shown under the overlay until it is covered (真屏 11-22)
        qtbot.waitUntil(lambda: window.findChild(TileMorph) is None, timeout=1500)

    window.findChild(QPushButton, "expandScreenTile").click()
    settled()
    assert screen.span == (1, 2) and not haptic.isVisible()
    assert editor._display_rotation.isVisible() and editor._display_hints.isVisible()
    window.findChild(QPushButton, "expandScreenTile").click()
    assert screen.span == (1, 1) and haptic.isVisible()
    window.findChild(QPushButton, "expandHapticTile").click()
    settled()
    assert haptic.span == (1, 2) and not screen.isVisible()
    assert haptic.geometry().left() == icons.geometry().left()
    assert window.findChild(QCheckBox, "hapticOnTask").isVisible()
    qtbot.mouseClick(light, Qt.LeftButton)  # a click outside closes it
    assert haptic.span == (1, 1) and screen.isVisible()
    editor.open_tile(screen)
    qtbot.keyClick(window, Qt.Key_Escape)
    assert screen.span == (1, 1)
    assert editor.values() == values

    window.findChild(QPushButton, "expandScreenIcons").click()
    qtbot.waitUntil(lambda: window.findChild(QWidget, "screenIconOptions").isVisible())
    assert window.findChild(QPushButton, "screenIconImport").isVisible()
    glyph_options = window.findChild(QWidget, "screenGlyphOptions")
    glyph_toggle = window.findChild(QPushButton, "expandScreenGlyphs")
    assert glyph_toggle.isVisible()
    assert not glyph_options.isVisible()
    glyph_toggle.click()
    assert glyph_options.isVisible()
    window.findChild(QDialog, "screenIconDialog").reject()


def test_level_slider_selects_real_firmware_values(session):
    """One dot bar with five stops (the designer, lighting page v0.4), no buttons."""
    window, vm, _gateway, _snapshot, _store = session
    vm.navigate("lighting")
    editor = window.findChild(PreferencesEditor)
    assert editor.findChildren(QPushButton, "lightingLevelSegment") == []
    slider = editor._lighting_brightness
    level_value = editor.findChild(QLabel, "lightingLevelValue")
    original_brightness = editor.lighting_value()["brightness"]
    for stop, brightness, name in zip(
        (1, 2, 3, 4), (10, 20, 40, 80), ("低", "中", "高", "最高"), strict=True,
    ):
        slider.setValue(stop)
        slider.sliderReleased.emit()  # as a click that lands on a stop
        assert editor.lighting_value()["brightness"] == brightness
        assert editor.lighting_value()["enabled"] is True
        assert level_value.text() == name
    slider.setValue(0)
    assert editor.lighting_value()["enabled"] is False
    assert editor.lighting_value()["brightness"] == original_brightness


def test_zero_lighting_preview_does_not_invent_agent_state(session, qtbot):
    window, vm, _gateway, _snapshot, _store = session
    vm.navigate("lighting")
    editor = window.findChild(PreferencesEditor)
    stage = window.findChild(DeviceTopView, "lightingDevicePreview")
    editor._lighting_brightness.setValue(0)
    assert stage.brightness() == 0
    plain = stage.key_fill("key.3")
    stage.set_brightness(0)
    for key in ("key.3", "key.8", "key.12"):
        assert stage.key_fill(key) == plain  # off: plain caps, no light


def test_preview_preserves_configured_color_values_and_level_order(session, qtbot):
    window, vm, gateway, _snapshot, _store = session
    vm.navigate("lighting")
    editor = window.findChild(PreferencesEditor)
    stage = window.findChild(DeviceTopView, "lightingDevicePreview")
    editor._light_buttons["key.3"]._set_rgb((230, 120, 40), emit=True)
    original_rgb = editor.lighting_value()["under_key"][2]
    before = len(gateway.commands)
    levels = []
    for level in range(5):
        editor._lighting_brightness.setValue(level)
        levels.append(stage.brightness())
        assert editor.lighting_value()["under_key"][2] == original_rgb
    assert levels == [0, 0.25, 0.5, 0.75, 1]
    assert len(gateway.commands) == before


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
    # ② 确认写入设备？ on the banner is the question; no second message box.
    confirm = window.findChild(QPushButton, "confirmConfigurationWrite")
    assert confirm.isEnabled()
    from controller_config.views.ui import notice
    monkeypatch.setattr(notice, "ask", lambda *_a, **_k: pytest.fail("asked twice"))
    monkeypatch.setattr(notice, "choose", lambda *_a, **_k: pytest.fail("asked twice"))
    confirm.click()
    assert gateway.commands[-1].name == "SET_CONFIG"
    assert not window.findChild(PreferencesEditor)._light_card.isEnabled()
    vm._write_transaction = replace(vm.write_transaction, state=ConfigTransactionState.UNKNOWN)
    vm.changed.emit(vm.model)
    assert window._save_banner.kind().value == "unknown"
    window.findChild(QPushButton, "configurationWriteDetails").click()
    dialog = window.findChild(QDialog, "saveDetailsDialog")
    reconcile = next(b for b in dialog.findChildren(QPushButton) if b.text() == "确认设备保存结果")
    assert reconcile.isEnabled()
    dialog.reject()


def test_unsaved_lighting_banner_stays_on_every_tab_at_90(session, qtbot):
    """the designer: 页签下面那一条要保留; every right-hand banner starts 90 from the top (design decision items 6, 7)."""
    from controller_config.views.main_window import SAVE_BANNER_TOP

    window, vm, *_ = session
    window.resize(1280, 800)
    window.show()
    vm.navigate("lighting")
    editor = window.findChild(PreferencesEditor)
    editor._lighting_brightness.setValue(2)
    banner = window._save_banner
    qtbot.waitUntil(banner.isVisible)
    page_top = window._page_area.mapTo(window, window._page_area.rect().topLeft()).y()
    expected = max(SAVE_BANNER_TOP, page_top)  # never above the page area (under the tabs)
    for page in ("prompts", "overview", "settings"):
        vm.navigate(page)
        qtbot.wait(50)
        assert banner.is_shown(), page
        assert banner.mapTo(window, banner.rect().topLeft()).y() == expected
    banner.apply(banner.kind().HIDDEN)
    window._place_save_banner()
    assert window._banners._top_inset == expected - page_top  # other notices start at the same line


@pytest.mark.parametrize("size", [(1280, 800), (1600, 1000)])
def test_save_banner_right_edge_meets_the_wall(session, qtbot, size):
    """-08 答复第 4 件 (Agent's reading): the banner's right edge is the wall's right edge."""
    from PySide6.QtCore import QPoint

    window, vm, *_ = session
    window.resize(*size)
    window.show()
    vm.navigate("lighting")
    window.findChild(PreferencesEditor)._lighting_brightness.setValue(2)
    banner = window._save_banner
    qtbot.waitUntil(banner.isVisible)
    wall = window.findChild(PreferencesEditor)

    def right(widget):
        return widget.mapTo(window, QPoint(widget.width(), 0)).x()

    qtbot.waitUntil(lambda: right(banner) == right(wall), timeout=1000)


def test_lighting_tile_widens_with_a_bounce_and_closes_without(session, qtbot, monkeypatch):
    """-08 答复第 3 件: 240 ms, the moving edge runs about 50 past and back; closing does not bounce,
    the open content is gone in the first 30 % and the closed content only comes back at the end."""
    from controller_config.style import tokens
    from controller_config.views.ui import motion
    from controller_config.views.ui.tile_morph import TileMorph

    monkeypatch.setattr(motion, "reduces_motion", lambda: False)
    window, vm, *_ = session
    window.resize(1280, 800)
    window.show()
    vm.navigate("lighting")
    editor = window.findChild(PreferencesEditor)
    screen = editor._screen_card
    qtbot.waitUntil(screen.isVisible)
    editor.open_tile(screen)
    morph = window.findChild(TileMorph)
    assert morph is not None and morph._opening and morph.animation.duration() == tokens.TILE_OPEN_MS == 240
    widest = max(morph.body(p / 100).right() for p in range(101))
    assert 40 <= widest - morph._end.right() <= 60      # about 50 past, then back
    assert morph.body(1.0) == morph._end
    # the designer 11-20 真屏验收: while it plays the real tile is veiled (no shadow re-render,
    # no frame of it before the overlay) and the overlay covers only the two cells.
    from PySide6.QtWidgets import QGraphicsDropShadowEffect, QGraphicsOpacityEffect
    assert isinstance(screen.graphicsEffect(), QGraphicsOpacityEffect) and screen.graphicsEffect().opacity() == 0
    assert morph.geometry() != window.rect() and morph.geometry().contains(morph._end.toRect())
    qtbot.waitUntil(lambda: window.findChild(TileMorph) is None, timeout=1000)
    assert isinstance(screen.graphicsEffect(), QGraphicsDropShadowEffect)   # open: a faint shadow under it
    editor.open_tile(None)
    morph = window.findChild(TileMorph)
    assert morph is not None and not morph._opening
    assert max(morph.body(p / 100).width() for p in range(101)) <= morph._start.width() + 0.5   # no bounce
    qtbot.waitUntil(lambda: window.findChild(TileMorph) is None, timeout=1000)
    assert screen.graphicsEffect() is None
