"""Customer SH-011/012/013/014/018: complete configuration journeys."""
import copy

import pytest
from PySide6.QtCore import QRect
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
    assert not any(c.name == 'SET_CONFIG' for c in gateway.commands)
    vm.confirm_device_write()
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
    window._select_physical_control('key.1')
    apply = window.findChild(QPushButton, 'applyMappingToDevice')
    assert not apply.isEnabled()
    editor = window.findChild(ActionEditor)
    editor.set_action({'type': 'key', 'usage': 27, 'modifiers': []})
    assert apply.isEnabled()
    apply.click()
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
    complete_write(vm, gateway, snapshot)
    assert not window.findChild(QPushButton, 'savePreferencesToDevice').isEnabled()
    assert '已同步' in window.findChild(QLabel, 'preferencesSyncSummary').text()
    assert not window.findChild(QWidget, 'lightingSyncCard').findChildren(QWidget, 'card')


@pytest.mark.parametrize('selected', [None, 'key.1'])
def test_mapping_write_has_one_confirmation_in_side_rail(session, selected):
    window, vm, gateway, snapshot, _ = session
    if selected:
        window._select_physical_control(selected)
    vm.rename_profile(snapshot.active_profile_id, 'Changed')
    vm.prepare_device_write()
    gateway.command_completed.emit('VALIDATE_CONFIG', _ack('VALIDATE_CONFIG'))
    buttons = [b for b in window.findChildren(QPushButton) if b.text() == '确认写入设备']
    assert len(buttons) == 1
    rail = window.findChild(QScrollArea, 'overviewScroll').findChild(QPushButton, 'confirmConfigurationWrite')
    assert rail is buttons[0]


def test_mode_context_identifies_edited_profile_without_switching_it(session):
    window, vm, gateway, snapshot, _ = session
    config = copy.deepcopy(vm.draft.config)
    for mode in ('normal', 'codex', 'claude_code'):
        gateway.status_updated.emit({**vm.model.snapshot.status, 'operating_mode': mode})
        context = window.findChild(QLabel, 'mappingModeContext')
        assert context is not None
        assert vm.draft.profile(vm.draft.config['active_profile'])['name'] in context.text()
        assert 'NORMAL' in context.text()
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


def test_mapping_write_keeps_scroll_container_and_selection(session, qtbot):
    window, vm, gateway, snapshot, _ = session
    # Use the supported narrow, stacked workspace to exercise a real scroll offset.
    window.show()
    window._fit_window_to_available_area(QRect(0, 0, 800, 700))
    window._select_physical_control('key.1')
    qtbot.wait(60)
    scroll = window.findChild(QScrollArea, 'overviewScroll')
    qtbot.waitUntil(lambda: scroll.verticalScrollBar().maximum() > 0)
    scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
    position = scroll.verticalScrollBar().value()
    assert position > 0
    window.findChild(QLineEdit, 'mappingShortNameEditor').setText('Changed')
    window.findChild(QPushButton, 'applyMappingToDevice').click()
    qtbot.wait(60)
    assert window.findChild(QScrollArea, 'overviewScroll') is scroll
    assert scroll.verticalScrollBar().value() == min(position, scroll.verticalScrollBar().maximum()), (position, scroll.verticalScrollBar().maximum())
    assert window._selected_control_id == 'key.1'
