import copy
import hashlib
from dataclasses import replace

import pytest

from controller_config.protocol.framing import canonical_json_bytes
from controller_config.transactions import ConfigTransactionState
from controller_config.transport.demo import _power_v2_snapshot
from controller_config.viewmodels.main import MainViewModel
from test_write_transaction import FakeWriteGateway, _ack, _active_status


def changed_config(snapshot, generation=None):
    config = copy.deepcopy(snapshot.config)
    config['profiles'][0]['name'] = 'Changed on device'
    mapping = next(
        item for item in config['profiles'][0]['mappings']
        if item['control_id'] == 'encoder.cw'
    )
    mapping['action'] = {'type':'consumer','usage':234}
    result = {'config':config, 'generation':generation or snapshot.config_result['generation']+1,
              'digest':hashlib.sha256(canonical_json_bytes(config)).hexdigest()}
    return result, _active_status(snapshot, result['digest'], result['generation'])


@pytest.mark.parametrize('dirty', [False, True])
def test_external_generation_reads_complete_config_and_preserves_dirty(qtbot, contract, dirty):
    gateway = FakeWriteGateway()
    vm = MainViewModel(gateway, contract)
    snapshot = _power_v2_snapshot(contract, read_only=False)
    gateway.snapshot_ready.emit(snapshot)
    if dirty:
        vm.rename_profile(0, 'My local draft')
    old_draft = vm.draft
    local = copy.deepcopy(old_draft.config)
    result, status = changed_config(snapshot)
    gateway.status_updated.emit(status)
    assert gateway.commands[-1].name == 'GET_CONFIG'
    count = len(gateway.commands)
    gateway.status_updated.emit(status)
    assert len(gateway.commands) == count  # One outstanding read.
    gateway.command_completed.emit('GET_CONFIG', _ack('GET_CONFIG', result))
    assert vm.model.snapshot.config == result['config']
    assert vm.model.snapshot.config_is_synced
    if dirty:
        assert vm.draft is old_draft and vm.draft.config == local
        assert vm.draft.base_generation == snapshot.config_result['generation']
        assert vm.write_transaction.state is ConfigTransactionState.CONFLICT
        vm.discard_conflicted_draft()
        assert not vm.pending_dirty_workspaces()
    else:
        assert vm.draft.config == result['config']
        assert not vm.draft.is_dirty
        assert vm.draft.base_generation == result['generation']
    assert not any(c.name in {'SET_CONFIG', 'VALIDATE_CONFIG'} for c in gateway.commands)
    vm.shutdown()


def setup_refresh(contract):
    gateway = FakeWriteGateway()
    vm = MainViewModel(gateway, contract)
    snapshot = _power_v2_snapshot(contract, read_only=False)
    gateway.snapshot_ready.emit(snapshot)
    return vm, gateway, snapshot


def test_busy_transaction_defers_refresh_until_idle_even_if_status_unchanged(qtbot, contract):
    from controller_config.firmware_update import FirmwareUpdateTransaction, FirmwareUpdateState
    vm, gateway, snapshot = setup_refresh(contract)
    result, status = changed_config(snapshot)
    vm._firmware_update = FirmwareUpdateTransaction(state=FirmwareUpdateState.TRANSFERRING)
    gateway.status_updated.emit(status)
    assert not gateway.commands
    vm._firmware_update = FirmwareUpdateTransaction()
    gateway.status_updated.emit(status)
    assert gateway.commands[-1].name == 'GET_CONFIG'
    gateway.command_completed.emit('GET_CONFIG', _ack('GET_CONFIG', result))
    assert vm.model.snapshot.config == result['config']
    vm.shutdown()


