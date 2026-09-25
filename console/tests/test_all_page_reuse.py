from dataclasses import replace
import pytest
from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QLineEdit, QProgressBar, QPushButton
from controller_config.views.actions import ActionsPage
from controller_config.views.action_editor import ActionEditor
from test_session_recovery import session

PAGES = ['overview', 'prompts', 'lighting', 'actions', 'settings:system', 'settings:device', 'firmware', 'diagnostics', 'joystick', 'sequences']

def navigate(window, vm, page):
    if page.startswith('settings:'):
        window._select_settings_section(page.split(':')[1])
    else:
        vm.navigate(page)

@pytest.mark.parametrize('page', PAGES)
def test_all_pages_keep_content_across_navigation_and_unchanged_refresh(session, page):
    window, vm, _, _, _ = session
    window.show()
    navigate(window, vm, page)
    content = window._content_layout.itemAt(window._content_layout.count()-1).widget()
    navigate(window, vm, 'settings:system' if page == 'overview' else 'overview')
    navigate(window, vm, page)
    assert window._content_layout.itemAt(window._content_layout.count()-1).widget() is content
    changes = []
    class Observer(QObject):
        def eventFilter(self, watched, event):
            if event.type() in {QEvent.ParentChange, QEvent.Hide, QEvent.Show}:
                changes.append(event.type())
            return False
    observer = Observer()
    content.installEventFilter(observer)
    vm.changed.emit(vm.model)
    assert changes == []
    assert window._content_layout.itemAt(window._content_layout.count()-1).widget() is content


def test_status_poll_keeps_mapping_editor_and_current_text(session):
    from controller_config.views.device_silhouette import DeviceModelCanvas
    from PySide6.QtWidgets import QLabel
    window, vm, gateway, snapshot, _ = session
    window._select_physical_control('key.9')
    editor = window._content.findChild(ActionEditor)
    name = window._content.findChild(QLineEdit, 'mappingShortNameEditor')
    name.setText('还没应用')
    canvas = window._content.findChild(DeviceModelCanvas)
    gateway.status_updated.emit({**snapshot.status, 'battery': 73, 'battery_valid': True})
    assert window._content.findChild(ActionEditor) is editor
    assert window._content.findChild(DeviceModelCanvas) is canvas
    assert name.text() == '还没应用'
    assert '73%' in window._content.findChild(QLabel, 'deviceBatterySummary').text()


def test_prompt_runtime_updates_without_replacing_fields(session):
    from controller_config.views.prompt_library_editor import PromptLibraryEditor
    from PySide6.QtWidgets import QLabel
    window, vm, _, _, _ = session
    vm.navigate('prompts')
    editor = window._content.findChild(PromptLibraryEditor)
    editor._body.setPlainText('正在编辑的内容')
    vm.prompt_device._helper_message = '本次粘贴失败，请检查权限'
    vm.changed.emit(vm.model)
    assert window._content.findChild(PromptLibraryEditor) is editor
    assert editor._body.toPlainText() == '正在编辑的内容'
    assert editor.findChild(QLabel, 'promptHelperStatus').text() == '本次粘贴失败，请检查权限'


def test_calibration_samples_update_bars_without_rebuilding(session):
    window, vm, _, _, _ = session
    vm.navigate('joystick')
    bar = window._content.findChild(QProgressBar, 'calibrationRawX')
    for value in (1000, 1800, 2600):
        vm._calibration = replace(vm.calibration, raw_x=value)
        vm.changed.emit(vm.model)
        assert window._content.findChild(QProgressBar, 'calibrationRawX') is bar
        assert bar.value() == value


