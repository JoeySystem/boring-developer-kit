"""设置与维护的方块墙：五个分组图标、固定大小的方块、Claude 和豆包两块的状态。"""
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QEvent, QObject, QPoint, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QHelpEvent, QPalette
from PySide6.QtWidgets import QLabel, QMessageBox, QPushButton, QToolTip, QVBoxLayout, QWidget

from controller_config import desktop_permissions as perm
from controller_config.viewmodels import desktop_agents as link_codes
from controller_config.views import settings_wall as wall_module
from controller_config.views.settings_wall import (
    GAP,
    MAX_UNIT,
    MIN_UNIT,
    SECTIONS,
    UNIT,
    CardDialog,
    ClaudeStatusTile,
    CodexStatusTile,
    DoubaoTile,
    LampLayout,
    LampsTile,
    RailButton,
    SettingsRail,
    SettingsWall,
    WallTile,
    square_tile,
    wall_scroll,
)
from test_session_recovery import session  # noqa: F401 - pytest fixture
from notice_helpers import answer_questions


class FakeBridge(QObject):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.enabled = False
        self.inspection = None
        self.message = "状态灯未启用"
        self.registry = SimpleNamespace(sessions=(), overflow=())
        self.delivery = None
        self.fault = False
        self.calls = []

    def enable(self):
        self.calls.append("enable")
        self.enabled = True

    def disable(self):
        self.calls.append("disable")
        self.enabled = False

    def repair(self):
        self.calls.append("repair")

    def retry(self):
        self.calls.append("retry")

    def inspect(self):
        self.calls.append("inspect")


class FakeLink(QObject):
    changed = Signal()

    def __init__(self, availability=link_codes.APP_NOT_RUNNING, lights=0):
        super().__init__()
        self.enabled = availability != link_codes.DISABLED
        self.availability = availability
        self.lights_in_use = lights
        self.fault_reason = ""
        self.fault_detail = ""
        self.last_open_problem = ""

    def set_enabled(self, enabled):
        self.enabled = enabled
        self.availability = link_codes.APP_NOT_RUNNING if enabled else link_codes.DISABLED
        self.changed.emit()

    def retry(self):
        pass


class FakePermissions:
    def __init__(self, *, mac=True, access=perm.TRUSTED):
        self.is_mac = mac
        self.access = access
        self.opened = 0

    def accessibility(self):
        return self.access

    def bluetooth(self):
        return perm.BT_ALLOWED

    def open_accessibility_settings(self):
        self.opened += 1


def test_sections_keep_the_agreed_order_and_names():
    assert [(key, label) for key, label, _icon in SECTIONS] == [
        ("device", "设备与连接"),
        ("ai", "AI 状态灯"),
        ("appearance", "灯光与反馈"),
        ("system", "通用"),
        ("updates", "更新"),
    ]


def test_rail_button_shows_only_an_icon_but_keeps_its_name(qtbot):
    button = RailButton("AI 状态灯", "lamps")
    qtbot.addWidget(button)
    assert button.objectName() == "settingsGroup"
    assert button.text() == "AI 状态灯" and button.accessibleName() == "AI 状态灯"
    assert (button.width(), button.height()) == (40, 40)


def test_tiles_have_fixed_sizes_and_keep_their_cells_when_the_wall_shrinks(qtbot):
    wall = SettingsWall("device")
    qtbot.addWidget(wall)
    big = wall.place(WallTile(2, 2), 0, 0)
    small = wall.place(square_tile("joystick", "摇杆校准", "说明"), 0, 2)
    wide = wall.place(WallTile(2, 1), 1, 2)
    assert (big.width(), big.height()) == (2 * UNIT + GAP, 2 * UNIT + GAP)
    assert (small.width(), small.height()) == (UNIT, UNIT)
    assert (wide.width(), wide.height()) == (2 * UNIT + GAP, UNIT)
    cells = [wall._grid.getItemPosition(wall._grid.indexOf(tile)) for tile in (big, small, wide)]

    wall.set_unit(10)
    assert small.width() == MIN_UNIT and big.width() == 2 * MIN_UNIT + GAP
    assert wall.width() == 4 * MIN_UNIT + 3 * GAP
    assert [wall._grid.getItemPosition(wall._grid.indexOf(tile)) for tile in (big, small, wide)] == cells

    wall.set_unit(10_000)
    assert small.width() == MAX_UNIT


