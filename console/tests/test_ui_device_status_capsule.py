"""The top bar's device status capsule (the designer, 顶条认证标 整条顶条 v1 定稿)."""
import pytest

from controller_config.models import AppState, ScreenModel
from controller_config.views.main_window import _device_status_kind
from controller_config.views.ui import DeviceStatusCapsule
from controller_config.views.ui.device_status_capsule import HEIGHT, LOOKS


@pytest.mark.parametrize("kind,shape,text,cream", [
    ("verified", "ok", "已认证", True),
    ("dev", "off", "开发设备 · 未认证", False),
    ("untrusted", "error", "BORING 身份未确认", True),
    ("no_device", "off", "未连接", False),
    ("disconnected", "off", "已断开", False),
    ("connecting", "busy", "连接中", False),
])
def test_each_state_has_one_shape_and_one_local_sentence(qtbot, kind, shape, text, cream):
    capsule = DeviceStatusCapsule()
    qtbot.addWidget(capsule)
    capsule.set_status(kind)
    assert capsule.icon.state() == shape
    assert capsule.label.text() == text and "[" not in capsule.label.text()
    assert capsule.label.property("cream") is cream
    assert capsule.height() == HEIGHT


def test_development_device_draws_a_cream_dashed_circle(qtbot):
    capsule = DeviceStatusCapsule()
    qtbot.addWidget(capsule)
    capsule.set_status("dev")
    assert capsule.icon._cream is True
    capsule.set_status("no_device")
    assert capsule.icon._cream is False


@pytest.mark.parametrize("state,kind", [
    (AppState.SCANNING, "connecting"),
    (AppState.CONNECTING, "connecting"),
    (AppState.AUTHENTICITY_FAILED, "untrusted"),
    (AppState.READ_FAILED, "read_failed"),
    (AppState.DISCONNECTED, "no_device"),
])
def test_states_without_a_snapshot(state, kind):
    assert _device_status_kind(ScreenModel(state, "")) == kind
    assert kind in LOOKS
