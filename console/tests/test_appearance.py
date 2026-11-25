from __future__ import annotations

import sys
from importlib.resources import files

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

from PySide6.QtWidgets import QCheckBox, QDialog, QVBoxLayout, QWidget

import controller_config.appearance as appearance
from controller_config.appearance import (
    MACOS_GLASS_MENU_STYLE,
    enable_native_window_controls,
    install_macos_vibrancy,
    macos_window_full_screen,
    refresh_native_window_controls,
    set_macos_window_backdrop,
    title_bar_double_click_action,
    uses_native_window_controls,
    V4_STYLE,
)


@pytest.mark.parametrize("checked", [False, True])
@pytest.mark.parametrize("enabled", [False, True])
def test_toggle_assets_render_distinct_position_and_disabled_state(checked, enabled):
    name = f"toggle-{'on' if checked else 'off'}{'' if enabled else '-disabled'}.svg"
    renderer = QSvgRenderer(str(files("controller_config.assets").joinpath(name)))
    assert renderer.isValid()
    image = QImage(38, 22, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()
    left = image.pixelColor(11, 11)
    right = image.pixelColor(27, 11)
    thumb, track = (right, left) if checked else (left, right)
    assert (thumb.lightness() < track.lightness()) is checked
    assert (max(thumb.lightness(), track.lightness()) > 230) is enabled
    if checked:
        assert track.name() == ("#efeae0" if enabled else "#77736b")
        assert thumb.name() == ("#242320" if enabled else "#393732")
    assert image.pixelColor(0, 0).alpha() == 0


def test_toggle_keeps_click_keyboard_label_and_disabled_semantics(qtbot):
    parent = QWidget()
    qtbot.addWidget(parent)
    parent.setStyleSheet(V4_STYLE)
    # Child dialogs must inherit the same indicator styling as the pages.
    dialog = QDialog(parent)
    qtbot.addWidget(dialog)
    layout = QVBoxLayout(dialog)
    toggle = QCheckBox("按键反馈", objectName="toggleUnderTest")
    layout.addWidget(toggle)
    dialog.show()
    qtbot.waitExposed(dialog)
    changes = []
    toggle.toggled.connect(changes.append)
    qtbot.mouseClick(toggle, Qt.MouseButton.LeftButton, pos=toggle.rect().center())
    assert toggle.isChecked()
    toggle.setFocus()
    qtbot.keyClick(toggle, Qt.Key.Key_Space)
    assert not toggle.isChecked()
    toggle.setEnabled(False)
    qtbot.mouseClick(toggle, Qt.MouseButton.LeftButton, pos=toggle.rect().center())
    assert changes == [True, False]
    assert toggle.text() == "按键反馈"
    assert toggle.height() >= 28


def test_toggle_gallery_fits_modifiers_and_haptics(qtbot, contract, tmp_path):
    from controller_config.views.action_editor import ModifierSelector
    from controller_config.views.preferences_editor import PreferencesEditor
    from controller_config.transport.demo import _power_v2_snapshot

    snapshot = _power_v2_snapshot(contract, read_only=False)
    window = QWidget()
    qtbot.addWidget(window)
    window.setWindowTitle("BORING · 开关样式预览（不连接设备）")
    window.setStyleSheet(V4_STYLE + "QWidget#toggleGallery { background: #1C1B19; }")
    window.setObjectName("toggleGallery")
    layout = QVBoxLayout(window)
    modifiers = ModifierSelector([224, 227], "darwin")
    layout.addWidget(modifiers)
    editor = PreferencesEditor(
        lighting=snapshot.config["lighting"], haptic=snapshot.config["haptic"],
        display=snapshot.config["display"],
        features={"haptic": True, "haptic_channels": True},
        under_key_control_ids=(), agent_status_control_ids=frozenset(),
        rules=contract.editor_rules,
    )
    qtbot.addWidget(editor)
    card = editor._haptic_card
    editor.layout().removeWidget(card)
    layout.addWidget(card)
    editor._haptic_channels["on_encoder"].setChecked(False)
    disabled = QCheckBox("暂不可用 · 保留开启状态")
    disabled.setChecked(True)
    disabled.setEnabled(False)
    layout.addWidget(disabled)
    window.resize(460, 680)
    window.show()
    qtbot.waitExposed(window)
    qtbot.wait(50)
    assert modifiers.values() == [224, 227]
    assert window.width() == 460
    for checkbox in window.findChildren(QCheckBox):
        assert checkbox.width() >= checkbox.sizeHint().width()
    assert window.grab().save(str(tmp_path / "apple-style-toggles.png"))


def test_macos_glass_menu_uses_neutral_system_material_tokens() -> None:
    assert "background-color: #242320" in MACOS_GLASS_MENU_STYLE
    assert "border: none" in MACOS_GLASS_MENU_STYLE
    assert "border-radius: 18px" in MACOS_GLASS_MENU_STYLE
    assert "#9a3b2b" not in MACOS_GLASS_MENU_STYLE


def test_native_vibrancy_matches_the_active_qt_platform(qapp) -> None:
    widget = QWidget()

    enabled = install_macos_vibrancy(widget, corner_radius=18.0)

    expected = sys.platform == "darwin" and qapp.platformName() == "cocoa"
    if expected:
        import AppKit
        expected = not AppKit.NSWorkspace.sharedWorkspace().accessibilityDisplayShouldReduceTransparency()
    assert enabled is expected
    if enabled:
        import AppKit

        effect = widget._boring_macos_effect_view
        assert effect.material() == AppKit.NSVisualEffectMaterialUnderWindowBackground


def test_full_screen_backdrop_covers_the_glass_with_the_base_colour(qapp, qtbot):
    widget = QWidget()
    qtbot.addWidget(widget)
    # Not shown yet: nothing to ask, and no native window gets created for it.
    assert macos_window_full_screen(widget) is None
    assert not set_macos_window_backdrop(widget, "#1C1B19")
    assert widget.windowHandle() is None
    if not uses_native_window_controls():
        return
    import AppKit
    import objc

    enable_native_window_controls(widget)
    widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    widget.resize(600, 400)
    widget.show()
    qtbot.waitExposed(widget)
    native = objc.objc_object(c_void_p=int(widget.winId())).window()
    install_macos_vibrancy(widget, corner_radius=25)
    effect = getattr(widget, "_boring_macos_effect_view", None)
    assert macos_window_full_screen(widget) is False

    assert set_macos_window_backdrop(widget, "#1C1B19")
    colour = native.backgroundColor().colorUsingColorSpace_(AppKit.NSColorSpace.sRGBColorSpace())
    assert colour.alphaComponent() == 1
    assert round(colour.redComponent() * 255) == 0x1C
    if effect is not None:
        assert effect.isHidden()

    assert set_macos_window_backdrop(widget, None)
    assert native.backgroundColor().alphaComponent() == 0
    if effect is not None:
        assert not effect.isHidden()


@pytest.mark.parametrize("close_to_background", [False, True])
def test_native_glass_window_really_hides_and_reopens(qapp, qtbot, close_to_background):
    if sys.platform != "darwin" or qapp.platformName() != "cocoa":
        pytest.skip("Requires the native macOS window lifecycle")
    import objc

    class BackgroundWindow(QWidget):
        def closeEvent(self, event):
            self.hide()
            event.ignore()

    widget = BackgroundWindow()
    qtbot.addWidget(widget)
    widget.setWindowFlag(Qt.WindowType.FramelessWindowHint)
    widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    widget.resize(600, 400)
    widget.show()
    qtbot.waitExposed(widget)
    content = objc.objc_object(c_void_p=int(widget.winId()))
    native = content.window()
    if not install_macos_vibrancy(widget, corner_radius=25):
        pytest.skip("Native transparency is disabled by system settings")

    for _ in range(3):
        if close_to_background:
            widget.close()
        else:
            widget.hide()
        qtbot.wait(50)
        assert not widget.isVisible()
        assert not native.isVisible(), "Qt content hid but left a dark native window onscreen"
        widget.show()
        qtbot.waitExposed(widget)
        assert install_macos_vibrancy(widget, corner_radius=25)
        assert native.isVisible()
        assert not content.isHidden()
        assert native.contentView() == content
        effect = widget._boring_macos_effect_view
        assert effect.superview() == content.superview()
        widget.resize(widget.width() + 20, widget.height() + 10)
        qtbot.wait(50)
        assert effect.frame() == content.frame()
    widget.showMinimized()
    qtbot.waitUntil(widget.isMinimized)
    widget.showNormal()
    qtbot.waitExposed(widget)
    assert native.isVisible()
    assert not content.isHidden()


def test_native_window_controls_are_only_requested_on_native_macos(qapp, monkeypatch):
    widget = QWidget()
    widget.setWindowFlag(Qt.WindowType.FramelessWindowHint)
    monkeypatch.setattr(appearance.sys, "platform", "win32")

    assert not uses_native_window_controls()
    assert not enable_native_window_controls(widget)
    assert not refresh_native_window_controls(widget)
    assert widget.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert not widget.windowFlags() & Qt.WindowType.ExpandedClientAreaHint


def _system_button_centers(widget):
    """Centres of close/minimize/zoom, in points from the window's top-left."""
    import AppKit
    import objc

    native = objc.objc_object(c_void_p=int(widget.winId())).window()
    centers = []
    for kind in (
        AppKit.NSWindowCloseButton,
        AppKit.NSWindowMiniaturizeButton,
        AppKit.NSWindowZoomButton,
    ):
        button = native.standardWindowButton_(kind)
        rect = button.convertRect_toView_(button.bounds(), None)
        centers.append(
            (
                rect.origin.x + rect.size.width / 2,
                native.frame().size.height - (rect.origin.y + rect.size.height / 2),
            )
        )
    return centers


def test_system_window_buttons_are_moved_in_from_the_rounded_corner(qapp, qtbot):
    widget = QWidget()
    qtbot.addWidget(widget)
    if not uses_native_window_controls():
        assert not appearance.position_native_window_controls(widget)
        return
    assert enable_native_window_controls(widget)
    widget.resize(600, 400)
    widget.show()
    qtbot.waitExposed(widget)
    assert refresh_native_window_controls(widget)

    system_default = _system_button_centers(widget)
    radius = appearance.V4_RADIUS
    # Where the system leaves them, the close button crowds the window corner.
    assert system_default[0][0] < radius * 0.7 and system_default[0][1] < radius * 0.7

    assert appearance.position_native_window_controls(widget)
    placed = _system_button_centers(widget)
    assert placed[0] == pytest.approx(appearance.NATIVE_WINDOW_CONTROLS_CENTER)
    assert {y for _, y in placed} == {placed[0][1]}
    # Only the group moves: the spacing between the buttons stays the system's.
    assert [b[0] - a[0] for a, b in zip(placed, placed[1:])] == pytest.approx(
        [b[0] - a[0] for a, b in zip(system_default, system_default[1:])]
    )
    # Applying it again does not walk the buttons further in.
    assert appearance.position_native_window_controls(widget)
    assert _system_button_centers(widget) == pytest.approx(placed)
    # The system lays them out again on resize; the caller re-applies.
    widget.resize(640, 420)
    qtbot.wait(20)
    assert appearance.position_native_window_controls(widget)
    assert _system_button_centers(widget) == pytest.approx(placed)


@pytest.mark.parametrize("close_to_background", [False, True])
def test_system_window_buttons_and_translucency_survive_hide_and_reopen(
    qapp, qtbot, close_to_background
):
    if not uses_native_window_controls():
        pytest.skip("Requires the native macOS window lifecycle")
    import AppKit
    import objc

    class BackgroundWindow(QWidget):
        def closeEvent(self, event):
            self.hide()
            event.ignore()

    widget = BackgroundWindow()
    qtbot.addWidget(widget)
    assert enable_native_window_controls(widget)
    widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    widget.resize(600, 400)

    def assert_system_buttons_over_translucent_window():
        content = objc.objc_object(c_void_p=int(widget.winId()))
        native = content.window()
        assert refresh_native_window_controls(widget)
        assert native.styleMask() & AppKit.NSWindowStyleMaskTitled
        assert native.styleMask() & AppKit.NSWindowStyleMaskFullSizeContentView
        assert native.titlebarAppearsTransparent()
        assert native.titleVisibility() == AppKit.NSWindowTitleHidden
        # The compositor prerequisites for seeing the desktop through the window.
        assert not native.isOpaque()
        assert native.backgroundColor().alphaComponent() == 0
        assert native.contentView() == content
        assert content.frame().size.height == native.frame().size.height
        for kind in (
            AppKit.NSWindowCloseButton,
            AppKit.NSWindowMiniaturizeButton,
            AppKit.NSWindowZoomButton,
        ):
            button = native.standardWindowButton_(kind)
            assert button is not None and not button.isHiddenOrHasHiddenAncestor()
        return native

    for _ in range(3):
        widget.show()
        qtbot.waitExposed(widget)
        native = assert_system_buttons_over_translucent_window()
        if install_macos_vibrancy(widget, corner_radius=25):
            effect = widget._boring_macos_effect_view
            assert effect.frame().size.height == native.frame().size.height
        if close_to_background:
            widget.close()
        else:
            widget.hide()
        qtbot.wait(50)
        assert not widget.isVisible()
        assert not native.isVisible(), "Qt content hid but left a native window onscreen"


@pytest.mark.parametrize(
    ("stored", "expected"),
    [("Minimize", "minimize"), ("None", "none"), ("Maximize", "zoom"), (None, "zoom")],
)
def test_title_bar_double_click_action_follows_the_system_setting(
    monkeypatch, stored, expected
):
    asked = []
    monkeypatch.setattr(
        appearance, "_macos_global_preference", lambda key: asked.append(key) or stored
    )

    assert title_bar_double_click_action() == expected
    assert asked == ["AppleActionOnDoubleClick"]
