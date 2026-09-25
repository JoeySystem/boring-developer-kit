from __future__ import annotations

import copy
from dataclasses import replace

import pytest

from controller_config.normal_agent import NORMAL_AGENT_BEHAVIOR_GET, NORMAL_AGENT_BEHAVIOR_SET
from controller_config.protocol.bootstrap import BootstrapError, BootstrapKind
from controller_config.protocol.device_auth import DeviceTrust, DeviceTrustState
from controller_config.transport.demo import _power_v2_snapshot
from controller_config.viewmodels.main import MainViewModel
from test_write_transaction import FakeWriteGateway, _ack


GET = 'NORMAL_AGENT_BEHAVIOR_GET'
SET = 'NORMAL_AGENT_BEHAVIOR_SET'


def device(contract, serial='NORMAL-DEVICE-A', *, supported=True, authenticated=True, readonly=False):
    snapshot = copy.deepcopy(_power_v2_snapshot(contract, read_only=readonly))
    snapshot.capabilities['features'].update(
        normal_agent_key_behavior=supported, codex_agent_focus=True, ble_name=False, host_action_usb=False)
    snapshot.status['operating_mode'] = 'normal'
    snapshot.status['codex_agent_press'] = {'sequence': 0, 'agent': None, 'transport': None}
    return replace(snapshot, identity={**snapshot.identity, 'serial': serial},
                   trust=DeviceTrust(DeviceTrustState.AUTHENTICATED if authenticated else
                                     DeviceTrustState.DEVELOPMENT_UNAUTHENTICATED, 'test', '', serial=serial))


def readback(gateway, behavior=None, *, status=None):
    gateway.command_completed.emit(GET, _ack(GET, {'behavior': behavior}))
    if behavior == 'open_conversation':
        gateway.command_completed.emit('GET_STATUS', _ack('GET_STATUS', status or gateway.normal_status))


def fail(gateway, command, name='TRANSPORT_TIMEOUT'):
    gateway.command_failed.emit(command, BootstrapError(BootstrapKind.READ_FAILED, 'failed', error_name=name))


def press(snapshot, sequence, agent=0):
    return {**snapshot.status, 'codex_agent_press': {'sequence': sequence, 'agent': agent, 'transport': 'usb'}}


@pytest.fixture
def connected(qtbot, contract):
    gateway = FakeWriteGateway()
    vm = MainViewModel(gateway, contract)
    snapshot = device(contract)
    gateway.normal_status = snapshot.status
    gateway.snapshot_ready.emit(snapshot)
    yield vm, gateway, snapshot
    vm.shutdown()


@pytest.mark.parametrize('supported,authenticated', [(False, True), (True, False)])
def test_unsupported_or_unauthenticated_never_sends_preference_commands(qtbot, contract, supported, authenticated):
    gateway = FakeWriteGateway()
    vm = MainViewModel(gateway, contract)
    try:
        gateway.snapshot_ready.emit(device(contract, supported=supported, authenticated=authenticated))
        assert not gateway.commands
        for operation in (vm.normal_agent.read, vm.normal_agent.save):
            with pytest.raises(ValueError):
                operation()
    finally:
        vm.shutdown()


def test_set_ack_requires_independent_readback_and_preserves_config(connected):
    vm, gateway, _ = connected
    assert [c.name for c in gateway.commands] == [GET]
    readback(gateway)
    before = copy.deepcopy(vm.draft.config)
    vm.normal_agent.edit('open_conversation')
    vm.normal_agent.save()
    command = gateway.commands[-1]
    assert (command.name, command.message_type, command.payload, command.retries) == (
        SET, 0x2B, {'behavior': 'open_conversation'}, 0)
    gateway.command_completed.emit(SET, _ack(SET, {'behavior': 'open_conversation'}))
    assert gateway.commands[-1].name == GET
    assert vm.normal_agent.busy and vm.normal_agent.value is None
    assert vm.normal_agent.message != '已应用'
    readback(gateway, 'open_conversation')
    assert vm.normal_agent.value == 'open_conversation'
    assert not vm.normal_agent.dirty and vm.normal_agent.message == '已应用'
    assert vm.draft.config == before and not vm.draft.is_dirty
    count = len(gateway.commands)
    vm.normal_agent.save()
    assert len(gateway.commands) == count


