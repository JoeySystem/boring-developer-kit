import pytest
from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QWidget, QPushButton, QMessageBox, QLabel

from controller_config.prompt_device import PromptListenerStatus
from controller_config.prompt_library import PromptEntry, PromptLibrary
from controller_config.views.prompt_library_editor import PromptLibraryEditor
from controller_config.style import tokens
from controller_config.views.ui import DeviceTopView, Menu, StepNumber
from test_session_recovery import session
from notice_helpers import answer_questions
from notice_helpers import answer_warnings


@pytest.fixture(autouse=True)
def restore_language(session):
    yield
    session[0]._language_manager.set_language("zh_CN")


@pytest.mark.parametrize("width,language", [(1440, "zh_CN"), (1280, "zh_CN"), (1280, "en_US"), (1280, "ja_JP")])
def test_prompt_workspace_layout_and_selection(session, qtbot, tmp_path, monkeypatch, width, language):
    """One wall (the designer Prompt 页定稿 v1): the line on top, the cross left, the editor right."""
    window, vm, gateway, snapshot, store = session
    window._language_manager.set_language(language)
    window.resize(width, 900)
    vm.navigate("prompts")
    window.show()
    window.resize(width, 900)  # Exercise the requested layout after screen-bound startup.
    qtbot.wait(200)
    editor = window._content.findChild(PromptLibraryEditor)
    cross = editor.findChild(QWidget, "promptChoicesCard")
    focus = editor.findChild(QWidget, "promptFocusCard")
    top = editor.findChild(QWidget, "promptStatusBar")
    assert cross.geometry().right() < focus.geometry().left()
    assert cross.span == (2, 2) and focus.span == (2, 2)
    assert top.mapTo(editor, top.rect().bottomLeft()).y() < cross.mapTo(editor, cross.rect().topLeft()).y()
    assert top.width() == editor._wall.width()
    assert not editor.findChild(QPushButton, "savePromptDraft").isVisible()
    assert editor.findChild(QPushButton, "writePromptDevice").isVisible()
    assert editor.findChild(QPushButton, "writePromptDevice").text() == (
        "Save to device" if language == "en_US"
        else "本体に保存" if language == "ja_JP" else "保存到设备"
    )
    if language == "en_US":
        assert "Enter is not pressed automatically" in editor._body.placeholderText()
    assert window.grab().save(str(tmp_path / "prompts-wide.png"))
    before = len(gateway.commands)
    for prompt_id in (2, 3, 4, 1):
        editor._select_direction(prompt_id)
        assert editor._selected_prompt_id() == prompt_id
        assert editor._cross.selected() == prompt_id
    assert len(gateway.commands) == before
    editor._name.setText("审查")
    editor._body.setPlainText("中文\nEnglish")
    asked = []
    # 「审查」还没存进设备: the page asks before leaving the edits (定稿 v1 第 4 张).
    monkeypatch.setattr(editor, "_ask_before_switching", lambda: asked.append(True) or editor._save())
    editor._select_direction(4)
    assert asked == [True]
    assert editor._selected_prompt_id() == 4
    assert store.load(snapshot.identity["serial"]).draft_entry(1).body == "中文\nEnglish"
    editor._select_direction(1)
    answer_warnings(monkeypatch, lambda *_: pytest.fail("保存到设备不应再要求二次确认"),
    )
    editor.findChild(QPushButton, "writePromptDevice").click()
    if gateway.commands[-1].name == "GET_PROMPT_EVENT":
        gateway.command_completed.emit(
            "GET_PROMPT_EVENT",
            {"command": "GET_PROMPT_EVENT", "result": {"poll_after_ms": 100, "event": None}},
        )
    entry = store.load(snapshot.identity["serial"]).draft_entry(1)
    assert entry is not None
    assert gateway.commands[-1].name == "SET_PROMPT"
    gateway.command_completed.emit("SET_PROMPT", {
        "command": "SET_PROMPT",
        "result": {"prompt_id": 1, "name": entry.name, "body_bytes": entry.body_bytes},
    })
    assert gateway.commands[-1].name == "GET_PROMPT"
    gateway.command_completed.emit("GET_PROMPT", {
        "command": "GET_PROMPT",
        "result": {"prompt_id": 1, "name": entry.name, "body": entry.body, "body_bytes": entry.body_bytes},
    })
    assert not vm.prompt_device.status.is_busy
    assert vm.prompt_device.status.technical == ""
    editor = window._content.findChild(PromptLibraryEditor)
    assert vm.prompt_library.confirmed_entry(1).body == "中文\nEnglish"
    assert not editor.findChild(QPushButton, "writePromptDevice").isEnabled()
    assert editor.findChild(QLabel, "promptSyncState").text() == (
        "Saved to device" if language == "en_US"
        else "本体に保存済み" if language == "ja_JP" else "已存进设备"
    )
    assert editor.findChild(QAction, "deletePromptDevice").isVisible()
    assert not editor.findChild(QAction, "discardPromptDraft").isVisible()
    editor._body.setPlainText("未保存")
    assert editor.findChild(QLabel, "promptSyncState").text() == (
        "Changes not saved to device yet" if language == "en_US"
        else "変更あり・本体に未保存" if language == "ja_JP" else "有修改，还没存进设备"
    )
    assert editor.findChild(QAction, "discardPromptDraft").isVisible()
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
    assert [editor._cross.state(prompt_id) for prompt_id in (1, 2, 3, 4)] == ["saved", "dirty", "empty", "empty"]
    assert editor.findChild(QPushButton, "writePromptDevice").text() == "保存到设备"
    assert editor.findChild(QLabel, "promptSyncState").text() == "已存进设备"
    assert not editor.findChild(QPushButton, "writePromptDevice").isEnabled()
    assert editor.findChild(QAction, "deletePromptDevice").isVisible()
    assert not editor.findChild(QAction, "deletePromptDraft").isVisible()
    editor._body.setPlainText("尚未保存的输入")
    assert editor.findChild(QPushButton, "writePromptDevice").text() == "保存到设备"
    assert editor.findChild(QLabel, "promptSyncState").text() == "有修改，还没存进设备"
    assert editor.findChild(QPushButton, "writePromptDevice").isEnabled()
    assert editor.findChild(QAction, "discardPromptDraft").isVisible()
    answer_questions(monkeypatch, lambda *_: QMessageBox.StandardButton.Cancel)
    editor.findChild(QPushButton, "promptStatusRefresh").click()
    assert calls == []
    assert editor._body.toPlainText() == "尚未保存的输入"
    editor._load_direction(2)
    assert editor.findChild(QPushButton, "writePromptDevice").text() == "保存到设备"
    assert editor.findChild(QLabel, "promptSyncState").text() == "有修改，还没存进设备"
    assert editor.findChild(QAction, "deletePromptDraft").isVisible()
    assert not editor.findChild(QAction, "deletePromptDevice").isVisible()


