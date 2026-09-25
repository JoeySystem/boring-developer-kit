"""Source classification and explicit recovery use the existing OTA pipeline."""
from dataclasses import replace
import pytest
from PySide6.QtCore import QSettings
from controller_config.firmware_release import RemoteFirmwareState
from controller_config.transport.demo import _power_v2_snapshot
from controller_config.viewmodels.main import MainViewModel
from test_firmware_release import FakeReleaseSource, _bundle, _release, trust_test_signing_key
from test_firmware_transaction import FirmwareGateway


def test_unmarked_build_is_not_an_automatic_official_update(qtbot, contract):
    source = FakeReleaseSource()
    vm = MainViewModel(FirmwareGateway(), contract, firmware_release_source=source)
    snapshot = _power_v2_snapshot(contract, read_only=False)
    snapshot = replace(snapshot, versions={**snapshot.versions, 'build_id':'local-unidentified'})
    vm._gateway.snapshot_ready.emit(snapshot)
    assert not source.snapshots
    assert vm.firmware_origin == 'unknown'
    vm.shutdown()


@pytest.mark.parametrize('build', ['custom-mykeys-1', 'local-unidentified'])
def test_manual_official_recovery_can_download_without_version_ranking(qtbot, contract, build):
    source = FakeReleaseSource()
    gateway = FirmwareGateway()
    vm = MainViewModel(gateway, contract, firmware_release_source=source)
    snapshot = _power_v2_snapshot(contract, read_only=False)
    gateway.snapshot_ready.emit(replace(snapshot, versions={**snapshot.versions,'firmware':'99.0.0','build_id':build}))
    vm.check_remote_firmware()
    assert len(source.snapshots) == 1
    source.release_found.emit(_release(_bundle(contract)))
    assert vm.remote_firmware.state.value == 'restore_available'
    assert not vm.can_auto_check_remote_firmware
    vm.download_remote_firmware()
    assert len(source.downloads) == 1
    assert not any(c.name.startswith('FW_') for c in gateway.commands)
    vm.shutdown()


def test_signed_history_persists_and_matches_all_identity_fields(contract, tmp_path):
    import json
    from controller_config.firmware_origin import FirmwareReleaseHistory
    from controller_config.firmware_signature import FirmwareSignatureError
    settings = QSettings(str(tmp_path/'history.ini'), QSettings.IniFormat)
    history = FirmwareReleaseHistory(settings, bundled=[])
    release = _release(_bundle(contract))
    snapshot = _power_v2_snapshot(contract, read_only=False)
    snapshot = replace(snapshot, versions={**snapshot.versions, 'firmware':release.version, 'build_id':release.build_id})
    assert history.classify(snapshot) == 'unknown'
    history.remember(release.manifest)
    settings.sync()
    restored = FirmwareReleaseHistory(QSettings(str(tmp_path/'history.ini'), QSettings.IniFormat), bundled=[])
    assert restored.classify(snapshot) == 'official'
    # Another genuine device with the same release should also be recognized.
    assert restored.classify(replace(snapshot, identity={**snapshot.identity, 'serial':'B'})) == 'official'
    for key in ('product_id', 'hardware_id'):
        assert restored.classify(replace(snapshot, identity={**snapshot.identity,key:'other'})) == 'unknown'
    for key in ('firmware', 'build_id'):
        assert restored.classify(replace(snapshot, versions={**snapshot.versions,key:'other'})) == 'unknown'
    with pytest.raises(FirmwareSignatureError):
        restored.remember({**release.manifest, 'version':'99.0.0'})
    settings.setValue('firmware/official_releases', json.dumps([{**release.manifest,'version':'99.0.0'}]))
    assert FirmwareReleaseHistory(settings,bundled=[]).classify(snapshot) == 'unknown'


def test_official_looking_unrecorded_build_is_unknown(qtbot, contract):
    source=FakeReleaseSource(); gateway=FirmwareGateway()
    vm=MainViewModel(gateway,contract,firmware_release_source=source)
    snapshot=_power_v2_snapshot(contract,read_only=False)
    gateway.snapshot_ready.emit(replace(snapshot,versions={**snapshot.versions,'build_id':'20260914.01-g12345678'}))
    assert vm.firmware_origin == 'unknown'
    assert not source.snapshots
    vm.shutdown()


def test_restoration_retry_dismiss_switch_and_exact_readback(qtbot, contract):
    from controller_config.firmware_update import FirmwareUpdateState
    from test_firmware_transaction import _booted_status
    source=FakeReleaseSource(); gateway=FirmwareGateway()
    vm=MainViewModel(gateway,contract,firmware_release_source=source)
    snapshot=_power_v2_snapshot(contract,read_only=False)
    snapshot=replace(snapshot,versions={**snapshot.versions,'build_id':'custom-dev-1','firmware':'99.0.0'})
    gateway.snapshot_ready.emit(snapshot)
    vm.check_remote_firmware(); bundle=_bundle(contract)
    source.release_found.emit(_release(bundle))
    vm.download_remote_firmware(); source.failed.emit(ValueError('network test'))
    assert vm.remote_firmware.restoration
    vm.download_remote_firmware(); source.download_completed.emit(bundle)
    assert vm.firmware_update.state is FirmwareUpdateState.PACKAGE_READY
    assert vm.firmware_origin == 'custom'  # Downloading is not installing.
    vm.dismiss_firmware_update()
    assert vm.remote_firmware.state.value == 'restore_available'
    vm.download_remote_firmware(); source.download_completed.emit(bundle)
    vm.start_firmware_update()
    assert gateway.commands[-1].name == 'FW_STATUS'
    # Exercise the existing post-reboot reconciliation, with no simulated physical flash.
    package=vm.firmware_update.package
    vm._firmware_update=replace(vm.firmware_update,state=FirmwareUpdateState.WAITING_RECONNECT,target_partition='ota_1')
    gateway.snapshot_ready.emit(replace(snapshot, versions={**snapshot.versions,'firmware':package.version,'build_id':package.build_id}))
    gateway.command_completed.emit('FW_STATUS',_booted_status(package,rollback_pending=True))
    assert vm.firmware_update.state is FirmwareUpdateState.VERIFYING
    gateway.command_completed.emit('FW_STATUS',_booted_status(package))
    assert vm.firmware_update.state is FirmwareUpdateState.COMPLETED
    assert vm.remote_firmware.state is RemoteFirmwareState.CURRENT
    assert vm.firmware_origin == 'official'
    gateway.snapshot_ready.emit(replace(snapshot,identity={**snapshot.identity,'serial':'OTHER'}))
    assert vm.remote_firmware.release is None
    assert vm.firmware_origin == 'custom'
    with pytest.raises(ValueError): vm.download_remote_firmware()
    vm.shutdown()


