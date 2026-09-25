from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile

import pytest
from PySide6.QtWidgets import QMessageBox, QPushButton, QLabel

from controller_config.firmware_release import load_local_firmware_package, release_is_newer, RemoteFirmwareRelease, FirmwareReleaseError
from controller_config.firmware_update import FirmwarePackageError, FirmwareUpdateState, FirmwareUpdateTransaction
from controller_config.firmware_signature import sign_manifest
from controller_config.transport.demo import _power_v2_snapshot
from controller_config.viewmodels.main import MainViewModel
from controller_config.views.main_window import MainWindow
from test_firmware_update import _write_package
from test_firmware_transaction import FirmwareGateway, _package
from test_firmware_release import _TEST_SIGNING_KEY, trust_test_signing_key

TOOL = Path(__file__).resolve().parents[2] / 'firmware/tools/pack_custom_firmware.py'


def custom_manifest(tmp_path, contract):
    path = _write_package(tmp_path, contract, build_id='custom-joey-20260912.01')
    raw = json.loads(path.read_text())
    image = path.parent / raw['image']
    data = bytearray(image.read_bytes())
    marker = raw['build_id'].encode() + b'\0'
    data[800:800+len(marker)] = marker
    image.write_bytes(data)
    raw.update(origin='custom', minimum_app_version='0.1.24', sha256=hashlib.sha256(data).hexdigest())
    path.write_text(json.dumps(raw))
    return path


