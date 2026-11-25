"""Lighting save banner (the designer, 灯光页排法二 v0.4, 5-保存横幅…各个状态.png)."""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QWidget

from controller_config.models import AppState
from controller_config.save_banner_state import SaveBannerKind as K
from controller_config.save_banner_state import SaveFlow, save_banner_kind
from controller_config.style.sheets import application_stylesheet
from controller_config.transactions import ConfigTransactionState as T
from controller_config.views.ui import SaveBanner
from controller_config.views.ui import save_banner as save_banner_module

READY, OFFLINE = AppState.READY, AppState.DISCONNECTED


def flow(state, *, model=READY, changes=True, base_changed=False, writable=True):
    return SaveFlow(model, state, changes, base_changed, writable)


# -- the mapping, row by row (table in save_banner_state's docstring) ---------

MAPPING = [
    # transaction, extra conditions -> banner
    (flow(T.VALIDATING), K.CHECKING),
    (flow(T.VALIDATING, model=OFFLINE), K.CHECKING),
    (flow(T.AWAITING_CONFIRMATION), K.CONFIRM),
    (flow(T.AWAITING_CONFIRMATION, model=AppState.READ_ONLY), K.OFFLINE),
    (flow(T.AWAITING_CONFIRMATION, writable=False), K.OFFLINE),
    (flow(T.WRITING), K.WRITING),
    (flow(T.PENDING), K.WRITING),
    (flow(T.VERIFYING), K.VERIFYING),   # -15 第 1 处: 正在读回核对…
    (flow(T.VERIFYING, model=OFFLINE), K.VERIFYING),
    (flow(T.UNKNOWN), K.UNKNOWN),
    (flow(T.UNKNOWN, model=OFFLINE), K.UNKNOWN),
    (flow(T.CONFLICT), K.CONFLICT),
    (flow(T.CONFLICT, base_changed=True), K.CONFLICT),
    (flow(T.FAILED), K.FAILED),
    (flow(T.FAILED, model=OFFLINE), K.OFFLINE),
    (flow(T.FAILED, writable=False), K.OFFLINE),
    (flow(T.ACTIVE, changes=False), K.SAVED),
    (flow(T.ACTIVE), K.DIRTY),
    (flow(T.ACTIVE, model=OFFLINE), K.OFFLINE),
    (flow(T.IDLE, changes=False), K.HIDDEN),
    (flow(T.IDLE, changes=False, model=OFFLINE), K.HIDDEN),
    (flow(T.IDLE), K.DIRTY),
    (flow(T.IDLE, model=OFFLINE), K.OFFLINE),
    (flow(T.IDLE, model=AppState.READ_ONLY), K.OFFLINE),
    (flow(T.IDLE, model=AppState.FIRMWARE_MAINTENANCE), K.OFFLINE),
    (flow(T.IDLE, writable=False), K.OFFLINE),
    (flow(T.IDLE, base_changed=True), K.CONFLICT),
    (flow(T.IDLE, base_changed=True, model=OFFLINE), K.OFFLINE),
    (flow(T.ACTIVE, base_changed=True), K.CONFLICT),
]


@pytest.mark.parametrize(("inputs", "expected"), MAPPING)
def test_mapping_row(inputs, expected):
    assert save_banner_kind(inputs) is expected


def test_every_transaction_and_app_state_has_a_banner():
    for transaction in T:
        for model in AppState:
            for changes in (False, True):
                for base_changed in (False, True):
                    for writable in (False, True):
                        kind = save_banner_kind(SaveFlow(model, transaction, changes, base_changed, writable))
                        assert isinstance(kind, K)
    assert {row[0].transaction_state for row in MAPPING} == set(T)


def test_every_state_but_hidden_has_a_look():
    assert set(save_banner_module.LOOKS) == set(K) - {K.HIDDEN}


# -- the widget ---------------------------------------------------------------

@pytest.fixture
def banner(qtbot):
    host = QWidget()
    host.setStyleSheet(application_stylesheet())
    host.resize(900, 300)
    qtbot.addWidget(host)
    host.show()
    qtbot.waitExposed(host)
    yield SaveBanner(host)  # keep `host` alive while the test runs


def labels(banner):
    return [(b.text(), b.isEnabled()) for b in banner.buttons()]


def tr(text):
    # The copy file can reword a string for Chinese too: "确认写入设备" shows
    # as "确认保存到设备" there (translations/ui_text.json, since 2026-09).
    from controller_config.i18n import translate_ui_text
    return translate_ui_text(text)


