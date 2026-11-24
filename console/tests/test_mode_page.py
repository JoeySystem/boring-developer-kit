"""The Mode page, batch 1 (Mode页 定稿 v1, the designer 11-19, 单 design ticket)."""
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QLineEdit, QWidget

from controller_config.style import tokens
from controller_config.views.mode_page import (
    BarState, BatteryIcon, KnobRow, ModeBottomBar, ModeStage, StageState, StageText,
)
from controller_config.views.ui.mode_device import ModeDeviceView, _fit_label
from test_session_recovery import session  # noqa: F401


def _pixel(widget, x, y) -> QColor:
    image = QImage(widget.size(), QImage.Format.Format_ARGB32)
    image.fill(0)
    widget.render(image, renderFlags=QWidget.RenderFlag.DrawChildren)  # no window background
    return image.pixelColor(x, y)


def _near(colour: QColor, expected: str, slack: int = 60) -> bool:
    target = QColor(expected)
    return all(abs(a - b) <= slack for a, b in zip(colour.getRgb()[:3], target.getRgb()[:3]))


# -- the stage on its own ----------------------------------------------------------------

def test_the_page_scales_with_the_window_and_stays_in_proportion(qtbot):
    stage = ModeStage()
    qtbot.addWidget(stage)
    stage.resize(tokens.MODE_BASE_WIDTH, tokens.MODE_BASE_HEIGHT)
    stage.show()
    assert abs(stage.scale() - 1.0) < 1e-6
    side = stage.device.width()
    stage.resize(1280, 800 - 114)        # the page area of a 1280×800 window
    assert abs(stage.scale() - 1.29) < 0.01
    assert stage.device.width() > side * 1.25
    assert abs(stage.device.geometry().center().x() - 640) <= 1


def test_page_dots_ring_the_screen_the_device_uses_and_flip_on_click(qtbot):
    stage = ModeStage()
    qtbot.addWidget(stage)
    stage.resize(tokens.MODE_BASE_WIDTH, tokens.MODE_BASE_HEIGHT)
    stage.show()
    stage.set_state(StageState(connected=True, screens=3, viewed=1, active=0))
    asked = []
    stage.screen_requested.connect(asked.append)
    flips = []
    stage.flip_requested.connect(flips.append)
    dots = stage.page_dot_rects()
    assert len(dots) == 3
    qtbot.mouseClick(stage, Qt.MouseButton.LeftButton, pos=dots[2].center().toPoint())
    assert asked == [2]
    left, right = stage.neighbour_rects()
    assert left is not None and right is not None   # a middle screen has both sides
    qtbot.mouseClick(stage, Qt.MouseButton.LeftButton, pos=QPointF(right.left() + 30, right.center().y()).toPoint())
    assert flips == [1]
    qtbot.keyClick(stage, Qt.Key.Key_Left)
    assert flips == [1, -1]
    ring = dots[0].center()
    assert _near(_pixel(stage, round(ring.x() + 6), round(ring.y())), tokens.MODE_PAGE_RING)


def test_without_a_device_there_is_no_word_no_floor_and_no_battery(qtbot):
    stage = ModeStage()
    qtbot.addWidget(stage)
    stage.resize(tokens.MODE_BASE_WIDTH, tokens.MODE_BASE_HEIGHT)
    stage.show()
    from controller_config.digital_font import layout_text
    word = layout_text("NORMAL", scale=tokens.MODE_WORD_SCALE)   # font-independent area

    def word_ink():
        image = QImage(stage.size(), QImage.Format.Format_ARGB32)
        image.fill(0)
        stage.render(image, renderFlags=QWidget.RenderFlag.DrawChildren)
        left = round(480 - word.width / 2)
        colour = QColor(tokens.MODE_WORD_DOT)
        return any(image.pixelColor(x, y) == colour
                   for x in range(left, left + round(word.width), 2)
                   for y in range(0, round(word.height), 2))

    stage.set_state(StageState(connected=True, battery=73, usb=True))
    assert word_ink()
    stage.set_state(StageState(connected=False, battery=73))
    # The stage lays no floor of its own (design ticket): nothing is painted there.
    assert not word_ink()
    assert _pixel(stage, 885, 26).alpha() == 0  # where the battery was


