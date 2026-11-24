"""The Mode page connect / disconnect animation (Mode页 定稿 v1, 06; 第 4 批)."""
import time

from controller_config.views import mode_motion
from controller_config.views.mode_motion import (
    BAND, LINK_IN_SECONDS, LINK_OUT_SECONDS, SCRAMBLE, SWEEP_SECONDS, Link, lead, revealed_from,
    scramble_frame, sweep_dots,
)
from controller_config.views.mode_page import ModeStage, StageState
from test_session_recovery import session  # noqa: F401


def test_the_sheet_is_half_way_at_0_26_and_gone_by_0_82():
    assert abs(lead(0.26 / SWEEP_SECONDS) - 532 / 2) < 40
    assert lead(0.56 / SWEEP_SECONDS) < 0                  # 0.56: swept to the top
    assert sweep_dots(1.0) == [] and sweep_dots(0.0) == []
    assert revealed_from(1.0) < 0


def test_small_dots_at_the_top_edge_growing_downwards_then_melting():
    dots = sweep_dots(0.3)
    middle = [dot for dot in dots if abs(dot[0] - 480) < 7]
    middle.sort(key=lambda dot: dot[1])
    radii = [dot[2] for dot in middle]
    biggest = radii.index(max(radii))
    assert radii[0] < radii[biggest] and radii[-1] < radii[biggest]
    assert max(radii) * 2 < 13                              # never wider than the floor pitch
    front = min(dot[1] for dot in middle)
    assert middle[biggest][1] - front > BAND * 0.8          # the big end is at the bottom of the band


def test_the_word_scrambles_then_settles_letter_by_letter():
    assert all(letter == " " for letter, _light, _flash in scramble_frame("NORMAL", SCRAMBLE[0] - 0.01))
    middle = "".join(letter for letter, *_ in scramble_frame("NORMAL", 0.9))
    assert middle != "NORMAL"
    assert "".join(letter for letter, *_ in scramble_frame("NORMAL", SCRAMBLE[1])) == "NORMAL"
    settled = [sum(letter == final for (letter, *_), final in zip(scramble_frame("NORMAL", t), "NORMAL"))
               for t in (1.0, 1.3, 1.6, 1.86)]
    assert settled == sorted(settled) and settled[-1] == 6


def test_going_out_sweeps_backwards_then_dims():
    started = time.monotonic()
    link = Link("out", started)
    assert link.sweep_progress(started) == 1.0 and link.dim(started) == 0
    assert link.sweep_progress(started + mode_motion.OUT_SWEEP_SECONDS + 1e-6) == 0
    assert link.dim(started + LINK_OUT_SECONDS + 1e-6) == 1 and link.done(started + LINK_OUT_SECONDS + 1e-6)


def test_stage_runs_the_animation_and_stops_by_itself(qtbot, monkeypatch):
    monkeypatch.setattr("controller_config.views.mode_page.system_reduces_motion", lambda: False)
    stage = ModeStage()
    qtbot.addWidget(stage)
    stage.resize(960, 532)
    stage.set_state(StageState(connected=True))
    stage.show()
    stage.play_link(Link("in", time.monotonic() - 0.3))
    assert stage.link() is not None and stage.link_overlay.isVisible()
    qtbot.waitUntil(lambda: stage.link_overlay.frames > 2, timeout=2000)
    assert stage.link_overlay.dots_drawn > 0
    stage.play_link(Link("in", time.monotonic() - LINK_IN_SECONDS))   # already over
    assert stage.link() is None and not stage.link_overlay.isVisible()


def test_reduced_motion_jumps_straight_to_the_end(qtbot, monkeypatch):
    monkeypatch.setattr("controller_config.views.mode_page.system_reduces_motion", lambda: True)
    stage = ModeStage()
    qtbot.addWidget(stage)
    stage.play_link(Link("in", time.monotonic()))
    assert stage.link() is None


def test_main_window_starts_it_when_the_connection_changes(session, monkeypatch):  # noqa: F811
    window, vm, *_ = session
    vm.navigate("overview")
    window._mode_seen_connected = False            # it was not connected a moment ago
    link = window._mode_link_for(True)
    assert link is not None and link.kind == "in"
    assert window._mode_link_for(True) is link     # a rebuilt page carries on with the same one
    assert window._mode_link_for(False).kind == "out"
    window._mode_link = None
    window._mode_seen_connected = True
    assert window._mode_link_for(True) is None     # nothing changed: nothing plays


# -- the knob and the joystick, zoomed (11, 12) ------------------------------------------------

from controller_config.views.mode_motion import ZOOM_IN_SECONDS, ZOOM_OUT_SECONDS, Zoom, ripple_dots  # noqa: E402


