"""快捷提示词页的三项逻辑（评审 Console快捷提示词易用性评审-20261118 的提示词 2、4、7）。

- 字数：按字显示，设备上限仍按字节校验（提示词 7）。
- 保存状态：编辑区底部一句话、按钮固定叫“保存到设备”（提示词 2，照 Prompt 页定稿 v1）。
- 权限：进页面就查辅助功能，缺了显示“还差一步〔去允许〕”，回到 Console 自动重查（提示词 4）。
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QLabel, QPushButton

from controller_config import desktop_permissions as perm
from controller_config.prompt_device import PromptListenerState, PromptListenerStatus
from controller_config.prompt_library import (
    PROMPT_BODY_MAX_BYTES,
    PROMPT_NAME_MAX_BYTES,
    PromptEntry,
    PromptLibrary,
)
from controller_config.prompt_status_state import PromptStatusKind
from controller_config.views.prompt_library_editor import (
    CHANGED,
    NOT_SET,
    SAVED,
    PromptLibraryEditor,
    estimated_bytes_per_character,
    paste_permission_missing,
    save_status,
    slot_status,
    text_allowance,
)
from controller_config.views.ui.status_icon import StatusIcon


def make_editor(qtbot, library=None, *, available=True, permissions=None):
    editor = PromptLibraryEditor(
        library if library is not None else PromptLibrary("CP01-AABBCCDDEEFF"),
        device_storage_available=available, protocol_message="", device_busy=False,
        helper_message="",
        listener_status=PromptListenerStatus(state=PromptListenerState.READY, message="监听已就绪"),
        event_log=(), background_message="",
        save_draft=lambda *_: None, delete_draft=lambda *_: None,
        discard_draft=lambda *_: None, refresh_device=lambda: None,
        write_device=lambda *_: None, delete_device=lambda *_: None,
        permissions=permissions,
    )
    qtbot.addWidget(editor)
    return editor


# -- 字数（提示词 7）------------------------------------------------------------

def test_chinese_counts_one_per_character_against_the_byte_limit():
    # 名称上限 48 字节 = 16 个汉字；“代码审查”是 4 个字，不是 12。
    allowance = text_allowance("代码审查", PROMPT_NAME_MAX_BYTES)
    assert (allowance.used, allowance.limit, allowance.left, allowance.over) == (4, 16, 12, 0)
    assert text_allowance("", PROMPT_NAME_MAX_BYTES).limit == 16


def test_mixed_text_estimates_room_in_whole_characters():
    # 英文字母占 1 字节：写了英文以后能再写的汉字按剩下的字节算，宁少勿多。
    allowance = text_allowance("Code review", PROMPT_NAME_MAX_BYTES)
    assert allowance.used == 11 and allowance.left == (48 - 11) // 3
    # 英文界面按英文字母估算剩余量。
    english = text_allowance("Code review", PROMPT_NAME_MAX_BYTES, bytes_per_character=1)
    assert (english.used, english.limit) == (11, 48)
    assert estimated_bytes_per_character("en_US") == 1
    assert estimated_bytes_per_character("zh_CN") == estimated_bytes_per_character("ja_JP") == 3


def test_over_the_limit_says_how_many_characters_too_many():
    # 定稿里的例子：18 个字的名称超出 2 个字。
    allowance = text_allowance("总结要点并列出下一步要做的事和负责人", PROMPT_NAME_MAX_BYTES)
    assert allowance.over == 2 and allowance.left == 0
    # 多出 1 个字节也算多 1 个字，不能显示“超出 0 个字”。
    assert text_allowance("a" * 49, PROMPT_NAME_MAX_BYTES).over == 1


def test_body_count_only_appears_when_nearly_full(qtbot):
    editor = make_editor(qtbot)
    editor._name.setText("代码审查")
    assert editor._name_count.text() == "4 / 16 字"
    editor._body.setPlainText("短正文")
    assert editor._body_count.text() == ""
    # 4096 字节里还剩 300 字节：约 100 个字。
    editor._body.setPlainText("字" * ((PROMPT_BODY_MAX_BYTES - 300) // 3))
    assert editor._body_count.text() == "还能写约 100 个字"
    assert not editor._body_count.property("countOver")


def test_too_long_name_is_blocked_and_explained(qtbot):
    editor = make_editor(qtbot)
    editor._name.setText("总结要点并列出下一步要做的事和负责人")
    editor._body.setPlainText("正文")
    assert editor._name_count.text() == "超出 2 个字"
    assert editor._name_count.property("countOver") is True
    assert editor.findChild(QLabel, "promptSyncState").text() == "名称太长，改短后才能保存"
    assert not editor.findChild(QPushButton, "writePromptDevice").isEnabled()
    editor._name.setText("总结要点")
    assert editor._name_count.property("countOver") is False
    assert editor.findChild(QPushButton, "writePromptDevice").isEnabled()


def test_too_long_body_is_blocked_and_explained(qtbot):
    editor = make_editor(qtbot)
    editor._name.setText("长")
    editor._body.setPlainText("字" * (PROMPT_BODY_MAX_BYTES // 3 + 1))
    assert editor._body_count.text() == "超出 1 个字"
    assert editor.findChild(QLabel, "promptSyncState").text() == "正文太长，改短后才能保存"
    assert not editor.findChild(QPushButton, "writePromptDevice").isEnabled()


# -- 保存状态（提示词 2）---------------------------------------------------------

DEVICE = PromptEntry(1, "代码审查", "设备正文")
LOCAL = PromptEntry(1, "代码审查", "本地正文")


@pytest.mark.parametrize("name,body,draft,confirmed,available,text,icon,write,local", [
    ("", "", None, None, True, "填好名称和正文就能保存", None, False, False),
    ("代码审查", "设备正文", DEVICE, DEVICE, True, SAVED, "ok", False, False),
    ("代码审查", "改过", DEVICE, DEVICE, True, CHANGED, "wait", True, True),
    # 切方向时存下的本地草稿，回来看还是“还没存进设备”，不再另起一种说法。
    ("代码审查", "本地正文", LOCAL, DEVICE, True, CHANGED, "wait", True, False),
    ("代码审查", "本地正文", LOCAL, None, True, CHANGED, "wait", True, False),
    # 设备没连上时只说这一句，按钮换成“先存在这台电脑”。
    ("代码审查", "改过", DEVICE, DEVICE, False, "设备没连上", None, False, True),
    ("代码审查", "本地正文", LOCAL, DEVICE, False, "设备没连上", None, False, False),
])
def test_save_status_is_one_sentence(name, body, draft, confirmed, available, text, icon, write, local):
    status = save_status(name, body, draft, confirmed, device_available=available)
    assert (status.text, status.icon, status.can_write_device, status.can_save_local) == (
        text, icon, write, local)


def test_direction_cards_use_the_same_three_words():
    assert slot_status(None, None) == (NOT_SET, "off")
    assert slot_status(DEVICE, DEVICE) == (SAVED, "ok")
    assert slot_status(None, DEVICE) == (SAVED, "ok")
    assert slot_status(LOCAL, DEVICE) == (CHANGED, "wait")
    assert slot_status(LOCAL, None) == (CHANGED, "wait")


def test_save_bar_is_one_line_with_a_fixed_button(qtbot):
    library = PromptLibrary("CP01-AABBCCDDEEFF", confirmed=(DEVICE,), draft=(DEVICE,))
    editor = make_editor(qtbot, library)
    editor.show()
    write = editor.findChild(QPushButton, "writePromptDevice")
    state = editor.findChild(QLabel, "promptSyncState")
    icon = editor._save_icon
    assert isinstance(icon, StatusIcon)
    # 状态和按钮在同一行：编辑区底部，左边一句话，右边按钮。
    assert abs(state.geometry().center().y() - write.geometry().center().y()) < write.height()
    assert state.geometry().right() < write.geometry().left()
    assert editor.findChild(QLabel, "promptActionHint") is None
    assert (state.text(), icon.state(), write.text(), write.isEnabled()) == (
        "已存进设备", "ok", "保存到设备", False)
    editor._body.setPlainText("改过")
    assert (state.text(), icon.state(), write.text(), write.isEnabled()) == (
        "有修改，还没存进设备", "wait", "保存到设备", True)
    assert editor._cross.state(1) == "saved"


def test_offline_save_bar_offers_this_computer(qtbot):
    editor = make_editor(qtbot, available=False)
    editor.show()
    local = editor.findChild(QPushButton, "savePromptDraft")
    assert local.isVisible() and local.text() == "先存在这台电脑"
    assert not editor.findChild(QPushButton, "writePromptDevice").isVisible()
    editor._name.setText("代码审查")
    editor._body.setPlainText("正文")
    assert editor.findChild(QLabel, "promptSyncState").text() == "设备没连上"
    assert local.isEnabled()
    assert editor._save_icon.isHidden()


# -- 权限（提示词 4）-------------------------------------------------------------

class FakePermissions(perm.SystemPermissions):
    def __init__(self, answer=perm.NOT_TRUSTED, platform="darwin"):
        self.answer = answer
        self.opened = 0
        super().__init__(platform=platform, accessibility=lambda: self.answer,
                         bluetooth=lambda: perm.BT_ALLOWED, url_opener=self._open)

    def _open(self, _url):
        self.opened += 1
        return True


@pytest.mark.parametrize("permissions,missing", [
    (None, False),
    (FakePermissions(perm.TRUSTED), False),
    (FakePermissions(perm.NOT_TRUSTED), True),
    (FakePermissions(perm.UNKNOWN), False),
    (FakePermissions(perm.NOT_TRUSTED, platform="win32"), False),
])
def test_paste_permission_is_asked_in_one_place(permissions, missing):
    assert paste_permission_missing(permissions) is missing


def test_missing_permission_shows_on_entry_and_rechecks_on_return(qtbot, monkeypatch):
    """还差一步 sits in the top line (定稿 v1: 权限引导提前，顶部一行，缺权限时给去允许)."""
    permissions = FakePermissions(perm.NOT_TRUSTED)
    editor = make_editor(qtbot, permissions=permissions)
    editor.show()
    bar = editor._top_bar
    # 一进页面就看得到，不用等第一次粘贴失败。
    assert bar.kind() is PromptStatusKind.PERMISSION
    assert editor.findChild(QLabel, "promptStatusWord").text() == "还差一步"
    editor.findChild(QPushButton, "promptStatusAllow").click()
    assert permissions.opened == 1
    # 用户在系统设置里允许了，切回 Console 时自己重查，提示收起。
    permissions.answer = perm.TRUSTED
    QGuiApplication.instance().applicationStateChanged.emit(Qt.ApplicationState.ApplicationActive)
    assert bar.kind() is not PromptStatusKind.PERMISSION
    # 之后又被关掉，再回来时重新出现。
    permissions.answer = perm.NOT_TRUSTED
    QGuiApplication.instance().applicationStateChanged.emit(Qt.ApplicationState.ApplicationInactive)
    QGuiApplication.instance().applicationStateChanged.emit(Qt.ApplicationState.ApplicationActive)
    assert bar.kind() is PromptStatusKind.PERMISSION


def test_permission_notice_stays_away_when_granted_or_not_needed(qtbot):
    for permissions in (FakePermissions(perm.TRUSTED), FakePermissions(platform="win32"), None):
        editor = make_editor(qtbot, permissions=permissions)
        editor.show()
        assert editor._top_bar.kind() is not PromptStatusKind.PERMISSION


def test_main_window_passes_its_permissions_to_the_prompt_page(session):  # noqa: F811
    window, vm, _gateway, _snapshot, _store = session
    window._system_permissions = FakePermissions(perm.NOT_TRUSTED)
    vm.navigate("prompts")
    window.show()
    editor = window._content.findChild(PromptLibraryEditor)
    assert editor._top_bar.kind() is PromptStatusKind.PERMISSION


from test_session_recovery import session  # noqa: E402,F401