def test_words_beside_the_device_hide_behind_the_card(qtbot):
    stage = ModeStage()
    qtbot.addWidget(stage)
    text = StageText(knob=(KnobRow("↺", "逆时针", "音量 −"),), status_line="按下打开对应对话",
                     joystick={"press": "回车"})
    stage.set_state(StageState(connected=True, text=text))
    assert {"逆时针", "音量 −", "按下打开对应对话", "回车"} <= set(stage.words())
    from PySide6.QtWidgets import QLabel
    stage.set_card(QLabel("card"))
    assert "按下打开对应对话" not in stage.words() and "音量 −" in stage.words()


def test_battery_knocks_the_number_out_and_turns_orange_at_twenty(qtbot):
    full = BatteryIcon(73, usb=True)
    low = BatteryIcon(20)
    for icon in (full, low):
        qtbot.addWidget(icon)
    assert _pixel(full, 2, 11) == QColor(tokens.MODE_BATTERY)
    assert _pixel(low, 2, 11) == QColor(tokens.MODE_BATTERY_LOW)
    assert _pixel(full, 46, 11).alpha() > 0            # the bolt
    assert _pixel(low, 46, 11).alpha() == 0             # no USB, no bolt


# -- the device ---------------------------------------------------------------------------

def test_device_labels_status_colours_and_every_control_can_be_chosen(qtbot):
    view = ModeDeviceView()
    qtbot.addWidget(view)
    view.resize(363, 363)
    view.show()
    view.set_labels({"key.9": "复制", "key.12": "未映射"}, unset={"key.12"})
    view.set_status_colors({"key.1": tokens.MODE_AGENT_WORKING})
    assert view.key_fill("key.1") == QColor(tokens.MODE_AGENT_WORKING)
    assert view.key_fill("key.2") == QColor(tokens.TOP_VIEW_STATUS_CAP)
    view.set_connected(False)
    # design ticket 第 1 件: without a device all 12 keys are one dimmed colour.
    assert view.key_fill("key.1") == QColor(tokens.TOP_VIEW_KEY_OFFLINE)
    chosen = []
    view.control_chosen.connect(chosen.append)
    for control_id in ("key.1", "key.9"):
        qtbot.mouseClick(view, Qt.MouseButton.LeftButton, pos=view.key_rect(control_id).center().toPoint())
    qtbot.mouseClick(view, Qt.MouseButton.LeftButton, pos=view._point(-41.45, 41.4).toPoint())
    qtbot.mouseClick(view, Qt.MouseButton.LeftButton, pos=view._point(27.5, 30.1).toPoint())
    assert chosen == ["key.1", "key.9", "encoder", "joystick"]
    assert view.selected() is None   # the knob and joystick have no ring yet


def test_key_labels_fit_without_breaking_a_word():
    assert _fit_label("新建任务", 30, 11)[0] == "新建\n任务"
    shown, font = _fit_label("Approve", 40, 11)
    assert "\n" not in shown and font.pixelSize() < 11
    assert _fit_label("Agent 1", 20, 11)[0] == "Agent\n1"


# -- the bottom bar -----------------------------------------------------------------------

def _bar(qtbot, state):
    bar = ModeBottomBar()
    qtbot.addWidget(bar)
    bar.set_state(state)
    bar.show()
    return bar


def test_bar_says_one_thing_on_the_right(qtbot):
    bar = _bar(qtbot, BarState(profile_name="Default", profile_active=True, right="synced"))
    assert bar.profile.isVisible() and bar.ble.isVisible()
    assert bar.status_text.text() == "已和设备同步"
    assert not bar.save.isVisible() and not bar.enable.isVisible() and not bar.discard.isVisible()

    bar.set_state(BarState(profile_name="Figma", right="enable", can_save=True))
    assert bar.enable.isVisible() and bar.enable.isEnabled() and not bar.status_text.isVisible()

    bar.set_state(BarState(profile_name="Default", right="pending", count=3, can_save=True))
    assert bar.pending.isVisible() and "3" in bar.pending.text() and bar.save.isVisible()

    bar.set_state(BarState(profile_name="Default", right="offline", ble_button=True))
    assert bar.ble_text.isVisible() and bar.status_text.text() == "没连上设备" and not bar.save.isVisible()

    bar.set_state(BarState(profile_name="Default", right="failed", text="没保存上，可以再试一次", can_save=True))
    assert bar.save.isVisible() and bar.save.text() == "再试一次"   # one click to retry

    bar.set_state(BarState(profile_name="Figma", right="enable", count=2, can_save=True))
    assert bar.enable.isVisible() and bar.pending.isVisible() and not bar.save.isVisible()