def test_wall_fits_a_narrow_viewport_without_sideways_scrolling(qtbot):
    wall = SettingsWall("system")
    scroll = wall_scroll(wall)
    qtbot.addWidget(scroll)
    scroll.resize(700, 500)
    scroll.show()
    qtbot.waitUntil(lambda: wall.width() <= scroll.viewport().width())
    assert scroll.widget().width() <= scroll.viewport().width()


def test_claude_tile_states(qtbot, monkeypatch):
    bridge = FakeBridge()
    tile = ClaudeStatusTile(bridge)
    qtbot.addWidget(tile)
    assert tile.state.text() == "未开启"
    assert not tile.enabled.isChecked()
    assert tile.fix.isHidden() and not tile.note.isHidden()

    # 拨开要先确认；取消就退回去，不动 Claude 的设置。
    answer_questions(monkeypatch, lambda *_a, **_k: QMessageBox.StandardButton.Cancel)
    tile.enabled.click()
    assert bridge.calls == [] and not tile.enabled.isChecked()

    answer_questions(monkeypatch, lambda *_a, **_k: QMessageBox.StandardButton.Yes)
    tile.enabled.click()
    assert bridge.calls == ["enable"] and tile.enabled.isChecked()
    assert tile.state.text() == "已开启 · 尚无 Claude 会话"

    bridge.registry = SimpleNamespace(
        sessions=(SimpleNamespace(session_id="a", slot=0), SimpleNamespace(session_id="b", slot=3),
                  SimpleNamespace(session_id="c", slot=None)),
        overflow=(),
    )
    bridge.changed.emit()
    assert tile.state.text() == "已开启 · 正在显示 2 个 Claude 会话"

    bridge.registry = SimpleNamespace(sessions=bridge.registry.sessions,
                                      overflow=(SimpleNamespace(session_id="z", slot=None),))
    bridge.changed.emit()
    assert tile.state.text() == "已开启 · 6 个灯已占满，新会话暂不显示"

    tile.enabled.click()
    assert bridge.calls == ["enable", "disable"]
    assert tile.state.text() == "未开启"


def test_claude_tile_shows_one_fix_action_when_something_is_wrong(qtbot):
    bridge = FakeBridge()
    bridge.enabled = True
    bridge.fault = True
    bridge.message = "设备没有确认灯的状态"
    tile = ClaudeStatusTile(bridge)
    qtbot.addWidget(tile)
    assert tile.state.text() == "状态灯没有生效：设备没有确认灯的状态"
    assert not tile.fix.isHidden() and tile.fix.text() == "重试"
    assert tile.note.isHidden()
    tile.fix.click()
    assert bridge.calls == ["retry"]

    bridge.fault = False
    bridge.inspection = SimpleNamespace(problem="", needs_repair=True)
    bridge.changed.emit()
    assert tile.fix.text() == "修复"
    tile.fix.click()
    assert bridge.calls == ["retry", "repair"]

    opened = []
    tile._open_details = lambda: opened.append(True)
    bridge.inspection = SimpleNamespace(problem="找不到 Claude 桌面端", needs_repair=False)
    bridge.changed.emit()
    assert tile.state.text() == "状态灯没有生效：找不到 Claude 桌面端"
    assert tile.fix.text() == "处理"
    tile.fix.click()
    assert opened == [True] and bridge.calls == ["retry", "repair"]


