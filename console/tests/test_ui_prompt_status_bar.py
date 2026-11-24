"""Prompt page top row and diagnostics (the designer, Prompt 页定稿 v1, 3-顶部一行… / 4-…三个弹窗.png)."""
from __future__ import annotations

import pytest
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QWidget

from controller_config import desktop_permissions as perm
from controller_config.i18n import ENGLISH, JAPANESE, LanguageManager
from controller_config.prompt_device import PromptEventLogEntry, PromptListenerStatus
from controller_config.prompt_device import PromptListenerState as L
from controller_config.prompt_status_state import PromptStatusInputs, prompt_status_kind
from controller_config.prompt_status_state import PromptStatusKind as K
from controller_config.style.sheets import application_stylesheet
from controller_config.views.ui import PromptDiagnostics, PromptStatusBar, prompt_diagnostics_dialog
from controller_config.views.ui import prompt_status_bar as bar_module
from controller_config.views.ui.permission_row import PermissionWatcher


def inputs(*, connected=True, ble=False, palette=True, storage=True, listener=L.READY, missing=False):
    return PromptStatusInputs(connected, ble, palette, storage, listener, missing)


# -- the mapping, row by row (table in prompt_status_state's docstring) -------

MAPPING = [
    # not connected wins over everything
    (inputs(connected=False), K.DISCONNECTED),
    (inputs(connected=False, ble=True, missing=True, listener=L.OFFLINE, storage=False), K.DISCONNECTED),
    # Bluetooth next
    (inputs(ble=True), K.BLUETOOTH),
    (inputs(ble=True, missing=True), K.BLUETOOTH),
    (inputs(ble=True, listener=L.OFFLINE), K.BLUETOOTH),
    (inputs(ble=True, palette=False), K.BLUETOOTH),
    # helper off for good
    (inputs(storage=False), K.HELPER_OFFLINE),
    (inputs(storage=False, listener=L.STARTING), K.HELPER_OFFLINE),
    (inputs(listener=L.OFFLINE), K.HELPER_OFFLINE),
    (inputs(listener=L.OFFLINE, missing=True), K.HELPER_OFFLINE),
    # the permission
    (inputs(missing=True), K.PERMISSION),
    (inputs(missing=True, listener=L.STARTING), K.PERMISSION),
    (inputs(missing=True, palette=False), K.PERMISSION),
    # getting ready
    (inputs(listener=L.STARTING), K.PREPARING),
    (inputs(listener=L.PAUSED), K.PREPARING),
    (inputs(listener=L.RETRYING), K.PREPARING),
    (inputs(listener=L.STARTING, palette=False), K.PREPARING),
    # ready
    (inputs(), K.READY),
    (inputs(palette=False), K.READY_DIRECT),
]


@pytest.mark.parametrize(("state", "expected"), MAPPING)
def test_mapping_row(state, expected):
    assert prompt_status_kind(state) is expected


def test_every_combination_has_a_status_and_every_status_is_reached():
    seen = set()
    for connected in (False, True):
        for ble in (False, True):
            for palette in (False, True):
                for storage in (False, True):
                    for listener in L:
                        for missing in (False, True):
                            seen.add(prompt_status_kind(inputs(connected=connected, ble=ble, palette=palette,
                                                               storage=storage, listener=listener,
                                                               missing=missing)))
    assert seen == set(K)
    assert {row[1] for row in MAPPING} == set(K)
    assert set(bar_module.LOOKS) == set(K)


# -- the widget ---------------------------------------------------------------

class FakePermissions:
    """A desktop_permissions.SystemPermissions double."""

    def __init__(self, accessibility=perm.TRUSTED, is_mac=True):
        self.is_mac = is_mac
        self.value = accessibility
        self.opened = 0

    def accessibility(self):
        return self.value

    def bluetooth(self):
        return perm.BT_ALLOWED

    def open_accessibility_settings(self):
        self.opened += 1
        return True

    def open_bluetooth_settings(self):
        return True


@pytest.fixture
def host(qtbot):
    widget = QWidget()
    widget.setStyleSheet(application_stylesheet())
    widget.resize(900, 120)
    qtbot.addWidget(widget)
    widget.show()
    qtbot.waitExposed(widget)
    return widget