def test_bar_while_a_key_is_being_changed(qtbot):
    bar = _bar(qtbot, BarState(profile_name="Default", editing_key=True, right="pending", can_save=True))
    assert bar.discard.isVisible() and bar.save.isVisible()
    assert not bar.profile.isVisible() and not bar.ble.isVisible()
    assert bar.edit_text.isVisible() and not bar.status_text.isVisible()
    clicked = []
    bar.discard_clicked.connect(lambda: clicked.append("discard"))
    bar.save_clicked.connect(lambda: clicked.append("save"))
    bar.discard.click()
    bar.save.click()
    assert clicked == ["discard", "save"]


def test_bar_offline_while_a_key_is_changed_says_why_it_cannot_save(qtbot):
    bar = _bar(qtbot, BarState(editing_key=True, right="offline", text="没连上设备，连上以后再保存"))
    assert bar.edit_text.isVisible() and bar.discard.isVisible()
    assert bar.status_text.isVisible() and bar.status_text.text() == "没连上设备，连上以后再保存"
    assert not bar.save.isVisible()


def test_bar_hides_bluetooth_when_the_device_has_no_computers_to_manage(qtbot):
    bar = _bar(qtbot, BarState(profile_name="Default", ble=False))
    assert not bar.ble.isVisible()


# -- in the window ------------------------------------------------------------------------

def _stage(window) -> ModeStage:
    return next(stage for stage in window.findChildren(ModeStage) if stage.isVisible())


def test_home_is_the_stage_with_one_screen_per_set_and_codex_last(session, qtbot):  # noqa: F811
    window, vm, *_ = session
    vm.navigate("overview")
    stage = _stage(window)
    screens = window._mode_screens()
    assert screens[-1] == ("codex", None)
    assert all(kind == "normal" for kind, _ in screens[:-1])
    assert stage.state().screens == len(screens)
    assert stage.state().word == "NORMAL"
    assert stage.bar.state().profile_name
    window._show_mode_screen(len(screens) - 1)
    stage = _stage(window)
    assert stage.state().word == "CODEX"
    assert stage.device.labels()["key.3"] == "Fork"
    assert stage.bar.state().profile_name == "Codex"


def test_choosing_a_key_opens_the_card_and_the_bar_follows_typing(session, qtbot):  # noqa: F811
    window, vm, *_ = session
    vm.navigate("overview")
    _stage(window).device.choose("key.12")
    stage = _stage(window)
    card = stage.card()
    assert card is not None and card.objectName() == "mappingEditorCard"
    assert card.findChild(QLineEdit, "mappingShortNameEditor") is not None
    assert card.findChild(type(stage.bar.save), "modeCardOtherActions") is not None
    name = card.findChild(QLineEdit, "mappingShortNameEditor")
    name.setText("命令面板")
    assert stage.bar.state().editing_key is True
    assert stage.bar.discard.isVisible()
    stage.bar.discard.click()
    stage = _stage(window)
    assert stage.bar.state().editing_key is False
    assert stage.card().findChild(QLineEdit, "mappingShortNameEditor").text() != "命令面板"
    stage.blank_clicked.emit()
    assert _stage(window).card() is None


def test_the_set_name_on_the_bar_is_never_translated(session, qtbot):  # noqa: F811
    window, vm, *_ = session
    vm.navigate("overview")
    profile_id = vm.draft.config["active_profile"]
    vm.rename_profile(profile_id, "新配置方案")   # also an interface phrase with its own translation
    window._activate_profile(profile_id)
    window.render(vm.model)
    assert _stage(window).bar.profile_label.text() == "新配置方案"


# -- batch 2: AI 额度, 状态灯键, 语音键 ----------------------------------------------------------

from controller_config.views.mode_page import ModeQuota, QuotaProvider, QuotaWindow  # noqa: E402


def _quota(qtbot, providers):
    quota = ModeQuota(reduce_motion=True)
    qtbot.addWidget(quota)
    quota.resize(190, 300)
    quota.set_data(providers, "更新于 22:20")
    quota.show()
    return quota


