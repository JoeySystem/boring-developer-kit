"""Authenticated config failures can update signed firmware without config access."""
from dataclasses import replace
import json
import pytest
from PySide6.QtWidgets import QLabel, QPushButton, QWidget
from controller_config.firmware_release import FirmwareReleaseError
from controller_config.firmware_update import FirmwarePackageError, FirmwareUpdateState
from controller_config.models import AppState
from controller_config.protocol.bootstrap import BootstrapSession
from controller_config.protocol.device_auth import DeviceAuthenticator, DeviceTrust, DeviceTrustState, TrustBundle, TrustPolicy
from controller_config.transport.demo import _power_v2_snapshot
from controller_config.viewmodels.main import MainViewModel
from controller_config.views.main_window import MainWindow
from test_bootstrap import _ack
from test_firmware_release import FakeReleaseSource, _bundle, _release, trust_test_signing_key
from test_firmware_transaction import FirmwareGateway, _package, _status, _booted_status


def close_test_vm(vm):
    from controller_config.transactions import ConfigTransaction
    from controller_config.joystick_calibration import CalibrationTransaction
    from controller_config.firmware_update import FirmwareUpdateTransaction
    vm._write_transaction = ConfigTransaction()
    vm._calibration = CalibrationTransaction()
    vm._firmware.replace_silently(FirmwareUpdateTransaction())
    vm.pending_dirty_workspaces = lambda: ()
    vm.shutdown()


def maintenance_snapshot(contract):
    base = _power_v2_snapshot(contract, read_only=True)
    return replace(base, config_result={}, status={}, maintenance_reason='GET_CONFIG timeout',
        port_name='/dev/cu.usbmodem-maintenance',
        trust=DeviceTrust(DeviceTrustState.AUTHENTICATED, '设备已认证', '', serial=base.identity['serial']))


def test_verified_bootstrap_config_timeout_to_signed_update_and_reconnect(qtbot, contract, load_fixture, tmp_path, trust_test_signing_key):
    gateway = FirmwareGateway()
    vm = MainViewModel(gateway, contract)
    package = vm.load_firmware_package(_package(tmp_path, contract))
    auth = load_fixture('device-auth-v1.json')
    certificate = auth['certificate_response']['result']['certificate']
    verifier = DeviceAuthenticator(TrustBundle.from_document(
        {'version': 1, 'roots': [{**auth['test_root'], 'purpose': 'production'}]},
        policy=TrustPolicy.PRODUCTION), nonce_factory=lambda: bytes(range(32)))
    hello = load_fixture('hello-v1.json')['ack']
    hello['result']['identity'].update({k: certificate[k] for k in ('product_id', 'hardware_id', 'serial')})
    caps = load_fixture('capabilities-matrix12-power-v2-v1.json')
    caps['result']['features'].update(device_authentication=True, firmware_update=True)
    caps['result']['limits'].update(firmware_chunk_bytes=4096, firmware_image_bytes=2*1024*1024)
    status = load_fixture('status-matrix12-power-v2-v1.json')
    hello['result']['config'] = dict(status['result']['active'])
    sent, failures = [], []
    session = BootstrapSession(contract=contract, port_name='/dev/cu.usbmodem-maintenance',
        usb_serial=certificate['serial'], authenticator=verifier,
        send=lambda command, request_id: sent.append((command, request_id)),
        completed=gateway.snapshot_ready.emit, failed=failures.append)
    session.start()
    for response in (hello, caps, auth['certificate_response'], auth['challenge_response'], status):
        session.accept(_ack(sent[-1][1], response))
    assert sent[-1][0].name == 'GET_CONFIG'
    session.timeout()
    assert sent[-1][0].name == 'FW_STATUS'
    probe = _status(package, 'IDLE')
    probe['result']['firmware_update']['running_version'] = hello['result']['versions']['firmware']
    session.accept(_ack(sent[-1][1], probe))
    assert not failures
    assert vm.model.state is AppState.FIRMWARE_MAINTENANCE
    assert vm.model.snapshot.trust.is_authenticated
    assert vm.draft is None and vm.page == 'firmware'
    assert gateway.commands == []
    with pytest.raises(ValueError, match='仅可维护固件'):
        vm.navigate('overview')
    vm.start_firmware_update()
    gateway.command_completed.emit('FW_STATUS', _status(package, 'IDLE'))
    assert gateway.commands[-1].name == 'FW_BEGIN'
    gateway.command_completed.emit('FW_BEGIN', {'command':'FW_BEGIN','result':{
        'state':'RECEIVING','offset':0,'chunk_bytes':4096,'target_partition':'ota_1'}})
    gateway.command_completed.emit('FW_DATA', {'command':'FW_DATA','result':{
        'state':'RECEIVING','received_size':package.size,'expected_size':package.size}})
    gateway.command_completed.emit('FW_END', {'command':'FW_END','result':{
        'state':'READY_TO_REBOOT','version':package.version,'reboot_scheduled':True}})
    assert vm.firmware_update.state is FirmwareUpdateState.WAITING_RECONNECT
    old = vm.model.snapshot
    gateway.disconnected.emit('reboot')
    gateway.snapshot_ready.emit(replace(old, versions={**old.versions,'firmware':package.version,'build_id':package.build_id}))
    assert vm.firmware_update.state is FirmwareUpdateState.VERIFYING
    gateway.command_completed.emit('FW_STATUS', _booted_status(package))
    assert vm.firmware_update.state is FirmwareUpdateState.COMPLETED
    assert vm.model.state is AppState.FIRMWARE_MAINTENANCE
    assert {c.name for c in gateway.commands} <= {'FW_STATUS','FW_BEGIN','FW_DATA','FW_END','FW_ABORT'}
    vm.shutdown()


