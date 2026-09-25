from __future__ import annotations

import copy
from dataclasses import replace

import pytest
from PySide6.QtWidgets import QApplication

from controller_config.automation import AutomationStore
from controller_config.device_events import DeviceEvent, HOST_ACTION_EVENT, HOST_ACTION_SOURCE
from controller_config.host_actions import HostActionRegistry
from controller_config.host_selection import SystemHostServices
from controller_config.prompt_library import PromptLibraryStore
from controller_config.transport.demo import _power_v2_snapshot
from controller_config.viewmodels.main import MainViewModel
from controller_config.workflows import LocalWorkflow, WorkflowStep, WorkflowStore
from test_viewmodel import FakeGateway


def setup_vm(qtbot, contract, tmp_path):
    gateway = FakeGateway()
    vm = MainViewModel(gateway, contract, workflow_store=WorkflowStore(tmp_path/'workflows'),
        automation_store=AutomationStore(tmp_path/'scripts'), prompt_library_store=PromptLibraryStore(tmp_path/'prompts'),
        host_action_registry=HostActionRegistry(SystemHostServices()))
    vm.host_tasks.directory = tmp_path/'bindings'
    s = _power_v2_snapshot(contract, read_only=False)
    s = replace(s, status={**s.status, 'operating_mode':'normal'})
    gateway.snapshot_ready.emit(s)
    vm.ble_name.detach()
    return gateway, vm, s


def definition(tmp_path):
    return LocalWorkflow('save-clipboard', '收藏文字', None, (
        WorkflowStep('read_clipboard', {}),
        WorkflowStep('append_text_file', {'path':str(tmp_path/'result.txt'), 'separator':'\n'})))


def confirm_mapping(vm, s):
    config = copy.deepcopy(vm.draft.config)
    new = replace(s, config_result={**s.config_result, 'config': config})
    vm.host_tasks.attach(new, readback=True)
    return new


def event(vm, sequence=1, action_id=1):
    return DeviceEvent(HOST_ACTION_EVENT, HOST_ACTION_SOURCE, vm.host_tasks.serial, sequence, {'action_id':action_id, 'task_token':next((b.task_token for b in vm.host_tasks.bindings if b.action_id==action_id), 'f'*32)})


def test_apply_readback_trial_and_real_file_only_once(qtbot, contract, tmp_path):
    gateway, vm, s = setup_vm(qtbot, contract, tmp_path)
    b = vm.host_tasks.apply('workflow', definition(tmp_path), 'key.8')
    assert b.pending is not None and not b.applied
    assert gateway.commands[-1].name == 'VALIDATE_CONFIG'
    assert not vm.workflow_host.workflows
    vm.host_tasks.dispatch(event(vm, action_id=b.action_id))
    assert not (tmp_path/'result.txt').exists()
    s = confirm_mapping(vm, s)
    b = vm.host_tasks.binding_for('workflow', 'save-clipboard')
    assert b.applied and not b.tested
    assert vm.workflow_host.workflows[0].trigger_prompt_id is None
    vm.host_tasks.dispatch(event(vm, action_id=b.action_id))
    assert not (tmp_path/'result.txt').exists()
    QApplication.clipboard().setText('真正保存一次')
    vm.host_tasks.arm_trial(b.action_id)
    vm.host_tasks.dispatch(event(vm, action_id=b.action_id))
    vm.host_tasks.dispatch(event(vm, sequence=2, action_id=b.action_id))
    qtbot.waitUntil(lambda: not vm.host_tasks.running)
    assert (tmp_path/'result.txt').read_text() == '真正保存一次'
    assert vm.host_tasks.binding_for('workflow','save-clipboard').tested
    assert vm.workflow_host.workflows[0].enabled
    vm.host_tasks.attach(None)
    vm.host_tasks.attach(s)
    QApplication.clipboard().setText('重连后仍可用')
    vm.host_tasks.dispatch(event(vm, sequence=3, action_id=b.action_id))
    qtbot.waitUntil(lambda: not vm.host_tasks.running)
    assert (tmp_path/'result.txt').read_text() == '真正保存一次\n重连后仍可用'
    vm.shutdown()


def test_missing_binding_never_pastes_prompt(qtbot, contract, tmp_path):
    _, vm, _ = setup_vm(qtbot, contract, tmp_path)
    QApplication.clipboard().setText('不要覆盖')
    vm.host_tasks.dispatch(event(vm, action_id=99))
    assert vm.host_tasks.status == '此电脑尚未设置这个任务'
    assert QApplication.clipboard().text() == '不要覆盖'
    assert not vm.event_bus.events
    vm.shutdown()


