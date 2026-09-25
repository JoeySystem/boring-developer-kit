import pytest
from PySide6.QtCore import Qt, QRect
from PySide6.QtWidgets import QWidget, QPushButton, QMessageBox, QLabel

from controller_config.prompt_device import PromptListenerStatus
from controller_config.prompt_library import PromptEntry, PromptLibrary
from controller_config.views.prompt_library_editor import PromptLibraryEditor, PromptDevicePreview
from controller_config.views.device_silhouette import DeviceModelCanvas
from test_session_recovery import session


@pytest.fixture(autouse=True)
def restore_language(session):
    yield
    session[0]._language_manager.set_language("zh_CN")


@pytest.mark.parametrize("width,language", [(1440, "zh_CN"), (1280, "zh_CN"), (1280, "en_US"), (1280, "ja_JP")])
def test_prompt_workspace_layout_and_selection(session, qtbot, tmp_path, monkeypatch, width, language):
    window, vm, gateway, snapshot, store = session
    window._language_manager.set_language(language)
    window.resize(width, 900)
    vm.navigate("prompts")
    window.show()
    window.resize(width, 900)  # Exercise the requested layout after screen-bound startup.
    qtbot.wait(200)
    editor = window._content.findChild(PromptLibraryEditor)
    left = editor.findChild(QWidget, "promptLeftColumn")
    right = editor.findChild(QWidget, "promptRightColumn")
    stage = editor.findChild(QWidget, "promptDirectionStage")
    assert left.geometry().right() < stage.geometry().left()
    assert stage.geometry().right() < right.geometry().left()
    assert not editor.findChild(QPushButton, "savePromptDraft").isVisible()
    assert editor.findChild(QPushButton, "writePromptDevice").isVisible()
    assert editor.findChild(QPushButton, "writePromptDevice").text() == (
        "Save to device" if language == "en_US"
        else "本体に保存" if language == "ja_JP" else "保存到设备"
    )
    if language == "en_US":
        assert "does not send it automatically" in editor._body.placeholderText()
    assert window.grab().save(str(tmp_path / "prompts-wide.png"))
    preview = editor.findChild(PromptDevicePreview)
    canvas = preview.findChild(DeviceModelCanvas, "deviceModelCanvas")
    assert canvas is not None
    assert canvas._preset == "prompt"
    assert canvas.modelYaw == -90.0
    before = len(gateway.commands)
    canvas.activateControl("joystick.right")
    assert editor._selected_prompt_id() == 2
    key_target = canvas._springs["key.1"].target
    canvas.activateControl("key.1")
    canvas.activateControl("encoder.press")
    canvas.activateControl("joystick.press")
    assert canvas._springs["key.1"].target == key_target
    assert canvas._springs["encoder.press"].target == 0.0
    assert canvas._springs["joystick.press"].target == 0.0
    for prompt_id, button in editor._preview_buttons.items():
        # Direction buttons are QWidget overlays anchored to the full 3D model.
        point = button.mapTo(preview, button.rect().center())
        assert preview.rect().contains(point)
        qtbot.mouseClick(button, Qt.LeftButton)
        assert editor._selected_prompt_id() == prompt_id
        assert editor._direction_buttons[prompt_id].property("selected") is True
    assert len(gateway.commands) == before
    editor._name.setText("审查")
    editor._body.setPlainText("中文\nEnglish")
    monkeypatch.setattr(
        "controller_config.views.prompt_library_editor.confirm_local_draft",
        lambda *_: QMessageBox.Cancel,
    )
    editor._preview_buttons[1].click()
    assert editor._selected_prompt_id() == 4
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *_: pytest.fail("保存到设备不应再要求二次确认"),
    )
    editor.findChild(QPushButton, "writePromptDevice").click()
    if gateway.commands[-1].name == "GET_PROMPT_EVENT":
        gateway.command_completed.emit(
            "GET_PROMPT_EVENT",
            {
                "command": "GET_PROMPT_EVENT",
                "result": {"poll_after_ms": 100, "event": None},
            },
        )
    entry = store.load(snapshot.identity["serial"]).draft_entry(4)
    assert entry is not None
    assert gateway.commands[-1].name == "SET_PROMPT"
    gateway.command_completed.emit(
        "SET_PROMPT",
        {
            "command": "SET_PROMPT",
            "result": {
                "prompt_id": 4,
                "name": entry.name,
                "body_bytes": entry.body_bytes,
            },
        },
    )
    assert gateway.commands[-1].name == "GET_PROMPT"
    gateway.command_completed.emit(
        "GET_PROMPT",
        {
            "command": "GET_PROMPT",
            "result": {
                "prompt_id": 4,
                "name": entry.name,
                "body": entry.body,
                "body_bytes": entry.body_bytes,
            },
        },
    )
    assert not vm.prompt_device.status.is_busy
    assert vm.prompt_device.status.technical == ""
    editor = window._content.findChild(PromptLibraryEditor)
    assert store.load(snapshot.identity["serial"]).draft_entry(4).body == "中文\nEnglish"
    assert vm.prompt_library.confirmed_entry(4).body == "中文\nEnglish"
    assert editor.findChild(QPushButton, "writePromptDevice").text() == (
        "Saved to device" if language == "en_US"
        else "本体に保存済み" if language == "ja_JP" else "已保存到设备"
    )
    assert editor.findChild(QPushButton, "deletePromptDevice").isVisible()
    assert not editor.findChild(QPushButton, "discardPromptDraft").isVisible()
    editor._body.setPlainText("未保存")
    assert editor.findChild(QLabel, "promptSyncState").text() == (
        "Editing — not saved" if language == "en_US"
        else "編集中・未保存" if language == "ja_JP" else "正在编辑，尚未保存"
    )
    assert editor.findChild(QPushButton, "discardPromptDraft").isVisible()
    window._fit_window_to_available_area(QRect(0, 0, 900, 800))
    qtbot.wait(150)
    assert editor._body.toPlainText() == "未保存"
    assert editor.widget().width() <= editor.viewport().width()
    assert window.grab().save(str(tmp_path / "prompts-compact.png"))
    editor._body.setPlainText("中文\nEnglish")
    window._language_manager.set_language("zh_CN")