def make_bar(host, watcher=None, width=bar_module.MAX_WIDTH):
    bar = PromptStatusBar(watcher, host)
    bar.setGeometry(0, 0, width, bar.height())
    bar.show()
    QApplication.processEvents()
    return bar


def tr(text):
    from controller_config.i18n import translate_ui_text
    return translate_ui_text(text)


ROUND = ["promptStatusRefresh", "promptStatusDiagnostics"]

# status, icon, word, sentence, buttons left to right (spec, 3-顶部一行… "顶部一行")
STATES = [
    (K.READY, "ok", "可以使用", "长按 12 号键打开 → 摇杆选 → 按旋钮确认 · 3 号键取消",
     ["promptStatusDemo", *ROUND]),
    (K.PREPARING, "busy", "正在准备…", "", ROUND),
    (K.PERMISSION, "error", "还差一步", "允许 BORING Console 控制这台电脑，按键才能把提示词打出来",
     ["promptStatusAllow", *ROUND]),
    (K.BLUETOOTH, "wait", "现在用蓝牙连接", bar_module.BLUETOOTH_TEXT, ["promptStatusExplain", *ROUND]),
    (K.DISCONNECTED, "off", "设备没连上", "现在改的先存在这台电脑，连上后再存进设备", ["promptStatusDiagnostics"]),
    (K.READY_DIRECT, "ok", "可以使用", "在 Mode 里把摇杆方向绑到提示词，拨一下就打出来", ROUND),
    (K.HELPER_OFFLINE, "off", "暂时打不出提示词", "点右边的诊断信息看原因", ROUND),
]


def test_starts_as_preparing(host):
    bar = make_bar(host)
    assert bar.kind() is K.PREPARING
    assert bar.height() == 44
    assert bar.minimumWidth() == 648 and bar.maximumWidth() == 848


@pytest.mark.parametrize(("kind", "icon", "word", "text", "buttons"), STATES)
def test_each_status_icon_text_and_buttons(host, kind, icon, word, text, buttons):
    bar = make_bar(host)
    assert bar.apply(kind) is kind
    assert bar.icon.state() == icon
    assert bar.word.text() == tr(word)
    assert bar.text.text() == (tr(text) if text else "")
    assert bar.divider.isVisibleTo(bar) is bool(text)
    assert bar.visible_buttons() == buttons


def test_status_shapes_follow_the_spec_sizes(host):
    bar = make_bar(host)
    # READY's check is 16 since the designer's 11-20 真屏验收 (one of three check sizes).
    for kind, size in ((K.READY, 16), (K.PREPARING, 14), (K.PERMISSION, 16), (K.DISCONNECTED, 8)):
        bar.apply(kind)
        assert bar.icon.width() == size
        assert bar.findChildren(type(bar.icon)) == [bar.icon]  # the old shape is gone


def test_preparing_word_is_quiet_grey(host):
    bar = make_bar(host)
    bar.apply(K.PREPARING)
    assert bar.word.property("quiet") is True
    bar.apply(K.READY)
    assert bar.word.property("quiet") is False


def test_bluetooth_dot_waits_for_the_device(host):
    bar = make_bar(host)
    bar.apply(K.BLUETOOTH)
    assert bar.icon.accessibleName() == "等待设备"


@pytest.mark.parametrize(("kind", "name", "signal"), [
    (K.READY, "promptStatusDemo", "demo_requested"),
    (K.READY, "promptStatusRefresh", "refresh_requested"),
    (K.READY, "promptStatusDiagnostics", "diagnostics_requested"),
    (K.BLUETOOTH, "promptStatusExplain", "explain_requested"),
    (K.DISCONNECTED, "promptStatusDiagnostics", "diagnostics_requested"),
])
def test_buttons_emit_their_signal(host, qtbot, kind, name, signal):
    bar = make_bar(host)
    bar.apply(kind)
    with qtbot.waitSignal(getattr(bar, signal), timeout=500):
        bar.findChild(QWidget, name).click()