def test_official_controls_transport_and_old_firmware_rejected(qtbot, contract, tmp_path):
    _, vm, s = setup_vm(qtbot, contract, tmp_path)
    with pytest.raises(ValueError, match='不支持'):
        vm.host_tasks.apply('workflow', definition(tmp_path), 'key.1')
    vm.host_tasks.attach(replace(s,port_name='ble:test'))
    with pytest.raises(ValueError, match='USB'):
        vm.host_tasks.apply('workflow', definition(tmp_path), 'key.8')
    caps = copy.deepcopy(s.capabilities)
    caps['features'].pop('host_action_usb')
    vm.host_tasks.attach(replace(s, capabilities=caps))
    with pytest.raises(ValueError, match='固件'):
        vm.host_tasks.apply('workflow', definition(tmp_path), 'key.8')
    vm.shutdown()


def test_unknown_other_computer_reference_is_not_reused(qtbot, contract, tmp_path):
    _, vm, s = setup_vm(qtbot, contract, tmp_path)
    config = copy.deepcopy(s.config)
    config['profiles'][0]['mappings'].append({'control_id':'key.9','short_name':'另一台电脑','action':{'type':'host_action','action_id':1,'task_token':'e'*32}})
    vm.host_tasks.attach(replace(s,config_result={**s.config_result,'config':config}))
    b = vm.host_tasks.apply('workflow',definition(tmp_path),'key.8')
    assert b.action_id == 2
    vm.shutdown()


def test_legacy_manual_event_allows_no_prompt_without_fake_payload():
    from controller_config.device_events import automation_manual_test_event
    assert automation_manual_test_event(device_serial='test',prompt_id=None).payload['prompt_id'] is None
    with pytest.raises(ValueError):
        DeviceEvent(HOST_ACTION_EVENT, HOST_ACTION_SOURCE,'test',1,{'action_id':0})


def complete_device_write(gateway, vm, snapshot):
    from test_write_transaction import _ack, _active_status

    config = copy.deepcopy(vm.draft.config)
    gateway.command_completed.emit('VALIDATE_CONFIG', _ack('VALIDATE_CONFIG'))
    assert gateway.commands[-1].name == 'SET_CONFIG'
    digest = vm.write_transaction.candidate_digest
    generation = snapshot.config_result['generation'] + 1
    gateway.command_completed.emit('SET_CONFIG', _ack('SET_CONFIG'))
    gateway.status_updated.emit(_active_status(snapshot, digest, generation))
    assert gateway.commands[-1].name == 'GET_CONFIG'
    assert not vm.host_tasks.bindings[0].applied
    gateway.command_completed.emit('GET_CONFIG', _ack('GET_CONFIG', {
        'generation': generation, 'digest': digest, 'config': config,
    }))
    assert not vm.draft.is_dirty
    return vm.model.snapshot


def test_pending_edit_is_committed_only_after_write_readback(qtbot, contract, tmp_path):
    from controller_config.protocol.bootstrap import BootstrapError, BootstrapKind

    gateway, vm, snapshot = setup_vm(qtbot, contract, tmp_path)
    original = definition(tmp_path)
    vm.host_tasks.apply('workflow', original, 'key.8')
    snapshot = complete_device_write(gateway, vm, snapshot)
    assert vm.host_tasks.bindings[0].applied
    replacement = replace(original, name='新收藏', steps=(
        WorkflowStep('read_clipboard', {}),
        WorkflowStep('append_text_file', {'path': str(tmp_path/'new.txt'), 'separator': '\n'}),
    ))
    vm.host_tasks.apply('workflow', replacement, 'key.8')
    vm.changed.emit(vm.model)
    assert vm.workflow_host.workflows[0].name == original.name
    assert not vm.host_tasks.bindings[0].applied
    gateway.command_failed.emit('VALIDATE_CONFIG', BootstrapError(BootstrapKind.READ_FAILED, 'test failure'))
    assert vm.workflow_host.workflows[0].steps == original.steps
    assert vm.host_tasks.bindings[0].pending is not None
    # Retry the same local task; no new identifier and no early activation.
    identity = vm.host_tasks.bindings[0].task_token
    vm.host_tasks.apply('workflow', replacement, 'key.8')
    assert vm.host_tasks.bindings[0].task_token == identity
    complete_device_write(gateway, vm, snapshot)
    assert vm.workflow_host.workflows[0].steps == replacement.steps
    assert not vm.host_tasks.bindings[0].tested
    vm.shutdown()