def test_quota_grows_in_place_and_closes_on_a_second_click(qtbot):
    codex = QuotaProvider("Codex", "CODEX", "PRO", (QuotaWindow("5h", 62, "00:20"), QuotaWindow("week", 39, "11-21")))
    quota = _quota(qtbot, (codex,))
    assert not quota.is_open() and quota.progress() == 0
    qtbot.mouseClick(quota, Qt.MouseButton.LeftButton, pos=QPointF(38, 40).toPoint())   # on the small ring
    assert quota.is_open() and quota.progress() == 1   # reduce motion: straight to the end
    qtbot.mouseClick(quota, Qt.MouseButton.LeftButton, pos=QPointF(60, 80).toPoint())
    assert not quota.is_open()


def test_quota_opens_over_0_48_seconds_and_closes_over_0_30(qtbot):
    quota = ModeQuota(reduce_motion=False)
    qtbot.addWidget(quota)
    quota.set_open(True)
    assert quota._animation.duration() == tokens.MOTION_LONG_MS
    qtbot.waitUntil(lambda: quota.progress() == 1, timeout=2000)
    quota.set_open(False)
    assert quota._animation.duration() == tokens.MOTION_MEDIUM_MS


def test_stage_click_beside_an_open_quota_closes_it_first(qtbot):
    stage = ModeStage()
    qtbot.addWidget(stage)
    stage.resize(tokens.MODE_BASE_WIDTH, tokens.MODE_BASE_HEIGHT)
    stage.show()
    stage.quota._reduce_motion = True
    stage.quota.set_open(True)
    blanks = []
    stage.blank_clicked.connect(lambda: blanks.append(1))
    qtbot.mouseClick(stage, Qt.MouseButton.LeftButton, pos=QPointF(600, 60).toPoint())
    assert not stage.quota.is_open() and not blanks   # 点别处，收回去; the card stays
    qtbot.mouseClick(stage, Qt.MouseButton.LeftButton, pos=QPointF(600, 60).toPoint())
    assert blanks == [1]


def _usage(window, monkeypatch, codex_windows, claude_windows):
    from types import SimpleNamespace
    from controller_config.codex_usage import CodexQuotaBucket, CodexQuotaWindow, CodexUsageSnapshot, CodexUsageStatus

    def snap(limit_id, windows, plan):
        five, week = windows
        bucket = CodexQuotaBucket(limit_id, None, plan,
                                  CodexQuotaWindow(100 - five, 300, None) if five is not None else None,
                                  CodexQuotaWindow(100 - week, 10_080, None) if week is not None else None)
        return CodexUsageSnapshot(CodexUsageStatus.AVAILABLE, (bucket,), "test", 1_700_000_000.0)

    class Background(SimpleNamespace):
        def __getattr__(self, _name):   # everything else the window asks a background helper for
            return lambda *_args, **_kwargs: None

    background = Background(codex_usage_snapshot=snap("codex", codex_windows, "pro"),
                            claude_usage_snapshot=snap("claude", claude_windows, "ultra"))
    monkeypatch.setattr(window, "_background_controller", background)


def test_quota_shows_the_week_for_an_app_without_a_five_hour_limit(session, monkeypatch, qtbot):  # noqa: F811
    window, vm, *_ = session
    vm.navigate("overview")
    _usage(window, monkeypatch, (62, 39), (None, 55))   # Claude Ultra: no 5-hour limit (08, 二)
    window._refresh_mode_quota()
    codex, claude = _stage(window).quota.providers()
    assert (codex.plan, [w.label for w in codex.windows], codex.windows[codex.shown].label) == ("PRO", ["5h", "week"], "5h")
    assert [w.label for w in claude.windows] == ["week"] and claude.windows[claude.shown].remaining == 55
    window._set_mode_quota_default("week")   # Settings › 首页的 AI 额度默认显示
    codex, _ = _stage(window).quota.providers()
    assert codex.windows[codex.shown].label == "week"
    window._set_mode_quota_default("5h")


def test_quota_without_any_usage_shows_a_dash_not_a_number(session):  # noqa: F811
    window, vm, *_ = session
    vm.navigate("overview")
    providers = _stage(window).quota.providers()
    assert [p.name for p in providers] == ["Codex", "Claude"]
    assert all(p.windows[0].remaining is None for p in providers)


def test_quota_reset_time_is_short():
    from datetime import datetime, timedelta
    from controller_config.views.main_window import _quota_reset
    soon = datetime.now().replace(second=0, microsecond=0) + timedelta(minutes=1)
    assert _quota_reset(int(soon.timestamp())) in {soon.strftime("%H:%M"), soon.strftime("%m-%d")}
    assert _quota_reset(int((datetime.now() + timedelta(days=3)).timestamp())) == \
        (datetime.now() + timedelta(days=3)).strftime("%m-%d")
    assert _quota_reset(None) == ""