def test_existing_prompt_actions_distinguish_device_and_draft(qtbot, monkeypatch):
    device_entry = PromptEntry(1, "设备版本", "设备正文")
    local_entry = PromptEntry(2, "仅本地", "本地正文")
    library = PromptLibrary(
        "CP01-AABBCCDDEEFF", confirmed=(device_entry,), draft=(device_entry, local_entry),
    )
    calls = []
    editor = PromptLibraryEditor(
        library,
        device_storage_available=True, protocol_message="", device_busy=False,
        helper_message="", listener_status=PromptListenerStatus(),
        event_log=(), background_message="",
        save_draft=lambda *_: None, delete_draft=lambda *_: None,
        discard_draft=lambda *_: None, refresh_device=lambda: calls.append("refresh"),
        write_device=lambda *_: None, delete_device=lambda *_: None,
    )
    qtbot.addWidget(editor)
    editor.show()
    assert editor.findChild(QLabel, "promptQuickCount").text() == "设备已保存 1/4"
    assert editor.findChild(QPushButton, "writePromptDevice").text() == "已保存到设备"
    assert not editor.findChild(QPushButton, "writePromptDevice").isEnabled()
    assert editor.findChild(QPushButton, "deletePromptDevice").isVisible()
    assert not editor.findChild(QPushButton, "deletePromptDraft").isVisible()
    editor._body.setPlainText("尚未保存的输入")
    assert editor.findChild(QPushButton, "writePromptDevice").text() == "保存更改到设备"
    assert editor.findChild(QPushButton, "discardPromptDraft").isVisible()
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Cancel)
    editor.findChild(QPushButton, "refreshPromptDevice").click()
    assert calls == []
    assert editor._body.toPlainText() == "尚未保存的输入"
    editor._load_direction(2)
    assert editor.findChild(QPushButton, "writePromptDevice").text() == "将草稿保存到设备"
    assert editor.findChild(QPushButton, "deletePromptDraft").isVisible()
    assert not editor.findChild(QPushButton, "deletePromptDevice").isVisible()