def test_recycled_device_slot_cannot_run_another_computers_task(qtbot, contract, tmp_path):
    gateway, vm, snapshot = setup_vm(qtbot, contract, tmp_path)
    vm.host_tasks.apply('workflow', definition(tmp_path), 'key.8')
    snapshot = complete_device_write(gateway, vm, snapshot)
    binding = vm.host_tasks.bindings[0]
    config = copy.deepcopy(snapshot.config)
    mapping = next(m for m in config['profiles'][0]['mappings'] if m['control_id'] == 'key.8')
    mapping['action']['task_token'] = 'b'*32
    other = replace(snapshot, config_result={**snapshot.config_result, 'config': config})
    vm.host_tasks.attach(other, readback=True)
    assert not vm.host_tasks.bindings[0].applied
    vm.host_tasks.dispatch(DeviceEvent(HOST_ACTION_EVENT, HOST_ACTION_SOURCE,
        vm.host_tasks.serial, 1, {'action_id': binding.action_id, 'task_token': 'b'*32}))
    assert vm.host_tasks.status == '此电脑尚未设置这个任务'
    assert not vm.host_tasks.running
    assert not (tmp_path/'result.txt').exists()
    vm.shutdown()


def test_leaving_trial_does_not_enable_task(qtbot, contract, tmp_path):
    gateway, vm, snapshot = setup_vm(qtbot, contract, tmp_path)
    binding = vm.host_tasks.apply('workflow', definition(tmp_path), 'key.8')
    complete_device_write(gateway, vm, snapshot)
    vm.host_tasks.arm_trial(binding.action_id)
    vm.host_tasks.cancel_trial()
    vm.host_tasks.dispatch(event(vm, action_id=binding.action_id))
    assert not vm.host_tasks.running
    assert not vm.host_tasks.bindings[0].tested
    assert not (tmp_path/'result.txt').exists()
    vm.shutdown()


def test_key_editor_preserves_task_identity_without_editable_codes(qtbot, contract):
    from controller_config.actions import action_definitions
    from controller_config.views.action_editor import ActionEditor

    action = {'type': 'host_action', 'action_id': 2, 'task_token': 'a'*32}
    editor = ActionEditor(action_definitions(contract, ('none', 'key', 'host_action')), action)
    qtbot.addWidget(editor)
    assert editor.action() == action
    assert not editor._field_widgets
    editor.set_action({'type': 'key', 'usage': 4})
    editor._type_selector.setCurrentIndex(editor._type_selector.findData('host_action'))
    assert editor.action() == action
    # Rollback firmware may no longer advertise host tasks; preserve readback.
    old_editor = ActionEditor(action_definitions(contract, ('none', 'key')), action)
    qtbot.addWidget(old_editor)
    assert old_editor.action() == action


@pytest.mark.parametrize('provider', ['script', 'extension'])
def test_independent_binding_runs_real_provider_and_checks_output(qtbot, contract, tmp_path, monkeypatch, provider):
    import json
    import tempfile
    import uuid
    from pathlib import Path

    from controller_config.automation import LocalScriptAutomation
    from controller_config.extensions.bindings import ExtensionBindingStore
    from controller_config.extensions.manager import ExtensionManager
    from controller_config.extensions.platform import ExtensionPlatformController
    from test_extension_platform import EXAMPLES

    gateway, vm, snapshot = setup_vm(qtbot, contract, tmp_path)
    with tempfile.TemporaryDirectory(prefix='bt-', dir='/tmp') as directory:
        for name in ('TMPDIR', 'TMP', 'TEMP'):
            monkeypatch.setenv(name, directory)
        try:
            if provider == 'script':
                task = LocalScriptAutomation('example-script', '保存事件', None,
                    str(EXAMPLES.parent/'scripts/save_host_task_event.py'), False)
            else:
                platform = ExtensionPlatformController(vm, contract, prompt_helper=None,
                    manager=ExtensionManager(tmp_path/'extensions'),
                    binding_store=ExtensionBindingStore(tmp_path/'old-bindings.json'),
                    server_name='bt-'+uuid.uuid4().hex)
                vm.attach_extension_platform(platform)
                platform.start()
                ext = platform.import_package(EXAMPLES/'save_host_task')
                identity = ext.manifest.extension_id
                platform.enable(identity)
                qtbot.waitUntil(lambda: platform.extension_is_ready(identity), timeout=5000)
                task = {'target_id':identity+':save_event', 'extension_id':identity,
                        'action_id':'save_event', 'name':'保存事件', 'timeout_ms':60000}
            b = vm.host_tasks.apply(provider, task, 'key.8')
            complete_device_write(gateway, vm, snapshot)
            assert not vm.host_tasks.bindings[0].tested
            assert not list(Path(directory).rglob('task-event.json'))
            vm.host_tasks.arm_trial(b.action_id)
            invocation = event(vm, action_id=b.action_id)
            vm.host_tasks.dispatch(invocation)
            qtbot.waitUntil(lambda: not vm.host_tasks.running, timeout=5000)
            assert vm.host_tasks.bindings[0].tested, vm.host_tasks.status
            outputs = list(Path(directory).rglob('task-event.json'))
            assert len(outputs) == 1
            assert json.loads(outputs[0].read_text()) == invocation.as_mapping()
            assert not vm.event_bus.events  # No legacy prompt takeover/paste path.
        finally:
            vm.shutdown()
