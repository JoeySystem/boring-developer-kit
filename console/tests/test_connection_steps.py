"""连接中分三步 on the Mode page (design decision, 单 design ticket 答复 11-22).

连接设备… → 确认是你的 BORING… → 读取设置…, a 36 ring that runs on estimates and
never goes back, "要读配置" known right after HELLO, "比平时慢" after 11 / 15 s,
"已连接" → 0.6 s → "已和设备同步", and nothing at all when it is done within a second.
"""
from __future__ import annotations

import time

import pytest
from PySide6.QtWidgets import QApplication

from controller_config import connection_steps as cs
from controller_config.config_cache import DeviceConfigCache
from controller_config.connection_steps import ConnectionSteps
from controller_config.models import AppState
from controller_config.views.mode_page import ModeBottomBar, ModeStage
from test_config_cache import FULL_SEQUENCE, _frame, _Handshake
from test_session_recovery import session  # noqa: F401

TOTAL_CACHED = cs.CHANNEL_S + cs.IDENTITY_S + cs.STATUS_S + cs.CONFIG_CACHED_S
TOTAL_READING = cs.CHANNEL_S + cs.IDENTITY_S + cs.STATUS_S + cs.CONFIG_READ_S


def _walk(steps: ConnectionSteps, *, reading: bool, at: float = 0.0) -> None:
    """A Bluetooth handshake with SH-005's typical times (seconds from `at`)."""
    steps.step("connect", "bluetooth", at)
    steps.step("channel_open", "", at + 1.0)
    steps.step("send", "HELLO", at + 1.0)
    steps.step("HELLO", "180 bytes", at + 1.6)
    if reading:
        steps.step("config_read", "expected", at + 1.6)
    steps.step("send", "GET_STATUS", at + 4.0)
    steps.step("GET_STATUS", "900 bytes", at + 6.5)


# ---- the steps on their own -------------------------------------------------------------


def test_done_within_a_second_shows_no_steps_at_all():
    steps = ConnectionSteps()
    steps.step("connect", "usb", 0.0)
    assert steps.view(0.5) is None
    steps.finish(0.8)
    assert all(steps.view(t) is None for t in (0.9, 1.0, 1.5, 3.0))


def test_three_steps_follow_the_real_stages():
    steps = ConnectionSteps()
    steps.step("connect", "bluetooth", 0.0)
    assert steps.view(0.5) is None                                   # first second: nothing yet
    assert steps.view(1.1).title == "连接设备…"
    steps.step("channel_open", "", 1.2)
    assert steps.view(1.3).title == "确认是你的 BORING…"
    for name in ("HELLO", "CAPABILITIES", "AUTH_GET_CERTIFICATE", "AUTH_CHALLENGE"):
        steps.step(name, "", 2.0)
        assert steps.view(2.0).title == "确认是你的 BORING…"
    steps.step("send", "GET_STATUS", 4.0)
    assert steps.view(4.1).title == "读取设置…"
    steps.step("GET_STATUS", "", 6.0)
    steps.step("GET_CONFIG", "skipped: cached config matches HELLO digest", 6.0)
    view = steps.view(6.1)
    assert view.title == "读取设置…" and view.hint == "" and view.ring == "running"


def test_ring_runs_on_estimates_stops_at_90_percent_and_never_goes_back():
    steps = ConnectionSteps()
    steps.step("connect", "bluetooth", 0.0)
    # The channel takes long: the ring stops at 90 % of its 1 s estimate.
    held = cs.HOLD_SHARE * cs.CHANNEL_S / TOTAL_CACHED * 100
    assert steps.view(1.5).value == pytest.approx(held, abs=0.1)
    assert steps.view(5.0).value == pytest.approx(held, abs=0.1)
    steps.step("channel_open", "", 5.0)
    steps.step("HELLO", "", 5.5)
    before = steps.view(5.5).value
    # HELLO says the configuration will be read: the estimate grows, the ring waits, never back.
    steps.step("config_read", "expected", 5.5)
    values = [steps.view(5.5 + i / 10).value for i in range(40)]
    assert values[0] == pytest.approx(before) and values == sorted(values)
    steps.finish(12.0)
    assert steps.view(12.0).value == 100 and steps.view(12.0).ring == "done"


def test_reading_hint_appears_right_after_hello_and_the_slow_hint_after_11_or_15_s():
    steps = ConnectionSteps()
    steps.step("connect", "bluetooth", 0.0)
    steps.step("channel_open", "", 1.0)
    assert steps.view(1.5).hint == ""
    steps.step("HELLO", "180 bytes", 1.6)
    steps.step("config_read", "expected", 1.6)
    assert steps.view(1.7).hint == "这次要把设置完整读一遍，多等几秒"
    assert steps.view(14.9).hint == "这次要把设置完整读一遍，多等几秒"
    assert steps.view(15.0).hint == "比平时慢，把 BORING 靠近电脑试试"
    quick = ConnectionSteps()
    _walk(quick, reading=False)
    assert quick.view(10.9).hint == ""
    assert quick.view(11.0).hint == "比平时慢，把 BORING 靠近电脑试试"