def test_packager_roundtrip_and_no_overwrite(tmp_path, contract):
    path = custom_manifest(tmp_path, contract)
    output = tmp_path/'custom.zip'
    args = [sys.executable, str(TOOL), '--manifest', str(path), '--output', str(output)]
    first = subprocess.run(args, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    package = load_local_firmware_package(output, contract, custom=True)
    assert package.source_kind == 'custom'
    assert package.manifest['origin'] == 'custom'
    assert package.manifest['validation_state'] == 'built'
    assert 'signature' not in package.manifest
    assert package.build_id == 'custom-joey-20260912.01'
    original = output.read_bytes()
    assert subprocess.run(args, capture_output=True).returncode != 0
    assert output.read_bytes() == original


@pytest.mark.parametrize('field,value', [('origin','official'), ('build_id','20260912.05-gabc'),
    ('build_id','custom-not-in-image'), ('validation_state','released'), ('signature',''),
    ('signature','ed25519-v1:fake'), ('minimum_app_version','99.0.0')])
def test_custom_rejects_misleading_or_incompatible_metadata(tmp_path, contract, field, value):
    path = custom_manifest(tmp_path, contract)
    raw = json.loads(path.read_text());raw[field] = value;path.write_text(json.dumps(raw))
    with pytest.raises((FirmwarePackageError, FirmwareReleaseError)):
        load_local_firmware_package(path, contract, custom=True)


def test_official_import_requires_signature_and_never_falls_back(tmp_path, contract):
    path = custom_manifest(tmp_path, contract)
    with pytest.raises(FirmwareReleaseError):
        load_local_firmware_package(path, contract)
    official_dir = tmp_path/'official';official_dir.mkdir()
    official = _package(official_dir, contract)
    assert load_local_firmware_package(official, contract).source_kind == 'official'
    # Keep image identity valid to specifically test the publisher signature.
    raw = json.loads(official.read_text());raw['build_id'] = 'tampered-build'
    official.write_text(json.dumps(raw))
    with pytest.raises(FirmwareReleaseError):
        load_local_firmware_package(official, contract)
    with pytest.raises(FirmwarePackageError):
        load_local_firmware_package(official, contract, custom=True)


def test_custom_build_not_compared_as_official_even_with_higher_offer(contract):
    snapshot = _power_v2_snapshot(contract, read_only=False)
    snapshot = replace(snapshot, versions={**snapshot.versions, 'build_id':'custom-joey-1'})
    release = RemoteFirmwareRelease(dict(version='999.0.0',build_id='20260912.05-gabc'), '', '')
    with pytest.raises(FirmwareReleaseError, match='自定义固件'):
        release_is_newer(release, snapshot)


@pytest.mark.parametrize('language', ['zh_CN','en_US','ja_JP'])
def test_custom_install_needs_confirmation_and_keeps_existing_pipeline(qtbot, contract, tmp_path, monkeypatch, language):
    gateway = FirmwareGateway()
    vm = MainViewModel(gateway, contract)
    gateway.snapshot_ready.emit(_power_v2_snapshot(contract, read_only=False))
    window = MainWindow(vm)
    qtbot.addWidget(window, before_close_func=lambda _: setattr(vm, '_firmware_update', FirmwareUpdateTransaction()))
    window._language_manager.set_language(language)
    package = vm.load_firmware_package(custom_manifest(tmp_path, contract), custom=True)
    assert package.source_kind == 'custom' and not gateway.commands
    vm.navigate('firmware');window.show()
    qtbot.waitUntil(lambda: window.findChild(QPushButton,'selectCustomFirmwarePackage').isVisible())
    assert window.findChild(QPushButton, 'startFirmwareUpdate').text() == {
        'zh_CN': '安装导入的自定义固件',
        'en_US': 'Install imported custom firmware',
        'ja_JP': '読み込んだカスタムファームウェアをインストール',
    }[language]
    assert window.findChild(QLabel,'firmwarePackageSource').text() in (
        '自定义固件 · 未经官方验证', 'Custom firmware · Not verified by BORING', 'カスタムファームウェア · BORING 未検証')
    dialogs=[]
    def cancel(*args): dialogs.append(args[2]); return QMessageBox.Cancel
    monkeypatch.setattr(QMessageBox,'warning',cancel)
    window._confirm_firmware_update()
    assert not gateway.commands
    assert dialogs and package.build_id in dialogs[-1]
    assert any(word in dialogs[-1] for word in ('未经官方','not verified','未検証'))
    monkeypatch.setattr(QMessageBox,'warning',lambda *_: QMessageBox.Yes)
    window._confirm_firmware_update()
    assert gateway.commands[-1].name == 'FW_STATUS'
    assert vm.firmware_update.state is FirmwareUpdateState.CHECKING
    vm._firmware_update = FirmwareUpdateTransaction()
    vm.shutdown()
    window._language_manager.set_language('zh_CN')


def test_official_restoration_requires_confirmation(qtbot, contract, tmp_path, monkeypatch):
    gateway = FirmwareGateway();vm = MainViewModel(gateway, contract)
    snapshot = _power_v2_snapshot(contract, read_only=False)
    gateway.snapshot_ready.emit(replace(snapshot, versions={**snapshot.versions,'build_id':'custom-joey-1'}))
    window = MainWindow(vm);qtbot.addWidget(window)
    vm.load_firmware_package(_package(tmp_path, contract))
    dialogs=[]
    monkeypatch.setattr(QMessageBox,'warning',lambda *args: dialogs.append(args[2]) or QMessageBox.Cancel)
    window._confirm_firmware_update()
    assert '替换当前固件及自定义功能' in dialogs[-1]
    assert not gateway.commands
    vm.shutdown()


def test_device_signature_requirement_blocks_custom_package(qtbot, contract, tmp_path):
    gateway = FirmwareGateway(); vm = MainViewModel(gateway, contract)
    snapshot = _power_v2_snapshot(contract, read_only=False)
    capabilities = {**snapshot.capabilities, 'features': {**snapshot.capabilities['features'], 'firmware_signature_required': True}}
    gateway.snapshot_ready.emit(replace(snapshot, capabilities=capabilities))
    with pytest.raises(FirmwarePackageError, match='设备端签名'):
        vm.load_firmware_package(custom_manifest(tmp_path, contract), custom=True)
    assert not gateway.commands and vm.firmware_update.package is None
    vm.shutdown()


def test_signed_custom_label_is_not_promoted_to_official(tmp_path, contract):
    path = custom_manifest(tmp_path, contract)
    path.write_text(json.dumps(sign_manifest(json.loads(path.read_text()), _TEST_SIGNING_KEY)))
    with pytest.raises(FirmwareReleaseError, match='自定义固件'):
        load_local_firmware_package(path, contract)


def test_custom_device_never_shows_cached_official_current_status(qtbot, contract):
    from controller_config.firmware_release import RemoteFirmwareCheck, RemoteFirmwareState
    gateway = FirmwareGateway(); vm = MainViewModel(gateway, contract)
    snapshot = _power_v2_snapshot(contract, read_only=False)
    gateway.snapshot_ready.emit(replace(snapshot, versions={**snapshot.versions,'build_id':'custom-joey-1'}))
    window = MainWindow(vm); qtbot.addWidget(window)
    vm._remote_firmware = RemoteFirmwareCheck(state=RemoteFirmwareState.CURRENT, message='已是最新版本')
    vm.navigate('firmware'); window.show()
    label = window.findChild(QLabel, 'remoteFirmwareMessage')
    assert '自定义' in label.text() and '已是最新' not in label.text()
    assert window.findChild(QPushButton, 'checkRemoteFirmware').text() == '查看官方版本'
    vm.changed.emit(vm.model)
    assert '已是最新' not in window.findChild(QLabel, 'remoteFirmwareMessage').text()
    vm.shutdown()


def test_custom_import_button_uses_custom_loader(qtbot, contract, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    gateway = FirmwareGateway(); vm = MainViewModel(gateway, contract)
    gateway.snapshot_ready.emit(_power_v2_snapshot(contract, read_only=False))
    path = custom_manifest(tmp_path, contract)
    window = MainWindow(vm); qtbot.addWidget(window)
    monkeypatch.setattr(QFileDialog,'getOpenFileName',lambda *_: (str(path),''))
    vm.navigate('firmware');window.show()
    window.findChild(QPushButton,'firmwarePackageToggle').click()
    button = window.findChild(QPushButton,'selectCustomFirmwarePackage')
    qtbot.waitUntil(button.isVisible)
    button.click()
    assert vm.firmware_update.package.source_kind == 'custom'
    assert not gateway.commands
    vm.shutdown()