def test_prompt_operation_demo_preserves_draft_and_never_sends(session, qtbot, monkeypatch):
    window, vm, gateway, _snapshot, _store = session
    vm.navigate("prompts")
    window.show()
    editor = window._content.findChild(PromptLibraryEditor)
    editor._select_direction(4)
    editor._name.setText("未保存名称")
    editor._body.setPlainText("未保存正文")
    monkeypatch.setattr(
        "controller_config.views.prompt_library_editor.confirm_local_draft",
        lambda *_: pytest.fail("模型演示不应离开当前编辑"),
    )
    canvas = editor.findChild(DeviceModelCanvas, "deviceModelCanvas")
    steps = editor.findChildren(QPushButton, "promptOperationStep")
    assert [button.property("guideControl") for button in steps] == [
        "key.12", "joystick.up", "encoder.press"
    ]
    assert all(button.isVisible() and button.isEnabled() for button in steps)
    assert editor.findChild(QWidget, "promptPaletteInstructions").isHidden()
    commands = list(gateway.commands)
    scroll = editor.verticalScrollBar().value()
    for button, expected_control in zip(steps, ("key.12", "joystick", "encoder")):
        button.click()
        assert canvas.selectedControlId == expected_control
        assert editor._selected_prompt_id() == 4
        assert editor._name.text() == "未保存名称"
        assert editor._body.toPlainText() == "未保存正文"
        assert editor.verticalScrollBar().value() == scroll
        assert gateway.commands == commands
    editor._name.clear()
    editor._body.clear()


@pytest.mark.parametrize("choice", [QMessageBox.Save, QMessageBox.Discard, QMessageBox.Cancel])
def test_prompt_leave_choice_keeps_local_and_device_states_distinct(session, monkeypatch, choice):
    window, vm, gateway, snapshot, store = session
    vm.save_prompt_draft(1, "已保留名称", "已保留正文")
    vm.navigate("prompts")
    editor = window._content.findChild(PromptLibraryEditor)
    editor._name.setText("修改名称")
    editor._body.setPlainText("修改正文")
    monkeypatch.setattr(
        "controller_config.views.prompt_library_editor.confirm_local_draft",
        lambda *_: choice,
    )
    commands = list(gateway.commands)
    assert editor.confirm_leave() is (choice != QMessageBox.Cancel)
    retained = store.load(snapshot.identity["serial"]).draft_entry(1)
    assert retained.body == ("修改正文" if choice == QMessageBox.Save else "已保留正文")
    if choice == QMessageBox.Discard:
        assert editor.editing_state().body == "已保留正文"
        assert not editor.has_unsaved_fields()
    elif choice == QMessageBox.Cancel:
        assert editor._body.toPlainText() == "修改正文"
        assert editor.has_unsaved_fields()
    assert gateway.commands == commands
    editor._load_selected()


@pytest.mark.parametrize("hardware_id", [None, "WMP-S3-REV-A", "WMP-S3-MATRIX12-V1", "WMP-S3-MATRIX12-POWER-V2"])
def test_prompt_operation_guide_matches_hardware(qtbot, hardware_id):
    editor = PromptLibraryEditor(
        PromptLibrary("CP01-AABBCCDDEEFF"),
        hardware_id=hardware_id,
        device_storage_available=False, protocol_message="设备离线", device_busy=False,
        helper_message="尚未粘贴", listener_status=PromptListenerStatus(),
        event_log=(), background_message="助手离线",
        save_draft=lambda *_: None, delete_draft=lambda *_: None,
        discard_draft=lambda *_: None, refresh_device=lambda: None,
        write_device=lambda *_: None, delete_device=lambda *_: None,
    )
    qtbot.addWidget(editor)
    steps = editor.findChildren(QPushButton, "promptOperationStep")
    guide_text = "\n".join(label.text() for label in editor.findChildren(QLabel))
    if hardware_id == "WMP-S3-MATRIX12-POWER-V2":
        assert [button.property("guideControl") for button in steps] == [
            "key.12", "joystick.up", "encoder.press"
        ]
        assert "Key12" in guide_text or "12 号按键" in guide_text
        assert editor.findChild(QLabel, "promptDirectTriggerGuide") is None
    else:
        assert not steps
        assert editor.findChild(QLabel, "promptDirectTriggerGuide") is not None
        assert "Key12" not in guide_text and "12 号" not in guide_text
        assert "长按" not in guide_text
        assert "直接触发" in guide_text
