"""权限自动重查（design decision，设置 4）和「复制诊断信息」这两个共享小件。"""
from datetime import datetime

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QDialog, QVBoxLayout, QWidget

from controller_config import desktop_permissions as perm
from controller_config.views.ui.banner import BannerStack
from controller_config.views.ui.copy_diagnostics import (
    BUTTON_TEXT, COPIED_TEXT, copy_diagnostics, copy_diagnostics_button, diagnostics_header,
)
from controller_config.views.ui.permission_row import PermissionRow, PermissionWatcher

TEXTS = {perm.TRUSTED: "辅助功能：已允许", perm.NOT_TRUSTED: "辅助功能：未允许", perm.UNKNOWN: "辅助功能：读不到"}


class Permissions(perm.SystemPermissions):
    """Answers that change when the test says the user changed them in System Settings."""

    def __init__(self, platform="darwin", accessibility=perm.NOT_TRUSTED, bluetooth=perm.BT_DENIED):
        self.answers = {perm.ACCESSIBILITY: accessibility, perm.BLUETOOTH: bluetooth}
        self.reads = 0
        self.opened: list[str] = []

        def read(kind):
            def answer():
                self.reads += 1
                return self.answers[kind]
            return answer

        super().__init__(platform=platform, accessibility=read(perm.ACCESSIBILITY),
                         bluetooth=read(perm.BLUETOOTH),
                         url_opener=lambda url: self.opened.append(url) or True)


def _come_back() -> None:
    QGuiApplication.instance().applicationStateChanged.emit(Qt.ApplicationState.ApplicationActive)


def test_watcher_rereads_when_console_comes_back_and_says_only_real_changes(qtbot):
    permissions = Permissions()
    watcher = PermissionWatcher(permissions)
    changes = []
    watcher.changed.connect(lambda: changes.append(True))
    assert watcher.status(perm.ACCESSIBILITY) == perm.NOT_TRUSTED and not watcher.granted(perm.ACCESSIBILITY)
    assert watcher.status(perm.BLUETOOTH) == perm.BT_DENIED

    _come_back()  # nothing changed: no signal
    assert changes == []

    permissions.answers[perm.ACCESSIBILITY] = perm.TRUSTED
    QGuiApplication.instance().applicationStateChanged.emit(Qt.ApplicationState.ApplicationInactive)
    assert changes == []  # leaving Console is not coming back
    _come_back()
    assert changes == [True] and watcher.granted(perm.ACCESSIBILITY)

    # A Console window becoming active again counts too (e.g. a dialog closes).
    permissions.answers[perm.BLUETOOTH] = perm.BT_ALLOWED
    window = QWidget()
    qtbot.addWidget(window)
    window.show()
    QGuiApplication.instance().focusWindowChanged.emit(window.windowHandle())
    assert changes == [True, True] and watcher.granted(perm.BLUETOOTH)


def test_watcher_reads_only_the_permissions_it_was_asked_for():
    permissions = Permissions()
    watcher = PermissionWatcher(permissions, kinds=(perm.ACCESSIBILITY,))
    reads = permissions.reads
    watcher.recheck()
    assert permissions.reads == reads + 1  # no Bluetooth read
    with pytest.raises(KeyError):
        watcher.status(perm.BLUETOOTH)
    with pytest.raises(ValueError):
        PermissionWatcher(permissions, kinds=("camera",))


def test_after_allow_it_watches_closely_until_granted(qtbot):
    permissions = Permissions()
    watcher = PermissionWatcher(permissions, kinds=(perm.ACCESSIBILITY,), poll_ms=20)
    assert not watcher.watching
    assert watcher.open_settings(perm.ACCESSIBILITY) is True
    assert permissions.opened == [perm.ACCESSIBILITY_SETTINGS_URL] and watcher.watching

    # Console stays in the background beside System Settings: the grant still shows up.
    permissions.answers[perm.ACCESSIBILITY] = perm.TRUSTED
    qtbot.waitUntil(lambda: watcher.granted(perm.ACCESSIBILITY), timeout=1000)
    assert not watcher.watching  # nothing left to wait for