@pytest.mark.parametrize('error_name', ['BUSY', 'VALIDATION_FAILED', 'STORAGE_FAILURE', 'INTERNAL', 'UNSUPPORTED_COMMAND'])
def test_rejected_write_retains_choice_and_allows_retry(connected, error_name):
    vm, gateway, _ = connected
    readback(gateway, 'status_only')
    vm.normal_agent.edit('open_conversation')
    vm.normal_agent.save()
    fail(gateway, SET, error_name)
    assert vm.normal_agent.value == 'status_only'
    assert vm.normal_agent.draft == 'open_conversation'
    assert vm.normal_agent.dirty and not vm.normal_agent.busy and not vm.normal_agent.uncertain
    assert gateway.commands[-1].name == SET
    vm.normal_agent.save()
    assert [c.name for c in gateway.commands].count(SET) == 2


@pytest.mark.parametrize('saved', [True, False])
def test_lost_ack_readback_decides_success_without_resending(connected, saved):
    vm, gateway, _ = connected
    readback(gateway, 'status_only')
    vm.normal_agent.edit('open_conversation')
    vm.normal_agent.save()
    fail(gateway, SET)
    assert gateway.commands[-1].name == GET and vm.normal_agent.busy
    readback(gateway, 'open_conversation' if saved else 'status_only')
    assert vm.normal_agent.dirty is not saved
    assert vm.normal_agent.draft == 'open_conversation'
    assert [c.name for c in gateway.commands].count(SET) == 1
    if not saved:
        assert '不一致' in vm.normal_agent.message


def test_failed_confirmation_stays_uncertain_until_reconnect_get(connected):
    vm, gateway, snapshot = connected
    readback(gateway, 'status_only')
    vm.normal_agent.edit('open_conversation')
    vm.normal_agent.save()
    fail(gateway, SET)
    fail(gateway, GET)
    assert vm.normal_agent.uncertain and not vm.normal_agent.loaded
    with pytest.raises(ValueError, match='待确认'):
        vm.normal_agent.save()
    gateway.disconnected.emit('unplugged')
    gateway.snapshot_ready.emit(snapshot)
    readback(gateway, 'open_conversation')
    assert vm.normal_agent.value == vm.normal_agent.draft == 'open_conversation'
    assert not vm.normal_agent.dirty and not vm.normal_agent.uncertain
    assert [c.name for c in gateway.commands].count(SET) == 1


@pytest.mark.parametrize('invalid', [None, {}, {'behavior': 'invalid'}, {'behavior': False}, {'behavior': []}])
def test_malformed_get_never_confirms_success(connected, invalid):
    vm, gateway, _ = connected
    gateway.command_completed.emit(GET, {'command': GET, 'result': invalid})
    assert not vm.normal_agent.loaded and not vm.normal_agent.busy
    assert '失败' in vm.normal_agent.message


def test_device_switch_preserves_independent_unsaved_choices(connected, contract):
    vm, gateway, a = connected
    readback(gateway)
    vm.rename_profile(0, 'Keep my mapping draft')
    vm.normal_agent.edit('open_conversation')
    gateway.disconnected.emit('a unplugged')
    with pytest.raises(ValueError):
        vm.normal_agent.save()
    gateway.snapshot_ready.emit(device(contract, serial='NORMAL-DEVICE-B'))
    readback(gateway)
    assert vm.normal_agent.draft is None
    vm.normal_agent.edit('status_only')
    gateway.disconnected.emit('b unplugged')
    gateway.snapshot_ready.emit(a)
    readback(gateway)
    assert vm.normal_agent.draft == 'open_conversation'
    assert dict(vm.normal_agent.pending_drafts()) == {'NORMAL-DEVICE-A': 'open_conversation', 'NORMAL-DEVICE-B': 'status_only'}
    assert vm.draft.config['profiles'][0]['name'] == 'Keep my mapping draft'


def test_readonly_reads_but_cannot_write(qtbot, contract):
    gateway = FakeWriteGateway()
    vm = MainViewModel(gateway, contract)
    try:
        gateway.snapshot_ready.emit(device(contract, readonly=True))
        readback(gateway, 'status_only')
        vm.normal_agent.edit('open_conversation')
        with pytest.raises(ValueError, match='只读'):
            vm.normal_agent.save()
        assert [c.name for c in gateway.commands] == [GET]
    finally:
        vm.shutdown()


