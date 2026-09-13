import pytest
from PySide6.QtCore import Qt, QRect
from PySide6.QtWidgets import QWidget, QPushButton, QMessageBox

from controller_config.views.prompt_library_editor import PromptLibraryEditor, PromptDevicePreview
from test_session_recovery import session


@pytest.fixture(autouse=True)
def restore_language(session):
    yield
    session[0]._language_manager.set_language("zh_CN")


@pytest.mark.parametrize("width,language", [(1440, "zh_CN"), (1280, "zh_CN"), (1280, "en_US")])
def test_prompt_workspace_layout_and_selection(session, qtbot, tmp_path, monkeypatch, width, language):
    window, vm, gateway, snapshot, store = session
    window._language_manager.set_language(language)
    window.resize(width, 900)
    vm.navigate("prompts")
    window.show()
    qtbot.wait(200)
    editor = window.findChild(PromptLibraryEditor)
    left = editor.findChild(QWidget, "promptLeftColumn")
    right = editor.findChild(QWidget, "promptRightColumn")
    stage = editor.findChild(QWidget, "promptDirectionStage")
    assert left.geometry().right() < stage.geometry().left()
    assert stage.geometry().right() < right.geometry().left()
    assert editor.findChild(QPushButton, "savePromptDraft").isVisible()
    if language == "en_US":
        assert "Paste never presses Enter" in editor._body.placeholderText()
    assert window.grab().save(str(tmp_path / "prompts-wide.png"))
    preview = editor.findChild(PromptDevicePreview)
    before = len(gateway.commands)
    for prompt_id, button in editor._preview_buttons.items():
        # Click the proxy in the actual graphics view, rather than only emitting a signal.
        proxy = button.graphicsProxyWidget()
        point = preview.mapFromScene(proxy.sceneBoundingRect().center())
        assert preview.viewport().rect().contains(point)
        qtbot.mouseClick(preview.viewport(), Qt.LeftButton, pos=point)
        assert editor._selected_prompt_id() == prompt_id
        assert editor._direction_buttons[prompt_id].property("selected") is True
    assert len(gateway.commands) == before
    editor._name.setText("审查")
    editor._body.setPlainText("中文\nEnglish")
    monkeypatch.setattr(QMessageBox, "warning", lambda *_: QMessageBox.Cancel)
    editor._preview_buttons[1].click()
    assert editor._selected_prompt_id() == 4
    editor.findChild(QPushButton, "savePromptDraft").click()
    qtbot.wait(100)
    editor = window.findChild(PromptLibraryEditor)
    assert store.load(snapshot.identity["serial"]).draft_entry(4).body == "中文\nEnglish"
    assert not editor.findChild(QWidget, "promptMoreActions").isVisible()
    editor.findChild(QPushButton, "promptMoreToggle").click()
    assert editor.findChild(QWidget, "promptMoreActions").isVisible()
    editor._body.setPlainText("未保存")
    window._fit_window_to_available_area(QRect(0, 0, 900, 800))
    qtbot.wait(150)
    assert editor._body.toPlainText() == "未保存"
    assert editor.widget().width() <= editor.viewport().width()
    assert window.grab().save(str(tmp_path / "prompts-compact.png"))
    editor._body.setPlainText("中文\nEnglish")
    window._language_manager.set_language("zh_CN")