def test_a_button_emits_only_its_own_signal(host):
    bar = make_bar(host)
    bar.apply(K.READY)
    fired = []
    for signal in ("demo_requested", "refresh_requested", "diagnostics_requested", "explain_requested"):
        getattr(bar, signal).connect(lambda s=signal: fired.append(s))
    bar.refresh_button.click()
    assert fired == ["refresh_requested"]


def test_kind_changed_only_when_it_changes(host):
    bar = make_bar(host)
    changes = []
    bar.kind_changed.connect(changes.append)
    bar.apply(inputs())
    bar.apply(inputs())
    bar.apply(inputs(connected=False))
    assert changes == ["ready", "disconnected"]


def test_round_buttons_have_names_and_glyphs(host):
    bar = make_bar(host)
    assert bar.refresh_button.glyph() == "refresh"
    assert bar.diagnostics_button.glyph() == "pulse"
    assert bar.refresh_button.accessibleName() == tr("重新读取")
    assert bar.diagnostics_button.toolTip() == tr("诊断信息")
    assert bar.refresh_button.size().width() == 30


@pytest.mark.parametrize("kind", [K.READY, K.PERMISSION, K.BLUETOOTH])
def test_buttons_follow_the_spec_sizes(host, kind):
    # Spec (measured): pills 28 high, round buttons 30, evenly 7 from the row's edge.
    bar = make_bar(host)
    bar.apply(kind)
    bar.layout().activate()
    QApplication.processEvents()
    pill = bar.findChild(QWidget, bar.visible_buttons()[0])
    assert pill.height() == 28
    for button in (bar.refresh_button, bar.diagnostics_button):
        assert button.size().width() == button.size().height() == 30
        assert button.geometry().top() == 7
    assert bar.width() - bar.diagnostics_button.geometry().right() - 1 == 7


# -- permission ----------------------------------------------------------------

def test_missing_permission_comes_from_the_watcher(host):
    permissions = FakePermissions(perm.NOT_TRUSTED)
    watcher = PermissionWatcher(permissions, kinds=(perm.ACCESSIBILITY,))
    bar = make_bar(host, watcher)
    # The page's inputs say nothing is missing; the watcher knows better.
    assert bar.apply(inputs(missing=False)) is K.PERMISSION
    assert bar.inputs().permission_missing is True


def test_allow_opens_settings_and_the_grant_turns_it_back(host):
    permissions = FakePermissions(perm.NOT_TRUSTED)
    watcher = PermissionWatcher(permissions, kinds=(perm.ACCESSIBILITY,))
    bar = make_bar(host, watcher)
    bar.apply(inputs())
    assert bar.kind() is K.PERMISSION
    bar.allow_button.click()
    assert permissions.opened == 1
    assert watcher.watching
    permissions.value = perm.TRUSTED
    assert watcher.recheck()
    assert bar.kind() is K.READY
    assert bar.visible_buttons() == ["promptStatusDemo", *ROUND]
    watcher._watch.stop()  # noqa: SLF001 - no timer left behind


def test_unknown_permission_is_not_asked_for(host):
    # Same as paste_permission_missing(): only NOT_TRUSTED asks.
    watcher = PermissionWatcher(FakePermissions(perm.UNKNOWN), kinds=(perm.ACCESSIBILITY,))
    bar = make_bar(host, watcher)
    assert bar.apply(inputs()) is K.READY


def test_no_permission_question_off_mac(host):
    watcher = PermissionWatcher(FakePermissions(perm.NOT_TRUSTED, is_mac=False), kinds=(perm.ACCESSIBILITY,))
    bar = make_bar(host, watcher)
    assert bar.apply(inputs(missing=True)) is K.READY


def test_without_watcher_the_inputs_decide(host):
    bar = make_bar(host)
    assert bar.permission_missing() is None
    assert bar.apply(inputs(missing=True)) is K.PERMISSION
    bar.allow_button.click()  # nothing to open, nothing breaks
    assert bar.kind() is K.PERMISSION