def test_prompt_operation_demo_preserves_draft_and_never_sends(session, qtbot, monkeypatch):
    """看演示 swaps the cross for the device and three steps; it never sends or edits."""
    window, vm, gateway, _snapshot, _store = session
    vm.navigate("prompts")
    window.show()
    editor = window._content.findChild(PromptLibraryEditor)
    editor._select_direction(4)
    editor._name.setText("未保存名称")
    editor._body.setPlainText("未保存正文")
    monkeypatch.setattr(
        "controller_config.views.prompt_library_editor.confirm_local_draft",
        lambda *_: pytest.fail("演示不应离开当前编辑"),
    )
    editor.show_demo(True)
    assert editor.findChild(QWidget, "promptOperationGuide").isVisible()
    assert not editor.findChild(QWidget, "promptCrossPage").isVisible()
    device = editor.findChild(DeviceTopView, "promptDemoDevice")
    steps = editor.findChildren(QPushButton, "promptOperationStep")
    assert [button.property("guideControl") for button in steps] == [
        "key.12", "joystick.up", "encoder.press"
    ]
    assert all(button.isVisible() and button.isEnabled() for button in steps)
    marks = editor.findChildren(StepNumber, "promptStepNumber")
    assert [mark.text() for mark in marks] == ["1", "2", "3"]
    assert editor._demo_timer.interval() == 2000  # 每步亮 2.0 秒（design decision）
    commands = list(gateway.commands)
    for index, button in enumerate(steps):
        button.click()
        assert button.property("current") is True
        assert [mark.is_current() for mark in marks] == [position == index for position in range(3)]
        # 第 1 步标 12 号键；第 2 步标摇杆（橙框＋朝上箭头）；第 3 步标旋钮（橙环＋变深）。
        assert device.selected() == ("key.12" if index == 0 else None)
        assert device.part_mark() == (None, "joystick.up", "encoder.press")[index]
        assert editor._selected_prompt_id() == 4
        assert editor._name.text() == "未保存名称"
        assert editor._body.toPlainText() == "未保存正文"
        assert gateway.commands == commands
    editor.findChild(QPushButton, "promptDemoBack").click()
    assert editor.findChild(QWidget, "promptCrossPage").isVisible()
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
        assert "12 号键" in guide_text
        assert editor.findChild(QLabel, "promptDirectTriggerGuide") is None
    else:
        assert not steps
        assert editor.findChild(QLabel, "promptDirectTriggerGuide") is not None
        assert "Key12" not in guide_text and "12 号" not in guide_text
        assert "长按" not in guide_text
        assert "拨一下就打出来" in guide_text


