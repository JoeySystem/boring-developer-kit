import struct
import sys
import zlib

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QProcess, QTimer, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QFileDialog, QMessageBox

from controller_config import icon_material_check as module
from controller_config.icon_material_check import IconMaterialCheck
from controller_config.i18n import ENGLISH, SIMPLIFIED_CHINESE, LanguageManager
from controller_config.screen_icon import MAX_FILE_BYTES
from controller_config.views.icon_material_review import REASONS
from controller_config.views.screen_glyph_editor import ScreenGlyphEditor
from controller_config.views.screen_icon_editor import ScreenIconDraft, ScreenIconEditor
from test_screen_glyph_editor import Transfer


def picture(path, color="orange"):
    image = QImage(20, 12, QImage.Format_ARGB32)
    image.fill(QColor(color))
    assert image.save(str(path), "PNG")
    return str(path)


@pytest.fixture
def check(qapp):
    checker = IconMaterialCheck()
    yield checker
    checker.cancel()
    checker.deleteLater()


@pytest.mark.parametrize("reason", ["format", "animated", "damaged", "file_size", "pixels", "transparent", "read"])
def test_real_worker_rejects_specific_reason_then_accepts_replacement(check, qtbot, tmp_path, reason):
    path = tmp_path / "bad.png"
    picture(path)
    png = path.read_bytes()
    if reason == "format":
        path.write_bytes(b"<svg/>")
    elif reason == "animated":
        payload = b"acTL" + struct.pack(">II", 2, 0)
        path.write_bytes(png[:33] + struct.pack(">I", 8) + payload + struct.pack(">I", zlib.crc32(payload)) + png[33:])
    elif reason == "damaged":
        path.write_bytes(png[:-12])
    elif reason == "file_size":
        path.write_bytes(b"x" * (MAX_FILE_BYTES + 1))
    elif reason == "pixels":
        ihdr = b"IHDR" + struct.pack(">II", 4001, 4000) + png[24:29]
        path.write_bytes(png[:12] + ihdr + struct.pack(">I", zlib.crc32(ihdr)) + png[33:])
    elif reason == "transparent":
        picture(path, "transparent")
    elif reason == "read":
        path = tmp_path / "missing.png"
    with qtbot.waitSignal(check.rejected, timeout=10000) as result:
        check.start(str(path))
    assert result.args == [reason]
    assert not check.busy
    good = picture(tmp_path / "good.png")
    with qtbot.waitSignal(check.accepted, timeout=10000) as result:
        check.start(good)
    assert result.args[0] == good
    assert result.args[1].pixelColor(0, 0) == QColor("orange")
    assert result.args[1].size() == QImage(good).size()


def stalled_worker(monkeypatch):
    original = module.worker_arguments
    monkeypatch.setattr(module, "worker_arguments", lambda path: (
        (sys.executable, "-c", "import time; time.sleep(30)")
        if path == "slow" else original(path)))


@pytest.mark.parametrize("action", ["cancel", "replace", "timeout"])
def test_slow_check_keeps_event_loop_live_and_next_image_works(check, qtbot, tmp_path, monkeypatch, action):
    stalled_worker(monkeypatch)
    check._timer.setInterval(150)
    failures = []
    accepted = []
    check.rejected.connect(failures.append)
    check.accepted.connect(lambda path, _image: accepted.append(path))
    check.start("slow")
    process = check._process
    ticks = []
    QTimer.singleShot(10, lambda: ticks.append(True))
    qtbot.waitUntil(lambda: bool(ticks))
    good = picture(tmp_path / "next.png")
    if action == "cancel":
        check.cancel()
    elif action == "timeout":
        qtbot.waitUntil(lambda: failures == ["timeout"])
    check._timer.setInterval(15000)
    with qtbot.waitSignal(check.accepted, timeout=10000):
        check.start(good)
    assert accepted == [good]
    assert failures == (["timeout"] if action == "timeout" else [])
    # The old decoder is reaped, not merely ignored while it continues running.
    qtbot.waitUntil(lambda: process not in QCoreApplication.instance().findChildren(QProcess))


def test_failed_worker_start_is_recoverable(check, qtbot, tmp_path, monkeypatch):
    original = module.worker_arguments
    monkeypatch.setattr(module, "worker_arguments", lambda path: ("/nonexistent/boring-checker",))
    with qtbot.waitSignal(check.rejected) as failed:
        check.start("unused")
    assert failed.args == ["worker"]
    monkeypatch.setattr(module, "worker_arguments", original)
    with qtbot.waitSignal(check.accepted, timeout=10000):
        check.start(picture(tmp_path / "valid.png"))


def test_crashed_worker_does_not_block_a_new_check(check, qtbot, tmp_path, monkeypatch):
    original = module.worker_arguments
    monkeypatch.setattr(module, "worker_arguments", lambda path: (sys.executable, "-c", "raise SystemExit(2)"))
    with qtbot.waitSignal(check.rejected) as failed:
        check.start("unused")
    assert failed.args == ["worker"]
    monkeypatch.setattr(module, "worker_arguments", original)
    with qtbot.waitSignal(check.accepted, timeout=10000):
        check.start(picture(tmp_path / "valid.png"))


@pytest.mark.parametrize("size,format", [((4000, 4000), "PNG"), ((40, 20), "JPEG")])
def test_supported_large_png_and_jpeg_decode_in_worker(check, qtbot, tmp_path, size, format):
    image = QImage(*size, QImage.Format_ARGB32)
    image.fill(QColor("red"))
    path = str(tmp_path / ("large." + format.lower()))
    assert image.save(path, format)
    with qtbot.waitSignal(check.accepted, timeout=15000) as result:
        check.start(path)
    assert result.args[1].size() == image.size()
    assert result.args[1].pixelColor(0, 0).red() >= 250