def test_watcher_for_other_permissions_is_ignored(host):
    watcher = PermissionWatcher(FakePermissions(), kinds=(perm.BLUETOOTH,))
    bar = make_bar(host, watcher)
    assert bar.permission_missing() is None
    assert bar.apply(inputs(missing=True)) is K.PERMISSION


# -- width and languages ---------------------------------------------------------

def test_long_sentence_is_cut_and_shown_whole_on_hover(host):
    bar = make_bar(host, width=bar_module.MIN_WIDTH)
    bar.apply(K.READY)
    bar.text.setText("长按 12 号键打开 → 摇杆选 → 按旋钮确认 · 3 号键取消 " * 3)
    QApplication.processEvents()
    assert bar.text.is_elided()
    assert bar.text.elided_text().endswith("…")
    assert bar.text.toolTip() == bar.text.text()


def test_short_sentence_has_no_tooltip(host):
    bar = make_bar(host)
    bar.apply(K.DISCONNECTED)
    QApplication.processEvents()
    assert not bar.text.is_elided()
    assert bar.text.toolTip() == ""


@pytest.mark.parametrize("language", ["zh_CN", ENGLISH, JAPANESE])
@pytest.mark.parametrize("kind", list(K))
def test_word_and_buttons_fit_at_the_narrowest_wall(host, language, kind):
    LanguageManager(QApplication.instance(), initial_language=language, persist=False)
    bar = make_bar(host, width=bar_module.MIN_WIDTH)
    bar.apply(kind)
    bar.layout().activate()
    QApplication.processEvents()
    assert bar.word.width() >= bar.word.sizeHint().width()  # never cut
    for name in bar.visible_buttons():
        button = bar.findChild(QWidget, name)
        assert button.width() >= button.sizeHint().width()
        assert button.geometry().right() <= bar.width()
    if bar.text.text():
        assert bar.text.width() > 40  # some of the sentence is left
    LanguageManager(QApplication.instance(), initial_language="zh_CN", persist=False)


def test_language_switch_retranslates_the_row(host):
    bar = make_bar(host)
    bar.apply(K.PERMISSION)
    manager = LanguageManager(QApplication.instance(), initial_language="zh_CN", persist=False)
    manager.set_language(ENGLISH)
    manager.retranslate_widget_tree(host)
    assert bar.word.text() == "One more step"
    assert bar.allow_button.text() == "Allow"
    assert bar.text.text().startswith("Allow BORING Console")
    manager.set_language("zh_CN")
    manager.retranslate_widget_tree(host)
    assert bar.word.text() == "还差一步"


# -- diagnostics -------------------------------------------------------------------

EVENTS = (
    PromptEventLogEntry(1, "info", "正在建立 USB 提示词事件监听"),
    PromptEventLogEntry(2, "warning", "设备事件轮询失败；助手将自动重试", technical="timeout 500 ms"),
)


def runtime(**overrides):
    values = dict(available=True, busy=False, message="设备已声明 UTF-8 提示词存储能力",
                  helper_message="摇杆往上「代码审查」已打出",
                  listener=PromptListenerStatus(L.READY, "助手在线；正在监听实体提示词按键"),
                  event_log=EVENTS, background_message="当前运行方式不会在关闭窗口后保留后台助手")
    values.update(overrides)
    return values


def test_diagnostics_online(qtbot):
    diagnostics = PromptDiagnostics()
    qtbot.addWidget(diagnostics)
    diagnostics.update_runtime(**runtime())
    assert diagnostics.helper_value.text() == tr("在线")
    assert diagnostics.helper_icon.state() == "ok"
    assert diagnostics.helper_note.isHidden()
    assert diagnostics.storage_value.text() == tr("可用")
    assert diagnostics.storage_note.isHidden()
    assert diagnostics.latest_value.text() == "摇杆往上「代码审查」已打出"
    log = diagnostics.log.toPlainText()
    assert log.splitlines()[0].startswith("01 · ")
    assert "     timeout 500 ms" in log
    assert diagnostics.log.isReadOnly()
    assert diagnostics.paste_help.text() == tr(
        "提示词通过剪贴板和 Command+V 打进电脑，不会自动按回车。粘贴失败时内容仍留在剪贴板。")


