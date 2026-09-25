from dataclasses import replace

import pytest
from PySide6.QtGui import QColor, QCloseEvent, QImage
from PySide6.QtWidgets import QLabel, QMessageBox, QPushButton, QWidget

from controller_config.transport.demo import DemoGateway
from controller_config.viewmodels.main import MainViewModel
from controller_config.views.main_window import MainWindow
from controller_config.views.screen_icon_editor import ScreenIconDraft, ScreenIconEditor
from controller_config.screen_icon_transfer import ScreenIconTransfer


@pytest.fixture
def image_path(tmp_path):
    image = QImage(240, 160, QImage.Format_ARGB32)
    image.fill(QColor("orange"))
    path = tmp_path / "test.png"
    assert image.save(str(path))
    return str(path)


def test_local_import_preserves_edits_after_failed_import_and_rebuild(qtbot, image_path):
    draft = ScreenIconDraft()
    editor = ScreenIconEditor(draft)
    qtbot.addWidget(editor)
    editor.import_image(image_path)
    editor.zoom.setValue(200)
    editor.horizontal.setValue(700)
    before = editor.payload
    with pytest.raises(OSError):
        editor.import_image(image_path + ".missing")
    assert editor.payload == before
    rebuilt = ScreenIconEditor(draft)
    qtbot.addWidget(rebuilt)
    assert rebuilt.payload == before
    assert rebuilt.zoom.value() == 200
    assert rebuilt.horizontal.value() == 700
    rebuilt.discard_candidate()
    assert not draft.is_dirty
    assert rebuilt.payload is None


@pytest.mark.parametrize("supported", [False, True])
def test_device_buttons_wait_for_real_protocol(qtbot, image_path, supported):
    editor = ScreenIconEditor(ScreenIconDraft(), device_supported=supported)
    qtbot.addWidget(editor)
    editor.import_image(image_path)
    assert len(editor.payload) == 32768
    assert not editor.findChild(QPushButton, "screenIconWrite").isEnabled()
    assert not editor.findChild(QPushButton, "screenIconReset").isEnabled()


def test_empty_editor_puts_import_first_without_blank_preview(qtbot, image_path):
    editor = ScreenIconEditor(ScreenIconDraft())
    qtbot.addWidget(editor)

    assert not editor.import_button.isHidden()
    assert editor.import_button.text() == "选择首页图片"
    assert editor.import_button.property("buttonRole") == "primary"
    assert editor.device_preview.isHidden()
    assert editor.preview.isHidden()
    assert editor.filename.isHidden()
    assert editor.findChild(QWidget, "screenIconCropControls").isHidden()
    assert editor.findChild(QWidget, "screenIconRequirements").isHidden()
    assert editor.device_buttons["screenIconReset"].isHidden()
    assert editor.refresh_button.isHidden()
    assert editor.cancel_button.isHidden()
    assert editor.local_retention.isHidden()

    editor.requirements_toggle.click()
    assert not editor.findChild(QWidget, "screenIconRequirements").isHidden()
    editor.import_image(image_path)
    assert not editor.preview.isHidden()
    assert not editor.filename.isHidden()
    assert not editor.findChild(QWidget, "screenIconCropControls").isHidden()
    assert editor.import_button.property("buttonRole") == "secondary"
    assert editor.device_buttons["screenIconWrite"].property("buttonRole") == "primary"
    assert not editor.local_retention.isHidden()


def test_device_actions_follow_current_home_image_state(qtbot, contract):
    transfer = ScreenIconTransfer(contract, lambda command: None)
    transfer.supported = transfer.writable = True
    editor = ScreenIconEditor(ScreenIconDraft())
    qtbot.addWidget(editor)
    editor.bind_transfer(transfer)

    transfer.metadata = {
        "revision": 1,
        "source": "default",
        "target": "normal_home",
        "format": "rgb565_le",
        "width": 128,
        "height": 128,
        "total_bytes": 0,
    }
    transfer.status = "设备正在使用默认图标"
    transfer.changed.emit()
    assert editor.device_label.text() == "当前：默认图片"
    assert editor.device_buttons["screenIconReset"].isHidden()
    assert editor.refresh_button.isHidden()

    transfer.metadata = {**transfer.metadata, "source": "custom", "total_bytes": 32768}
    transfer.pixels = bytes(32768)
    transfer.status = "已读取设备自定义图标"
    transfer.changed.emit()
    assert editor.device_label.text() == "当前：自定义图片"
    assert not editor.device_buttons["screenIconReset"].isHidden()
    assert not editor.reset_scope.isHidden()

    transfer.metadata = None
    transfer.pixels = None
    transfer.state = "error"
    transfer.status = "图标操作失败：读取超时"
    transfer.changed.emit()
    assert not editor.refresh_button.isHidden()
    assert not editor.transfer_status.isHidden()


