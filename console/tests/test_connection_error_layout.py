from PySide6.QtCore import QPoint,QRect
from PySide6.QtWidgets import QWidget,QPushButton,QMessageBox
from controller_config.models import AppState,ScreenModel
from controller_config.protocol.bootstrap import BootstrapKind
from controller_config.views.mode_page import ModeStage
from test_session_recovery import session


def test_expanded_connection_error_keeps_profile_selector_inside_card(session,qtbot):
    window,vm,*_=session
    window.resize(1280,800)
    window._select_physical_control('encoder.ccw')
    vm._set(ScreenModel(AppState.READ_FAILED,'配置连接失败','响应 payload CRC32 不匹配',snapshot=vm.model.snapshot))
    window._connection_details_toggle.setChecked(True)
    qtbot.wait(800)
    # The set's name chip moved from the device card to the bottom bar of the Mode page.
    def inside_bar(widget):
        stage=next(stage for stage in window.findChildren(ModeStage) if stage.isVisible())
        bar=stage.bar
        assert widget(bar).isVisible()
        assert bar.rect().contains(QRect(widget(bar).mapTo(bar,QPoint()),widget(bar).size()))
        assert stage.rect().contains(bar.geometry())
        assert widget(bar).height() >= widget(bar).minimumSizeHint().height()
    # With the knob zoomed in, the bar leads with ‹ 回到整台设备 instead of the set chip (Mode页 定稿 v1).
    inside_bar(lambda bar: bar.back)
    window._close_control_editor()
    qtbot.wait(100)
    inside_bar(lambda bar: bar.profile)


def test_usb_read_failure_does_not_suggest_switching_to_usb(session):
    _,vm,*_=session
    vm._connection.port='cu.usbmodem-test'
    vm._on_failure(BootstrapKind.READ_FAILED,'设备返回了无法解析的数据','响应 payload CRC32 不匹配')
    assert 'USB' in vm.model.message
    assert '或使用 USB' not in vm.model.message
    assert 'CRC32' in vm.model.technical_message


def test_config_read_failure_distinguishes_open_link_from_port_open_failure(session):
    _, vm, *_ = session
    vm._connection.port = 'cu.usbmodem-test'
    vm._connection.reading_config = True
    vm._on_failure(BootstrapKind.READ_FAILED, '设备配置损坏', 'GET_CONFIG payload invalid')
    assert '设备已连接，但配置未能读取' in vm.model.message
    vm._connection.reading_config = False
    vm._on_failure(BootstrapKind.READ_FAILED, '无法打开设备通信端口', 'port busy')
    assert '连接失败' in vm.model.message
    assert '设备已连接' not in vm.model.message


def test_background_disconnect_notifies_once_and_focus_failure_stays_inline(session, monkeypatch):
    window, vm, _, snapshot, _ = session
    notices = []
    notifier = type('Notifier', (), {
        'notify_failure': lambda _self, title, body: notices.append((title, body)),
        'set_helper_status': lambda _self, **_kwargs: None,
        'codex_usage_snapshot': None,
        'claude_usage_snapshot': None,   # the Mode page AI quota reads both apps
    })()
    # The fake has no hide_window()/quitting: it must be gone before the window closes,
    # or closing the window in teardown crashes the test process.
    monkeypatch.setattr(window, '_background_controller', notifier)
    monkeypatch.setattr(window, 'isActiveWindow', lambda: False)
    try:
        window.render(ScreenModel(AppState.DISCONNECTED, '连接已断开', snapshot=snapshot))
        window.render(ScreenModel(AppState.DISCONNECTED, '连接已断开', snapshot=snapshot))
        assert len(notices) == 1
        window.show_agent_focus_error('未能打开对应对话')
        assert len(notices) == 2
        assert window.findChild(QWidget, 'agentFocusNotice').isVisible()
        assert not window.findChildren(QMessageBox, 'agentFocusError')
    finally:
        window._background_controller = None