def test_settings_has_the_quota_default_option(session):  # noqa: F811
    from controller_config.views.ui import SegmentedControl
    window, vm, *_ = session
    vm.navigate("settings")
    window._select_settings_section("system")   # the AI wall is full; 通用 has a free row
    option = window.findChild(SegmentedControl, "modeQuotaDefault")
    assert option is not None and option.value() == "5h"
    option.setValue("week")
    assert window._mode_quota_default() == "week"
    option.setValue("5h")


def test_a_status_light_key_rings_all_six_and_saves_through_the_bar(qtbot, contract, tmp_path):
    from controller_config.official_controls import MATRIX12_AGENT_STATUS_KEYS
    from test_agent_status_settings_ui import make_window
    window, _card, vm, _ = make_window(qtbot, contract, tmp_path, "normal", supported=True)
    window.show()
    vm.navigate("overview")
    window._select_configuration_mode("normal")
    window._close_control_editor()   # make_window picked key 1 already; a second press would close it
    _stage(window).device.choose("key.1")
    stage = _stage(window)
    assert stage.device.ringed() == set(MATRIX12_AGENT_STATUS_KEYS)
    card = stage.card()
    assert card.findChild(type(stage.bar.save), "normalAgentApply").isHidden()   # merged into the bar
    segments = [b for b in card.findChildren(type(stage.bar.save), "modeCardPurpose") if b.property("behavior")]
    assert [b.property("behavior") for b in segments] == ["open_conversation", "status_only"]
    vm.shutdown()


# -- batch 3: 全部方案 -------------------------------------------------------------------------

def _menu_actions(window, monkeypatch, open_menu):
    """Open a menu without its event loop; return {objectName: action}."""
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QMenu
    import controller_config.views.main_window as main_window_module
    shown = []

    class RecordingMenu(QMenu):   # PySide6 ignores QMenu.exec patched on the class itself
        def exec(self, *_args, **_kwargs):  # noqa: A003 - Qt's name
            shown.append(self)

    monkeypatch.setattr(main_window_module, "QMenu", RecordingMenu)
    open_menu()
    assert shown, "no menu opened"
    return {action.objectName(): action for action in shown[-1].findChildren(QAction) if action.objectName()}


def test_the_set_name_opens_all_screens_and_a_click_goes_into_one(session, qtbot):  # noqa: F811
    window, vm, *_ = session
    vm.navigate("overview")
    stage = _stage(window)
    stage.bar.profile.click()
    assert stage.overview_shown()
    names = [card.screen.name for card in stage.overview.cards if card.screen is not None]
    assert names[-1] == "Codex" and len(names) == len(window._mode_screens())
    codex = next(card for card in stage.overview.cards if card.screen is not None and card.screen.codex)
    assert codex.more is None and codex.enable is None   # Codex: fixed keys, no menu
    qtbot.mouseClick(codex, Qt.MouseButton.LeftButton)
    stage = _stage(window)
    assert not stage.overview_shown() and stage.state().word == "CODEX"


def test_the_device_in_use_has_the_green_edge_and_others_can_be_enabled(session, qtbot):  # noqa: F811
    window, vm, gateway, snapshot, _ = session
    second = vm.create_profile()
    vm.navigate("overview")
    window._mode_show_overview(True)
    stage = _stage(window)
    cards = {card.screen.key: card for card in stage.overview.cards if card.screen is not None}
    active = window._active_mode_screen(vm.model.snapshot)
    active_key = window._mode_screens()[active] if active is not None else None
    for key, card in cards.items():
        assert card.property("active") is (key == active_key)
    new = cards[("normal", second)]
    assert new.screen.note == "设备没在用" and new.enable is not None
    assert stage.overview.cards[-1].screen is None   # 新建一屏


def test_each_screen_menu_renames_saves_as_and_deletes_that_screen(session, monkeypatch):  # noqa: F811
    window, vm, *_ = session
    second = vm.create_profile()
    vm.navigate("overview")
    window._mode_show_overview(True)
    stage = _stage(window)
    card = next(c for c in stage.overview.cards if c.screen is not None and c.screen.key == ("normal", second))
    actions = _menu_actions(window, monkeypatch, card.more.click)
    assert {"renameProfileAction", "saveProfileAsAction", "deleteProfileAction"} <= set(actions)
    asked = []
    monkeypatch.setattr(window, "_delete_profile", lambda: asked.append(window._editing_profile(vm.draft)))
    actions["deleteProfileAction"].trigger()
    assert asked == [second]   # it acted on the screen whose ··· was pressed


