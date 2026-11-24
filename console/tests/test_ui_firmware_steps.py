"""-15 第 8 处 (乙): 传输 → 写入 → 重启 → 读回 with the status shapes v1, from the flow's own stages."""
from controller_config.firmware_update import FirmwareUpdateState as S
from controller_config.views.ui.firmware_steps import FirmwareSteps, step_of, step_states


def test_each_stage_lights_its_step():
    assert step_states(S.IDLE) == ("off",) * 4
    assert step_states(S.BEGINNING) == ("busy", "off", "off", "off")
    assert step_states(S.TRANSFERRING) == ("busy", "off", "off", "off")
    assert step_states(S.FINALIZING) == ("ok", "busy", "off", "off")
    assert step_states(S.WAITING_RECONNECT) == ("ok", "ok", "busy", "off")
    assert step_states(S.VERIFYING) == ("ok", "ok", "ok", "busy")
    assert step_states(S.COMPLETED) == ("ok",) * 4


def test_pause_and_failure_stay_on_the_last_step_seen():
    last = step_of(S.WAITING_RECONNECT)
    assert step_states(S.PAUSED, last) == ("ok", "ok", "wait", "off")
    assert step_states(S.FAILED, last) == ("ok", "ok", "error", "off")


def test_widget_shows_the_shapes(qtbot):
    steps = FirmwareSteps()
    qtbot.addWidget(steps)
    steps.set_states(step_states(S.FINALIZING))
    assert [icon.state() for icon in steps.icons] == ["ok", "busy", "off", "off"]
    assert [label.text() for label in steps.labels] == ["传输", "写入", "重启", "读回"]
