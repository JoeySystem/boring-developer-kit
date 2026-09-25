from PySide6.QtWidgets import QLineEdit, QPushButton

from controller_config.models import AppState, ScreenModel
from controller_config.viewmodels.main import MainViewModel
from controller_config.views.main_window import MainWindow
from test_connection_terminal import ready_model
from test_viewmodel import FakeGateway


def test_connection_steps_reuse_preview_and_keep_protocol_details_collapsed(qtbot, contract):
    vm = MainViewModel(FakeGateway(), contract)
    window = MainWindow(vm)
    qtbot.addWidget(window)
    window.show()
    try:
        window.render(ScreenModel(AppState.SCANNING, '扫描设备'))
        progress = window._connection_progress
        canvas = progress.canvas
        assert progress.isVisible()
        assert progress.title.text() == '连接中'
        assert window._device_auth_summary.text() == '连接中'
        assert progress.hint.text() == '正在寻找设备'
        assert canvas.objectName() == "deviceModelCanvas"
        for command in ('HELLO', 'AUTH_CHALLENGE', 'GET_CONFIG'):
            window.render(ScreenModel(AppState.CONNECTING, '正在读取：' + command))
            assert window._connection_progress is progress
            assert progress.canvas is canvas
            assert progress.title.text() == '连接中'
            assert not window._connection_message.isVisible()
            assert not window._connection_details.isVisible()
        window.resize(800, 600)
        qtbot.wait(30)
        assert window.rect().contains(progress.title.mapTo(window, progress.title.rect().center()))
        window._connection_details_toggle.click()
        assert 'loading configuration' in window._connection_details.toPlainText()
        window.render(ScreenModel(AppState.AUTHENTICITY_FAILED, '认证失败', '检查设备身份'))
        assert not progress.isVisible()
        assert window._page_area.isVisible()
        assert window.findChild(QPushButton, 'connectDeviceButton').isEnabled()
        assert window._connection_message.text() == '认证失败'
    finally:
        vm.shutdown()


def test_reconnect_fades_into_same_editor_and_preserves_unsaved_name(qtbot, contract):
    gateway = FakeGateway()
    vm = MainViewModel(gateway, contract)
    window = MainWindow(vm)
    window._reduce_motion = False
    qtbot.addWidget(window)
    window.show()
    try:
        model = ready_model(contract)
        gateway.snapshot_ready.emit(model.snapshot)
        qtbot.waitUntil(lambda: window._connection_fade_target is None, timeout=1200)
        # This fixture lacks codex_voice, so CODEX key.8 is intentionally read-only.
        window._select_control('key.9')
        editor = window.findChild(QLineEdit, 'mappingShortNameEditor')
        original_name = editor.text()
        editor.setText('保留我的编辑')
        window.render(ScreenModel(AppState.CONNECTING, '正在读取：GET_CONFIG', snapshot=model.snapshot))
        progress = window._connection_progress
        assert progress.isVisible()
        assert not window._page_area.isVisible()
        window.render(vm.model)
        assert progress.isVisible()
        assert progress.title.text() == '已连接'
        qtbot.waitUntil(lambda: window._connection_fade_target is None, timeout=1200)
        assert not progress.isVisible()
        assert window._page_area.isVisible()
        assert window._page_area.graphicsEffect() is None
        assert window.findChild(QLineEdit, 'mappingShortNameEditor') is editor
        assert editor.text() == '保留我的编辑'
        window.render(ScreenModel(AppState.CONNECTING, snapshot=model.snapshot))
        assert window._connection_progress is progress
        editor.setText(original_name)
        window.render(vm.model)
    finally:
        window._cancel_connection_transition()
        current_editor = window.findChild(QLineEdit, 'mappingShortNameEditor')
        if current_editor is not None and 'original_name' in locals():
            current_editor.setText(original_name)
        vm.discard_draft()
        vm.shutdown()


def test_connection_fade_is_interrupted_by_failure_and_new_attempt(qtbot, contract):
    vm = MainViewModel(FakeGateway(), contract)
    window = MainWindow(vm)
    window._reduce_motion = False
    qtbot.addWidget(window)
    window.show()
    try:
        for state in (AppState.SCANNING, AppState.DISCONNECTED):
            window.render(ScreenModel(AppState.CONNECTING))
            window.render(ready_model(contract))
            assert window._connection_transition_model is not None
            window.render(ScreenModel(state, '连接已中断'))
            assert window._connection_transition_model is None
            assert window._connection_fade_target is None
            qtbot.wait(350)
            assert window._connection_progress.isVisible() == (state is AppState.SCANNING)
    finally:
        vm.shutdown()


def test_connection_completion_skips_motion_when_requested(qtbot, contract):
    vm = MainViewModel(FakeGateway(), contract)
    window = MainWindow(vm)
    window._reduce_motion = True
    qtbot.addWidget(window)
    window.show()
    try:
        window.render(ScreenModel(AppState.CONNECTING))
        window.render(ready_model(contract))
        assert not window._connection_progress.isVisible()
        assert window._page_area.isVisible()
        assert window._connection_fade_target is None
    finally:
        vm.shutdown()
