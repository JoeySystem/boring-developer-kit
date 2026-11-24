"""全部方案: press a screen card and drag it to a new place (design decision, 单 design ticket).

The order lives on the device: `profiles` is an ordered list, every screen keeps
its `id`, and `active_profile` / "switch to profile" actions point at ids. The
knob double-click walks the list in order, so a new order is just a new list,
written and read back like any other change.
"""
from __future__ import annotations

import copy
import hashlib
from dataclasses import replace

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QMouseEvent
from PySide6.QtWidgets import QApplication

from controller_config.drafts import LocalDraft, reorder_visible
from controller_config.protocol.framing import canonical_json_bytes
from controller_config.style import tokens
from controller_config.transport.demo import _power_v2_snapshot
from controller_config.viewmodels.main import MainViewModel
from controller_config.views.mode_page import ModeStage
from controller_config.views.ui import motion
from test_session_recovery import session  # noqa: F401
from test_write_transaction import FakeWriteGateway, _ack, _active_status

NAMES = {0: "Default", 1: "Two", 2: "Three"}


def _three_screens(contract, *, active: int = 1):
    """The demo device with three screens (ids 0, 1, 2), the second one in use."""
    snapshot = _power_v2_snapshot(contract, read_only=False)
    config = copy.deepcopy(snapshot.config)
    base = config["profiles"][0]
    for profile_id in (1, 2):
        profile = copy.deepcopy(base)
        profile["id"], profile["name"] = profile_id, NAMES[profile_id]
        config["profiles"].append(profile)
    config["active_profile"] = active
    digest = hashlib.sha256(canonical_json_bytes(config)).hexdigest()
    generation = snapshot.config_result["generation"] + 1   # a newer read than the demo's own
    return replace(
        snapshot,
        config_result={**snapshot.config_result, "config": config, "digest": digest, "generation": generation},
        hello_config={**snapshot.hello_config, "digest": digest},
        status={**snapshot.status, "operating_mode": "normal",
                "active": {"generation": generation, "digest": digest}},
    )


def _ids(profiles) -> list[int]:
    return [profile["id"] for profile in profiles]


def _write_and_read_back(view_model, gateway) -> dict:
    """保存到设备: validate, set, wait for activation, read back. Returns what was written."""
    snapshot = view_model.model.snapshot
    view_model.prepare_device_write()
    assert gateway.commands[-1].name == "VALIDATE_CONFIG"
    gateway.command_completed.emit("VALIDATE_CONFIG", _ack("VALIDATE_CONFIG"))
    view_model.confirm_device_write()
    set_command = gateway.commands[-1]
    assert set_command.name == "SET_CONFIG"
    written = copy.deepcopy(set_command.payload["config"])
    digest = set_command.payload["digest"]
    generation = snapshot.config_result["generation"] + 1
    gateway.command_completed.emit("SET_CONFIG", _ack("SET_CONFIG"))
    gateway.status_updated.emit(_active_status(snapshot, digest, generation))
    assert gateway.commands[-1].name == "GET_CONFIG"
    gateway.command_completed.emit("GET_CONFIG", _ack(
        "GET_CONFIG", {"generation": generation, "digest": digest, "config": written}))
    return written


# -- the draft ------------------------------------------------------------------------

def test_a_new_order_is_one_change_and_keeps_ids_and_the_screen_in_use(contract):
    draft = LocalDraft.from_snapshot(_three_screens(contract), contract)
    assert draft.reorder_profiles([2, 0, 1]) is True
    assert _ids(draft.profiles) == [2, 0, 1]
    assert [p["name"] for p in draft.profiles] == ["Three", "Default", "Two"]
    assert draft.config["active_profile"] == 1
    assert [change.path for change in draft.changes] == ["config.profiles.order"]
    assert draft.validate(contract) == ()
    # A moved screen that is also renamed: still compared with itself, so two changes.
    draft.rename_profile(0, "Renamed")
    assert [change.path for change in draft.changes] == ["config.profiles.order", "config.profiles[1].name"]
    draft.rename_profile(0, "Default")
    assert draft.reorder_profiles([0, 1, 2]) is True
    assert not draft.is_dirty   # back where it was: nothing left to save


def test_an_order_must_name_every_screen_once(contract):
    draft = LocalDraft.from_snapshot(_three_screens(contract), contract)
    assert draft.reorder_profiles([0, 1, 2]) is False
    for bad in ([0, 1], [0, 1, 1], [0, 1, 2, 3], [0, 1, 5]):
        with pytest.raises(ValueError):
            draft.reorder_profiles(bad)
    assert _ids(draft.profiles) == [0, 1, 2]


def test_hidden_screens_keep_their_places_when_the_shown_ones_move():
    # The other platform's screens are not shown in 全部方案; they stay where they are.
    assert reorder_visible([0, 5, 1, 2], [2, 0, 1]) == [2, 5, 0, 1]
    assert reorder_visible([0, 1, 2], [1, 0, 2]) == [1, 0, 2]
    with pytest.raises(ValueError):
        reorder_visible([0, 1, 2], [0, 0, 1])