@pytest.mark.parametrize("availability,lights,expected", [
    (link_codes.DISABLED, 0, "未开启"),
    (link_codes.APP_NOT_RUNNING, 0, "等待豆包打开"),
    (link_codes.ACTIVE, 0, "工作中 · 有对话时才亮灯"),
    (link_codes.ACTIVE, 2, "工作中，占用 2 颗灯"),
    (link_codes.UNAVAILABLE_NO_DEVICE, 0, "等待设备"),
])
def test_doubao_tile_states(qtbot, availability, lights, expected):
    tile = DoubaoTile(FakeLink(availability, lights), FakePermissions(), poll_ms=60_000)
    qtbot.addWidget(tile)
    assert tile.state.text() == expected
    assert tile.enabled.isChecked() is (availability != link_codes.DISABLED)
    assert tile.access.isHidden() and tile.allow.isHidden() and not tile.note.isHidden()


def test_doubao_tile_says_it_is_on_when_firmware_cannot_do_it_and_offers_the_update(qtbot):
    # 开关开着、固件不支持：先说"已开启"，再说差什么，旁边一个按钮跳到「更新」。
    jumps = []
    tile = DoubaoTile(FakeLink(link_codes.NEEDS_FIRMWARE), FakePermissions(), poll_ms=60_000,
                      open_updates=lambda: jumps.append("updates"))
    qtbot.addWidget(tile)
    assert tile.enabled.isChecked()
    assert tile.state.text() == "已开启，但设备固件不支持"
    assert not tile.update_firmware.isHidden() and tile.update_firmware.text() == "去更新"
    assert tile.allow.isHidden() and not tile.more.isHidden()
    tile.update_firmware.click()
    assert jumps == ["updates"]

    # 别的状态不出现这个按钮；关掉开关后也不出现。
    tile.enabled.click()
    assert tile.state.text() == "未开启" and tile.update_firmware.isHidden()
    for availability in (link_codes.APP_NOT_RUNNING, link_codes.ACTIVE, link_codes.UNAVAILABLE_NO_DEVICE):
        other = DoubaoTile(FakeLink(availability), FakePermissions(), poll_ms=60_000, open_updates=lambda: None)
        qtbot.addWidget(other)
        assert other.update_firmware.isHidden()


def _text_colour(label: QLabel) -> str:
    label.ensurePolished()
    return label.palette().color(QPalette.ColorRole.WindowText).name().upper()


def test_states_that_are_not_running_are_grey_whatever_the_label_is_called(qtbot):
    # "未开启""等待豆包打开"要是灰的。各块给这行换了 objectName，样式不能靠名字匹配。
    host = QWidget()
    host.setStyleSheet(wall_module.WALL_STYLE)
    box = QVBoxLayout(host)
    claude = ClaudeStatusTile(FakeBridge())
    doubao = DoubaoTile(FakeLink(link_codes.APP_NOT_RUNNING), FakePermissions(), poll_ms=60_000)
    working = DoubaoTile(FakeLink(link_codes.ACTIVE, 2), FakePermissions(), poll_ms=60_000)
    for tile in (claude, doubao, working):
        box.addWidget(tile)
    qtbot.addWidget(host)
    host.show()
    assert claude.state.label.objectName() == "claudeTileState"
    assert doubao.state.label.objectName() == "doubaoLinkStatus"
    assert claude.state.text() == "未开启" and _text_colour(claude.state.label) == wall_module.MUTED
    assert doubao.state.text() == "等待豆包打开" and _text_colour(doubao.state.label) == wall_module.MUTED
    assert _text_colour(working.state.label) == wall_module.TEXT