def test_drag_and_slider_edge_have_no_hidden_pan_offset(qtbot, image_path):
    editor = ScreenIconEditor(ScreenIconDraft())
    qtbot.addWidget(editor)
    editor.import_image(image_path)
    editor.zoom.setValue(200)
    editor.horizontal.setValue(0)
    assert editor.draft.center_x == pytest.approx(1 / 6)
    editor._pan(-0.1, 0)
    assert editor.draft.center_x > 1 / 6
    assert editor.horizontal.value() > 167


def test_window_isolates_devices_and_retains_local_icon_across_pages(qtbot, contract, image_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Discard)
    vm = MainViewModel(DemoGateway(contract, "ready"), contract)
    window = MainWindow(vm)
    qtbot.addWidget(window)
    vm.start()
    qtbot.waitUntil(lambda: vm.model.snapshot is not None)
    snapshot = vm.model.snapshot
    window._navigate("lighting")
    editor = window.findChild(ScreenIconEditor)
    assert editor is not None
    editor.import_image(image_path)
    before = editor.payload
    assert not vm.draft.is_dirty
    assert window._screen_icon_draft_for(snapshot).is_dirty
    other = replace(snapshot, identity={**snapshot.identity, "serial": "CP01-112233445566"})
    assert not window._screen_icon_draft_for(other).is_dirty
    reconnect = replace(snapshot, port_name="ble:reconnected")
    assert window._screen_icon_draft_for(reconnect) is editor.draft
    window._navigate("overview")
    window._navigate("lighting")
    assert window.findChild(ScreenIconEditor).payload == before
    window.findChild(ScreenIconEditor).discard_candidate()
    vm.shutdown()


def test_quit_cancel_keeps_image_and_discard_accepts(qtbot, contract, image_path, monkeypatch):
    vm = MainViewModel(DemoGateway(contract, "ready"), contract)
    window = MainWindow(vm)
    qtbot.addWidget(window)
    vm.start()
    qtbot.waitUntil(lambda: vm.model.snapshot is not None)
    draft = window._screen_icon_draft_for(vm.model.snapshot)
    editor = ScreenIconEditor(draft)
    qtbot.addWidget(editor)
    editor.import_image(image_path)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Cancel)
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted()
    assert draft.is_dirty
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Discard)
    event = QCloseEvent()
    window.closeEvent(event)
    assert event.isAccepted()
    assert not window._screen_icon_drafts


def test_verified_readback_clears_only_matching_candidate(qtbot, contract, image_path):
    transfer = ScreenIconTransfer(contract, lambda command: None)
    transfer.supported = transfer.writable = True
    editor = ScreenIconEditor(ScreenIconDraft())
    qtbot.addWidget(editor)
    editor.bind_transfer(transfer)
    editor.import_image(image_path)
    assert editor.device_buttons["screenIconWrite"].isEnabled()
    pixels = editor.payload
    transfer.verified_upload = b"\x00" * 32768
    transfer.changed.emit()
    assert editor.draft.is_dirty
    transfer.pixels = transfer.verified_upload = pixels
    transfer.changed.emit()
    assert not editor.draft.is_dirty
    assert not editor.device_preview.pixmap().isNull()
    assert not editor.device_buttons["screenIconWrite"].isEnabled()


@pytest.mark.parametrize("operation", ["_confirm_upload", "_confirm_reset"])
def test_confirmation_cannot_target_reconnected_device(qtbot, contract, image_path, monkeypatch, operation):
    transfer = ScreenIconTransfer(contract, lambda command: None)
    editor = ScreenIconEditor(ScreenIconDraft())
    qtbot.addWidget(editor)
    editor.bind_transfer(transfer)
    editor.import_image(image_path)
    requests = []
    warnings = []
    editor.upload_requested.connect(lambda data: requests.append(data))
    editor.reset_requested.connect(lambda: requests.append("reset"))
    def reconnect(*args):
        transfer.attach(None)
        return QMessageBox.Yes
    monkeypatch.setattr(QMessageBox, "question", reconnect)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))
    getattr(editor, operation)()
    assert not requests
    assert len(warnings) == 1


def test_busy_transfer_blocks_quit_but_unknown_result_does_not(qtbot, contract, monkeypatch):
    vm = MainViewModel(DemoGateway(contract, "ready"), contract)
    window = MainWindow(vm)
    qtbot.addWidget(window)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: QMessageBox.Ok)
    vm.screen_icon.busy = True
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted()
    vm.screen_icon.busy = False
    vm.screen_icon.state = "unknown"
    event = QCloseEvent()
    window.closeEvent(event)
    assert event.isAccepted()
