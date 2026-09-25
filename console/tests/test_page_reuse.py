from dataclasses import replace

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from controller_config.i18n import ENGLISH
from controller_config.views.prompt_library_editor import PromptLibraryEditor
from test_session_recovery import session


def test_prompt_page_reuses_fields_and_scroll_across_navigation(session, qtbot):
    window, vm, _gateway, _snapshot, _store = session
    window.show()
    vm.navigate("prompts")
    qtbot.wait(20)
    editor = window._content.findChild(PromptLibraryEditor)
    editor._load_direction(3)
    editor._name.setText("待保存")
    editor._body.setPlainText("尚未保存的输入")
    scroll = editor.verticalScrollBar()
    scroll.setValue(scroll.maximum())
    position = scroll.value()

    vm.navigate("settings")
    assert not editor.isVisible()
    vm.navigate("prompts")
    qtbot.wait(20)

    assert window._content.findChild(PromptLibraryEditor) is editor
    assert editor._selected_prompt_id() == 3
    assert editor._body.toPlainText() == "尚未保存的输入"
    assert editor.verticalScrollBar().value() == position
    vm.changed.emit(vm.model)
    assert window._content.findChild(PromptLibraryEditor) is editor


def test_prompt_page_refreshes_when_library_changes_while_hidden(session):
    window, vm, _gateway, _snapshot, _store = session
    vm.navigate("prompts")
    editor = window._content.findChild(PromptLibraryEditor)
    vm.navigate("settings")
    vm.save_prompt_draft(2, "新草稿", "新的提示词正文")
    vm.navigate("prompts")
    refreshed = window._content.findChild(PromptLibraryEditor)
    assert refreshed is not editor
    refreshed._load_direction(2)
    assert refreshed._name.text() == "新草稿"
    assert refreshed._body.toPlainText() == "新的提示词正文"


def test_cached_prompt_page_does_not_follow_another_device(session):
    window, vm, gateway, snapshot, _store = session
    vm.navigate("prompts")
    editor = window._content.findChild(PromptLibraryEditor)
    editor._name.setText("电脑 A 的草稿")
    editor._body.setPlainText("不能出现在设备 B")
    vm.navigate("settings")
    gateway.disconnected.emit("unplugged")
    gateway.snapshot_ready.emit(replace(snapshot, identity={**snapshot.identity, "serial": "CP01-001122334455"}))
    vm.navigate("prompts")
    refreshed = window._content.findChild(PromptLibraryEditor)
    assert refreshed is not editor
    assert refreshed._body.toPlainText() == ""


def test_cached_prompt_page_refreshes_language(session):
    window, vm, _gateway, _snapshot, _store = session
    vm.navigate("prompts")
    editor = window._content.findChild(PromptLibraryEditor)
    vm.navigate("settings")
    window._language_manager.set_language(ENGLISH)
    vm.navigate("prompts")
    refreshed = window._content.findChild(PromptLibraryEditor)
    assert refreshed is not editor
    assert "Hold key 12" in refreshed.findChildren(QPushButton, "promptOperationStep")[0].text()


def test_building_prompt_page_does_not_show_temporary_windows(session):
    window, vm, _gateway, _snapshot, _store = session
    shown = []

    class Observer(QObject):
        def eventFilter(self, watched, event):
            if event.type() == QEvent.Show and isinstance(watched, QWidget) and watched.isWindow():
                shown.append((type(watched).__name__, watched.objectName()))
            return False

    observer = Observer()
    app = QApplication.instance()
    app.installEventFilter(observer)
    try:
        vm.navigate("prompts")
    finally:
        app.removeEventFilter(observer)
    assert shown == []