def test_close_watching_gives_up_after_a_while(qtbot):
    watcher = PermissionWatcher(Permissions(), kinds=(perm.BLUETOOTH,), poll_ms=10, watch_ms=50)
    watcher.open_settings(perm.BLUETOOTH)
    assert watcher.permissions.opened == [perm.BLUETOOTH_SETTINGS_URL]
    qtbot.waitUntil(lambda: not watcher.watching, timeout=1000)
    assert not watcher.granted(perm.BLUETOOTH)


def test_other_platforms_need_nothing_and_never_watch():
    permissions = Permissions(platform="win32")
    watcher = PermissionWatcher(permissions)
    assert watcher.status(perm.ACCESSIBILITY) == perm.NOT_NEEDED and watcher.granted(perm.ACCESSIBILITY)
    assert watcher.open_settings(perm.ACCESSIBILITY) is False and not watcher.watching
    assert permissions.reads == 0


def test_row_shows_the_sentence_and_allow_only_while_missing(qtbot):
    permissions = Permissions()
    watcher = PermissionWatcher(permissions, kinds=(perm.ACCESSIBILITY,), poll_ms=60_000)
    host = QWidget()
    QVBoxLayout(host).addWidget(row := PermissionRow(watcher, perm.ACCESSIBILITY, TEXTS))
    qtbot.addWidget(host)
    assert row.label.text() == "辅助功能：未允许" and row.icon.state() == "wait"
    assert not row.allow.isHidden() and row.allow.text() == "去允许"
    row.allow.click()
    assert permissions.opened == [perm.ACCESSIBILITY_SETTINGS_URL]

    permissions.answers[perm.ACCESSIBILITY] = perm.TRUSTED
    _come_back()
    assert row.label.text() == "辅助功能：已允许" and row.icon.state() == "ok" and row.allow.isHidden()

    permissions.answers[perm.ACCESSIBILITY] = "something new"
    watcher.recheck()
    assert row.label.text() == "辅助功能：读不到" and not row.allow.isHidden()


def test_row_is_hidden_where_the_grant_does_not_exist(qtbot):
    watcher = PermissionWatcher(Permissions(platform="win32"))
    row = PermissionRow(watcher, perm.ACCESSIBILITY, TEXTS, object_name="custom", button_name="customAllow")
    qtbot.addWidget(row)
    assert row.isHidden() and row.label.objectName() == "custom" and row.allow.objectName() == "customAllow"


def test_copy_diagnostics_puts_text_on_the_clipboard_and_tells_the_main_window(qtbot):
    window = QWidget()
    window.resize(800, 500)
    window._banners = BannerStack(window)
    qtbot.addWidget(window)
    dialog = QDialog(window)  # the button lives in a dialog the window owns
    collected = []
    button = copy_diagnostics_button(lambda: collected.append(1) or "line one\nline two")
    QVBoxLayout(dialog).addWidget(button)
    assert button.text() == BUTTON_TEXT
    button.click()
    assert collected == [1]
    assert QGuiApplication.clipboard().text() == "line one\nline two"
    (banner,) = window._banners.banners()
    assert banner.key == "copy-diagnostics" and banner.accessibleName() == COPIED_TEXT

    # Without a main window it still copies.
    loose = QWidget()
    qtbot.addWidget(loose)
    copy_diagnostics(loose, "only text")
    assert QGuiApplication.clipboard().text() == "only text"


def test_diagnostics_header_says_where_a_pasted_block_came_from():
    from controller_config import __version__

    header = diagnostics_header(datetime(2026, 11, 18, 22, 40))
    assert header[0] == f"BORING Console {__version__}"
    assert header[1].startswith("系统：") and header[2].startswith("时间：2026-11-18T22:40:00")