def test_preference_write_blocks_other_device_writes_until_confirmed(connected):
    vm, gateway, _ = connected
    readback(gateway)
    vm.normal_agent.edit('open_conversation')
    vm.normal_agent.save()
    for action in (vm.factory_reset_device, vm.prepare_device_write, vm.start_firmware_update, vm.start_joystick_calibration):
        with pytest.raises(ValueError, match='状态灯'):
            action()
    assert gateway.commands[-1].name == SET


def test_device_maintenance_blocks_preference_write(connected):
    from controller_config.firmware_update import FirmwareUpdateState, FirmwareUpdateTransaction
    vm, gateway, _ = connected
    readback(gateway)
    vm.normal_agent.edit('open_conversation')
    vm._firmware_update = FirmwareUpdateTransaction(state=FirmwareUpdateState.TRANSFERRING)
    with pytest.raises(ValueError, match='维护'):
        vm.normal_agent.save()
    assert [c.name for c in gateway.commands] == [GET]


def test_vm_normal_focus_requires_get_and_no_replay_after_reconnect(connected):
    vm, gateway, snapshot = connected
    activated = []
    vm.codex_agent_focus.requested.connect(activated.append)
    gateway.status_updated.emit(press(snapshot, 1))
    assert not activated
    readback(gateway, 'open_conversation', status=press(snapshot, 1))
    gateway.status_updated.emit(press(snapshot, 1))  # Identical status still drains baseline.
    gateway.status_updated.emit(press(snapshot, 2, 4))
    assert activated == [4]
    gateway.disconnected.emit('unplugged')
    gateway.snapshot_ready.emit(replace(snapshot, status=press(snapshot, 2, 4)))
    gateway.status_updated.emit(press(snapshot, 3, 2))
    assert activated == [4]
    readback(gateway, 'open_conversation', status=press(snapshot, 3, 2))
    gateway.status_updated.emit(press(snapshot, 3, 2))
    gateway.status_updated.emit(press(snapshot, 4, 5))
    assert activated == [4, 5]
    vm.normal_agent.edit('status_only')
    vm.normal_agent.save()
    gateway.status_updated.emit(press(snapshot, 5))
    gateway.command_completed.emit(SET, _ack(SET, {'behavior': 'status_only'}))
    readback(gateway, 'status_only')
    gateway.status_updated.emit(press(snapshot, 6))
    assert activated == [4, 5]


def test_open_is_not_ready_until_explicit_status_baseline_and_first_press_works(connected):
    vm, gateway, snapshot = connected
    activated = []
    vm.codex_agent_focus.requested.connect(activated.append)
    readback(gateway, 'status_only')
    vm.normal_agent.edit('open_conversation')
    vm.normal_agent.save()
    gateway.command_completed.emit(SET, _ack(SET, {'behavior': 'open_conversation'}))
    gateway.command_completed.emit(GET, _ack(GET, {'behavior': 'open_conversation'}))
    assert gateway.commands[-1].name == 'GET_STATUS'
    assert vm.normal_agent.preparing and vm.normal_agent.busy and not vm.normal_agent.loaded
    assert vm.normal_agent.message != '已应用'
    with pytest.raises(ValueError):
        vm.normal_agent.save()
    gateway.command_completed.emit('GET_STATUS', _ack('GET_STATUS', press(snapshot, 7)))
    assert vm.normal_agent.loaded and not vm.normal_agent.busy
    assert vm.normal_agent.message == '已应用' and activated == []
    gateway.status_updated.emit(press(snapshot, 8, 3))
    assert activated == [3]
    vm.normal_agent.edit('status_only')
    assert vm.normal_agent.message == '尚未应用到设备'
    vm.normal_agent.edit('open_conversation')
    assert vm.normal_agent.message == ''