def test_new_screen_offers_blank_save_current_and_ai_templates(session, monkeypatch):  # noqa: F811
    window, vm, *_ = session
    vm.navigate("overview")
    window._mode_show_overview(True)
    stage = _stage(window)
    new = stage.overview.cards[-1]
    actions = _menu_actions(window, monkeypatch, lambda: stage.overview.new_requested.emit(new))
    assert {"createProfileAction", "saveProfileAsAction"} <= set(actions)
    before = len(vm.draft.profiles)
    actions["createProfileAction"].trigger()
    assert len(vm.draft.profiles) == before + 1
    assert _stage(window).overview_shown()   # still on 全部方案, now with the new screen
    names = [c.screen.name for c in _stage(window).overview.cards if c.screen is not None]
    assert len(names) == before + 2   # every set plus Codex


def test_import_export_and_done_sit_on_the_overview_bar(session, monkeypatch):  # noqa: F811
    window, vm, *_ = session
    vm.navigate("overview")
    window._mode_show_overview(True)
    overview = _stage(window).overview
    calls = []
    monkeypatch.setattr(window, "_import_configuration", lambda: calls.append("import"))
    monkeypatch.setattr(window, "_export_configuration", lambda kind, **_: calls.append(kind) or True)
    overview.import_requested.disconnect()
    overview.import_requested.connect(window._import_configuration)
    overview.import_button.click()
    overview.export_button.click()   # nothing unsaved: exports what the device has
    assert calls == ["import", "confirmed"]
    overview.done.click()
    assert not _stage(window).overview_shown()


def test_status_card_says_what_the_device_has_now(qtbot, contract, tmp_path):
    from test_agent_status_settings_ui import finish_read, make_window
    window, card, vm, _ = make_window(qtbot, contract, tmp_path, "normal", supported=True)
    finish_read(vm, "status_only")
    from PySide6.QtWidgets import QLabel
    assert card.findChild(QLabel, "modeCardCurrent").text() == "设备当前"
    assert card.findChild(QLabel, "normalAgentCurrent").text() == "仅显示状态"
    vm.shutdown()


# -- 照稿改 (单 design ticket) ------------------------------------------------------

def _enable_voice(session, monkeypatch):  # noqa: F811
    from dataclasses import replace
    window, vm, gateway, snapshot, _ = session
    monkeypatch.setattr("controller_config.views.main_window.sys.platform", "darwin")
    snapshot = replace(
        snapshot,
        status={**snapshot.status, "operating_mode": "normal"},
    )
    gateway.snapshot_ready.emit(snapshot)
    window._select_configuration_mode("normal")
    vm.navigate("overview")
    window._show_mode_screen(0)
    _stage(window).device.choose("key.8")
    return window


def _purpose(card, value):
    from PySide6.QtWidgets import QPushButton
    return next(button for button in card.findChildren(QPushButton) if button.property("purpose") == value)


def _row_shown(card, widget) -> bool:
    return widget is not None and widget.isVisibleTo(card)


def test_voice_and_ai_voice_cards_have_no_other_actions_and_no_name(session, monkeypatch, qtbot):  # noqa: F811
    from PySide6.QtWidgets import QPushButton
    window = _enable_voice(session, monkeypatch)
    for purpose, plain in (("voice", False), ("native", False), ("shortcut", True)):
        _purpose(_stage(window).card(), purpose).click()
        card = _stage(window).card()
        assert _purpose(card, purpose).isChecked()
        other = card.findChild(QPushButton, "modeCardOtherActions")
        name = card.findChild(QLineEdit, "mappingShortNameEditor")
        assert _row_shown(card, other) is plain, purpose
        assert _row_shown(card, name.parentWidget()) is plain, purpose


def test_knob_and_joystick_short_names_follow_the_draft():
    from controller_config.actions import describe_action
    volume_down = {"type": "consumer", "usage": 0x00EA}
    assert describe_action(volume_down, compact=True) == "音量 −"
    assert describe_action({"type": "consumer", "usage": 0x00E9}, compact=True) == "音量 +"
    assert describe_action({"type": "consumer", "usage": 0x00E2}, compact=True) == "静音"
    assert describe_action({"type": "device", "name": "macro_cancel"}, compact=True) == "取消"
    assert describe_action(volume_down) == "音量降低"          # the card keeps the full name