def test_zoom_runs_0_72_in_and_0_40_back_on_the_same_timeline():
    started = time.monotonic()
    going_in, going_out = Zoom("in", started), Zoom("out", started)
    assert abs(going_in.timeline(started + 0.36) - 0.36) < 1e-6
    assert going_in.done(started + ZOOM_IN_SECONDS + 1e-6)
    assert going_out.timeline(started) == ZOOM_IN_SECONDS        # back starts from the end ...
    assert going_out.timeline(started + ZOOM_OUT_SECONDS + 1e-6) == 0   # ... and reaches the start in 0.4 s


def test_the_ripple_is_one_ring_that_spreads_once():
    from PySide6.QtCore import QPointF
    origin = QPointF(300, 380)
    early, late = ripple_dots(origin, 0.15), ripple_dots(origin, 0.5)
    assert early and late
    distance = lambda dots: sum(((x - 300) ** 2 + (y - 380) ** 2) ** 0.5 for x, y, _ in dots) / len(dots)
    assert distance(late) > distance(early)
    assert ripple_dots(origin, 0.75) == []                       # 0.70: gone, only once


def test_the_ripple_lights_the_floors_own_dots_down_to_the_floors_bottom():
    """the designer 11-22: 摇杆脉冲圆心不在摇杆正中心 — the ring's dots were 1 unit off the
    floor's rows, and stopped at the page's bottom while the floor went on to the window's."""
    from PySide6.QtCore import QPointF
    from controller_config.style import tokens
    from controller_config.views.mode_motion import BASE_H, BASE_W
    pitch = tokens.MODE_FLOOR_PITCH
    origin = QPointF(555, 335)
    rows = {round(y, 2) for _x, y, _ in ripple_dots(origin, 0.3, bottom=640)}
    floor_rows = set()
    y = BASE_H - pitch / 2
    while y < 640:
        floor_rows.add(round(y % pitch, 2)); y += pitch
    assert {round(y % pitch, 2) for y in rows} == floor_rows        # the same grid
    assert max(rows) > BASE_H                                       # past the page's edge
    assert max(y for _x, y, _ in ripple_dots(origin, 0.3)) < BASE_H  # the default stays the page
    columns = {round(x % pitch, 2) for x, _y, _ in ripple_dots(origin, 0.3)}
    assert columns == {round((BASE_W / 2) % pitch, 2)}


def _zoom_stage(qtbot, monkeypatch):
    from controller_config.views.mode_page import KnobRow, StageText
    monkeypatch.setattr("controller_config.views.mode_page.system_reduces_motion", lambda: False)
    stage = ModeStage()
    qtbot.addWidget(stage)
    stage.resize(960, 532)
    stage.set_state(StageState(connected=True, text=StageText(
        knob=(KnobRow("↺", "逆时针", "音量 −"), KnobRow("↻", "顺时针", "音量 +"), KnobRow("●", "按下", "静音")),
        joystick={"up": "↑", "down": "↓", "left": "←", "right": "→", "press": "回车"})))
    stage.show()
    return stage


def test_zooming_hides_the_device_widget_and_offers_the_parts(qtbot, monkeypatch):
    from PySide6.QtCore import Qt
    stage = _zoom_stage(qtbot, monkeypatch)
    chosen = []
    stage.zoom_part_chosen.connect(chosen.append)
    stage.set_zoom("encoder", "encoder.ccw", animate=False)
    assert stage.zoom_timeline() == ZOOM_IN_SECONDS and not stage.device.isVisible()
    parts = stage.zoom_parts()
    assert set(parts) == {"encoder.ccw", "encoder.press", "encoder.cw"}
    assert parts["encoder.ccw"].center().x() < parts["encoder.press"].center().x() < parts["encoder.cw"].center().x()
    qtbot.mouseClick(stage, Qt.MouseButton.LeftButton, pos=parts["encoder.cw"].center().toPoint())
    assert chosen == ["encoder.cw"]
    stage.grab()                                               # draws without error
    stage.set_zoom("joystick", "joystick.up", animate=False)
    assert set(stage.zoom_parts()) == {f"joystick.{d}" for d in ("up", "down", "left", "right", "press")}
    stage.grab()
    stage.set_zoom(None, animate=False)
    assert stage.zoom_target() is None and stage.device.isVisible() and stage.zoom_parts() == {}


def test_zoom_animates_in_then_back_out_by_itself(qtbot, monkeypatch):
    stage = _zoom_stage(qtbot, monkeypatch)
    stage.set_zoom("joystick", "joystick.press")
    assert 0 <= stage.zoom_timeline() < ZOOM_IN_SECONDS
    assert stage.zoom_parts() == {}                            # nothing to click before the words are out
    qtbot.waitUntil(lambda: stage.zoom_timeline() >= ZOOM_IN_SECONDS and stage._zoom is None, timeout=3000)
    stage.set_zoom(None)
    assert stage.zoom_target() == "joystick"                   # still drawn while it goes back
    qtbot.waitUntil(lambda: stage.zoom_target() is None, timeout=3000)
    assert stage.device.isVisible()