# -- the view model: saved like any other change -------------------------------------

def test_reorder_is_written_and_read_back_and_discard_undoes_it(contract):
    gateway = FakeWriteGateway()
    view_model = MainViewModel(gateway, contract)
    gateway.snapshot_ready.emit(_three_screens(contract))
    assert not view_model.draft.is_dirty

    assert view_model.reorder_profiles([1, 2, 0]) is True
    assert len(view_model.draft.changes) == 1
    view_model.discard_draft()   # 放弃全部改动…
    assert _ids(view_model.draft.profiles) == [0, 1, 2] and not view_model.draft.is_dirty

    assert view_model.reorder_profiles([0, 1, 2]) is False   # the same order is not a change
    view_model.reorder_profiles([2, 0, 1])
    written = _write_and_read_back(view_model, gateway)
    assert _ids(written["profiles"]) == [2, 0, 1]
    assert written["active_profile"] == 1
    assert {p["id"]: p["name"] for p in written["profiles"]} == NAMES
    before = _three_screens(contract).config
    for profile in written["profiles"]:   # nothing inside a screen changed
        assert profile == next(p for p in before["profiles"] if p["id"] == profile["id"])
    assert _ids(view_model.model.snapshot.config["profiles"]) == [2, 0, 1]
    assert not view_model.draft.is_dirty


# -- the page: press and drag ----------------------------------------------------------

@pytest.fixture
def three_screen_session(session, contract):  # noqa: F811
    window, vm, gateway, *_ = session
    gateway.snapshot_ready.emit(_three_screens(contract))
    window._select_configuration_mode("normal")
    vm.navigate("overview")
    window._mode_show_overview(True)
    assert [p["id"] for p in vm.draft.profiles] == [0, 1, 2]
    return window, vm, gateway


def _overview(window):
    stage = next(s for s in window._content.findChildren(ModeStage) if s.isVisible())
    assert stage.overview_shown()
    return stage


def _mouse(widget, kind, pos: QPoint, buttons=Qt.MouseButton.LeftButton):
    button = Qt.MouseButton.LeftButton if kind != QEvent.Type.MouseMove else Qt.MouseButton.NoButton
    local = QPointF(pos)
    event = QMouseEvent(kind, local, QPointF(widget.mapToGlobal(pos)), button, buttons,
                        Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, event)


def _drag(card, dx: int, *, steps: int = 8, release: bool = True) -> None:
    start = card.rect().center()
    _mouse(card, QEvent.Type.MouseButtonPress, start)
    for step in range(1, steps + 1):
        _mouse(card, QEvent.Type.MouseMove, start + QPoint(round(dx * step / steps), 0))
    if release:
        _mouse(card, QEvent.Type.MouseButtonRelease, start + QPoint(dx, 0), Qt.MouseButton.NoButton)


def _keys(overview):
    return [card.screen.key if card.screen is not None else "new" for card in overview.cards]


@pytest.mark.parametrize("reduced", [False, True])
def test_dragging_a_card_reorders_the_screens_and_counts_one_unsaved_change(
        three_screen_session, monkeypatch, qtbot, reduced):
    monkeypatch.setattr(motion, "reduces_motion", lambda: reduced)
    window, vm, _gateway = three_screen_session
    stage = _overview(window)
    overview = stage.overview
    assert _keys(overview) == [("normal", 0), ("normal", 1), ("normal", 2), ("codex", None), "new"]
    assert stage.bar.state().count == 0
    first = overview.cards[0]
    pitch = overview.cards[1].x() - first.x()
    _drag(first, pitch * 2, release=False)
    assert overview.dragging(first)
    ghost = overview._drag.ghost
    lifted = overview._drag.card.graphicsEffect()
    assert lifted is not None and lifted.opacity() == 0.0   # its place is left empty
    others = overview._drag.others
    if reduced:
        # No animation: picked up and moved out of the way at once.
        assert ghost.lift == 1.0 and ghost.scale() == pytest.approx(tokens.OVERVIEW_LIFT_SCALE)
    else:
        assert overview._lift_animation.duration() == tokens.OVERVIEW_LIFT_MS
        assert all(a.duration() == tokens.OVERVIEW_SETTLE_MS for a in overview._slides.values())
        qtbot.waitUntil(lambda: ghost.lift == 1.0 and not any(
            a.state() == a.State.Running for a in overview._slides.values()), timeout=2000)
        assert ghost.scale() == pytest.approx(tokens.OVERVIEW_LIFT_SCALE)
    # No shadow: picked up, the outline is one step brighter, the wall's "open tile" step (design decision).
    assert ghost.graphicsEffect() is None
    assert ghost.edge() == QColor(*tokens.OVERVIEW_LIFT_EDGE) == QColor(*tokens.WALL_EDGE_RAISED)
    # "Two" and "Three" moved one place left to make room at the third place.
    slots = overview._drag.slots
    assert [card.pos() for card in others] == [slots[0], slots[1]]
    start = first.rect().center() + QPoint(pitch * 2, 0)
    _mouse(first, QEvent.Type.MouseButtonRelease, start, Qt.MouseButton.NoButton)
    if not reduced:
        assert overview.dragging()   # it settles into its place first (中, 0.26 s)
        qtbot.waitUntil(lambda: not overview.dragging(), timeout=2000)
    assert not overview.dragging()
    assert [p["id"] for p in vm.draft.profiles] == [1, 2, 0]
    assert vm.draft.config["active_profile"] == 1
    stage = _overview(window)   # rebuilt with the new order, still open
    assert _keys(stage.overview) == [("normal", 1), ("normal", 2), ("normal", 0), ("codex", None), "new"]
    # The rebuilt row is laid out and seen, not squeezed to nothing.
    assert stage.overview.scroll.width() > 0 and all(card.isVisible() for card in stage.overview.cards)
    xs = [card.x() for card in stage.overview.cards]
    assert xs == sorted(xs) and len(set(xs)) == len(xs)
    window.render(vm.model)
    assert _overview(window).bar.state().count == 1   # "1 处没保存"
    # Flipping on the Mode page follows the new order; Codex stays last.
    assert window._mode_screens() == [("normal", 1), ("normal", 2), ("normal", 0), ("codex", None)]
    vm.discard_draft()   # 放弃全部改动… puts it back
    assert [p["id"] for p in vm.draft.profiles] == [0, 1, 2]


