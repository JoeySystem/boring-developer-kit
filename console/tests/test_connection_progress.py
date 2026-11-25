import pytest
from PySide6.QtWidgets import QLineEdit, QPushButton

from controller_config.models import AppState, ScreenModel
from controller_config.viewmodels.main import MainViewModel
from controller_config.views.main_window import MainWindow
from test_connection_terminal import ready_model
from test_viewmodel import FakeGateway

pytestmark = pytest.mark.skip(reason="社区版不含 3D 连接动画片段")


def test_connection_steps_reuse_preview_and_keep_protocol_details_collapsed(qtbot, contract):
    vm = MainViewModel(FakeGateway(), contract)
    window = MainWindow(vm)
    qtbot.addWidget(window)
    window.show()
    try:
        window.render(ScreenModel(AppState.SCANNING, '扫描设备'))
        progress = window._connection_progress
        canvas = progress.canvas
        stage = progress.stage
        assert progress.isVisible()
        assert progress.title.text() == '连接中'
        assert window._device_auth_summary.text() == '连接中'
        assert progress.hint.text() == '正在寻找设备'
        # Pre-rendered device clip (design decision 乙): the key-light chase loops while scanning.
        assert canvas is None and stage.current == 'search-loop.webp'
        for command in ('HELLO', 'AUTH_CHALLENGE', 'GET_CONFIG'):
            window.render(ScreenModel(AppState.CONNECTING, '正在读取：' + command))
            assert window._connection_progress is progress
            assert progress.canvas is canvas
            assert stage.current == 'found.webp'
            assert progress.title.text() == '连接中'
            assert not window._connection_message.isVisible()
            assert not window._connection_details.isVisible()
        window.resize(800, 600)
        qtbot.wait(30)
        assert window.rect().contains(progress.hint.mapTo(window, progress.hint.rect().center()))
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
        # The found clip's ending plays first (about 1.6 s), then the fade.
        qtbot.waitUntil(lambda: window._connection_transition_model is None
                        and window._connection_fade_target is None, timeout=3500)
        window._select_configuration_mode('normal')
        window._select_control('key.9')
        editor = window.findChild(QLineEdit, 'mappingShortNameEditor')
        original_name = editor.text()
        editor.setText('保留我的编辑')
        window.render(ScreenModel(AppState.CONNECTING, '正在读取：GET_CONFIG', snapshot=model.snapshot))
        # Reconnecting to a device seen before keeps the page: the Mode page greys and its
        # bar shows the connecting steps; no 3D "连接中" page (design decision).
        progress = window._connection_progress
        assert progress is None or not progress.isVisible()
        assert window._page_area.isVisible()
        assert window.findChild(QLineEdit, 'mappingShortNameEditor') is editor
        window.render(vm.model)
        qtbot.waitUntil(lambda: window._connection_fade_target is None, timeout=1200)
        assert window._page_area.isVisible()
        assert window._page_area.graphicsEffect() is None
        assert window.findChild(QLineEdit, 'mappingShortNameEditor') is editor
        assert editor.text() == '保留我的编辑'
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


# --- Found clip ending before the Mode page (design ticket 第 2 条) ---------------------

def _first_connection(qtbot, contract, *, reduce_motion=False):
    vm = MainViewModel(FakeGateway(), contract)
    window = MainWindow(vm)
    window._reduce_motion = reduce_motion
    qtbot.addWidget(window)
    window.show()
    window.render(ScreenModel(AppState.SCANNING, '扫描设备'))
    progress = window._connection_progress
    progress._reduce_motion = reduce_motion
    return vm, window, progress


def _on_mode_page(window):
    return (not window._connection_progress.isVisible() and window._page_area.isVisible()
            and window._connection_transition_model is None and window._connection_fade_target is None)


def test_found_ending_plays_once_then_mode_page(qtbot, contract):
    vm, window, progress = _first_connection(qtbot, contract)
    try:
        window.render(ScreenModel(AppState.CONNECTING, '正在读取：GET_CONFIG'))
        found = progress.stage._movies['found.webp']
        starts = []
        found.started.connect(lambda: starts.append(1))
        ready = ready_model(contract)
        window.render(ready)
        # Connected, but the ending still plays: the page waits for it.
        assert window._connection_finale_waiting
        assert progress.isVisible() and not window._page_area.isVisible()
        assert progress.title.text() == '已连接'
        window.render(ready)  # more READY updates during the ending: kept, no restart
        qtbot.waitUntil(lambda: progress.stage.is_settled, timeout=2400)
        assert starts == []   # never started over
        assert found.currentFrameNumber() == found.frameCount() - 1
        qtbot.waitUntil(lambda: _on_mode_page(window), timeout=1200)
        assert not window._connection_finale_guard.isActive()
    finally:
        vm.shutdown()


