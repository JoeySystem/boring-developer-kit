import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QMessageBox

from controller_config.views.screen_glyph_editor import GlyphDraft, ScreenGlyphEditor, render_glyph


class Transfer(QObject):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.supported = self.writable = True
        self.busy = False
        self.connection_epoch = 1
        self.device_key = ("CP01-AABBCCDDEEFF", "MIST")
        self.catalog = [dict(id=name, resource_id=i, width=3, height=3, editable=name != "warning")
                        for i, name in enumerate(("timer", "settings", "warning", "normal_mode"))]
        self.selected_id = "timer"
        self.metadata = None
        self.pixels = None
        self.status = "已连接"
        self.verified_write = None

    def select(self, name):
        self.selected_id = name
        self.metadata = self.pixels = None
        self.changed.emit()


@pytest.fixture
def image_path(tmp_path):
    image = QImage(3, 3, QImage.Format_ARGB32)
    image.fill(QColor("white"))
    path = str(tmp_path / "white.png")
    assert image.save(path)
    return path


@pytest.fixture
def editor(qtbot):
    transfer = Transfer()
    widget = ScreenGlyphEditor({})
    widget.bind_transfer(transfer)
    widget.selection_requested.connect(transfer.select)
    qtbot.addWidget(widget)
    return widget, transfer


def test_fit_preserves_aspect_and_keeps_black_letterbox():
    image = QImage(4, 2, QImage.Format_ARGB32)
    image.fill(QColor("white"))
    assert render_glyph(image, 4, 4) == bytes([0] * 4 + [255] * 8 + [0] * 4)


def test_alpha_and_inversion_are_not_color_or_circular_masks():
    image = QImage(2, 2, QImage.Format_ARGB32)
    image.fill(QColor(255, 255, 255, 128))
    image.setPixelColor(0, 0, QColor(0, 0, 0, 255))
    image.setPixelColor(1, 0, QColor(0, 0, 0, 0))
    assert render_glyph(image, 2, 2) == bytes([0, 0, 128, 128])
    assert render_glyph(image, 2, 2, invert=True) == bytes([255, 0, 0, 0])


def test_selection_retains_distinct_drafts_and_bad_import(editor, image_path):
    widget, transfer = editor
    widget.import_image(image_path)
    first = widget.payload
    widget.selector.setCurrentIndex(1)
    assert transfer.selected_id == "settings"
    assert widget.payload is None
    widget.import_image(image_path)
    widget.invert.setChecked(True)
    assert widget.payload != first
    with pytest.raises(OSError):
        widget.import_image(image_path + ".missing")
    assert widget.draft.invert
    widget.selector.setCurrentIndex(0)
    assert widget.payload == first
    assert len(widget.drafts) == 2


def test_devices_reconnect_to_own_draft(editor, image_path):
    widget, transfer = editor
    widget.import_image(image_path)
    original = transfer.device_key
    transfer.device_key = ("CP01-112233445566", "MIST")
    transfer.changed.emit()
    assert widget.payload is None
    widget.import_image(image_path)
    widget.invert.setChecked(True)
    transfer.device_key = original
    transfer.connection_epoch += 1
    transfer.changed.emit()
    assert widget.payload == b"\xff" * 9
    assert not widget.invert.isChecked()


def test_readonly_warning_can_read_but_not_change(editor, image_path):
    widget, transfer = editor
    transfer.select("warning")
    assert widget.refresh_button.isEnabled()
    assert not widget.import_button.isEnabled()
    assert not widget.write_button.isEnabled()
    assert not widget.reset_button.isEnabled()
    with pytest.raises(ValueError):
        widget.import_image(image_path)


@pytest.mark.parametrize("changed", ["epoch", "selection", "device"])
@pytest.mark.parametrize("operation", ["write", "reset"])
def test_confirmation_cannot_mutate_changed_target(editor, image_path, monkeypatch, changed, operation):
    widget, transfer = editor
    widget.import_image(image_path)
    requests = []
    widget.write_requested.connect(lambda data: requests.append(data))
    widget.reset_requested.connect(lambda: requests.append("reset"))
    def question(*args):
        if changed == "epoch":
            transfer.connection_epoch += 1
        elif changed == "selection":
            transfer.select("settings")
        else:
            transfer.device_key = ("CP01-112233445566", "MIST")
        return QMessageBox.Yes
    monkeypatch.setattr(QMessageBox, "question", question)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: QMessageBox.Ok)
    getattr(widget, "_confirm_" + operation)()
    assert not requests


def test_write_carries_frozen_payload_and_verified_readback_clears_only_that_draft(editor, image_path, monkeypatch):
    widget, transfer = editor
    widget.import_image(image_path)
    requests = []
    original = widget.payload
    widget.write_requested.connect(requests.append)
    def question(*args):
        widget.invert.setChecked(True)
        return QMessageBox.Yes
    monkeypatch.setattr(QMessageBox, "question", question)
    widget._confirm_write()
    assert requests == [original]
    transfer.verified_write = ("timer", original)
    transfer.changed.emit()
    assert widget.draft.is_dirty
    transfer.verified_write = None
    transfer.changed.emit()
    transfer.verified_write = ("timer", widget.payload)
    transfer.changed.emit()
    assert not widget.draft.is_dirty
    widget.import_image(image_path)
    transfer.changed.emit()
    assert widget.draft.is_dirty


def test_disconnected_old_firmware_and_busy_states(editor, image_path):
    widget, transfer = editor
    widget.import_image(image_path)
    transfer.busy = True
    transfer.changed.emit()
    assert not widget.write_button.isEnabled()
    assert not widget.selector.isEnabled()
    transfer.busy = False
    transfer.supported = False
    transfer.changed.emit()
    assert not widget.reset_button.isEnabled()
    transfer.supported = True
    transfer.device_key = None
    transfer.changed.emit()
    assert widget.payload is None
    assert not widget.import_button.isEnabled()


def test_default_and_custom_readbacks_show_device_pixels(editor):
    widget, transfer = editor
    transfer.metadata = dict(id="timer", source="default")
    transfer.pixels = b"\xff" * 9
    transfer.changed.emit()
    assert not widget.device_preview.pixmap().isNull()
    assert "默认" in widget.device_label.text()
    transfer.metadata["source"] = "custom"
    transfer.changed.emit()
    assert "自定义" in widget.device_label.text()


def test_rebuilding_page_does_not_clear_new_draft_matching_old_success(qtbot, editor, image_path):
    widget, transfer = editor
    transfer.verified_write = ("timer", b"\xff" * 9)
    transfer.changed.emit()
    widget.import_image(image_path)
    rebuilt = ScreenGlyphEditor(widget.drafts)
    rebuilt.bind_transfer(transfer)
    qtbot.addWidget(rebuilt)
    assert rebuilt.draft.is_dirty
    assert rebuilt.payload == b"\xff" * 9