def test_beside_the_zoomed_part_goes_back_and_the_bar_says_how(qtbot, monkeypatch):
    from PySide6.QtCore import QPoint, Qt
    from controller_config.views.mode_page import BarState
    stage = _zoom_stage(qtbot, monkeypatch)
    stage.set_zoom("encoder", "encoder.press", animate=False)
    back = []
    stage.blank_clicked.connect(lambda: back.append(True))
    qtbot.mouseClick(stage, Qt.MouseButton.LeftButton, pos=QPoint(900, 40))
    assert back == [True]
    stage.bar.set_state(BarState(profile_name="Default", zoomed=True))
    assert stage.bar.back.isVisible() and not stage.bar.profile.isVisible() and not stage.bar.ble.isVisible()
    stage.bar.set_state(BarState(profile_name="Default", zoomed=True, editing_key=True))
    assert not stage.bar.back.isVisible()                      # while a part has changes: × and 保存 as usual


def test_main_window_zooms_on_the_knob_and_a_part_click_switches_the_card(session, monkeypatch):  # noqa: F811
    from PySide6.QtWidgets import QComboBox, QLabel
    monkeypatch.setattr("controller_config.views.mode_page.system_reduces_motion", lambda: True)
    window, vm, *_ = session
    vm.navigate("overview")
    window._show_mode_screen(0)
    stage = next(stage for stage in window.findChildren(ModeStage) if stage.isVisible())
    stage.device.choose("encoder")
    assert stage.zoom_target() == "encoder"
    assert stage.bar.back.isVisible()
    card = stage.card()
    assert card.findChild(QLabel, "inspectorTitle").text() == "旋钮 · 逆时针"
    assert not card.findChild(QComboBox, "triggerSelector").isVisibleTo(card)   # chosen on the knob now
    stage.zoom_part_chosen.emit("encoder.cw")
    assert window._selected_control_id == "encoder.cw"
    assert stage.zoom_sub() == "encoder.cw"
    assert stage.card().findChild(QLabel, "inspectorTitle").text() == "旋钮 · 顺时针"
    stage.bar.back.click()
    assert window._selected_control_id is None and stage.zoom_target() is None


def test_floor_zoom_and_ripple_stay_on_the_part_centre(qtbot, monkeypatch):
    """the designer 11-20 真屏验收 第 17 条: the floor's zoom and the ring of dots are centred on
    the knob / joystick wherever the camera has moved it, not on where it was."""
    from PySide6.QtCore import QPointF
    from controller_config.views.mode_page import floor_zoom
    stage = _zoom_stage(qtbot, monkeypatch)
    for part in ("encoder", "joystick"):
        stage.set_zoom(part, animate=False)
        stage._zoom = mode_motion.Zoom("in", time.monotonic() - 0.3)   # mid push-in
        camera, now, _grow, _radius = stage._zoom_frame()
        origin = stage.part_page(part)[0]
        assert 0 < camera < 1 and (now - origin).manhattanLength() > 50   # the part has moved
        mapped = floor_zoom(origin, now, camera).map(origin)
        assert (mapped - now).manhattanLength() < 0.01
        seen = []
        monkeypatch.setattr(type(stage), "_paint_ripple",
                            lambda self, painter: seen.append(painter.transform().map(origin)))
        stage.grab()
        assert seen   # the ring is drawn under the floor's zoom
        stage.set_zoom(None, animate=False)
        monkeypatch.undo()
        monkeypatch.setattr("controller_config.views.mode_page.system_reduces_motion", lambda: False)


def test_the_sheet_crosses_the_whole_window_not_just_the_page():
    """design decision: 连上、断开的点阵扫描铺满整个窗口，连顶条一起扫过."""
    from PySide6.QtCore import QRectF
    window = QRectF(-72, -85, 1104, 670)          # 1280×800 in page units
    assert lead(0.0, window) == window.bottom()
    assert revealed_from(1.0, window) < window.top()
    dots = sweep_dots(0.5, window)
    assert min(x for x, *_ in dots) < 0 and max(x for x, *_ in dots) > 960
    assert min(y for _x, y, *_ in sweep_dots(0.6, window)) < 0      # over the top bar


def test_going_out_keeps_the_device_as_it_was_until_the_sheet_has_passed(qtbot, monkeypatch):
    """design decision / -06 第 3 件: 断开时设备等倒扫扫完再变灰."""
    monkeypatch.setattr("controller_config.views.mode_page.system_reduces_motion", lambda: False)
    stage = ModeStage()
    qtbot.addWidget(stage)
    stage.resize(960, 532)
    stage.show()
    stage.set_state(StageState(connected=True))
    stage.set_state(StageState(connected=False))
    stage.play_link(Link("out", time.monotonic()))
    assert stage.before_picture() is not None and not stage.before_picture().isNull()
    stage.play_link(None)
    assert stage.before_picture() is None