@pytest.mark.parametrize(("state", "word", "shape"), [
    (L.OFFLINE, "离线", "off"), (L.STARTING, "正在连接", "busy"),
    (L.RETRYING, "正在重试", "busy"), (L.PAUSED, "暂时暂停", "wait"),
])
def test_diagnostics_helper_not_online_shows_its_reason(qtbot, state, word, shape):
    diagnostics = PromptDiagnostics()
    qtbot.addWidget(diagnostics)
    diagnostics.update_runtime(**runtime(listener=PromptListenerStatus(state, "助手离线；后台轮询未启用", "detail")))
    assert diagnostics.helper_value.text() == tr(word)
    assert diagnostics.helper_icon.state() == shape
    assert not diagnostics.helper_note.isHidden()
    assert diagnostics.helper_note.toolTip() == "detail"


def test_diagnostics_storage_unavailable_and_empty_log(qtbot):
    diagnostics = PromptDiagnostics()
    qtbot.addWidget(diagnostics)
    diagnostics.update_runtime(**runtime(available=False, message="设备没有声明 prompt 配置动作",
                                         helper_message="", event_log=()))
    assert diagnostics.storage_value.text() == tr("不可用")
    assert not diagnostics.storage_note.isHidden()
    assert diagnostics.latest_value.text() == "—"
    assert diagnostics.log.toPlainText() == tr("尚无事件记录")


def test_diagnostics_text_is_plain_and_complete(qtbot):
    diagnostics = PromptDiagnostics()
    qtbot.addWidget(diagnostics)
    diagnostics.update_runtime(**runtime(listener=PromptListenerStatus(L.RETRYING, "轮询失败", "errno 5")))
    text = diagnostics.diagnostics_text()
    assert text.startswith("BORING Console ")
    for part in ("提示词助手：正在重试（retrying）", "技术信息：errno 5", "设备存储：可用",
                 "最近一次：摇杆往上「代码审查」已打出", "后台：当前运行方式",
                 "01 [info] 正在建立 USB 提示词事件监听", "02 [warning]", "     timeout 500 ms"):
        assert part in text


def test_diagnostics_dialog_copies(qtbot):
    diagnostics = PromptDiagnostics()
    diagnostics.update_runtime(**runtime())
    dialog = prompt_diagnostics_dialog(None, diagnostics)
    qtbot.addWidget(dialog)
    assert dialog.title_label.text() == tr("诊断信息")
    assert dialog.close_button is not None
    copy = dialog.findChild(QWidget, "copyDiagnostics")
    assert copy.text() == tr("复制诊断信息")
    QGuiApplication.clipboard().setText("")
    copy.click()
    copied = QGuiApplication.clipboard().text()
    assert copied.startswith("BORING Console ")
    # Below the header (version, system, time) it is the same text.
    assert copied.split("\n", 3)[3] == diagnostics.diagnostics_text().split("\n", 3)[3]
    dialog.fit()
    assert dialog.width() == 480


def test_permission_coming_back_cross_fades_and_the_check_pops(host, qtbot, monkeypatch):
    """-15 第 5 处 (乙): 还差一步 -> 可以使用, cross-fade 短, the check pops 0.6 -> 1.12 -> 1."""
    from controller_config.views.ui import motion
    from controller_config.views.ui.page_motion import CrossFade

    monkeypatch.setattr(motion, "reduces_motion", lambda: False)
    bar = make_bar(host)
    bar.apply(K.PERMISSION)
    bar.apply(K.READY)
    fades = bar.findChildren(CrossFade)
    assert len(fades) == 1 and fades[0].animation.duration() == 180
    pop = bar.icon._pop
    assert pop is not None and pop.animation.duration() == 300
    pop.animation.pause()
    pop.animation.setCurrentTime(180)
    assert abs(pop.scale() - 1.12) < 1e-6
    pop.animation.setCurrentTime(300)
    pop.animation.stop()
    assert pop.scale() == 1.0
    bar.apply(K.DISCONNECTED)
    bar.apply(K.READY)   # not from 还差一步: no pop, no fade
    assert bar.icon._pop is None or bar.icon._pop.animation.state() != pop.animation.State.Running