def test_connected_straight_from_scanning_still_plays_the_ending(qtbot, contract):
    vm, window, progress = _first_connection(qtbot, contract)
    try:
        assert progress.stage.current == 'search-loop.webp'
        window.render(ready_model(contract))
        assert progress.stage.current == 'found.webp'
        assert window._connection_finale_waiting
        qtbot.waitUntil(lambda: _on_mode_page(window), timeout=3500)
    finally:
        vm.shutdown()


def test_ending_already_held_fades_without_waiting(qtbot, contract):
    vm, window, progress = _first_connection(qtbot, contract)
    try:
        window.render(ScreenModel(AppState.CONNECTING, '正在读取：GET_CONFIG'))
        qtbot.waitUntil(lambda: progress.stage.is_settled, timeout=2400)
        window.render(ready_model(contract))
        assert not window._connection_finale_waiting
        assert window._connection_fade_target is progress
        qtbot.waitUntil(lambda: _on_mode_page(window), timeout=1200)
    finally:
        vm.shutdown()


def test_failure_cancel_or_disconnect_during_ending_never_sticks(qtbot, contract):
    for state in (AppState.AUTHENTICITY_FAILED, AppState.DISCONNECTED, AppState.SCANNING):
        vm, window, progress = _first_connection(qtbot, contract)
        try:
            window.render(ScreenModel(AppState.CONNECTING, '正在读取：HELLO'))
            window.render(ready_model(contract))
            assert window._connection_finale_waiting
            window.render(ScreenModel(state, '连接已中断'))
            assert not window._connection_finale_waiting
            assert not window._connection_finale_guard.isActive()
            assert window._connection_transition_model is None
            # A late "ending finished" from the old clip must not fade anything.
            progress.settled.emit()
            assert window._connection_fade_target is None
            assert progress.isVisible() == (state is AppState.SCANNING)
            assert window._page_area.isVisible() == (state is not AppState.SCANNING)
        finally:
            vm.shutdown()


def test_stalled_ending_is_handed_over_by_the_guard(qtbot, contract, monkeypatch):
    import controller_config.views.main_window as main_window_module
    monkeypatch.setattr(main_window_module, 'CONNECTION_FINALE_GUARD_MS', 200)
    vm, window, progress = _first_connection(qtbot, contract)
    try:
        window.render(ScreenModel(AppState.CONNECTING, '正在读取：HELLO'))
        window.render(ready_model(contract))
        progress.stage.stop()   # decoder stalls / window minimised: no "settled" ever comes
        qtbot.waitUntil(lambda: _on_mode_page(window), timeout=1500)
    finally:
        vm.shutdown()


def test_closing_during_ending_leaves_the_connection_page(qtbot, contract):
    vm, window, progress = _first_connection(qtbot, contract)
    try:
        window.render(ScreenModel(AppState.CONNECTING, '正在读取：HELLO'))
        window.render(ready_model(contract))
        assert window._connection_finale_waiting
        window._cancel_connection_transition()
        assert not window._connection_finale_guard.isActive()
        progress.settled.emit()
        assert window._connection_fade_target is None
    finally:
        vm.shutdown()


def test_reduced_motion_in_the_clip_hands_over_at_once(qtbot, contract):
    vm, window, progress = _first_connection(qtbot, contract)
    progress._reduce_motion = True
    try:
        window.render(ScreenModel(AppState.CONNECTING, '正在读取：HELLO'))
        window.render(ready_model(contract))
        assert not window._connection_finale_waiting
        qtbot.waitUntil(lambda: _on_mode_page(window), timeout=1200)
    finally:
        vm.shutdown()


def test_live_3d_fallback_does_not_wait_for_a_clip(qtbot, contract, monkeypatch):
    from controller_config.views import connection_progress
    monkeypatch.setattr(connection_progress.ClipStage, 'load', lambda self, name: False)
    vm, window, progress = _first_connection(qtbot, contract)
    try:
        assert progress.canvas is not None
        window.render(ScreenModel(AppState.CONNECTING, '正在读取：HELLO'))
        window.render(ready_model(contract))
        assert not window._connection_finale_waiting
        qtbot.waitUntil(lambda: _on_mode_page(window), timeout=1200)
    finally:
        vm.shutdown()


# --- Small versions in the two dialogs (design ticket 第 2.2 条) -------------------------

def test_dialog_clip_waits_for_its_asset_then_plays_it(qtbot, monkeypatch):
    from controller_config.views import connection_progress as cp
    assert not cp.clip_exists(cp.BLE_HELP_CLIP)       # not rendered yet: live 3D stays
    assert cp.dialog_clip(cp.BLE_HELP_CLIP, 320) is None
    real_path = cp._clip_path
    monkeypatch.setattr(cp, 'clip_exists', lambda name: True)
    monkeypatch.setattr(cp, '_clip_path', lambda name: real_path(cp.FOUND_CLIP))
    clip = cp.dialog_clip(cp.BLE_HELP_CLIP, 320)
    qtbot.addWidget(clip)
    assert isinstance(clip, cp.ClipStage) and clip.size().width() == 320
    assert clip.current == cp.BLE_HELP_CLIP