@pytest.mark.parametrize('invalid', [False, True])
def test_status_preparation_failure_offers_read_retry_without_rewrite(connected, invalid):
    vm, gateway, snapshot = connected
    gateway.command_completed.emit(GET, _ack(GET, {'behavior': 'open_conversation'}))
    if invalid:
        gateway.command_completed.emit('GET_STATUS', _ack('GET_STATUS', {}))
    else:
        fail(gateway, 'GET_STATUS')
    assert not vm.normal_agent.loaded and not vm.normal_agent.busy
    assert vm.normal_agent.message == '跳转准备失败，请重新读取'
    activated = []
    vm.codex_agent_focus.requested.connect(activated.append)
    gateway.status_updated.emit(press(snapshot, 1))
    assert activated == []
    vm.normal_agent.read()
    readback(gateway, 'open_conversation', status=press(snapshot, 1))
    gateway.status_updated.emit(press(snapshot, 2))
    assert activated == [0]
    assert all(command.name != SET for command in gateway.commands)


def test_codex_status_preparation_does_not_swallow_first_normal_press(connected):
    vm, gateway, snapshot = connected
    activated = []
    vm.codex_agent_focus.requested.connect(activated.append)
    readback(gateway, 'open_conversation', status={**snapshot.status, 'operating_mode': 'codex'})
    assert vm.normal_agent.loaded
    gateway.status_updated.emit(press(snapshot, 1, 5))
    assert activated == [5]


def test_disconnect_cancels_preparation_and_late_status_cannot_complete_it(connected):
    vm, gateway, snapshot = connected
    gateway.command_completed.emit(GET, _ack(GET, {'behavior': 'open_conversation'}))
    assert vm.normal_agent.preparing
    gateway.disconnected.emit('unplugged during status read')
    gateway.command_completed.emit('GET_STATUS', _ack('GET_STATUS', snapshot.status))
    assert not vm.normal_agent.connected and not vm.normal_agent.loaded
    assert not vm.normal_agent.preparing and not vm._normal_agent_status_refresh


@pytest.mark.parametrize('transport', ['usb', 'bluetooth'])
def test_wire_set_ack_get_through_real_transport_session(qtbot, contract, transport):
    from controller_config.protocol.framing import FrameDecoder, HEADER, RESPONSE_FLAG, encode_request
    from controller_config.transport.qt_serial import SerialWorker
    from controller_config.transport.ble import BleWorker

    class DeviceChannel:
        def __init__(self):
            self.outgoing, self.incoming = [], b''

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
    saved, observed = None, []

    def serve():
        nonlocal saved
        request = decoder.feed(channel.outgoing.pop(0))[0]
        payload = request.json_payload()
        if request.message_type == NORMAL_AGENT_BEHAVIOR_SET:
            assert payload == {'behavior': 'open_conversation'}
            saved = payload['behavior']
            command = SET
        elif request.message_type == NORMAL_AGENT_BEHAVIOR_GET:
            assert request.message_type == NORMAL_AGENT_BEHAVIOR_GET and payload == {}
            command = GET
        else:
            assert request.message_type == 0x13 and payload == {}
            command = 'GET_STATUS'
        observed.append((command, payload))
        raw = encode_request(protocol_major=contract.protocol_major, protocol_minor=contract.protocol_minor,
                             message_type=0x7E, request_id=request.request_id,
                             payload=_ack(command, snapshot.status if command == 'GET_STATUS' else {'behavior': saved}), max_payload_bytes=contract.max_payload_bytes)
        header = list(HEADER.unpack_from(raw))
        header[4] = RESPONSE_FLAG
        channel.incoming = HEADER.pack(*header) + raw[HEADER.size:]
        worker._on_ready_read()

    try:
        snapshot = device(contract)
        if transport == 'bluetooth':
            snapshot = replace(snapshot, port_name='ble:normal-test')
        gateway.snapshot_ready.emit(snapshot)
        serve()
        assert vm.normal_agent.loaded and vm.normal_agent.value is None
        vm.normal_agent.edit('open_conversation')
        vm.normal_agent.save()
        serve()
        assert vm.normal_agent.busy and vm.normal_agent.value is None
        serve()
        assert vm.normal_agent.preparing and not vm.normal_agent.loaded
        serve()
        assert vm.normal_agent.value == 'open_conversation' and not vm.normal_agent.dirty
        assert vm.normal_agent.loaded and not vm.normal_agent.busy
        assert observed == [(GET, {}), (SET, {'behavior': 'open_conversation'}), (GET, {}), ('GET_STATUS', {})]
        assert worker._session is None and not worker._queued_commands
    finally:
        worker._serial = None
        vm.shutdown()