def test_action_draft_and_lazy_tools_survive_other_pages(session):
    from controller_config.views.automation import AutomationPage
    from controller_config.views.extensions import ExtensionsPage
    window, vm, _, _, _ = session
    vm.navigate('actions')
    page = window._content.findChild(ActionsPage)
    assert page.findChild(AutomationPage) is None
    assert page.findChild(ExtensionsPage) is None
    page._create.click()
    page._workflow_page._name.setText('未保存的工作流程')
    vm.navigate('lighting')
    vm.navigate('actions')
    assert window._content.findChild(ActionsPage) is page
    assert page._workflow_page._name.text() == '未保存的工作流程'
    assert page._stack.currentIndex() == ActionsPage.EDIT_ACTION
    page.show_developer_lane(0)
    script = page.findChild(AutomationPage)
    assert script is not None
    script._timeout.setValue(137)
    vm.navigate("lighting")
    vm.navigate("actions")
    assert script.editing_state().timeout_ms == 137_000
    assert page.findChild(ExtensionsPage) is None
    page.show_developer_lane(1)
    extensions = page.findChild(ExtensionsPage)
    page.show_developer_lane(0)
    assert page.findChild(AutomationPage) is script
    assert page.findChild(ExtensionsPage) is extensions


def test_other_device_refreshes_retained_overview_and_device_settings(session):
    from PySide6.QtWidgets import QLabel
    window, vm, gateway, snapshot, _ = session
    window._select_physical_control('key.9')
    name = window._content.findChild(QLineEdit, 'mappingShortNameEditor')
    name.setText('设备 A 的输入')
    window._select_settings_section('device')
    vm.navigate('lighting')
    gateway.disconnected.emit('unplugged')
    other = replace(snapshot, identity={**snapshot.identity, 'serial': 'CP01-001122334455'})
    gateway.snapshot_ready.emit(other)
    window._select_settings_section('device')
    assert any('CP01-001122334455' in label.text() for label in window._content.findChildren(QLabel))
    vm.navigate('overview')
    name = window._content.findChild(QLineEdit, 'mappingShortNameEditor')
    assert name is None or name.text() != '设备 A 的输入'


def test_discarding_mapping_while_another_page_is_open_clears_retained_fields(session, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    window, vm, _, _, _ = session
    window._select_physical_control('key.9')
    name = window._content.findChild(QLineEdit, 'mappingShortNameEditor')
    original = name.text()
    name.setText('应被放弃的输入')
    vm.navigate('settings')
    monkeypatch.setattr('controller_config.views.main_window.confirm_local_draft', lambda *_: QMessageBox.Discard)
    assert window._confirm_leave_mapping_editor()
    vm.navigate('overview')
    assert window._content.findChild(QLineEdit, 'mappingShortNameEditor').text() == original


def test_connection_progress_keeps_page_and_selected_device(session):
    from controller_config.models import AppState, PortCandidate, ScreenModel
    from PySide6.QtWidgets import QScrollArea
    window, vm, _, _, _ = session
    candidates = (PortCandidate('/dev/device-a'), PortCandidate('/dev/device-b'))
    vm._model = ScreenModel(AppState.MULTIPLE_DEVICES, candidates=candidates)
    window.render(vm.model)
    page = window._content.findChild(QScrollArea, 'devicePreviewScroll')
    window._connection_candidates.setCurrentIndex(1)
    vm._model = replace(vm.model, message='请选择要连接的设备', technical_message='扫描完成')
    window.render(vm.model)
    assert window._content.findChild(QScrollArea, 'devicePreviewScroll') is page
    assert window._connection_candidates.currentData() == '/dev/device-b'
    vm._model = replace(vm.model, candidates=candidates + (PortCandidate('/dev/device-c'),))
    window.render(vm.model)
    assert window._connection_candidates.currentData() == '/dev/device-b'
    details = window._connection_details
    details.show()
    details.resize(200, 80)
    vm._model = replace(vm.model, technical_message='\n'.join(str(n) for n in range(100)))
    window.render(vm.model)
    bar = details.verticalScrollBar()
    bar.setValue(5)
    window.render(vm.model)
    assert bar.value() == 5
    vm._model = replace(vm.model, technical_message=vm.model.technical_message + '\n新的记录')
    window.render(vm.model)
    assert bar.value() == 5
