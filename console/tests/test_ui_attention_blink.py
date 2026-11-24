"""-15 第 7 处 (乙): a status light turning 待处理 blinks twice (0.25 on, 0.3 off, 0.25 on), then stays."""
from controller_config.style import tokens
from controller_config.views.ui import motion
from controller_config.views.ui.attention_blink import AttentionBlink, blink_lit

ORANGE = tokens.MODE_AGENT_ATTENTION


def test_rhythm():
    assert [blink_lit(t) for t in (0, 249, 250, 549, 550, 799)] == [True, True, False, False, True, True]
    assert blink_lit(800) is None


def test_only_a_change_into_pending_blinks(qtbot, monkeypatch):
    monkeypatch.setattr(motion, "reduces_motion", lambda: False)
    shown = []
    blink = AttentionBlink(None, ORANGE)
    blink.freeze_at(0)
    blink.apply({"key.1": ORANGE, "key.2": "#00FF00"}, shown.append)
    assert blink.blinking() == set()          # already pending when first shown: stays on
    blink.apply({"key.1": ORANGE, "key.2": ORANGE}, shown.append)
    assert blink.blinking() == {"key.2"}
    blink.freeze_at(300)
    assert shown[-1] == {"key.1": ORANGE, "key.2": None}   # the dark beat
    blink.freeze_at(600)
    assert shown[-1]["key.2"] == ORANGE
    blink.freeze_at(900)
    assert shown[-1]["key.2"] == ORANGE and blink.blinking() == set()


def test_reduce_motion_does_not_blink(monkeypatch):
    monkeypatch.setattr(motion, "reduces_motion", lambda: True)
    blink = AttentionBlink(None, ORANGE)
    blink.apply({"key.2": None}, lambda _c: None)
    blink.apply({"key.2": ORANGE}, lambda _c: None)
    assert blink.blinking() == set()