def test_maintenance_ui_exposes_official_file_and_blocks_other_workspaces(qtbot, contract, tmp_path, trust_test_signing_key):
    gateway = FirmwareGateway()
    vm = MainViewModel(gateway, contract)
    gateway.snapshot_ready.emit(maintenance_snapshot(contract))
    window = MainWindow(vm)
    qtbot.addWidget(window, before_close_func=lambda _: close_test_vm(vm))
    window.show()
    official = window.findChild(QPushButton, 'selectFirmwarePackage')
    assert official.isVisible() and official.isEnabled()
    assert window.findChild(QLabel, 'firmwareMaintenanceNotice').isVisible()
    # 仅固件维护 sits at the top of the firmware page; the top bar keeps only the trust state (design ticket B)
    title = window.findChild(QLabel, 'firmwareMaintenanceTitle')
    assert title.isVisible() and title.text() == '仅固件维护'
    banner = window.findChild(QWidget, 'firmwareMaintenanceBanner')
    focus = window.findChild(QWidget, 'firmwareUpdateFocusCard')
    # 方块墙 (design decision): 仅固件维护 is the first tile on the right, level with 当前固件 in the top row.
    assert banner.mapTo(window, banner.rect().topLeft()).y() <= focus.mapTo(window, focus.rect().topLeft()).y()
    assert banner.mapTo(window, banner.rect().topLeft()).x() > focus.mapTo(window, focus.rect().topLeft()).x()
    assert window._device_auth_summary.text() in {'已认证', '开发设备 · 未认证'}
    assert window.findChild(QPushButton, 'prepareOfficialRecovery').isEnabled()
    assert not window._nav_buttons['overview'].isEnabled()
    assert not window._nav_buttons['prompts'].isEnabled()
    assert not window.findChild(QPushButton, 'selectCustomFirmwarePackage').isEnabled()
    vm.load_firmware_package(_package(tmp_path, contract))
    install = window.findChild(QPushButton, 'startFirmwareUpdate')
    assert install.isEnabled()
    focus = window.findChild(QWidget,'firmwareUpdateFocusCard')
    assert focus.isAncestorOf(install)
    assert window.findChild(QLabel,'firmwareTargetVersion') is not None
    for button in window.findChildren(QPushButton, 'settingsGroup'):
        if button.property('settingsSection') not in {'updates', 'firmware'}:
            assert not button.isEnabled()
    assert gateway.commands == []


