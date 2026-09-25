"""Customer SH-011/012/013/014/018: complete configuration journeys."""
import copy

import pytest
from PySide6.QtWidgets import QLabel, QLineEdit, QPushButton, QScrollArea, QWidget

from controller_config.transactions import ConfigTransactionState
from controller_config.views.action_editor import ActionEditor
from controller_config.views.preferences_editor import PreferencesEditor
from test_session_recovery import session
from test_write_transaction import _ack, _active_status


@pytest.fixture(autouse=True)
def restore_language(session):
    yield
    session[0]._language_manager.set_language('zh_CN')


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
    window._select_physical_control('key.8')
    apply = window.findChild(QPushButton, 'applyMappingToDevice')
    assert not apply.isEnabled()
    editor = window.findChild(ActionEditor)
    editor.set_action({'type': 'key', 'usage': 27, 'modifiers': []})
    assert apply.isEnabled()
    apply.click()
    assert window.findChild(QPushButton, 'saveMappingDraft').isHidden()
    generation = complete_write(vm, gateway, snapshot)
    apply = window.findChild(QPushButton, 'applyMappingToDevice')
    assert not apply.isEnabled()
    commands = list(gateway.commands)
    apply.click()
    assert gateway.commands == commands
    assert vm.model.snapshot.config_result['generation'] == generation
    assert not window.findChild(QPushButton, 'saveConfigurationToDevice').isEnabled()
    assert not window.findChild(QWidget, 'syncSummaryCard').findChildren(QWidget, 'card')
    window.findChild(QLineEdit, 'mappingShortNameEditor').setText('New name')
    assert apply.isEnabled()


def test_two_mapping_writes_read_back_and_survive_same_device_reconnect(session):
    window, vm, gateway, snapshot, _ = session
    window._select_physical_control('key.8')
    editor = window.findChild(ActionEditor)

    editor.set_action({'type': 'key', 'usage': 20, 'modifiers': []})  # Q
    window.findChild(QPushButton, 'applyMappingToDevice').click()
    complete_write(vm, gateway, snapshot)
    first_snapshot = vm.model.snapshot
    assert first_snapshot is not None
    assert vm.draft.mapping(0, 'key.8')['action']['usage'] == 20

    gateway.disconnected.emit('unplugged')
    gateway.snapshot_ready.emit(first_snapshot)
    assert not vm.draft.is_dirty
    assert vm.draft.mapping(0, 'key.8')['action']['usage'] == 20

    window._select_physical_control('key.8')
    window.findChild(ActionEditor).set_action(
        {'type': 'key', 'usage': 26, 'modifiers': []}  # W
    )
    window.findChild(QPushButton, 'applyMappingToDevice').click()
    complete_write(vm, gateway, first_snapshot)

    assert not vm.draft.is_dirty
    assert vm.draft.mapping(0, 'key.8')['action']['usage'] == 26
    names = [command.name for command in gateway.commands]
    assert names.count('VALIDATE_CONFIG') == 2
    assert names.count('SET_CONFIG') == 2
    assert names.count('GET_CONFIG') == 2


def test_connected_mapping_flow_has_one_customer_action_and_offline_keeps_local_save(session):
    window, vm, gateway, _snapshot, _ = session
    window._select_physical_control('key.8')
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
    button = window.findChild(QPushButton, 'savePreferencesToDevice')
    assert not button.isEnabled()
    editor = window.findChild(PreferencesEditor)
    editor._haptic_enabled.setChecked(not editor._haptic_enabled.isChecked())
    assert button.isEnabled()
    assert not vm.draft.is_dirty  # No separate save-draft click.
    button.click()
    assert window.findChild(QPushButton, 'savePreferencesDraft').isHidden()
    complete_write(vm, gateway, snapshot)
    assert not window.findChild(QPushButton, 'savePreferencesToDevice').isEnabled()
    assert '已同步' in window.findChild(QLabel, 'preferencesSyncSummary').text()
    assert not window.findChild(QWidget, 'lightingSyncCard').findChildren(QWidget, 'card')


def test_connected_preferences_flow_has_one_customer_action_and_offline_keeps_local_save(session):
    window, vm, gateway, _snapshot, _ = session
    vm.navigate('lighting')
    device = window.findChild(QPushButton, 'savePreferencesToDevice')
    local = window.findChild(QPushButton, 'savePreferencesDraft')
    assert not device.isHidden()
    assert local.isHidden()

    gateway.disconnected.emit('unplugged')
    device = window.findChild(QPushButton, 'savePreferencesToDevice')
    local = window.findChild(QPushButton, 'savePreferencesDraft')
    assert not device.isEnabled()
    assert not local.isHidden()


@pytest.mark.parametrize('selected', [None, 'key.8'])
def test_generic_save_writes_without_a_second_confirmation(session, selected):
    window, vm, gateway, snapshot, _ = session
    if selected:
        window._select_physical_control(selected)
    vm.rename_profile(snapshot.active_profile_id, 'Changed')
    save = window.findChild(QPushButton, 'saveConfigurationToDevice')
    assert save is not None and save.isEnabled()
    save.click()
    complete_write(vm, gateway, snapshot)
    assert window.findChild(QPushButton, 'confirmConfigurationWrite') is None


def test_mode_context_identifies_edited_profile_without_switching_it(session):
    window, vm, gateway, snapshot, _ = session
    config = copy.deepcopy(vm.draft.config)
    for mode in ('normal', 'codex', 'claude_code'):
        gateway.status_updated.emit({**vm.model.snapshot.status, 'operating_mode': mode})
        context = window.findChild(QLabel, 'mappingModeContext')
        assert context is not None
        assert vm.draft.profile(vm.draft.config['active_profile'])['name'] in context.text()
        assert '普通模式' in context.text()
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
    window._select_physical_control('key.8')
    qtbot.wait(60)
    scroll = window.findChild(QScrollArea, 'overviewScroll')
    scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
    position = scroll.verticalScrollBar().value()
    assert position == 0
    window.findChild(QLineEdit, 'mappingShortNameEditor').setText('Changed')
    window.findChild(QPushButton, 'applyMappingToDevice').click()
    qtbot.wait(60)
    assert window.findChild(QScrollArea, 'overviewScroll') is scroll
    assert scroll.verticalScrollBar().value() == min(position, scroll.verticalScrollBar().maximum()), (position, scroll.verticalScrollBar().maximum())
    assert window._selected_control_id == 'key.8'
    assert window.findChild(QPushButton, 'applyMappingToDevice').isVisible()
