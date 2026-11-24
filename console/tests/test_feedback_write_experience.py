"""Customer SH-011/012/013/014/018: complete configuration journeys."""
import copy

import pytest
from PySide6.QtWidgets import QLabel, QLineEdit, QPushButton, QScrollArea, QWidget

from controller_config.transactions import ConfigTransactionState
from controller_config.views.action_editor import ActionEditor
from controller_config.views.mode_page import ModeStage
from controller_config.save_banner_state import SaveBannerKind
from controller_config.views.preferences_editor import PreferencesEditor
from test_session_recovery import session
from test_write_transaction import _ack, _active_status


@pytest.fixture(autouse=True)
def restore_language(session):
    yield
    session[0]._language_manager.set_language('zh_CN')


def _stage(window):
    return next(stage for stage in window.findChildren(ModeStage) if stage.isVisible())


def complete_write(vm, gateway, snapshot):
    assert vm.write_transaction.state is ConfigTransactionState.VALIDATING
    candidate = copy.deepcopy(vm.write_transaction.candidate_config)
    digest = vm.write_transaction.candidate_digest
    generation = vm.draft.base_generation + 1
    gateway.command_completed.emit('VALIDATE_CONFIG', _ack('VALIDATE_CONFIG'))
    assert any(c.name == 'SET_CONFIG' for c in gateway.commands)
    gateway.command_completed.emit('SET_CONFIG', _ack('SET_CONFIG'))
    gateway.status_updated.emit(_active_status(snapshot, digest, generation))
    gateway.command_completed.emit('GET_CONFIG', _ack('GET_CONFIG', {
        'generation': generation, 'digest': digest, 'config': candidate,
    }))
    assert vm.write_transaction.state is ConfigTransactionState.ACTIVE
    assert not vm.draft.is_dirty
    return generation


def test_mapping_apply_disables_without_changes_and_after_readback(session):
    window, vm, gateway, snapshot, _ = session
    window._select_physical_control('key.9')
    bar = _stage(window).bar
    assert not bar.save.isVisible()          # nothing typed: nothing to save
    editor = window.findChild(ActionEditor)
    editor.set_action({'type': 'key', 'usage': 27, 'modifiers': []})
    bar = _stage(window).bar
    assert bar.save.isVisible() and bar.save.isEnabled()
    bar.save.click()
    assert window.findChild(QPushButton, 'saveMappingDraft').isHidden()
    generation = complete_write(vm, gateway, snapshot)
    bar = _stage(window).bar
    assert not bar.save.isVisible() and bar.state().right in {'saved', 'synced'}
    commands = list(gateway.commands)
    window.findChild(QPushButton, 'applyMappingToDevice').click()   # nothing changed: no write
    assert gateway.commands == commands
    assert vm.model.snapshot.config_result['generation'] == generation
    window.findChild(QLineEdit, 'mappingShortNameEditor').setText('New name')
    bar = _stage(window).bar
    assert bar.save.isVisible() and bar.save.isEnabled()


def test_two_mapping_writes_read_back_and_survive_same_device_reconnect(session):
    window, vm, gateway, snapshot, _ = session
    window._select_physical_control('key.9')
    editor = window.findChild(ActionEditor)

    editor.set_action({'type': 'key', 'usage': 20, 'modifiers': []})  # Q
    window.findChild(QPushButton, 'applyMappingToDevice').click()
    complete_write(vm, gateway, snapshot)
    first_snapshot = vm.model.snapshot
    assert first_snapshot is not None
    assert vm.draft.mapping(0, 'key.9')['action']['usage'] == 20

    gateway.disconnected.emit('unplugged')
    gateway.snapshot_ready.emit(first_snapshot)
    assert not vm.draft.is_dirty
    assert vm.draft.mapping(0, 'key.9')['action']['usage'] == 20

    window._select_physical_control('key.9')
    window.findChild(ActionEditor).set_action(
        {'type': 'key', 'usage': 26, 'modifiers': []}  # W
    )
    window.findChild(QPushButton, 'applyMappingToDevice').click()
    complete_write(vm, gateway, first_snapshot)

    assert not vm.draft.is_dirty
    assert vm.draft.mapping(0, 'key.9')['action']['usage'] == 26
    names = [command.name for command in gateway.commands]
    assert names.count('VALIDATE_CONFIG') == 2
    assert names.count('SET_CONFIG') == 2
    assert names.count('GET_CONFIG') == 2


def test_connected_mapping_flow_has_one_customer_action_and_offline_keeps_local_save(session):
    window, vm, gateway, _snapshot, _ = session
    window._select_physical_control('key.9')
    apply = window.findChild(QPushButton, 'applyMappingToDevice')
    local = window.findChild(QPushButton, 'saveMappingDraft')
    assert not apply.isHidden()
    assert local.isHidden()

    gateway.disconnected.emit('unplugged')
    apply = window.findChild(QPushButton, 'applyMappingToDevice')
    local = window.findChild(QPushButton, 'saveMappingDraft')
    assert not apply.isEnabled()
    assert not local.isHidden()


