from __future__ import annotations

import pytest
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QPushButton

from controller_config.appearance import MACOS_GLASS_MENU_STYLE, V4_STYLE, V4_TOKENS
from controller_config.views.v4_widgets import STATUS_MARKS, StatusMark, UsageRings, V4Card


def test_usage_rings_keep_unknown_separate_from_empty(qapp):
    rings = UsageRings()
    assert rings.seven_day is None and rings.five_hour is None
    assert "—" in rings.accessibleName()
    rings.set_usage(seven_day=0, five_hour=72)
    assert "7D 0%" in rings.accessibleName()
    assert "5H 72%" in rings.accessibleName()
    assert not rings.grab().isNull()


@pytest.mark.parametrize("value", [-1, 101, float("nan"), float("inf")])
def test_usage_rings_reject_not_a_percentage(qapp, value):
    with pytest.raises(ValueError):
        UsageRings().set_usage(seven_day=value, five_hour=None)


@pytest.mark.parametrize("role", ["primary", "secondary", "widget", "focus"])
def test_v4_cards_are_opaque_and_render_without_mutating_children(qapp, role):
    card = V4Card(role=role, dots=True)
    card.resize(300, 180)
    image = card.grab().toImage()
    expected = V4_TOKENS["focus"] if role == "focus" else "#242320" if role == "widget" else V4_TOKENS["baseDark"]
    # Sample below the decorative dot fade, in logical coordinates on Retina too.
    ratio = image.devicePixelRatio()
    colour = image.pixelColor(round(card.width() / 2 * ratio), round((card.height() - 25) * ratio))
    assert colour.alpha() == 255
    assert colour.name() == QColor(expected).name()
    assert card.property("cardRole") == role
    assert card.graphicsEffect() is None


def test_status_marks_have_non_colour_identity(qapp):
    for status, rows in STATUS_MARKS.items():
        assert len(rows) == 5 and all(len(row) == 5 for row in rows)
        mark = StatusMark(status)
        assert mark.accessibleName().startswith("[ ")
        assert not mark.grab().isNull()


def test_focus_card_has_no_orange_outline_or_glow(qapp):
    from controller_config.views.main_window import APP_STYLE

    card = V4Card(role="focus", dots=True)
    card.setStyleSheet(APP_STYLE + V4_STYLE)
    card.resize(300, 180)
    image = card.grab().toImage()
    # The old six-pixel halo and orange border were painted at both sides.
    ratio = image.devicePixelRatio()
    for logical_x in (*range(0, 10), *range(290, 300)):
        colour = image.pixelColor(round(logical_x * ratio), round(90 * ratio))
        assert not (colour.red() > colour.green() * 1.5 and colour.red() > 50)


@pytest.mark.parametrize("name", ["promptDirectionCard", "encoderControl", "joystickControl", "controlKey"])
def test_selected_controls_do_not_restore_legacy_orange_borders(qapp, name):
    from controller_config.views.device_silhouette import KeycapButton
    from controller_config.views.main_window import APP_STYLE

    button_type = KeycapButton if name == "controlKey" else QPushButton
    button = button_type(objectName=name)
    button.setProperty("selected", True)
    button.setStyleSheet(APP_STYLE + V4_STYLE)
    button.resize(200, 100)
    image = button.grab().toImage()
    ratio = image.devicePixelRatio()
    for logical_x in range(8):
        colour = image.pixelColor(round(logical_x * ratio), round(button.height() / 2 * ratio))
        assert not (colour.red() > colour.green() * 1.5 and colour.red() > 50)


def test_v4_text_surfaces_use_opaque_fallbacks():
    assert "background-color: #242320" in MACOS_GLASS_MENU_STYLE
    assert "border-radius: 18px" in MACOS_GLASS_MENU_STYLE
    assert 'QFrame#consoleFrame[nativeGlass="true"]' in V4_STYLE
    assert "backdrop-filter" not in V4_STYLE