def test_prompt_more_menu_and_save_button_follow_the_draft(session, qtbot, monkeypatch):
    """「···」菜单照定稿 .menu；按不了的「保存到设备」是灰底浅字（design decision）。"""
    window, vm, _gateway, _snapshot, _store = session
    vm.navigate("prompts")
    window.show()
    editor = window._content.findChild(PromptLibraryEditor)
    editor._select_direction(4)
    editor._name.clear()
    editor._body.clear()
    qtbot.wait(50)

    save = editor.findChild(QPushButton, "writePromptDevice")
    assert not save.isEnabled()
    assert save.height() == tokens.CONTROL_HEIGHT
    assert save.grab().toImage().pixelColor(save.width() // 2, 3).name().upper() == tokens.PROMPT_SAVE_OFF
    editor._name.setText("翻译成英文")
    editor._body.setPlainText("请翻译")
    assert save.isEnabled()
    assert save.grab().toImage().pixelColor(save.width() // 2, 3).name().upper() == tokens.ACCENT

    menu = editor._more_menu
    assert isinstance(menu, Menu) and isinstance(editor._example_menu, Menu)
    draft = editor.findChild(QAction, "deletePromptDraft")
    device = editor.findChild(QAction, "deletePromptDevice")
    assert draft.text() == "放弃这次修改"
    device.setVisible(True)
    device.setEnabled(True)
    assert Menu.item_color(device) == tokens.MENU_TEXT_DANGER
    actions = menu.actions()
    assert actions[actions.index(device) - 1].isSeparator()  # 「从设备删除」上面一条细线
    editor._discard_action.setVisible(True)
    editor._discard_action.setEnabled(False)
    assert Menu.item_color(editor._discard_action) == tokens.MENU_TEXT_DISABLED
    editor._more_button.click()
    qtbot.waitUntil(menu.isVisible)
    card = menu.card_rect()
    assert card.width() >= tokens.MENU_WIDTH
    assert all(menu.actionGeometry(action).height() == tokens.MENU_ITEM_HEIGHT
               for action in actions if action.isVisible() and not action.isSeparator())
    menu.hide()

    asked = []
    monkeypatch.setattr("controller_config.views.prompt_library_editor.confirm",
                        lambda _parent, title, text, *_a, **_k: asked.append((title, text)) or False)
    editor._delete()
    assert asked == [("放弃这次修改", "这台电脑上还没存进设备的修改会丢掉，设备里的内容不变。确定放弃吗？")]
    editor._refresh_primary_action()
    editor._name.clear()
    editor._body.clear()


def test_english_translate_example_button_is_short_and_keeps_the_prompt_name():
    """design decision 第 3 件: the three example buttons are as wide as their words; the
    English "translate" button says "To English" so the row fits (zh text unchanged)."""
    from controller_config.text_catalog import get_text_catalog
    from controller_config.views.prompt_library_editor import PROMPT_EXAMPLE_CHIPS
    catalog = get_text_catalog()
    short = PROMPT_EXAMPLE_CHIPS[1][0]
    assert catalog.translate(short, "zh_CN") == "翻译成英文"
    assert catalog.translate(short, "en_US") == "To English"
    assert PROMPT_EXAMPLE_CHIPS[1][1] == "翻译成英文"   # the prompt's name stays