def test_haptic_has_direct_apply_and_becomes_synced_after_readback(session):
    window, vm, gateway, snapshot, _ = session
    vm.navigate('lighting')
    vm.screen_icon.attach(None)
    vm.screen_glyphs.attach(None)
    banner = window._save_banner  # 灯光页 v0.4 第 5 张: no changes, no banner
    assert not banner.is_shown()
    editor = window.findChild(PreferencesEditor)
    editor._haptic_enabled.setChecked(not editor._haptic_enabled.isChecked())
    assert banner.kind() is SaveBannerKind.DIRTY
    button = window.findChild(QPushButton, 'savePreferencesToDevice')
    assert button.isEnabled()
    assert not vm.draft.is_dirty  # No separate save-draft click.
    button.click()
    assert window.findChild(QPushButton, 'savePreferencesDraft') is None
    complete_write(vm, gateway, snapshot)
    assert banner.kind() is SaveBannerKind.SAVED
    assert window.findChild(QLabel, 'preferencesSyncSummary').text() == '已存进设备'
    assert not window.findChild(QWidget, 'lightingSyncCard').findChildren(QWidget, 'card')


def test_connected_preferences_flow_has_one_customer_action_and_offline_keeps_local_save(session):
    window, vm, gateway, _snapshot, _ = session
    vm.navigate('lighting')
    editor = window.findChild(PreferencesEditor)
    editor._display_brightness.setValue(editor._display_brightness.value() + 1)
    device = window.findChild(QPushButton, 'savePreferencesToDevice')
    assert device is not None and device.isEnabled()
    assert window.findChild(QPushButton, 'savePreferencesDraft') is None

    gateway.disconnected.emit('unplugged')
    assert window._save_banner.kind() is SaveBannerKind.OFFLINE
    assert window.findChild(QPushButton, 'savePreferencesToDevice') is None
    local = window.findChild(QPushButton, 'savePreferencesDraft')
    assert local is not None and local.isEnabled()


@pytest.mark.parametrize('selected', [None, 'key.9'])
def test_generic_save_writes_without_a_second_confirmation(session, selected):
    window, vm, gateway, snapshot, _ = session
    if selected:
        window._select_physical_control(selected)
    vm.rename_profile(snapshot.active_profile_id, 'Changed')
    save = _stage(window).bar.save
    assert save.isVisible() and save.isEnabled()
    save.click()
    complete_write(vm, gateway, snapshot)
    assert window.findChild(QPushButton, 'confirmConfigurationWrite') is None


def test_mode_context_identifies_edited_profile_without_switching_it(session):
    window, vm, gateway, snapshot, _ = session
    config = copy.deepcopy(vm.draft.config)
    for mode in ('normal', 'codex', 'claude_code'):
        gateway.status_updated.emit({**vm.model.snapshot.status, 'operating_mode': mode})
        stage = _stage(window)
        assert stage.bar.profile_label.text() == vm.draft.profile(vm.draft.config['active_profile'])['name']
        assert stage.state().word == 'NORMAL' and window._configuration_mode == 'normal'
        assert vm.draft.config == config


@pytest.mark.parametrize('language', ['zh_CN', 'en_US'])
def test_navigation_keeps_destinations_in_place(session, qtbot, language):
    window, vm, *_ = session
    window._language_manager.set_language(language)
    window.resize(1280, 820)
    window.show()
    qtbot.wait(60)
    positions = {name: button.geometry() for name, button in window._nav_buttons.items()}
    for page in ('lighting', 'settings', 'overview'):
        vm.navigate(page)
        qtbot.wait(50)
        assert {name: b.geometry() for name, b in window._nav_buttons.items()} == positions
    window._language_manager.set_language('zh_CN')


def test_mapping_write_keeps_container_selection_and_visible_actions(session, qtbot):
    window, vm, gateway, snapshot, _ = session
    window.resize(1100, 700)
    window.show()
    window._select_physical_control('key.9')
    qtbot.wait(60)
    scroll = window.findChild(QScrollArea, 'overviewScroll')
    scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
    position = scroll.verticalScrollBar().value()
    assert position == 0
    window.findChild(QLineEdit, 'mappingShortNameEditor').setText('Changed')
    _stage(window).bar.save.click()
    qtbot.wait(60)
    assert window.findChild(QScrollArea, 'overviewScroll') is scroll
    assert scroll.verticalScrollBar().value() == min(position, scroll.verticalScrollBar().maximum()), (position, scroll.verticalScrollBar().maximum())
    assert window._selected_control_id == 'key.9'
    stage = _stage(window)
    assert stage.card() is not None and stage.card().isVisible()
    assert stage.bar.isVisible() and stage.bar.state().right == 'writing'