def test_maintenance_import_keeps_signature_and_hardware_checks(qtbot, contract, tmp_path, trust_test_signing_key):
    gateway = FirmwareGateway()
    vm = MainViewModel(gateway, contract)
    snapshot = maintenance_snapshot(contract)
    gateway.snapshot_ready.emit(snapshot)
    path = _package(tmp_path, contract)
    document = json.loads(path.read_text(encoding='utf-8'))
    signed = path.read_text(encoding='utf-8')
    document.pop('signature')
    path.write_text(json.dumps(document), encoding='utf-8')
    with pytest.raises(FirmwareReleaseError, match='签名'):
        vm.load_firmware_package(path)
    assert vm.firmware_update.package is None and gateway.commands == []
    path.write_text(signed, encoding='utf-8')
    gateway.snapshot_ready.emit(replace(snapshot, identity={**snapshot.identity,'hardware_id':'OTHER'}))
    with pytest.raises(FirmwarePackageError, match='当前设备'):
        vm.load_firmware_package(path)
    assert gateway.commands == []
    vm.shutdown()


def test_maintenance_online_check_and_download_do_not_require_configuration(qtbot, contract, trust_test_signing_key):
    gateway, source = FirmwareGateway(), FakeReleaseSource()
    vm = MainViewModel(gateway, contract, firmware_release_source=source)
    gateway.snapshot_ready.emit(maintenance_snapshot(contract))
    vm.check_remote_firmware()
    bundle = _bundle(contract)
    source.release_found.emit(_release(bundle))
    vm.download_remote_firmware()
    source.download_completed.emit(bundle)
    assert vm.firmware_update.state is FirmwareUpdateState.PACKAGE_READY
    assert vm.firmware_update.package.source_kind == 'official'
    vm.start_firmware_update()
    assert gateway.commands[-1].name == 'FW_STATUS'
    vm.shutdown()


def test_bluetooth_local_import_waits_for_usb_before_confirmation(qtbot, contract, tmp_path, trust_test_signing_key):
    gateway = FirmwareGateway()
    vm = MainViewModel(gateway, contract)
    snapshot = replace(_power_v2_snapshot(contract,read_only=False),port_name='ble:test')
    gateway.snapshot_ready.emit(snapshot)
    vm.load_firmware_package(_package(tmp_path, contract))
    vm.navigate('firmware')
    window = MainWindow(vm)
    qtbot.addWidget(window,before_close_func=lambda _: close_test_vm(vm))
    window.show()
    button = window.findChild(QPushButton,'startFirmwareUpdate')
    assert not button.isEnabled() and 'USB' in button.text()
    assert gateway.commands == []
    gateway.snapshot_ready.emit(replace(snapshot,port_name='/dev/cu.usbmodem-same-device'))
    assert window.findChild(QPushButton,'startFirmwareUpdate').isEnabled()
    assert vm.firmware_update.package is not None


def test_maintenance_abort_and_different_reconnected_device(qtbot, contract, tmp_path, trust_test_signing_key):
    gateway = FirmwareGateway()
    vm = MainViewModel(gateway, contract)
    snapshot = maintenance_snapshot(contract)
    gateway.snapshot_ready.emit(snapshot)
    package = vm.load_firmware_package(_package(tmp_path, contract))
    vm.start_firmware_update()
    vm.abort_firmware_update()
    assert vm.firmware_update.state is FirmwareUpdateState.ABORTING
    gateway.command_completed.emit('FW_STATUS', _status(package, 'IDLE'))
    assert vm.firmware_update.state is FirmwareUpdateState.PACKAGE_READY
    vm.start_firmware_update()
    before = len(gateway.commands)
    gateway.snapshot_ready.emit(replace(snapshot,identity={**snapshot.identity,'serial':'CP01-001122334455'}))
    assert vm.firmware_update.state is FirmwareUpdateState.FAILED
    assert '另一台设备' in vm.firmware_update.message
    assert len(gateway.commands)==before
    vm.shutdown()


