from dataclasses import replace

import pytest
from PySide6.QtCore import Signal

from controller_config.models import AppState, PortCandidate
from controller_config.protocol.device_auth import DeviceTrustState
from controller_config.firmware_update import FirmwareUpdateState, FirmwareUpdateTransaction
from controller_config.firmware_release import RemoteFirmwareCheck, RemoteFirmwareState
from controller_config.transactions import ConfigTransaction, ConfigTransactionState
from controller_config.transport.device_gateway import DeviceGateway
from controller_config.transport.demo import _power_v2_snapshot
from controller_config.viewmodels.main import MainViewModel
from test_ble_transport import FakeGateway
from test_write_transaction import FakeWriteGateway


def test_usb_probe_keeps_ble_live_and_only_offers_same_serial(qtbot, contract):
    usb, ble = FakeGateway(), FakeGateway()
    gateway = DeviceGateway(contract, usb_gateway=usb, ble_gateway=ble)
    gateway.connect_port('ble:current')
    found, ordinary, status = [], [], []
    gateway.usb_candidate_found.connect(found.append)
    gateway.candidates_found.connect(ordinary.append)
    gateway.status_updated.connect(status.append)
    usb.candidates = (PortCandidate('cu.other', serial_number='OTHER'),)
    gateway.probe_usb('CURRENT')
    ble.status_updated.emit({'live': True})
    assert not found and not ordinary
    assert status == [{'live': True}]
    assert ble.shutdowns == 0
    usb.candidates += (PortCandidate('cu.current', serial_number='CURRENT'),)
    gateway.probe_usb('CURRENT')
    assert [c.port_name for c in found] == ['cu.current']
    assert gateway._active == 'ble'
    gateway.connect_port(found[0].port_name)
    assert ble.shutdowns == 1
    assert usb.connected == 'cu.current'


def test_manual_refresh_reuses_pending_usb_probe(qtbot, contract, monkeypatch):
    usb, ble = FakeGateway(), FakeGateway()
    gateway = DeviceGateway(contract, usb_gateway=usb, ble_gateway=ble)
    gateway.connect_port('ble:current')
    monkeypatch.setattr(usb, 'scan', lambda: None)
    ordinary, background = [], []
    gateway.candidates_found.connect(ordinary.append)
    gateway.usb_candidate_found.connect(background.append)
    gateway.probe_usb('CURRENT')
    gateway.scan()
    candidate = PortCandidate('cu.current', serial_number='CURRENT')
    usb.candidates_found.emit((candidate,))
    assert ordinary == [(candidate,)] and not background
    gateway.connect_port(candidate.port_name)
    assert ble.shutdowns == 1


class HandoffGateway(FakeWriteGateway):
    usb_candidate_found = Signal(object)

    def __init__(self):
        super().__init__()
        self.probes, self.connections = [], []

    def probe_usb(self, serial):
        self.probes.append(serial)

    def connect_port(self, port):
        self.connections.append(port)


@pytest.fixture
def handoff(qtbot, contract):
    gateway = HandoffGateway()
    vm = MainViewModel(gateway, contract)
    snapshot = replace(_power_v2_snapshot(contract, read_only=False), port_name='ble:current')
    snapshot = replace(snapshot, trust=replace(snapshot.trust, state=DeviceTrustState.AUTHENTICATED))
    gateway.snapshot_ready.emit(snapshot)
    # Fake transport has no replies for optional background readers.
    vm.screen_icon.attach(None)
    vm.screen_glyphs.attach(None)
    vm.ble_name.detach()
    vm.normal_agent.detach()
    yield vm, gateway, snapshot
    vm.shutdown()


def test_ble_ready_probes_usb_and_keeps_draft_on_handoff(handoff):
    vm, gateway, snapshot = handoff
    vm.rename_profile(snapshot.active_profile_id, 'Unsaved draft')
    draft = vm.draft
    vm._usb_probe_timer.timeout.emit()
    assert gateway.probes == [snapshot.identity['serial']]
    gateway.usb_candidate_found.emit(PortCandidate('cu.same', serial_number=snapshot.identity['serial']))
    assert gateway.connections == ['cu.same']
    assert vm.model.state is AppState.CONNECTING
    assert not vm._usb_probe_timer.isActive()
    gateway.snapshot_ready.emit(replace(snapshot, port_name='cu.same'))
    assert vm.model.state is AppState.READY
    assert vm.model.snapshot.connection_kind == 'usb'
    assert vm.draft is draft and vm.draft.is_dirty
    assert not vm._usb_probe_timer.isActive()
    assert not any(c.name == 'SET_CONFIG' for c in gateway.commands)


@pytest.mark.parametrize('busy', ['write', 'firmware', 'download'])
def test_usb_arrival_waits_for_active_transaction(handoff, busy):
    vm, gateway, snapshot = handoff
    if busy == 'write':
        vm._write_transaction = ConfigTransaction(state=ConfigTransactionState.WRITING)
    elif busy == 'firmware':
        vm._firmware_update = FirmwareUpdateTransaction(state=FirmwareUpdateState.TRANSFERRING)
    else:
        vm._remote_firmware = RemoteFirmwareCheck(state=RemoteFirmwareState.DOWNLOADING)
    vm._usb_probe_timer.timeout.emit()
    gateway.usb_candidate_found.emit(PortCandidate('cu.same', serial_number=snapshot.identity['serial']))
    assert not gateway.probes and not gateway.connections
    vm._write_transaction = ConfigTransaction()
    vm._firmware_update = FirmwareUpdateTransaction()
    vm._remote_firmware = RemoteFirmwareCheck()
    vm._usb_probe_timer.timeout.emit()
    assert gateway.probes == [snapshot.identity['serial']]


def test_usb_other_device_does_not_replace_ble_session(handoff):
    vm, gateway, snapshot = handoff
    gateway.usb_candidate_found.emit(PortCandidate('cu.other', serial_number='OTHER'))
    assert not gateway.connections
    assert vm.model.snapshot is snapshot
