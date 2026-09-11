import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter
from controller_config.digital_font import draw_qpainter, layout_text, normalize_text, supports_text
from controller_config.views.digital_label import Boring5RLabel


def test_system_font_layout_scales_without_bundled_font(qapp):
    normal = layout_text("COMMUNITY")
    doubled = layout_text("COMMUNITY", scale=2)
    assert normal.width > 0
    assert doubled.width == pytest.approx(normal.width * 2)
    assert doubled.height == normal.height * 2
    assert normalize_text("codex 12") == "CODEX 12"


def test_system_font_draws_visible_text(qapp):
    image = QImage(220, 40, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    layout = draw_qpainter(painter, "COMMUNITY", 4, 4, color=QColor("#202324"))
    painter.end()
    assert layout.width < image.width() - 8
    assert any(QColor.fromRgba(image.pixel(x, y)).alpha() > 0
               for x in range(image.width()) for y in range(image.height()))


def test_label_keeps_text_and_accessibility(qtbot):
    label = Boring5RLabel("codex", scale=0.75)
    qtbot.addWidget(label)
    assert label.text() == label.accessibleName() == "CODEX"
    assert label.sizeHint().width() >= layout_text("codex", scale=0.75).width
    assert label.sizeHint().height() > 0


def test_non_ascii_actions_use_normal_label_fallback(qapp):
    assert supports_text("CODEX 12")
    assert not supports_text("状态灯")
    with pytest.raises(ValueError, match="不支持字符"):
        normalize_text("状态灯")