def test_a_late_read_says_so_when_get_config_is_sent():
    """HELLO thought the copy here would do (GET_STATUS then disagreed): the hint comes
    with the read, not before."""
    steps = ConnectionSteps()
    _walk(steps, reading=False)
    assert steps.view(6.6).hint == ""
    steps.step("send", "GET_CONFIG", 6.6)
    steps.step("config_read", "started", 6.6)
    assert steps.view(6.7).hint == "这次要把设置完整读一遍，多等几秒"


def test_done_says_connected_then_in_sync_then_hands_back_to_the_bar():
    steps = ConnectionSteps()
    _walk(steps, reading=False)
    steps.finish(7.0)
    view = steps.view(7.0)
    assert (view.title, view.ring, view.value, view.hint) == ("已连接", "done", 100, "")
    assert steps.view(7.0 + cs.SYNCED_AFTER_S + 1e-6).title == "已和设备同步"
    assert steps.needs_frames(7.5)
    assert steps.view(7.0 + cs.DONE_HOLD_S + 1e-6) is None and not steps.needs_frames(7.0 + cs.DONE_HOLD_S + 1e-6)


def test_failed_turns_the_lit_dots_orange_where_it_stopped():
    steps = ConnectionSteps()
    _walk(steps, reading=True)
    steps.fail(8.0)
    view = steps.view(8.0)
    assert view.ring == "failed" and steps.view(9.5).value == view.value   # stops where it failed
    assert steps.view(8.0 + cs.FAILED_HOLD_S + 1e-6) is None


def test_a_quick_retry_carries_on_and_a_late_connect_starts_over():
    steps = ConnectionSteps()
    _walk(steps, reading=False)
    shown = steps.view(6.6).value
    steps.step("connect", "bluetooth", 7.0)       # authentication interrupted, reconnecting at once
    assert steps.started == 0.0 and steps.view(7.1).value >= shown
    assert steps.view(7.1).title == "连接设备…"
    later = ConnectionSteps()
    _walk(later, reading=False)
    later.step("connect", "bluetooth", 60.0)      # left hanging, then a new connection
    assert later.started == 60.0 and later.view(60.5) is None


# ---- the handshake: what HELLO already says -------------------------------------------


def _after_hello(handshake: _Handshake) -> None:
    handshake.session.start()
    command, request_id = handshake.sent[-1]
    assert command.name == "HELLO"
    handshake.session.accept(_frame(request_id, handshake.hello))


def test_hello_already_says_whether_the_configuration_will_be_read(contract, load_fixture, tmp_path):
    cache = DeviceConfigCache(tmp_path)
    fresh = _Handshake(contract, load_fixture, cache)
    _after_hello(fresh)
    assert fresh.session.expects_config_read()           # first time on this computer
    saved = _Handshake(contract, load_fixture, cache)
    assert cache.store(saved.serial, saved.config_result)
    _after_hello(saved)
    assert not saved.session.expects_config_read()       # the same configuration is saved here
    assert _Handshake(contract, load_fixture, cache).run() == FULL_SEQUENCE[:-1]   # ... and it is not read
    changed = _Handshake(contract, load_fixture, cache)
    changed.hello["result"]["config"] = {"generation": 99, "digest": "c" * 64}
    _after_hello(changed)
    assert changed.session.expects_config_read()         # changed since: read again


def test_without_a_cache_every_connection_reads(contract, load_fixture):
    handshake = _Handshake(contract, load_fixture, None)
    _after_hello(handshake)
    assert handshake.session.expects_config_read()


def test_the_session_names_each_command_as_it_is_sent(contract, load_fixture, tmp_path):
    handshake = _Handshake(contract, load_fixture, DeviceConfigCache(tmp_path))
    started = []
    handshake.session._command_started = started.append
    handshake.run()
    assert started == FULL_SEQUENCE