def test_no_card_scrolls_and_nothing_is_wider_than_the_card(session, qtbot):  # noqa: F811
    from PySide6.QtWidgets import QScrollArea
    window, vm, *_ = session
    vm.navigate("overview")
    window._show_mode_screen(0)
    window.resize(1100, 700)   # the smallest window on a Mac screen (the test window allows less)
    for control in ("key.12", "key.8", "encoder", "joystick"):
        stage = _stage(window)
        stage.device.choose(control)
        stage.resize(stage.size())
        stage = _stage(window)
        card = stage.card()
        body = card.findChild(QScrollArea, "mappingEditorBody")
        assert body.verticalScrollBar().maximum() == 0, control
        assert body.widget().minimumSizeHint().width() <= body.viewport().width(), control
        bar = stage.bar.geometry()
        assert stage.card_host.geometry().bottom() < bar.top(), control


def test_purpose_segments_are_twelve_with_the_draft_colours():
    from controller_config.views.mode_page import card_sheet
    sheet = card_sheet(1.0)
    rule = sheet[sheet.index("QPushButton#modeCardPurpose {{".replace("{{", "{")):]
    rule = rule[:rule.index("}")]
    assert "font-size: 12px" in rule and tokens.MODE_CARD_SEGMENT_TEXT in rule
    assert "border-radius: 7px" in rule and "padding: 5px 8px" in rule
    assert tokens.MODE_CARD_SEGMENT_TEXT == "#5D584F"


def test_codex_screen_draws_a_lock_before_the_fixed_note(qtbot, monkeypatch):
    import controller_config.views.mode_page as mode_page
    calls = []
    real = mode_page.paint_lock
    monkeypatch.setattr(mode_page, "paint_lock", lambda painter, box, colour: (calls.append((box, colour)),
                                                                              real(painter, box, colour)))
    stage = ModeStage()
    qtbot.addWidget(stage)
    stage.resize(960, 532)
    stage.set_state(StageState(connected=True, text=StageText(fixed_note=("按键功能固定", "要调整，请到 Codex 里改"))))
    stage.grab()
    assert calls and calls[0][0].width() == 12 and calls[0][1] == tokens.MODE_LABEL_TITLE


def test_ai_voice_drops_the_two_rows_even_without_voice_input_firmware(session, qtbot):  # noqa: F811
    from PySide6.QtWidgets import QPushButton
    window, vm, *_ = session
    vm.navigate("overview")
    window._show_mode_screen(0)
    _stage(window).device.choose("key.8")
    _purpose(_stage(window).card(), "native").click()
    card = _stage(window).card()
    assert _purpose(card, "native").isChecked()
    assert not _row_shown(card, card.findChild(QPushButton, "modeCardOtherActions"))
    assert not _row_shown(card, card.findChild(QLineEdit, "mappingShortNameEditor").parentWidget())


def test_the_screens_either_side_are_smaller_than_the_one_being_set(qtbot):
    stage = ModeStage()
    qtbot.addWidget(stage)
    stage.resize(960, 532)
    stage.set_state(StageState(connected=True, screens=3, viewed=1, active=0))
    left, right = stage.neighbour_rects()
    device = stage.rect_to_widget(stage.device_page_rect())
    for side in (left, right):
        assert side.width() < device.width() * 0.75 and side.height() < device.height() * 0.75
        assert abs(side.center().y() - device.center().y()) < 1      # level with the device
    assert left.right() < device.left() and right.left() > device.right()


def test_a_key_without_a_name_shows_unmapped_in_the_ui_language_and_saves_no_name(session, monkeypatch):  # noqa: F811
    from controller_config.actions import mapping_display_name
    from controller_config.i18n import translate_ui_text
    from controller_config.views.main_window import _editor_name, _stored_name
    window, vm, *_ = session
    window._language_manager.set_language("en_US")
    try:
        assert _editor_name("key.12", None) == translate_ui_text("未映射") != "未映射"
        assert _stored_name(translate_ui_text("未映射")) == "" and _stored_name("未映射") == ""
        assert _stored_name("命令面板") == "命令面板"
        vm.navigate("overview")
        window._show_mode_screen(0)
        _stage(window).device.choose("key.12")
        mapping = vm.draft.mapping(vm.model.snapshot.active_profile_id, "key.12")
        field = _stage(window).card().findChild(QLineEdit, "mappingShortNameEditor")
        assert field.text() == _editor_name("key.12", mapping)
        if mapping_display_name("key.12", mapping) == "未映射":
            assert field.text() == translate_ui_text("未映射")   # not the Chinese stand-in
        assert _stage(window).bar.state().editing_key is False  # showing the stand-in is not a change
    finally:
        window._language_manager.set_language("zh_CN")