def test_rail_tip_sits_beside_its_icon_with_one_rounded_shape(qtbot):
    rail = SettingsRail()
    button = RailButton("AI 状态灯", "lamps")
    rail.add(button)
    qtbot.addWidget(rail)
    rail.move(200, 200)
    rail.show()
    qtbot.waitExposed(rail)
    assert button.tip() is None

    # 鼠标停在图标的右下角：提示不跟着鼠标走。
    corner = QPoint(button.width() - 2, button.height() - 2)
    button.event(QHelpEvent(QEvent.Type.ToolTip, corner, button.mapToGlobal(corner)))
    tip = button.tip()
    assert tip is not None and tip.isVisible() and tip.text() == "AI 状态灯"
    assert not QToolTip.isVisible()  # 不用系统自带的提示（直角窗口套圆角样式会露出直角底）
    right_middle = button.mapToGlobal(QPoint(button.width(), button.height() // 2))
    assert tip.x() == right_middle.x() + tip.GAP
    assert abs(tip.frameGeometry().center().y() - right_middle.y()) <= 1
    # 只有自己画的那一层圆角：窗口本身透明、没有边框。
    assert tip.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert tip.windowFlags() & Qt.WindowType.FramelessWindowHint
    corner_pixel = tip.grab().toImage().pixelColor(0, 0)
    assert corner_pixel.alpha() == 0

    button.event(QEvent(QEvent.Type.Leave))
    assert not tip.isVisible()


def test_card_dialog_is_dark_and_only_as_big_as_its_content(qtbot):
    card = QWidget()
    box = QVBoxLayout(card)
    title = QLabel("蓝牙名称")
    box.addWidget(title)
    extra = QLabel("保存后的提示")
    extra.hide()
    box.addWidget(extra)
    dialog = CardDialog(None, "蓝牙名称", card)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitExposed(dialog)

    # 深色底配浅色字：系统默认的浅灰底上，卡片里的浅色字看不清。
    background = dialog.grab().toImage().pixelColor(4, 4)
    assert background.lightness() < 60
    assert QColor(_text_colour(title)).lightness() > 180

    # 只装下内容，不是固定的大窗口（统一弹窗外壳：小档 360 宽，标题、四周 24 和左下角圆形关闭）。
    assert dialog.width() == 360
    small = dialog.height()
    assert small < 240
    assert small - dialog.content_height() < 170

    # 内容变高，对话框跟着长；有上限。
    extra.setMinimumHeight(120)
    extra.show()
    qtbot.waitUntil(lambda: dialog.height() >= small + 100)
    extra.setMinimumHeight(2000)
    qtbot.waitUntil(lambda: dialog.height() == wall_module.DIALOG_MAX_HEIGHT)


def test_doubao_tile_asks_for_accessibility_only_when_it_is_missing(qtbot):
    permissions = FakePermissions(access=perm.NOT_TRUSTED)
    link = FakeLink()
    tile = DoubaoTile(link, permissions, poll_ms=60_000)
    qtbot.addWidget(tile)
    assert not tile.access.isHidden() and not tile.allow.isHidden()
    assert tile.note.isHidden()
    assert tile.access.text() == DoubaoTile.NEEDS_ACCESS
    tile.allow.click()
    assert permissions.opened == 1 and tile.watcher.watching

    # 从系统设置回来（应用回到前台）后自动重读，提示自己消失，不用手动刷新。
    permissions.access = perm.TRUSTED
    QGuiApplication.instance().applicationStateChanged.emit(Qt.ApplicationState.ApplicationActive)
    assert tile.access.isHidden() and tile.allow.isHidden() and not tile.note.isHidden()

    # 关掉豆包状态灯时不提权限。
    permissions.access = perm.NOT_TRUSTED
    tile.watcher.recheck()
    tile.enabled.click()
    assert link.enabled is False and tile.state.text() == "未开启"
    assert tile.access.isHidden() and tile.allow.isHidden()

    windows = DoubaoTile(FakeLink(), FakePermissions(mac=False, access=perm.NOT_TRUSTED), poll_ms=60_000)
    qtbot.addWidget(windows)
    assert windows.access.isHidden() and windows.allow.isHidden()


def test_doubao_details_share_the_tiles_watcher_and_have_no_second_switch(qtbot, monkeypatch):
    # 设置 1、3：开关只在方块上；「详情」里没有第二套开关，权限行和方块读同一份结果。
    from PySide6.QtWidgets import QCheckBox

    from controller_config.views.desktop_agent_settings import DoubaoLinkSettings

    shown = []
    monkeypatch.setattr(wall_module, "show_card_dialog", lambda _parent, title, card, **_k: shown.append(card))
    permissions = FakePermissions(access=perm.NOT_TRUSTED)
    tile = DoubaoTile(FakeLink(), permissions, poll_ms=60_000)
    qtbot.addWidget(tile)
    tile._open_details()
    (card,) = shown
    qtbot.addWidget(card)
    assert isinstance(card, DoubaoLinkSettings) and card.watcher is tile.watcher
    assert card.findChildren(QCheckBox) == []
    assert not card.access_row.allow.isHidden()
    permissions.access = perm.TRUSTED
    tile.watcher.recheck()
    assert card.access_row.allow.isHidden() and tile.allow.isHidden()


def _usage(status, *, source="app_server", updated_at=None):
    from controller_config.codex_usage import CodexUsageSnapshot

    return CodexUsageSnapshot(status=status, source=source, updated_at=updated_at)


@pytest.mark.parametrize("micro,detected,tone,text", [
    ({"status_age_ms": 1_500}, ("Codex",), "ok", "工作中 · Codex 刚发来任务状态"),
    ({"status_age_ms": 5 * 60_000}, ("Codex",), "ok", "5 分钟前收到 Codex 的任务状态"),
    ({"status_age_ms": 3 * 3_600_000}, (), "ok", "3 小时前收到 Codex 的任务状态"),
    ({"status_age_ms": None}, (), "off", "没有检测到 Codex 或 ChatGPT"),
    (None, ("ChatGPT",), "wait", "连接设备后显示 Codex 的状态"),
    (None, None, "wait", "连接设备后显示 Codex 的状态"),
    ({"connected": True}, ("Codex",), "ok", "内置支持 · 当前固件不报告 Codex 的状态"),
    ({"status_age_ms": None}, ("Codex",), "wait", "等待 Codex · 设备这次开机后还没收到任务状态"),
])
def test_codex_state_comes_from_the_device_and_this_computer(micro, detected, tone, text):
    assert wall_module.codex_state(micro, detected) == (tone, text)


def test_codex_usage_note_says_whether_the_data_is_fresh():
    from datetime import datetime

    from controller_config.codex_usage import CodexUsageStatus

    moment = datetime(2026, 11, 18, 22, 40).timestamp()
    assert wall_module.codex_usage_note(None) == ""
    assert wall_module.codex_usage_note(_usage(CodexUsageStatus.LOADING)) == "正在更新额度数据…"
    assert wall_module.codex_usage_note(_usage(CodexUsageStatus.ERROR)) == "暂时读不到额度数据"
    assert wall_module.codex_usage_note(_usage(CodexUsageStatus.AVAILABLE, updated_at=moment)) == (
        "额度数据更新于 11-18 22:40")
    assert wall_module.codex_usage_note(
        _usage(CodexUsageStatus.AVAILABLE, source="session_log", updated_at=moment)) == (
        "额度数据更新于 11-18 22:40 · 来自本地日志，可能滞后")


def test_codex_tile_follows_the_device_and_the_usage_data(qtbot):
    from controller_config.codex_usage import CodexUsageStatus

    class Source(QObject):
        changed = Signal()

    source = Source()
    state = {"micro": None, "usage": None}
    tile = CodexStatusTile(codex_micro=lambda: state["micro"], usage=lambda: state["usage"],
                           detect=lambda: ("Codex",), signals=(source.changed,))
    qtbot.addWidget(tile)
    assert tile.state.text() == "连接设备后显示 Codex 的状态"
    assert tile.note.isHidden()  # 没有额度读取时不写这一行

    state["micro"] = {"status_age_ms": 800}
    state["usage"] = _usage(CodexUsageStatus.LOADING)
    source.changed.emit()
    assert tile.state.text() == "工作中 · Codex 刚发来任务状态"
    assert not tile.note.isHidden() and tile.note.text() == "正在更新额度数据…"
    assert "内置支持，无需设置" not in tile.state.text()


def test_lamps_are_numbered_one_two_on_top_and_three_to_six_below():
    # the firmware owner 定的编号（design decision）：固件的 Agent 1～6，上排 1、2，下排 3～6。
    top, bottom = LampLayout.ROWS
    assert [number for _column, number in top] == [1, 2]
    assert [number for _column, number in bottom] == [3, 4, 5, 6]
    assert [column for column, _number in top] == [1, 2]
    assert [column for column, _number in bottom] == [0, 1, 2, 3]


def test_lamps_tile_reminds_only_when_claude_and_doubao_are_both_on(qtbot):
    bridge = FakeBridge()
    link = FakeLink(link_codes.DISABLED)
    tile = LampsTile(bridge, link)
    qtbot.addWidget(tile)
    assert tile.note.text() == LampsTile.RULE and tile.shared.text() == LampsTile.SHARED
    assert not tile.note.isHidden() and tile.shared.isHidden()

    bridge.enabled = True
    bridge.changed.emit()
    assert not tile.note.isHidden() and tile.shared.isHidden()

    link.set_enabled(True)
    assert tile.note.isHidden() and not tile.shared.isHidden()

    bridge.enabled = False
    bridge.changed.emit()
    assert not tile.note.isHidden() and tile.shared.isHidden()


def test_new_wall_copy_is_translated():
    from controller_config.text_catalog import get_text_catalog

    catalog = get_text_catalog()
    for source in (
        "AI 状态灯", "未开启", "已开启", "了解更多 ›", "详情 ›", "去允许", "开始校准",
        "正在显示 {count} 个 Claude 会话", "6 个灯已占满，新会话暂不显示", "状态灯没有生效",
        "等待豆包打开", "工作中，占用 {count} 颗灯", ClaudeStatusTile.USE, DoubaoTile.USE,
        DoubaoTile.NEEDS_ACCESS, "6 颗状态灯", "复制诊断信息", "诊断信息已复制，可以粘贴给客服",
        "工作中 · Codex 刚发来任务状态", "{count} 分钟前收到 Codex 的任务状态",
        "{count} 小时前收到 Codex 的任务状态", "没有检测到 Codex 或 ChatGPT", "连接设备后显示 Codex 的状态",
        "内置支持 · 当前固件不报告 Codex 的状态", "等待 Codex · 设备这次开机后还没收到任务状态",
        "正在更新额度数据…", "暂时读不到额度数据", "额度数据更新于 {time}",
        "额度数据更新于 {time} · 来自本地日志，可能滞后", LampsTile.RULE, LampsTile.SHARED,
        DoubaoTile.NEEDS_FIRMWARE, DoubaoTile.ACTIVE_IDLE, "去更新", "恢复出厂…",
        "用动画演示怎么开机、连接和操作设备。", "开启豆包状态灯",
    ):
        for language in ("en_US", "ja_JP"):
            assert catalog.translate(source, language) != source, (source, language)


def _current(window):
    """主窗口当前显示的那一页（别的页会留在内存里备用，不算）。"""
    layout = window._content_layout
    return layout.itemAt(layout.count() - 1).widget()


def test_settings_page_is_a_rail_of_five_icons_and_a_wall(session):  # noqa: F811
    window, vm, _gateway, _snapshot, _store = session
    window.show()
    for section, title in (("device", "设备与连接"), ("ai", "AI 状态灯"), ("system", "通用"), ("updates", "更新")):
        window._select_settings_section(section)
        assert vm.page == "settings"
        page = _current(window)
        assert page.objectName() == "settingsWorkspace"
        groups = page.findChildren(QPushButton, "settingsGroup")
        assert [button.text() for button in groups] == [label for _key, label, _icon in SECTIONS]
        assert [button.text() for button in groups if button.property("selected") is True] == [title]
        walls = page.findChildren(SettingsWall)
        assert len(walls) == 1
        assert walls[0].findChild(QLabel, "settingsWallTitle").text() == title

    window._select_settings_section("ai")
    page = _current(window)
    assert len(page.findChildren(ClaudeStatusTile)) == 1 and len(page.findChildren(DoubaoTile)) == 1
    assert len(page.findChildren(LampsTile)) == 1

    # 固件更新是「更新」分组里点进去的一页，图标仍然亮在「更新」上。
    window._select_settings_section("updates")
    _current(window).findChild(QPushButton, "openFirmwareUpdates").click()
    assert vm.page == "firmware"
    selected = [button.text() for button in _current(window).findChildren(QPushButton, "settingsGroup")
                if button.property("selected") is True]
    assert selected == ["更新"]
    # 窗口比墙大时方块不变（视觉稿里的"甲"）；改成跟着放大要先由 the firmware owner 定。
    assert wall_module.MAX_UNIT == UNIT


@pytest.mark.parametrize(("answer", "text"), [
    (perm.BT_ALLOWED, "蓝牙：已允许"),
    (perm.BT_NOT_DETERMINED, "蓝牙：还没有授权"),
    (perm.BT_DENIED, "蓝牙：未允许"),
    (perm.BT_RESTRICTED, "蓝牙：被系统限制"),
    (perm.UNKNOWN, "蓝牙：无法读取授权状态"),
])
def test_bluetooth_permission_shows_in_device_info_only_over_bluetooth(session, answer, text):  # noqa: F811
    # the firmware owner 定的（design decision）：这行说的是 Console 用蓝牙连设备的系统权限，只在用蓝牙连接时出现。
    from dataclasses import replace

    window, vm, gateway, snapshot, _store = session
    window._system_permissions = perm.SystemPermissions(
        platform="darwin", accessibility=lambda: perm.TRUSTED, bluetooth=lambda: answer)
    window._select_settings_section("device")
    assert _current(window).findChild(QLabel, "deviceBluetoothPermission") is None  # USB：不出现

    gateway.snapshot_ready.emit(replace(vm.model.snapshot, port_name="ble:test-device"))
    window._select_settings_section("device")
    window._content.render_inputs = None
    window.render(vm.model)
    line = _current(window).findChild(QLabel, "deviceBluetoothPermission")
    assert line is not None and line.text().startswith(text)

    window._system_permissions = perm.SystemPermissions(platform="win32")
    window._content.render_inputs = None
    window.render(vm.model)
    assert _current(window).findChild(QLabel, "deviceBluetoothPermission") is None  # Windows：不出现


def test_rail_sits_right_next_to_the_wall_when_the_window_is_wide(session):  # noqa: F811
    # 窗口拉大：图标栏跟着墙一起居中，紧挨在墙的左边，中间不空出一大截。
    window, _vm, _gateway, _snapshot, _store = session
    window.resize(1600, 960)
    window._select_settings_section("device")
    from PySide6.QtWidgets import QApplication

    QApplication.processEvents()
    page = _current(window)
    scroll = page.findChild(QWidget, "settingsScroll")
    rail = page.findChild(SettingsRail)
    wall = page.findChild(SettingsWall)
    left = lambda widget: widget.mapTo(page, widget.rect().topLeft()).x()
    assert left(wall) - (left(rail) + rail.width()) == wall_module.RAIL_GAP
    # 两样一起居中：左右留白一样多（差不超过 1 像素）。
    blank_left = left(rail)
    blank_right = page.width() - (left(wall) + wall.width())
    assert blank_left > 200 and abs(blank_left - blank_right) <= 1
    # 图标栏对着方块那一片上下居中。
    grid = wall._grid_host
    middle = lambda widget: widget.mapTo(page, widget.rect().center()).y()
    assert abs(middle(rail) - middle(grid)) <= 1
    assert scroll is not None and wall.width() == 4 * UNIT + 3 * GAP


def test_settings_opens_on_the_first_group_unless_an_update_brought_you_here(session):  # noqa: F811
    window, vm, _gateway, _snapshot, _store = session
    assert window._settings_section == "device"
    button = window._nav_buttons["settings"]
    button.click()
    assert vm.page == "settings" and window._settings_section == "device"
    selected = [b.text() for b in _current(window).findChildren(QPushButton, "settingsGroup")
                if b.property("selected") is True]
    assert selected == ["设备与连接"]

    # 在别的分组待过，再从一级导航点进来，还是落在第一组。
    window._select_settings_section("system")
    window._navigate("overview")
    button.click()
    assert window._settings_section == "device"

    # 有固件更新提醒时，落在「更新」。
    window._navigate("overview")
    button.setProperty("firmwareUpdateAvailable", True)
    assert window._settings_landing_section() == "updates"
    button.click()
    assert window._settings_section == "updates"
    button.setProperty("firmwareUpdateAvailable", False)
    assert window._settings_landing_section() == "device"


def test_device_group_copy_matches_what_each_tile_does(session):  # noqa: F811
    window, _vm, _gateway, _snapshot, _store = session
    window._select_settings_section("device")
    page = _current(window)
    reset = page.findChild(QPushButton, "factoryResetDevice")
    assert reset.text() == "恢复出厂…"  # "恢复…"像被截断，对清空设备的动作也太轻
    notes = [label.text() for label in page.findChildren(QLabel)]
    assert "用动画演示怎么开机、连接和操作设备。" in notes
    assert "蓝牙配置请先在系统设置中配对；固件安装需要 USB。" not in notes

    # 三种语言下，按钮上的字都要放得下（1 格的方块里，按钮和图标排在同一行）。
    for language in ("en_US", "ja_JP", "zh_CN"):
        window._language_manager.set_language(language)
        button = _current(window).findChild(QPushButton, "factoryResetDevice")
        needed = button.fontMetrics().horizontalAdvance(button.text()) + 2 * 14 + 2
        assert needed <= button.width(), (language, button.text(), needed, button.width())


def test_doubao_tile_on_the_ai_wall_jumps_to_updates(session):  # noqa: F811
    window, vm, _gateway, _snapshot, _store = session
    window._select_settings_section("ai")
    tile = _current(window).findChild(DoubaoTile)
    tile._go_update()
    assert vm.page == "settings" and window._settings_section == "updates"


def test_ai_wall_codex_tile_shows_the_real_state_not_a_fixed_sentence(session, monkeypatch):  # noqa: F811
    window, vm, _gateway, _snapshot, _store = session
    monkeypatch.setattr(wall_module, "detect_codex", lambda: ("Codex",))
    window.show()
    window._select_settings_section("ai")
    tile = _current(window).findChild(CodexStatusTile)
    assert tile is not None
    micro = window._codex_micro_status()
    assert isinstance(micro, dict)  # the demo device reports codex_micro
    assert (tile.state._mark._tone, tile.state.text()) == wall_module.codex_state(micro, ("Codex",))
    assert window.findChild(QWidget, "codexStatusBuiltIn") is None



def test_doubao_tile_pops_its_mark_when_permission_comes_back(qtbot, monkeypatch):
    """-15 第 5 处 (乙): 缺权限 -> 允许了, the tile cross-fades 短 and the green mark pops once."""
    from controller_config.views.ui import motion
    from controller_config.views.ui.page_motion import CrossFade

    monkeypatch.setattr(motion, "reduces_motion", lambda: False)
    permissions = FakePermissions(access=perm.NOT_TRUSTED)
    tile = DoubaoTile(FakeLink(link_codes.ACTIVE), permissions, poll_ms=60_000)
    qtbot.addWidget(tile)
    tile.show()
    assert not tile.allow.isHidden()
    permissions.access = perm.TRUSTED
    tile.watcher.recheck()
    tile.refresh()
    assert tile.allow.isHidden()
    assert len(tile.findChildren(CrossFade)) == 1
    pop = tile.state._mark._pop
    assert pop is not None and pop.animation.state() == pop.animation.State.Running
    tile.refresh()   # nothing new: no second pop
    assert len(tile.findChildren(CrossFade)) == 1
