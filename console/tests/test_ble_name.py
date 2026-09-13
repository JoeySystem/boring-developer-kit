from __future__ import annotations

import copy
from dataclasses import replace

import pytest

from controller_config.ble_name import BleNameSession
from controller_config.models import AppState, PortCandidate
from controller_config.protocol.bootstrap import BootstrapError, BootstrapKind
from controller_config.protocol.contract import (
    BLE_NAME_GET, BLE_NAME_SET, ContractError, validate_ble_name, validate_ble_name_response,
)
from controller_config.protocol.device_auth import DeviceTrust, DeviceTrustState
from controller_config.transport.demo import _power_v2_snapshot
from controller_config.viewmodels.main import MainViewModel
from test_write_transaction import FakeWriteGateway, _ack


def named_snapshot(contract, serial="WMP-001122334455", *, supported=True, readonly=False, authenticated=True):
    snapshot = _power_v2_snapshot(contract, read_only=readonly)
    capabilities = copy.deepcopy(snapshot.capabilities)
    capabilities['features'].pop('ble_name', None)
    capabilities['limits'].pop('ble_name_max_utf8_bytes', None)
    if supported:
        capabilities['features']['ble_name'] = True
        capabilities['limits']['ble_name_max_utf8_bytes'] = 24
    return replace(snapshot, identity={**snapshot.identity, 'serial': serial}, capabilities=capabilities,
                   trust=DeviceTrust(DeviceTrustState.AUTHENTICATED if authenticated else DeviceTrustState.DEVELOPMENT_UNAUTHENTICATED, 'test', '', serial=serial))


def result(saved='Boring Mist', active='Boring Mist'):
    return dict(saved_name=saved, active_name=active, default_name='Boring Mist', restart_required=saved != active)


@pytest.fixture
def naming(qtbot, contract):
    gateway = FakeWriteGateway()
    vm = MainViewModel(gateway, contract)
    snapshot = named_snapshot(contract)
    gateway.snapshot_ready.emit(snapshot)
    yield vm, gateway, snapshot
    vm.shutdown()


def readback(gateway, value=None):
    gateway.command_completed.emit('BLE_NAME_GET', _ack('BLE_NAME_GET', value or result()))


def test_shared_valid_names_and_exchanges(load_fixture):
    fixture = load_fixture('ble-name-v1.json')
    assert fixture['message_types'] == {'BLE_NAME_GET': BLE_NAME_GET, 'BLE_NAME_SET': BLE_NAME_SET}
    for name in fixture['valid_names']:
        validate_ble_name(name)
    for exchange in fixture['exchanges']:
        assert validate_ble_name_response(_ack(exchange['command'], exchange['result'])) == exchange['result']


def test_shared_invalid_names(load_fixture):
    for case in load_fixture('ble-name-v1.json')['invalid_requests']:
        if case['name'] == 'extra field':
            continue  # Session constructs exactly {'name': draft}; no caller-supplied payload.
        with pytest.raises(ValueError):
            validate_ble_name(case['payload'].get('name'))


@pytest.mark.parametrize('name', ['🎹'*6, '中文' * 4, 'Mix 键 🎹', 'A\u2003B'])
def test_utf8_boundaries_and_mixed_strings(name):
    validate_ble_name(name)
    with pytest.raises(ValueError):
        validate_ble_name('🎹' * 7)


def test_capabilities_requires_matching_limit(contract, load_fixture):
    payload = load_fixture('capabilities-v1.json')
    contract.validate_capabilities(payload)  # Legacy remains valid.
    payload['result']['features']['ble_name'] = True
    with pytest.raises(ContractError):
        contract.validate_capabilities(payload)
    payload['result']['limits']['ble_name_max_utf8_bytes'] = 24
    contract.validate_capabilities(payload)


@pytest.mark.parametrize('supported,authenticated', [(False, True), (True, False)])
def test_unsupported_or_unauthenticated_never_sends_new_command(qtbot, contract, supported, authenticated):
    gateway = FakeWriteGateway()
    vm = MainViewModel(gateway, contract)
    gateway.snapshot_ready.emit(named_snapshot(contract, supported=supported, authenticated=authenticated))
    assert not gateway.commands
    with pytest.raises(ValueError):
        vm.ble_name.save()
    with pytest.raises(ValueError):
        vm.ble_name.read()
    vm.shutdown()