def test_codex_and_the_new_card_do_not_move_and_nothing_goes_after_codex(three_screen_session, monkeypatch):
    monkeypatch.setattr(motion, "reduces_motion", lambda: True)
    window, vm, _gateway = three_screen_session
    overview = _overview(window).overview
    codex, new = overview.cards[3], overview.cards[4]
    assert not codex.movable and not new.movable
    for fixed in (codex, new):
        _drag(fixed, -400)
        assert not overview.dragging()
    assert [p["id"] for p in vm.draft.profiles] == [0, 1, 2] and not vm.draft.is_dirty
    # Dragged far past Codex, the first card stops at the last place before it.
    first = overview.cards[0]
    _drag(first, 3000, release=False)
    assert overview._drag.target == 2
    assert overview._drag.ghost.x() <= overview.row.mapTo(overview, overview._drag.slots[2]).x()
    _mouse(first, QEvent.Type.MouseButtonRelease, first.rect().center(), Qt.MouseButton.NoButton)
    assert [p["id"] for p in vm.draft.profiles] == [1, 2, 0]
    assert _keys(_overview(window).overview)[-2:] == [("codex", None), "new"]


def test_a_click_still_goes_into_the_screen_and_a_short_wobble_is_a_click(three_screen_session, qtbot):
    window, vm, _gateway = three_screen_session
    overview = _overview(window).overview
    chosen = []
    overview.screen_chosen.disconnect()
    overview.screen_chosen.connect(chosen.append)
    card = overview.cards[2]
    _drag(card, 1, steps=1)   # under the drag distance: a click
    assert chosen == [("normal", 2)] and not vm.draft.is_dirty
    qtbot.mouseClick(overview.cards[0], Qt.MouseButton.LeftButton)
    assert chosen[-1] == ("normal", 0)


def test_cards_cannot_be_dragged_while_a_write_is_going_on(three_screen_session, monkeypatch):
    monkeypatch.setattr(motion, "reduces_motion", lambda: True)
    window, vm, _gateway = three_screen_session
    overview = _overview(window).overview
    screens = [card.screen for card in overview.cards if card.screen is not None]
    overview.set_screens(screens, can_add=True, connected=True, can_reorder=False)
    _drag(overview.cards[0], 600)
    assert not overview.dragging()
    assert [p["id"] for p in vm.draft.profiles] == [0, 1, 2]


def test_a_lifted_card_brightens_its_outline_but_the_card_in_use_keeps_its_green_ring(qtbot):
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import QWidget
    from controller_config.views.mode_page import _LiftedCard

    host = QWidget()
    qtbot.addWidget(host)
    picture = QPixmap(204, 315)
    lifted = _LiftedCard(picture, QSize(204, 315), host)
    assert lifted.edge() == QColor(239, 234, 224, 18)            # resting: the card's own MODE_BAR_EDGE
    lifted.set_lift(0.5)
    rest, lit = 18, tokens.OVERVIEW_LIFT_EDGE[3]
    assert rest < lifted.edge().alpha() < lit                       # brightens as it rises
    lifted.set_lift(1.0)
    assert lifted.edge() == QColor(*tokens.OVERVIEW_LIFT_EDGE)
    in_use = _LiftedCard(picture, QSize(204, 315), host, active=True)
    in_use.set_lift(1.0)
    assert in_use.edge() is None