def test_stale_read_waits_for_next_poll_and_cannot_overwrite_draft(qtbot, contract):
    vm, gateway, snapshot = setup_refresh(contract)
    result, status = changed_config(snapshot)
    gateway.status_updated.emit(status)
    newer = {**result, 'generation':result['generation']+1}
    newer_status = _active_status(snapshot, newer['digest'], newer['generation'])
    gateway.status_updated.emit(newer_status)
    gateway.command_completed.emit('GET_CONFIG', _ack('GET_CONFIG', result))
    assert vm.model.snapshot.config == snapshot.config
    gateway.status_updated.emit(newer_status)
    gateway.command_completed.emit('GET_CONFIG', _ack('GET_CONFIG', newer))
    assert vm.draft.base_generation == newer['generation']
    vm.shutdown()


def test_refresh_failure_keeps_workspace_and_is_not_unknown_write(qtbot, contract):
    from controller_config.protocol.bootstrap import BootstrapError, BootstrapKind
    vm, gateway, snapshot = setup_refresh(contract)
    vm.rename_profile(0, 'Keep this draft')
    local = copy.deepcopy(vm.draft.config)
    result, status = changed_config(snapshot)
    gateway.status_updated.emit(status)
    gateway.command_failed.emit('GET_CONFIG', BootstrapError(BootstrapKind.READ_FAILED, 'timeout', 'read timeout'))
    assert vm.draft.config == local
    assert vm.write_transaction.state is ConfigTransactionState.IDLE
    assert '失败' in vm.model.message
    gateway.status_updated.emit(status)
    gateway.command_completed.emit('GET_CONFIG', _ack('GET_CONFIG', result))
    assert vm.write_transaction.state is ConfigTransactionState.CONFLICT
    vm.shutdown()


def test_refresh_and_configuration_write_do_not_share_readback(qtbot, contract):
    vm, gateway, snapshot = setup_refresh(contract)
    vm.rename_profile(0, 'Local')
    result, status = changed_config(snapshot)
    gateway.status_updated.emit(status)
    with pytest.raises(ValueError, match='正在读取'):
        vm.prepare_device_write()
    gateway.command_completed.emit('GET_CONFIG', _ack('GET_CONFIG', result))
    vm.prepare_device_write()
    assert vm.write_transaction.state is ConfigTransactionState.CONFLICT
    assert not any(c.name == 'VALIDATE_CONFIG' for c in gateway.commands)
    vm.rebase_conflicted_draft()
    vm.prepare_device_write()
    assert gateway.commands[-1].name == 'VALIDATE_CONFIG'
    assert vm.write_transaction.base_generation == result['generation']
    vm.shutdown()


def test_disconnection_discards_late_refresh_result(qtbot, contract):
    vm, gateway, snapshot = setup_refresh(contract)
    result, status = changed_config(snapshot)
    gateway.status_updated.emit(status)
    gateway.disconnected.emit('unplugged')
    other = replace(snapshot, identity={**snapshot.identity,'serial':'CP01-001122334455'})
    gateway.snapshot_ready.emit(other)
    gateway.command_completed.emit('GET_CONFIG', _ack('GET_CONFIG', result))
    assert vm.model.snapshot.identity['serial'] == other.identity['serial']
    assert vm.model.snapshot.config == other.config
    vm.shutdown()


def test_external_change_invalidates_pending_confirmation(qtbot, contract):
    vm, gateway, snapshot = setup_refresh(contract)
    vm.rename_profile(0, 'Local')
    vm.prepare_device_write()
    gateway.command_completed.emit('VALIDATE_CONFIG', _ack('VALIDATE_CONFIG'))
    result, status = changed_config(snapshot)
    gateway.status_updated.emit(status)
    assert vm.write_transaction.state is ConfigTransactionState.CONFLICT
    assert gateway.commands[-1].name == 'GET_CONFIG'
    with pytest.raises(ValueError):
        vm.confirm_device_write()
    gateway.command_completed.emit('GET_CONFIG', _ack('GET_CONFIG', result))
    assert vm.model.snapshot.config == result['config']
    assert not any(c.name == 'SET_CONFIG' for c in gateway.commands)
    vm.shutdown()