@pytest.mark.parametrize("packaged", ["source", "frozen", "compiled"])
def test_worker_command_handles_source_and_installed_executable(monkeypatch, packaged):
    monkeypatch.setattr(sys, "frozen", packaged == "frozen", raising=False)
    monkeypatch.delattr(module, "__compiled__", raising=False)
    if packaged == "compiled":
        monkeypatch.setattr(module, "__compiled__", object(), raising=False)
    command = module.worker_arguments("/tmp/素材.png")
    assert command[-2:] == ("--check-icon-material", "/tmp/素材.png")
    assert ("-m" in command) == (packaged == "source")


@pytest.fixture(params=["home", "glyph"])
def editor(request, qtbot):
    if request.param == "home":
        widget = ScreenIconEditor(ScreenIconDraft())
    else:
        widget = ScreenGlyphEditor({})
        widget.bind_transfer(Transfer())
    qtbot.addWidget(widget)
    return widget


def test_ui_rejection_is_inline_keeps_old_draft_and_next_import_succeeds(editor, qtbot, tmp_path, monkeypatch):
    editor.import_image(picture(tmp_path / "old.png"))
    before = editor.payload
    bad = tmp_path / "wrong.svg"
    bad.write_text("<svg/>")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(bad), ""))
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: pytest.fail("Material rejection must not open a modal"))
    with qtbot.waitSignal(editor.material_review.check.rejected, timeout=10000):
        editor._choose_image()
    assert editor.payload == before
    assert "格式不支持" in editor.material_review.message.text()
    assert editor.import_button.isEnabled()
    good = picture(tmp_path / "replacement.png", "white")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (good, ""))
    with qtbot.waitSignal(editor.material_review.check.accepted, timeout=10000):
        editor._choose_image()
    assert editor.draft.filename == "replacement.png"
    assert editor.payload != before
    assert "检查通过" in editor.material_review.message.text()
    assert editor.material_review.cancel_button.isHidden()


def test_ui_cancel_and_leave_page_do_not_apply_pending_result(editor, qtbot, monkeypatch):
    stalled_worker(monkeypatch)
    editor.show()
    editor.review_image("slow")
    assert editor.import_button.isEnabled()
    qtbot.mouseClick(editor.material_review.cancel_button, Qt.LeftButton)
    assert not editor.material_review.check.busy
    assert "已取消" in editor.material_review.message.text()
    editor.review_image("slow")
    editor.hide()
    assert not editor.material_review.check.busy
    assert not editor.draft or not editor.draft.is_dirty


def test_deleting_review_kills_worker_without_waiting_on_gui(editor, qtbot, monkeypatch):
    stalled_worker(monkeypatch)
    editor.review_image("slow")
    process = editor.material_review.check._process
    review = editor.material_review
    review.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    qtbot.waitUntil(lambda: process not in QCoreApplication.instance().findChildren(QProcess))


def test_home_reconnect_cancels_pending_material(qtbot, contract, monkeypatch):
    from controller_config.screen_icon_transfer import ScreenIconTransfer
    stalled_worker(monkeypatch)
    transfer = ScreenIconTransfer(contract, lambda command: None)
    widget = ScreenIconEditor(ScreenIconDraft())
    qtbot.addWidget(widget)
    widget.bind_transfer(transfer)
    widget.review_image("slow")
    transfer.attach(None)
    assert not widget.material_review.check.busy
    assert not widget.draft.is_dirty


@pytest.mark.parametrize("change", ["selection", "reconnect", "device", "transfer"])
def test_check_cannot_write_to_new_glyph_or_connection(qtbot, monkeypatch, change):
    stalled_worker(monkeypatch)
    widget = ScreenGlyphEditor({})
    qtbot.addWidget(widget)
    transfer = Transfer()
    widget.bind_transfer(transfer)
    widget.review_image("slow")
    assert not widget.write_button.isEnabled()
    assert widget.selector.isEnabled()
    if change == "selection":
        transfer.select("settings")
    elif change == "reconnect":
        transfer.connection_epoch += 1
    elif change == "device":
        transfer.device_key = ("CP01-112233445566", "MIST")
    else:
        transfer.busy = True
    transfer.changed.emit()
    assert not widget.material_review.check.busy
    assert not widget.drafts
    assert widget.import_button.isEnabled() == (change != "transfer")


def test_invisible_glyph_can_be_inverted_without_reimport(qtbot, tmp_path):
    widget = ScreenGlyphEditor({})
    qtbot.addWidget(widget)
    widget.bind_transfer(Transfer())
    with qtbot.waitSignal(widget.material_review.check.accepted, timeout=10000):
        widget.review_image(picture(tmp_path / "black.png", "black"))
    assert not widget.empty_notice.isHidden()
    assert not widget.write_button.isEnabled()
    assert widget.invert.isEnabled()
    widget.invert.setChecked(True)
    assert widget.empty_notice.isHidden()
    assert widget.write_button.isEnabled()


def test_all_review_reasons_translate(qapp, qtbot):
    from controller_config.views.icon_material_review import IconMaterialReview
    manager = LanguageManager(qapp, initial_language=ENGLISH, persist=False)
    widget = IconMaterialReview()
    qtbot.addWidget(widget)
    try:
        for reason in REASONS:
            widget._rejected(reason)
            assert "Existing content is unchanged" in widget.message.text()
        widget.start("unused")
        assert "Checking image" in widget.message.text()
        widget.cancel()
        assert "cancelled" in widget.message.text()
    finally:
        manager.set_language(SIMPLIFIED_CHINESE)