# state, text, icon, buttons (text, enabled)
STATES = [
    (K.DIRTY, "有修改，还没存进设备", "wait", [("保存到设备", True)]),
    (K.CONFIRM, "确认写入设备？", "wait", [("取消", True), ("确认写入设备", True)]),
    (K.WRITING, "正在写入设备…", "busy", [("取消", False), ("正在写入…", False)]),
    (K.FAILED, "没有存成功，设备上还是原来的设置", "error", [("查看原因", True), ("再试一次", True)]),
    (K.OFFLINE, "设备没连上，修改还在这台电脑上", "off", [("存为本地草稿", True)]),
    (K.CHECKING, "有修改，还没存进设备", "busy", [("正在检查…", False)]),
    (K.CONFLICT, "设备上的设置在别处改过了", "wait", [("查看原因", True)]),
    (K.UNKNOWN, "不确定有没有存进设备", "error", [("查看原因", True)]),
]


@pytest.mark.parametrize(("kind", "text", "icon", "buttons"), STATES)
def test_each_state_text_icon_and_buttons(banner, kind, text, icon, buttons):
    assert banner.apply(kind) is kind
    assert banner.isVisible() and banner.is_shown()
    assert banner.text() == tr(text)
    assert banner.icon().state() == icon
    assert labels(banner) == [(tr(label), enabled) for label, enabled in buttons]
    primaries = [b for b in banner.buttons() if b.property("boringButton") == "primary"]
    assert len(primaries) <= 1


SIGNALS = [
    (K.DIRTY, "savePreferencesToDevice", "save_requested"),
    (K.CONFIRM, "confirmConfigurationWrite", "confirm_requested"),
    (K.CONFIRM, "cancelConfigurationWrite", "cancel_requested"),
    (K.FAILED, "savePreferencesToDevice", "retry_requested"),
    (K.FAILED, "configurationWriteDetails", "details_requested"),
    (K.CONFLICT, "configurationWriteDetails", "details_requested"),
    (K.UNKNOWN, "configurationWriteDetails", "details_requested"),
    (K.OFFLINE, "savePreferencesDraft", "save_draft_requested"),
]


@pytest.mark.parametrize(("kind", "name", "signal"), SIGNALS)
def test_buttons_emit_their_signal(banner, qtbot, kind, name, signal):
    banner.apply(kind)
    with qtbot.waitSignal(getattr(banner, signal), timeout=500):
        banner.button(name).click()


def test_a_button_emits_once_and_only_its_own_signal(banner):
    fired = []
    for name in ("save_requested", "confirm_requested", "cancel_requested", "retry_requested",
                 "details_requested", "save_draft_requested"):
        getattr(banner, name).connect(lambda name=name: fired.append(name))
    banner.apply(K.FAILED)
    banner.apply(K.DIRTY)  # old buttons go; their connections must not linger
    banner.button("savePreferencesToDevice").click()
    assert fired == ["save_requested"]


def test_greyed_buttons_do_nothing(banner):
    fired = []
    banner.confirm_requested.connect(lambda: fired.append("confirm"))
    banner.cancel_requested.connect(lambda: fired.append("cancel"))
    banner.apply(K.WRITING)
    for button in banner.buttons():
        button.click()
    assert fired == []


def test_hidden_when_nothing_changed_and_no_close_button(banner, qtbot):
    assert not banner.isVisible() and banner.kind() is K.HIDDEN
    banner.apply(flow(T.IDLE))
    assert banner.isVisible()
    assert banner.findChild(QWidget, "bannerClose") is None
    with qtbot.waitSignal(banner.shown_changed) as shown:
        banner.apply(flow(T.IDLE, changes=False))
    assert shown.args == [False]
    qtbot.waitUntil(lambda: not banner.isVisible(), timeout=1000)   # slides out to the right first


def test_saved_after_a_write_hides_itself(banner, qtbot, monkeypatch):
    monkeypatch.setattr(save_banner_module, "SAVED_HIDE_MS", 50)
    hovered = [True]  # offscreen puts the pointer at (0, 0), over the banner
    monkeypatch.setattr(banner, "underMouse", lambda: hovered[0])
    for state in (T.IDLE, T.VALIDATING, T.AWAITING_CONFIRMATION, T.WRITING, T.PENDING, T.VERIFYING):
        banner.apply(flow(state))
    assert banner.apply(flow(T.ACTIVE, changes=False)) is K.SAVED
    assert banner.text() == "已存进设备" and banner.buttons() == []
    qtbot.wait(200)
    assert banner.isVisible()  # the pointer resting on it keeps it
    hovered[0] = False
    qtbot.waitUntil(lambda: not banner.isVisible(), timeout=2000)
    assert banner.kind() is K.HIDDEN
    # Later refreshes with the same saved state do not bring it back.
    assert banner.apply(flow(T.ACTIVE, changes=False)) is K.HIDDEN
    assert not banner.isVisible()


