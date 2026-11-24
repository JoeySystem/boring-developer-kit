"""Language keys and the “点阵长满” switch (the designer 11-19, design decision items 2 and 4)."""
import time

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QPushButton

from controller_config.style import tokens
from controller_config.views.ui import LanguageKeys, LanguageSwitch, OrbitalMark
from test_session_recovery import session  # noqa: F401

OPTIONS = [("zh_CN", "文", "简体中文"), ("en_US", "A", "English"), ("ja_JP", "あ", "日本語")]


def _keys(qtbot, chosen=None):
    keys = LanguageKeys(OPTIONS, on_choose=lambda code, origin: chosen.append((code, origin))
                        if chosen is not None else None)
    keys.resize(400, tokens.LANGUAGE_KEY_HEIGHT)
    qtbot.addWidget(keys)
    keys.show()
    keys.set_active("zh_CN")
    return keys


def _pixel(widget, x, y) -> QColor:
    image = QImage(widget.size(), QImage.Format.Format_ARGB32)
    image.fill(0)
    widget.render(image)
    return image.pixelColor(x, y)


def test_three_keys_glyph_over_name_and_only_the_selected_is_cream(qtbot):
    keys = _keys(qtbot)
    assert [(k.glyph, k.text()) for k in keys.keys.values()] == [(g, n) for _c, g, n in OPTIONS]
    assert all(k.objectName() == "settingsLanguageButton" for k in keys.keys.values())
    assert all(k.height() <= tokens.LANGUAGE_KEY_HEIGHT for k in keys.keys.values())
    zh, en = keys.keys["zh_CN"], keys.keys["en_US"]
    assert _pixel(zh, 6, 20) == QColor(tokens.LANGUAGE_KEY_SELECTED)
    assert _pixel(en, 6, 20) == QColor(tokens.LANGUAGE_KEY)


def test_a_press_reports_the_key_and_where_it_was_pressed(qtbot):
    chosen = []
    keys = _keys(qtbot, chosen)
    qtbot.mouseClick(keys.keys["ja_JP"], Qt.MouseButton.LeftButton, pos=QPointF(10, 12).toPoint())
    assert chosen and chosen[0][0] == "ja_JP"
    assert chosen[0][1] == QPointF(10, 12)


def test_switch_grows_dots_on_the_new_key_and_shrinks_the_old_one(qtbot):
    keys = _keys(qtbot)
    keys.set_active("ja_JP")
    switch = LanguageSwitch("zh_CN", "ja_JP", QPointF(10, 10), time.monotonic())
    keys.play(switch)
    keys._step(0.3)  # eased ≈ 0.66: the front has passed the press point, not yet the far corner
    ja, zh = keys.keys["ja_JP"], keys.keys["zh_CN"]
    assert 0 < ja.fill_level() < 1 and 0 < zh.fill_level() < 1
    near = _pixel(ja, 10, 10)
    far = _pixel(ja, ja.width() - 12, ja.height() - 12)
    assert near == QColor(tokens.LANGUAGE_KEY_SELECTED)
    assert far == QColor(tokens.LANGUAGE_KEY)
    # the old key loses its edge first, its middle last
    assert _pixel(zh, 4, zh.height() // 2) == QColor(tokens.LANGUAGE_KEY)
    qtbot.waitUntil(lambda: ja._fill is None and zh._fill is None, timeout=2000)
    assert ja.fill_level() == 1.0 and zh.fill_level() == 0.0


def test_rebuilt_keys_carry_on_from_where_the_switch_is(qtbot):
    keys = _keys(qtbot)
    keys.set_active("en_US")
    started = time.monotonic() - tokens.LANGUAGE_FILL_MS / 2000  # half way already
    keys.play(LanguageSwitch("zh_CN", "en_US", QPointF(5, 5), started))
    assert keys._animation.duration() <= tokens.LANGUAGE_FILL_MS / 2 + 5
    over = LanguageKeys(OPTIONS, on_choose=lambda *_: None)
    qtbot.addWidget(over)
    over.play(LanguageSwitch("zh_CN", "en_US", QPointF(5, 5), time.monotonic() - 5))
    assert over._switch is None  # long finished: nothing to play


def test_orbital_mark_keeps_its_shape_and_never_grows_past_150(qtbot):
    mark = OrbitalMark()
    qtbot.addWidget(mark)
    assert mark.maximumHeight() == tokens.ORBITAL_MARK_HEIGHT
    mark.resize(400, 150)
    rect = mark.drawn_rect()
    assert rect.height() == tokens.ORBITAL_MARK_HEIGHT
    assert abs(rect.width() / rect.height() - mark.aspect()) < 1e-6
    assert rect.right() == 400 and rect.bottom() == 150  # bottom-right corner
    mark.resize(120, 150)
    assert mark.drawn_rect().width() <= 120


def test_settings_language_keys_switch_the_window(session, qtbot):  # noqa: F811
    window, vm, *_ = session
    window.show()
    vm.navigate("settings")
    window._select_settings_section("system")
    advanced = window.findChild(QPushButton, "openAdvancedCustomization")
    assert advanced.parentWidget().span == (1, 1)  # 高级自定义 is one cell now (design decision item 3)
    english = next(b for b in window.findChildren(QPushButton, "settingsLanguageButton") if b.text() == "English")
    english.click()
    assert window._language_manager.language == "en_US"
    assert window._language_switch is not None and window._language_switch.new == "en_US"
    rebuilt = next(b for b in window.findChildren(QPushButton, "settingsLanguageButton") if b.text() == "English")
    assert rebuilt.property("active") is True
    window._language_manager.set_language("zh_CN")