def test_save_requires_ack_then_get_preserves_config(naming):
    vm, gateway, snapshot = naming
    assert gateway.commands[-1].name == 'BLE_NAME_GET'
    readback(gateway)
    config = copy.deepcopy(vm.draft.config)
    vm.ble_name.edit('办公室键盘')
    vm.ble_name.save()
    command = gateway.commands[-1]
    assert (command.name, command.message_type, command.payload, command.retries) == ('BLE_NAME_SET', 29, {'name': '办公室键盘'}, 0)
    gateway.command_completed.emit('BLE_NAME_SET', _ack('BLE_NAME_SET', result('办公室键盘')))
    assert gateway.commands[-1].name == 'BLE_NAME_GET'
    assert vm.ble_name.busy and vm.ble_name.result == result()  # ACK alone isn't success.
    readback(gateway, result('办公室键盘'))
    assert not vm.ble_name.dirty and '下次正常重启' in vm.ble_name.message
    assert vm.draft.config == config and not vm.draft.is_dirty
    count = len(gateway.commands)
    vm.ble_name.save()
    assert len(gateway.commands) == count  # No duplicate SET.
    gateway.disconnected.emit('test')
    gateway.snapshot_ready.emit(snapshot)
    readback(gateway, result('办公室键盘'))
    assert vm.ble_name.result['restart_required']  # A mere reconnect is not reboot.
    gateway.disconnected.emit('test')
    gateway.snapshot_ready.emit(snapshot)
    readback(gateway, result('办公室键盘', '办公室键盘'))
    assert not vm.ble_name.result['restart_required']
    vm.ble_name.restore_default()
    assert gateway.commands[-1].payload == {'name': 'Boring Mist'}
    assert not any(c.name in {'SET_CONFIG', 'FACTORY_DEFAULT', 'FW_BEGIN'} for c in gateway.commands)


@pytest.mark.parametrize('error_name', ['STORAGE_FAILURE', 'BUSY', 'VALIDATION_FAILED', 'INTERNAL'])
def test_save_failure_keeps_input_and_allows_explicit_retry(naming, error_name):
    vm, gateway, _ = naming
    readback(gateway)
    vm.ble_name.edit('Mine')
    vm.ble_name.save()
    gateway.command_failed.emit('BLE_NAME_SET', BootstrapError(BootstrapKind.READ_FAILED, 'failed', error_name=error_name))
    assert vm.ble_name.draft == 'Mine' and vm.ble_name.dirty and not vm.ble_name.busy
    assert '失败' in vm.ble_name.message
    vm.ble_name.save()
    assert gateway.commands[-1].name == 'BLE_NAME_SET'


@pytest.mark.parametrize('actually_saved', [True, False])
def test_lost_ack_reconnect_get_reconciles_without_resend(naming, actually_saved):
    vm, gateway, snapshot = naming
    readback(gateway)
    vm.ble_name.edit('Mine')
    vm.ble_name.save()
    gateway.command_failed.emit('BLE_NAME_SET', BootstrapError(BootstrapKind.READ_FAILED, 'timeout', error_name='TRANSPORT_TIMEOUT'))
    with pytest.raises(ValueError, match='待确认'):
        vm.ble_name.save()
    gateway.disconnected.emit('USB unplug')
    gateway.snapshot_ready.emit(snapshot)
    assert gateway.commands[-1].name == 'BLE_NAME_GET'
    readback(gateway, result('Mine' if actually_saved else 'Boring Mist'))
    assert vm.ble_name.draft == 'Mine'
    assert vm.ble_name.dirty is not actually_saved
    assert [c.name for c in gateway.commands].count('BLE_NAME_SET') == 1
    if not actually_saved:
        assert '不一致' in vm.ble_name.message