@pytest.mark.parametrize('kind', ['unpublished','network','signature'])
def test_custom_manual_failure_is_not_masked_by_source_notice(qtbot,contract,kind):
    from controller_config.firmware_release import FirmwareReleaseError
    source=FakeReleaseSource();gateway=FirmwareGateway()
    vm=MainViewModel(gateway,contract,firmware_release_source=source)
    snapshot=_power_v2_snapshot(contract,read_only=False)
    gateway.snapshot_ready.emit(replace(snapshot,versions={**snapshot.versions,'build_id':'custom-test'}))
    vm.check_remote_firmware();source.failed.emit(FirmwareReleaseError('test',kind=kind))
    assert vm.firmware_online_message == vm.remote_firmware.message
    assert '最新' not in vm.firmware_online_message and '成功' not in vm.firmware_online_message
    assert vm.remote_firmware.state is (RemoteFirmwareState.UNPUBLISHED if kind == 'unpublished' else RemoteFirmwareState.FAILED)
    vm.shutdown()


@pytest.mark.parametrize('language', ['zh_CN','en_US','ja_JP'])
@pytest.mark.parametrize('origin', ['custom','unknown','official'])
def test_origin_ui_locales_and_manual_restore(qtbot,qapp,contract,tmp_path,language,origin):
    from controller_config.i18n import LanguageManager
    from controller_config.views.main_window import MainWindow
    from PySide6.QtWidgets import QLabel, QPushButton
    gateway=FirmwareGateway();source=FakeReleaseSource()
    vm=MainViewModel(gateway,contract,firmware_release_source=source)
    snapshot=_power_v2_snapshot(contract,read_only=False)
    bundle=_bundle(contract)
    if origin=='official':
        vm._firmware_release_history.remember(bundle.manifest)
        versions={'firmware':bundle.manifest['version'],'build_id':bundle.manifest['build_id']}
    else:
        versions={'firmware':'99.0.0','build_id':'custom-ui-1' if origin=='custom' else 'local-unidentified'}
    gateway.snapshot_ready.emit(replace(snapshot,versions={**snapshot.versions,**versions}))
    lm=LanguageManager(qapp,settings=QSettings(str(tmp_path/'language.ini'),QSettings.IniFormat),initial_language=language)
    window=MainWindow(vm,language_manager=lm);qtbot.addWidget(window)
    window.resize(1280,800);vm.navigate('firmware');window.show()
    label=window.findChild(QLabel,'firmwareOrigin')
    source_text={'custom':'正在使用自定义固件','unknown':'固件来源未确认','official':'官方固件'}[origin]
    assert label.text()==lm.translate(source_text)
    if origin!='official':
        button=window.findChild(QPushButton,'checkRemoteFirmware')
        assert button.isEnabled() and button.text()==lm.translate('查看官方版本')
        assert button.property('buttonRole') == 'secondary'
        button.click();source.release_found.emit(_release(bundle))
        button=window.findChild(QPushButton,'installRemoteFirmware')
        assert button.isEnabled() and button.text()==lm.translate('切换到官方固件…')
        assert button.property('buttonRole') == 'secondary'
        assert source.downloads == []
        assert not any(c.name.startswith('FW_') for c in gateway.commands)
        assert window._firmware_update_footer.isHidden()
    else:
        if vm.remote_firmware.state.value != 'checking':
            window.findChild(QPushButton, 'checkRemoteFirmware').click()
        source.release_found.emit(_release(bundle))
        assert vm.remote_firmware.state.value == 'current'
        assert window.findChild(QPushButton, 'installRemoteFirmware') is None
        assert window.findChild(QLabel, 'remoteFirmwareMessage').text() == lm.translate('已是最新官方固件')
        assert window.findChild(QPushButton, 'checkRemoteFirmware').property('buttonRole') == 'secondary'
    for locale in ('en_US', 'ja_JP', 'zh_CN', language):
        lm.set_language(locale)
        summary = window.findChild(QLabel, 'remoteFirmwareReleaseSummary')
        assert summary.text().startswith(lm.translate('官方版本：{version}').split('{version}')[0])
        assert '#FF6A00' not in summary.styleSheet()
    qtbot.wait(50)
    assert window.findChild(QLabel,'firmwareOrigin').wordWrap()
    import os
    from pathlib import Path
    if directory:=os.environ.get('BORING_ORIGIN_UI_EVIDENCE'):
        Path(directory).mkdir(parents=True,exist_ok=True)
        window.grab().save(str(Path(directory)/f'{origin}-{language}.png'))
    vm.shutdown()