def test_three_dot_letters_shrink_to_the_width_of_two(qtbot):
    """design decision / -06 第 5 件: CCW 三个字母时自动缩小，不挤掉标题；OK、DN 照现在."""
    from controller_config.views.mode_page import ModeKeyGlyph
    two, three = ModeKeyGlyph("CW"), ModeKeyGlyph("CCW")
    qtbot.addWidget(two)
    qtbot.addWidget(three)
    assert three.width() <= two.width() + 1
    assert ModeKeyGlyph("DN").glyph_scale() == ModeKeyGlyph.SCALE
    assert three.glyph_scale() < ModeKeyGlyph.SCALE


def test_dragging_the_window_does_not_restyle_until_it_stops(qtbot):
    """design decision: 拖动中不重设样式，松手后套一次."""
    from PySide6.QtWidgets import QMainWindow
    window = QMainWindow()
    qtbot.addWidget(window)
    stage = ModeStage()
    window.setCentralWidget(stage)
    window.resize(960, 532)
    window.show()
    qtbot.wait(100)
    bar = stage.bar
    before = bar.styleSheet()
    window.resize(1000, 555)                      # one resize on its own is laid out at once
    assert not stage.resizing() and bar.styleSheet() != before
    before = bar.styleSheet()
    window.resize(1100, 610)                      # a second one right after: a drag
    assert stage.resizing()
    assert bar.styleSheet() == before             # nothing restyled mid-drag
    qtbot.waitUntil(lambda: not stage.resizing(), timeout=2000)
    qtbot.wait(50)
    assert bar.styleSheet() != before             # restyled once it stopped
    assert abs(bar.height() - round(52 * stage.scale())) <= 1


def test_the_device_is_drawn_every_frame_of_a_drag(qtbot):
    """design decision 补: the picture the device is stretched from during a drag is taken
    outside its paint; taken inside, that frame's paint had no device and drew nothing."""
    from PySide6.QtCore import qInstallMessageHandler
    from PySide6.QtWidgets import QMainWindow
    from controller_config.views.ui.mode_device import ModeDeviceView
    window = QMainWindow()
    qtbot.addWidget(window)
    stage = ModeStage()
    window.setCentralWidget(stage)
    window.resize(960, 532)
    window.show()
    qtbot.wait(100)
    view = stage.findChild(ModeDeviceView)
    messages = []
    previous = qInstallMessageHandler(lambda kind, context, message: messages.append(message))
    try:
        window.resize(1000, 555)
        window.resize(1100, 610)                  # a drag
        assert stage.resizing() and view._resize_picture is not None
        assert view._resize_picture.width() < view.width() * view.devicePixelRatioF()   # its size before
        view.repaint()
        frame = view.grab().toImage()
    finally:
        qInstallMessageHandler(previous)
    assert not [m for m in messages if "paintEngine" in m or "engine == 0" in m]
    lit = sum(frame.pixelColor(x, frame.height() // 2).alpha() > 0 for x in range(0, frame.width(), 4))
    assert lit > 0


def test_a_slow_frame_does_not_keep_a_hand_drag_from_counting_as_one(qtbot, monkeypatch):
    """真屏 11-22 手拖窗口: by hand the system sends the next size only once a frame is
    on screen; the gap is counted from that frame drawn, so a slow frame still drags."""
    import time as clock
    from PySide6.QtWidgets import QMainWindow
    window = QMainWindow()
    qtbot.addWidget(window)
    stage = ModeStage()
    window.setCentralWidget(stage)
    window.resize(960, 532)
    window.show()
    qtbot.wait(300)
    slow = ModeStage._paint_page
    monkeypatch.setattr(ModeStage, "_paint_page", lambda self, painter: (clock.sleep(0.12), slow(self, painter)))
    window.resize(1000, 555)
    stage.repaint()                                # a frame of 120 ms, longer than the gap
    assert not stage.resizing()                    # one resize on its own: laid out at once
    clock.sleep(0.03)
    window.resize(1040, 577)                       # the next size, 30 ms after that frame
    assert stage.resizing()