def test_saved_is_not_announced_without_a_write(banner):
    """A fresh banner on an already saved device says nothing."""
    assert banner.apply(flow(T.ACTIVE, changes=False)) is K.HIDDEN
    assert not banner.isVisible()


def test_uncertain_write_that_turns_out_fine_says_saved(banner):
    banner.apply(flow(T.UNKNOWN))
    assert banner.apply(flow(T.ACTIVE, changes=False)) is K.SAVED


def test_width_follows_the_state(banner, qtbot):
    from controller_config.views.ui.banner import HEIGHT

    def settled():  # buttons get their real size once styled
        qtbot.waitUntil(lambda: banner.width() == banner.layout().sizeHint().width(), timeout=1000)
        qtbot.wait(50)
        return banner.width()

    banner.apply(K.DIRTY)
    dirty = settled()
    banner.apply(K.FAILED)
    assert settled() > dirty
    for button in banner.buttons():
        assert button.width() >= button.sizeHint().width()
    assert banner.height() == HEIGHT


def test_disabled_buttons_keep_an_outline():
    from controller_config.style.sheets import COMPONENT_STYLE
    assert 'QFrame#lightingSyncCard QPushButton[bannerButton="true"]:disabled' in COMPONENT_STYLE
    assert 'QLabel#preferencesSyncSummary' in COMPONENT_STYLE


def test_keeps_the_old_save_card_names(banner):
    """Old tests look these up with findChild; keep them findable."""
    from PySide6.QtWidgets import QLabel, QPushButton
    host = banner.parentWidget()
    assert banner.objectName() == "lightingSyncCard"
    banner.apply(K.DIRTY)
    assert host.findChild(QLabel, "preferencesSyncSummary").text() == tr("有修改，还没存进设备")
    save = host.findChild(QPushButton, "savePreferencesToDevice")
    assert save.isEnabled() and save.text() == tr("保存到设备")
    banner.apply(K.CONFIRM)  # no event loop: the old button must already be gone
    assert host.findChild(QPushButton, "savePreferencesToDevice") is None
    assert host.findChild(QPushButton, "confirmConfigurationWrite").isEnabled()
    banner.apply(K.WRITING)
    assert host.findChild(QPushButton, "confirmConfigurationWrite") is None
    assert not host.findChild(QPushButton, "savePreferencesToDevice").isEnabled()
    banner.apply(K.OFFLINE)
    assert host.findChild(QPushButton, "savePreferencesDraft").isEnabled()
    assert host.findChild(QPushButton, "savePreferencesToDevice") is None


def test_write_then_read_back_then_saved_rises_away(banner, qtbot, monkeypatch):
    """-15 第 1 处 (乙): 正在写入设备… -> 正在读回核对… -> 已存进设备, gone after 1.6 s."""
    assert save_banner_module.SAVED_HIDE_MS == 1600
    monkeypatch.setattr(save_banner_module, "SAVED_HIDE_MS", 50)
    monkeypatch.setattr(banner, "underMouse", lambda: False)
    for state in (T.IDLE, T.AWAITING_CONFIRMATION, T.WRITING):
        banner.apply(flow(state))
    assert banner.text() == "正在写入设备…"
    banner.apply(flow(T.VERIFYING))
    assert banner.text() == "正在读回核对…" and banner.icon().state() == "busy"
    assert banner.apply(flow(T.ACTIVE, changes=False)) is K.SAVED
    qtbot.waitUntil(lambda: banner.sliding() == "rise" or not banner.isVisible(), timeout=1000)
    qtbot.waitUntil(lambda: not banner.isVisible(), timeout=1000)


def test_changing_words_stretches_the_pill_like_the_dynamic_island(banner, qtbot):
    """the designer 11-20 真屏验收: no grabbed box or second layer of words; the pill
    changes length with a small bounce and keeps its right edge."""
    from controller_config.views.ui.page_motion import CrossFade

    banner._reduce_motion = False
    banner.apply(K.WRITING)
    qtbot.waitUntil(lambda: banner.sliding() is None, timeout=2000)
    qtbot.waitUntil(lambda: not banner.changing(), timeout=2000)
    banner.place(600 - banner.width(), 10)
    wide = banner.width()
    banner.apply(K.SAVED)
    assert banner.changing()
    assert not banner.findChildren(CrossFade)          # no still with a background
    assert banner.width() >= wide                      # room for the shrinking pill
    seen = []
    banner._morph.valueChanged.connect(lambda _v: seen.append(banner._pill))
    qtbot.waitUntil(lambda: not banner.changing(), timeout=2000)
    narrow = banner.layout().sizeHint().width()
    assert banner.width() == narrow < wide
    assert min(seen) < narrow                          # ran a little past, then back
    assert min(seen) > narrow - 7