def test_maintenance_never_accepts_custom_or_unauthenticated_package(qtbot, contract, tmp_path, trust_test_signing_key):
    from controller_config.firmware_release import load_local_firmware_package
    package = load_local_firmware_package(_package(tmp_path,contract),contract)
    snapshot = maintenance_snapshot(contract)
    with pytest.raises(FirmwarePackageError,match='官方签名'):
        replace(package,source_kind='custom').validate_for_device(snapshot)
    untrusted = replace(snapshot,trust=DeviceTrust(DeviceTrustState.DEVELOPMENT_UNAUTHENTICATED,'DEV',''))
    with pytest.raises(FirmwarePackageError,match='已认证'):
        package.validate_for_device(untrusted)


@pytest.mark.parametrize('unresolved', ['write', 'calibration'])
def test_unresolved_configuration_does_not_block_signed_recovery(qtbot, contract, tmp_path, trust_test_signing_key, unresolved):
    from controller_config.joystick_calibration import CalibrationState
    from controller_config.transactions import ConfigTransactionState
    from test_session_recovery import _disconnect_during_write
    gateway, source = FirmwareGateway(), FakeReleaseSource()
    vm = MainViewModel(gateway, contract, firmware_release_source=source)
    gateway.snapshot_ready.emit(_power_v2_snapshot(contract, read_only=False))
    if unresolved == 'write':
        _disconnect_during_write(vm,gateway)
        assert vm.write_transaction.state is ConfigTransactionState.UNKNOWN
    else:
        vm._calibration = replace(vm.calibration,state=CalibrationState.UNKNOWN)
        vm._calibration_sample_timer.start(60000)
        vm._calibration_activation_deadline.start(60000)
    gateway.snapshot_ready.emit(maintenance_snapshot(contract))
    assert not vm._write_deadline.isActive()
    assert not vm._calibration_sample_timer.isActive()
    assert not vm._calibration_activation_deadline.isActive()
    window=MainWindow(vm)
    qtbot.addWidget(window,before_close_func=lambda _:close_test_vm(vm))
    window.show()
    assert window.findChild(QPushButton,'prepareOfficialRecovery').isEnabled()
    assert window.findChild(QPushButton,'checkRemoteFirmware').isEnabled()
    vm.check_remote_firmware()
    assert source.snapshots
    source.failed.emit('offline test')
    vm.load_firmware_package(_package(tmp_path,contract))
    assert window.findChild(QPushButton,'startFirmwareUpdate').isEnabled()
    vm.start_firmware_update()
    assert gateway.commands[-1].name == 'FW_STATUS'
    if unresolved=='write':
        assert vm.write_transaction.state is ConfigTransactionState.UNKNOWN
        exported=tmp_path/'unresolved-after-maintenance.json'
        vm.export_configuration(exported,kind='draft')
        document=json.loads(exported.read_text(encoding='utf-8'))
        assert document['unresolved_write']['state']=='unknown'
        assert document['config']==vm.write_transaction.candidate_config
        assert not window._configuration_blocks_close()
    else:
        assert vm.calibration.state is CalibrationState.UNKNOWN


def test_needs_abort_never_targets_a_different_connected_device(qtbot, contract, tmp_path, trust_test_signing_key):
    gateway=FirmwareGateway()
    vm=MainViewModel(gateway,contract)
    first=maintenance_snapshot(contract)
    gateway.snapshot_ready.emit(first)
    package=vm.load_firmware_package(_package(tmp_path,contract))
    vm.start_firmware_update()
    status=_status(package,'RECEIVING',128)
    status['result']['firmware_update']['expected_sha256']='0'*64
    gateway.command_completed.emit('FW_STATUS',status)
    assert vm.firmware_update.state is FirmwareUpdateState.NEEDS_ABORT
    before=len(gateway.commands)
    second=replace(first,identity={**first.identity,'serial':'CP01-001122334455'})
    gateway.snapshot_ready.emit(second)
    assert vm.firmware_update.state is FirmwareUpdateState.FAILED
    assert len(gateway.commands)==before
    with pytest.raises(ValueError):
        vm.abort_firmware_update()
    assert len(gateway.commands)==before
    vm.shutdown()