def test_worker_reports_steps_and_the_expected_read_after_hello(qtbot, contract, load_fixture, tmp_path):
    from controller_config.transport.qt_serial import SerialWorker

    handshake = _Handshake(contract, load_fixture, DeviceConfigCache(tmp_path), port_name="/dev/cu.test")
    worker = SerialWorker(contract, handshake.authenticator, config_cache=DeviceConfigCache(tmp_path))
    sent, steps = [], []
    worker.connect_step.connect(lambda step, detail: steps.append((step, detail)))
    worker._send_command = lambda command, request_id: sent.append((command, request_id))
    worker._serial = object()
    worker._start_bootstrap("/dev/cu.test", handshake.serial)
    assert steps == [("channel_open", ""), ("send", "HELLO")]
    command, request_id = sent[-1]
    worker._session.accept(_frame(request_id, handshake.hello))
    assert steps[2][0] == "HELLO" and steps[3:] == [("config_read", "expected"), ("send", "CAPABILITIES")]
    worker._serial = None
    worker.close()


# ---- the Mode page ----------------------------------------------------------------------


def _bar(window) -> ModeBottomBar:
    stages = [stage for stage in window.findChildren(ModeStage) if stage.isVisible()]
    assert stages, "the Mode page is not showing"
    return stages[0].bar


def test_reconnecting_keeps_the_mode_page_grey_and_shows_the_steps_in_its_bar(session, qtbot):  # noqa: F811
    window, vm, gateway, snapshot, _ = session
    gateway.disconnected.emit("asleep")
    qtbot.waitUntil(lambda: vm.model.state is AppState.DISCONNECTED)
    vm.connect_candidate("ble:AA")
    assert vm.model.state is AppState.CONNECTING
    QApplication.processEvents()
    # No 3D "连接中" page: the Mode page stays, greyed, the Bluetooth button round.
    assert window._connection_progress is None or not window._connection_progress.isVisible()
    bar = _bar(window)
    assert bar.state().right == "connecting" and bar.state().dimmed and not bar.state().ble_button
    now = time.monotonic()
    vm.connection_steps.step("connect", "bluetooth", now - 2.0)   # found two seconds ago
    vm.connection_steps.step("channel_open", "", now - 1.0)
    vm._on_connect_step("HELLO", "180 bytes")
    vm._on_connect_step("config_read", "expected")
    qtbot.waitUntil(lambda: not bar.steps.isHidden(), timeout=1000)
    assert bar.status.isHidden() and bar.status_text.isHidden()
    assert bar.steps.title.text() == "确认是你的 BORING…"
    assert bar.steps.hint.text() == "这次要把设置完整读一遍，多等几秒"
    assert bar.steps.ring.ring_size() == 36 and bar.steps.ring.dot_count() == 16
    assert bar.steps.ring.number_text() == ""                    # no number in the middle
    vm._on_connect_step("send", "GET_STATUS")
    qtbot.waitUntil(lambda: bar.steps.title.text() == "读取设置…", timeout=1000)
    gateway.snapshot_ready.emit(snapshot)
    qtbot.waitUntil(lambda: vm.model.state is AppState.READY)
    bar = _bar(window)
    qtbot.waitUntil(lambda: not bar.steps.isHidden() and bar.steps.title.text() == "已连接", timeout=1000)
    assert bar.steps.ring.state() == "done" and bar.steps.ring.lit_dots() == 16
    qtbot.waitUntil(lambda: bar.steps.title.text() == "已和设备同步", timeout=2000)
    qtbot.waitUntil(lambda: bar.steps.isHidden(), timeout=3000)   # back to the bar's own cell
    assert bar.status.isVisible()


def test_a_failed_connection_turns_the_ring_orange(session, qtbot):  # noqa: F811
    window, vm, gateway, *_ = session
    gateway.disconnected.emit("asleep")
    qtbot.waitUntil(lambda: vm.model.state is AppState.DISCONNECTED)
    vm.connect_candidate("ble:AA")
    vm.connection_steps.step("connect", "bluetooth", time.monotonic() - 3.0)
    vm.connection_steps_changed.emit()
    from controller_config.protocol.bootstrap import BootstrapKind
    gateway.failure.emit(BootstrapKind.READ_FAILED, "读取设备超时", "GET_STATUS 未在超时前返回")
    qtbot.waitUntil(lambda: vm.model.state is AppState.READ_FAILED)
    bar = _bar(window)
    qtbot.waitUntil(lambda: not bar.steps.isHidden(), timeout=1000)
    assert bar.steps.ring.state() == "failed"


def test_a_quick_connection_never_shows_the_steps(session, qtbot):  # noqa: F811
    window, vm, gateway, snapshot, _ = session
    gateway.disconnected.emit("asleep")
    qtbot.waitUntil(lambda: vm.model.state is AppState.DISCONNECTED)
    vm.connect_candidate("ble:AA")
    vm._on_connect_step("connect", "usb")
    gateway.snapshot_ready.emit(snapshot)
    qtbot.waitUntil(lambda: vm.model.state is AppState.READY)
    bar = _bar(window)
    for _ in range(5):
        QApplication.processEvents()
        assert bar.steps.isHidden()
