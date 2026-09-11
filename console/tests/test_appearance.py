from __future__ import annotations

import sys
from importlib.resources import files

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

from PySide6.QtWidgets import QCheckBox, QDialog, QVBoxLayout, QWidget

from controller_config.appearance import (
    MACOS_GLASS_MENU_STYLE,
    install_macos_vibrancy,
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