def test_device_switch_preserves_separate_name_and_mapping_drafts(naming, contract):
    vm, gateway, a = naming
    readback(gateway)
    vm.rename_profile(0, 'A config')
    vm.ble_name.edit('A name')
    gateway.disconnected.emit('A left')
    assert vm.ble_name.draft == 'A name' and not vm.ble_name.connected
    with pytest.raises(ValueError):
        vm.ble_name.save()
    b = named_snapshot(contract, 'WMP-556677889900')
    gateway.snapshot_ready.emit(b)
    readback(gateway)
    assert vm.ble_name.draft == 'Boring Mist' and not vm.ble_name.dirty
    vm.ble_name.edit('B name')
    gateway.disconnected.emit('B left')
    gateway.snapshot_ready.emit(a)
    readback(gateway)
    assert vm.ble_name.draft == 'A name'
    assert dict(vm.ble_name.pending_drafts()) == {a.identity['serial']: 'A name', b.identity['serial']: 'B name'}
    assert vm.draft.config['profiles'][0]['name'] == 'A config'


@pytest.mark.parametrize('invalid_result', [None, {}, result('', ''), {**result(), 'restart_required': True}])
def test_malformed_readback_cannot_claim_success(naming, invalid_result):
    vm, gateway, _ = naming
    gateway.command_completed.emit('BLE_NAME_GET', {'command': 'BLE_NAME_GET', 'result': invalid_result})
    assert vm.ble_name.result is None and '失败' in vm.ble_name.message
    assert not vm.ble_name.busy


def test_readback_mismatch_retains_input(naming):
    vm, gateway, _ = naming
    readback(gateway)
    vm.ble_name.edit('Mine')
    vm.ble_name.save()
    gateway.command_completed.emit('BLE_NAME_SET', _ack('BLE_NAME_SET', result('Mine')))
    readback(gateway)
    assert vm.ble_name.draft == 'Mine' and vm.ble_name.dirty
    assert '不一致' in vm.ble_name.message


def test_readonly_can_read_but_not_write(qtbot, contract):
    gateway = FakeWriteGateway()
    vm = MainViewModel(gateway, contract)
    gateway.snapshot_ready.emit(named_snapshot(contract, readonly=True))
    readback(gateway)
    vm.ble_name.edit('Mine')
    with pytest.raises(ValueError, match='只读'):
        vm.ble_name.save()
    assert all(c.name == 'BLE_NAME_GET' for c in gateway.commands)
    vm.shutdown()


def test_name_save_blocks_other_writes_until_readback(naming):
    vm, gateway, _ = naming
    readback(gateway)
    vm.ble_name.edit('Mine')
    vm.ble_name.save()
    for action in (vm.factory_reset_device, vm.prepare_device_write, vm.start_firmware_update, vm.start_joystick_calibration):
        with pytest.raises(ValueError, match='蓝牙名称'):
            action()
    assert gateway.commands[-1].name == 'BLE_NAME_SET'


def test_busy_firmware_blocks_name_save(naming):
    from controller_config.firmware_update import FirmwareUpdateState, FirmwareUpdateTransaction
    vm, gateway, _ = naming
    readback(gateway)
    vm.ble_name.edit('Mine')
    vm._firmware_update = FirmwareUpdateTransaction(state=FirmwareUpdateState.TRANSFERRING)
    with pytest.raises(ValueError, match='维护'):
        vm.ble_name.save()
    assert gateway.commands[-1].name == 'BLE_NAME_GET'


def test_same_name_candidates_keep_authenticated_serial_suffix(naming):
    vm, gateway, snapshot = naming
    readback(gateway)
    a = PortCandidate(snapshot.port_name, 'Same name', transport='bluetooth')
    b = PortCandidate('ble:unseen', 'Same name', transport='bluetooth')
    gateway.candidates_found.emit((a, b))
    assert len(vm.model.candidates) == 2
    first, second = vm.model.candidates
    assert first.serial_number == snapshot.identity['serial']
    assert snapshot.identity['serial'][-6:] in first.display_name
    assert '蓝牙' in first.display_name and 'Same name' in first.display_name
    assert not second.serial_number and second.display_name == 'Same name · 蓝牙'


