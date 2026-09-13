import pytest
from PySide6.QtWidgets import QLabel, QPushButton, QStyle, QStyleOptionButton

from controller_config.i18n import ENGLISH, SIMPLIFIED_CHINESE, LanguageManager
from controller_config.views.screen_glyph_editor import ScreenGlyphEditor
from controller_config.views.screen_icon_editor import ScreenIconDraft, ScreenIconEditor
from test_screen_glyph_editor import Transfer


def test_selected_dimensions_follow_device_catalog_and_clear_on_disconnect(qtbot):
    transfer = Transfer()
    transfer.catalog[0].update(width=14, height=14)
    transfer.catalog[1].update(width=8, height=12)
    editor = ScreenGlyphEditor({})
    qtbot.addWidget(editor)
    editor.bind_transfer(transfer)
    editor.selection_requested.connect(transfer.select)
    assert editor.dimensions.text() == "14 × 14"
    editor.selector.setCurrentIndex(1)
    assert editor.dimensions.text() == "8 × 12"
    transfer.device_key = None
    transfer.changed.emit()
    assert editor.dimensions.text() == "连接设备并选择图标后显示"


def test_guidance_round_trips_languages_without_losing_dimensions(qapp, qtbot):
    manager = LanguageManager(qapp, initial_language=SIMPLIFIED_CHINESE, persist=False)
    glyph = ScreenGlyphEditor({})
    home = ScreenIconEditor(ScreenIconDraft())
    qtbot.addWidget(glyph)
    qtbot.addWidget(home)
    glyph.bind_transfer(Transfer())
    for editor, prefix in ((glyph, "screenGlyph"), (home, "screenIcon")):
        assert "1600 万" in editor.findChild(QLabel, prefix + "Formats").text()
        assert "APNG" in editor.findChild(QLabel, prefix + "UnsupportedFormats").text()
    assert "不保留原图颜色" in glyph.findChild(QLabel, "screenGlyphConversion").text()
    assert "圆形" in home.findChild(QLabel, "screenIconCropHint").text()
    assert glyph.write_button.text() == "写入当前图标"
    manager.set_language(ENGLISH)
    assert glyph.write_button.text() == "Write This Icon"
    assert home.device_buttons["screenIconReset"].text() == "Reset Home"
    assert "16 million pixels" in glyph.findChild(QLabel, "screenGlyphFormats").text()
    assert "circular area" in home.findChild(QLabel, "screenIconCropHint").text()
    assert glyph.dimensions.text() == "3 × 3"
    manager.set_language(SIMPLIFIED_CHINESE)
    assert glyph.write_button.text() == "写入当前图标"
    assert "其他图标、按键映射和提示词不变" in glyph.findChild(QLabel, "screenGlyphScope").text()
    assert "不重置设备" in home.findChild(QLabel, "screenIconResetScope").text()


def test_readonly_warning_stays_explicit_with_material_guidance(qtbot):
    transfer = Transfer()
    editor = ScreenGlyphEditor({})
    qtbot.addWidget(editor)
    editor.bind_transfer(transfer)
    transfer.select("warning")
    assert not editor.readonly_notice.isHidden()
    assert "仅可查看" in editor.readonly_notice.text()
    assert "不支持修改" in editor.readonly_notice.text()
    assert not editor.import_button.isEnabled()
    assert not editor.reset_button.isEnabled()
    assert editor.refresh_button.isEnabled()


def test_converted_preview_clears_placeholder_and_restores_it_on_discard(qtbot, tmp_path):
    from PySide6.QtGui import QImage, QColor

    transfer = Transfer()
    editor = ScreenGlyphEditor({})
    qtbot.addWidget(editor)
    editor.bind_transfer(transfer)
    assert "单色点阵效果" in editor.preview.text()
    picture = QImage(3, 3, QImage.Format_ARGB32)
    picture.fill(QColor("white"))
    path = str(tmp_path / "shape.png")
    assert picture.save(path)
    editor.import_image(path)
    assert editor.preview.text() == ""
    assert not editor.preview.pixmap().isNull()
    assert editor.write_button.isEnabled()
    editor.discard_candidate()
    assert "单色点阵效果" in editor.preview.text()
    assert not editor.write_button.isEnabled()


@pytest.mark.parametrize("language", [SIMPLIFIED_CHINESE, ENGLISH])
@pytest.mark.parametrize("width", [300, 420])
def test_guidance_fits_narrow_and_regular_columns(qapp, qtbot, tmp_path, language, width):
    from controller_config.appearance import V4_STYLE
    from PySide6.QtGui import QColor, QPalette

    manager = LanguageManager(qapp, initial_language=language, persist=False)
    glyph = ScreenGlyphEditor({})
    transfer = Transfer()
    transfer.catalog[0].update(width=14, height=14)
    glyph.bind_transfer(transfer)
    home = ScreenIconEditor(ScreenIconDraft())
    glyph.material_review._rejected("format")
    home.material_review._rejected("format")
    for name, editor in (("glyph", glyph), ("home", home)):
        qtbot.addWidget(editor)
        editor.setStyleSheet(V4_STYLE)
        palette = editor.palette()
        palette.setColor(QPalette.Window, QColor("#1E1E1A"))
        editor.setPalette(palette)
        editor.setAutoFillBackground(True)
        editor.setFixedWidth(width)
        editor.adjustSize()
        editor.show()
        qtbot.wait(30)
        # The production page scrolls vertically; do not use the top-level
        # adjustSize screen-height cap as the available content height.
        editor.resize(width, editor.layout().totalHeightForWidth(width))
        qtbot.wait(30)
        for label in editor.findChildren(QLabel):
            if not label.isVisible():
                continue
            assert label.width() <= width
            if label.wordWrap() and label.text():
                assert label.height() >= label.heightForWidth(label.width()), label.text()
        for button in editor.findChildren(QPushButton):
            option = QStyleOptionButton()
            option.initFrom(button)
            content = button.style().subElementRect(QStyle.SE_PushButtonContents, option, button)
            assert button.fontMetrics().horizontalAdvance(button.text()) <= content.width(), button.text()
        path = tmp_path / f"{name}-{language}-{width}.png"
        assert editor.grab().save(str(path))
        print(path)
    manager.set_language(SIMPLIFIED_CHINESE)