@pytest.mark.parametrize('transport', ['usb', 'bluetooth'])
def test_wire_request_ack_readback_through_real_command_session(qtbot, contract, transport):
    """Mock only the device channel; use real encoding, queue, decoder and session."""
    from controller_config.protocol.framing import FrameDecoder, HEADER, RESPONSE_FLAG, encode_request
    from controller_config.transport.qt_serial import SerialWorker
    from controller_config.transport.ble import BleWorker

    class DeviceChannel:
        def __init__(self):
            self.outgoing = []
            self.incoming = b''

        def isOpen(self):
            return True

        def write(self, frame):
            self.outgoing.append(bytes(frame))
            return len(frame)

        def readAll(self):
            value, self.incoming = self.incoming, b''
            return value

    worker = SerialWorker(contract) if transport == 'usb' else BleWorker(contract)
    channel = DeviceChannel()
    worker._serial = channel
    gateway = FakeWriteGateway()
    gateway.execute_command = worker.execute_command
    worker.command_completed.connect(gateway.command_completed)
    worker.command_failed.connect(gateway.command_failed)
    vm = MainViewModel(gateway, contract)
    decoder = FrameDecoder(max_payload_bytes=contract.max_payload_bytes,
                           protocol_major=contract.protocol_major, protocol_minor=contract.protocol_minor)
    saved = 'Boring Mist'
    observed = []

    def serve_one():
        nonlocal saved
        request = decoder.feed(channel.outgoing.pop(0))[0]
        payload = request.json_payload()
        if request.message_type == BLE_NAME_SET:
            assert set(payload) == {'name'}
            validate_ble_name(payload['name'])
            saved = payload['name']
            command = 'BLE_NAME_SET'
        else:
            assert request.message_type == BLE_NAME_GET and payload == {}
            command = 'BLE_NAME_GET'
        observed.append((command, payload))
        raw = encode_request(protocol_major=contract.protocol_major, protocol_minor=contract.protocol_minor,
                             message_type=0x7E, request_id=request.request_id,
                             payload=_ack(command, result(saved)), max_payload_bytes=contract.max_payload_bytes)
        header = list(HEADER.unpack_from(raw))
        header[4] = RESPONSE_FLAG
        channel.incoming = HEADER.pack(*header) + raw[HEADER.size:]
        worker._on_ready_read()

    try:
        gateway.snapshot_ready.emit(named_snapshot(contract))
        serve_one()
        assert vm.ble_name.result == result()
        vm.ble_name.edit('中文 🎹')
        vm.ble_name.save()
        serve_one()
        assert vm.ble_name.result == result() and vm.ble_name.busy
        serve_one()
        assert vm.ble_name.result == result('中文 🎹') and not vm.ble_name.dirty
        assert observed == [('BLE_NAME_GET', {}), ('BLE_NAME_SET', {'name': '中文 🎹'}), ('BLE_NAME_GET', {})]
        assert worker._session is None and not worker._queued_commands
    finally:
        worker._serial = None
        vm.shutdown()


def test_usb_com_port_reuse_does_not_relabel_new_device_as_old_serial(naming):
    vm, gateway, snapshot = naming
    readback(gateway)
    gateway.disconnected.emit('A unplugged')
    newcomer = PortCandidate(snapshot.port_name, 'Boring USB', serial_number='WMP-AABBCCDDEEFF')
    other = PortCandidate('COM6', 'Boring USB', serial_number='WMP-112233445566')
    gateway.candidates_found.emit((newcomer, other))
    assert vm.model.candidates[0].serial_number == 'WMP-AABBCCDDEEFF'
    assert 'DDEEFF' in vm.model.candidates[0].display_name


@pytest.mark.parametrize('language', ['en_US', 'ja_JP'])
def test_candidate_suffix_translation_keeps_custom_name_literal(language):
    from controller_config.text_catalog import TextCatalog
    catalog = TextCatalog.load()
    candidate = PortCandidate('ble:one', '保存名称 · 蓝牙', serial_number='WMP-AABBCCDDEEFF', transport='bluetooth')
    assert catalog.translate(candidate.display_name, language) == '保存名称 · 蓝牙 · Bluetooth · DDEEFF'
