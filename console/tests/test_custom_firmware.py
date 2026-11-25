from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox, QPushButton, QLabel, QScrollArea, QWidget

from controller_config.firmware_release import load_local_firmware_package, release_is_newer, RemoteFirmwareRelease, FirmwareReleaseError
from controller_config.firmware_update import FirmwarePackageError, FirmwareUpdateState, FirmwareUpdateTransaction
from controller_config.firmware_signature import sign_manifest
from controller_config.transport.demo import _power_v2_snapshot
from controller_config.viewmodels.main import MainViewModel
from controller_config.views.main_window import MainWindow
from test_firmware_update import _write_package
from test_firmware_transaction import FirmwareGateway, _package
from test_firmware_release import _TEST_SIGNING_KEY, trust_test_signing_key
from notice_helpers import answer_dialogs

TOOL = Path(__file__).resolve().parents[2] / 'firmware/tools/pack_custom_firmware.py'


def custom_manifest(tmp_path, contract):
    path = _write_package(tmp_path, contract, build_id='custom-demo-20260912.01')
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
    assert package.build_id == 'custom-demo-20260912.01'
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
    snapshot = replace(snapshot, versions={**snapshot.versions, 'build_id':'custom-demo-1'})
    release = RemoteFirmwareRelease(dict(version='999.0.0',build_id='20260912.05-gabc'), '', '')
    with pytest.raises(FirmwareReleaseError, match='自定义固件'):
        release_is_newer(release, snapshot)


@pytest.mark.parametrize('language', ['zh_CN','en_US','ja_JP'])
def test_custom_install_needs_confirmation_and_keeps_existing_pipeline(qtbot, contract, tmp_path, monkeypatch, language):
    gateway = FirmwareGateway()
    vm = MainViewModel(gateway, contract)
    gateway.snapshot_ready.emit(_power_v2_snapshot(contract, read_only=False))
    window = MainWindow(vm)
    qtbot.addWidget(window, before_close_func=lambda _: vm._firmware.replace_silently(FirmwareUpdateTransaction()))
    window._language_manager.set_language(language)
    package = vm.load_firmware_package(custom_manifest(tmp_path, contract), custom=True)
    assert package.source_kind == 'custom' and not gateway.commands
    vm.navigate('firmware');window.show()
    # 高级固件选项 no longer opens by itself (design decision); the user opens it.
    assert not window.findChild(QPushButton, 'selectCustomFirmwarePackage').isVisible()
    window.findChild(QPushButton, 'firmwarePackageToggle').click()
    qtbot.waitUntil(lambda: window.findChild(QPushButton,'selectCustomFirmwarePackage').isVisible())
    assert window.findChild(QPushButton, 'startFirmwareUpdate').text() == {
        'zh_CN': '安装导入的自定义固件',
        'en_US': 'Install imported custom firmware',
        'ja_JP': '読み込んだカスタムファームウェアをインストール',
    }[language]
    assert window.findChild(QLabel,'firmwarePackageSource').text() in (
        '自定义固件 · 未经官方验证', 'Custom firmware · Not verified by BORING', 'カスタムファームウェア · BORING 未検証')
    dialogs=[]
    decision = ["cancel"]
    def choose(info):
        dialogs.append((info["text"], info["buttons"]["install"], info["buttons"]["cancel"]))
        return decision[0]
    answer_dialogs(monkeypatch, choose)
    window._firmware_page.confirm_update()
    assert not [command for command in gateway.commands if command.name != 'GET_PROMPT_EVENT']
    assert dialogs and package.build_id in dialogs[-1][0]
    assert any(word in dialogs[-1][0] for word in ('未经官方','not verified','未検証'))
    assert dialogs[-1][1] in ('安装自定义固件', 'Install Custom Firmware', 'カスタムファームウェアをインストール')
    assert dialogs[-1][2] in ('取消', 'Cancel', 'キャンセル')  # design decision: the other button is 取消
    decision[0] = "install"
    window._firmware_page.confirm_update()
    assert gateway.commands[-1].name == 'FW_STATUS'
    assert vm.firmware_update.state is FirmwareUpdateState.CHECKING
    vm._firmware.replace_silently(FirmwareUpdateTransaction())
    vm.shutdown()
    window._language_manager.set_language('zh_CN')


def test_official_restoration_requires_confirmation(qtbot, contract, tmp_path, monkeypatch):
    gateway = FirmwareGateway();vm = MainViewModel(gateway, contract)
    snapshot = _power_v2_snapshot(contract, read_only=False)
    gateway.snapshot_ready.emit(replace(snapshot, versions={**snapshot.versions,'build_id':'custom-demo-1'}))
    window = MainWindow(vm);qtbot.addWidget(window)
    vm.load_firmware_package(_package(tmp_path, contract))
    dialogs=[]
    def cancel_install(info):
        dialogs.append((info["text"], info["buttons"]["install"], info["buttons"]["cancel"]))
        return "cancel"
    answer_dialogs(monkeypatch, cancel_install)
    window._firmware_page.confirm_update()
    assert '替换当前固件及自定义功能' in dialogs[-1][0]
    assert dialogs[-1][1:] == ('安装官方固件', '取消')
    assert not [command for command in gateway.commands if command.name != 'GET_PROMPT_EVENT']
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
    gateway.snapshot_ready.emit(replace(snapshot, versions={**snapshot.versions,'build_id':'custom-demo-1'}))
    window = MainWindow(vm); qtbot.addWidget(window)
    vm._remote_firmware = RemoteFirmwareCheck(state=RemoteFirmwareState.CURRENT, message='已是最新版本')
    vm.navigate('firmware'); window.show()
    label = window.findChild(QLabel, 'remoteFirmwareMessage')
    assert '自定义' in label.text() and '已是最新' not in label.text()
    assert window.findChild(QPushButton, 'checkRemoteFirmware').text() == '查看官方版本'
    vm.changed.emit(vm.model)
    assert '已是最新' not in window.findChild(QLabel, 'remoteFirmwareMessage').text()
    vm.shutdown()


def test_custom_import_dialog_is_deferred_nonblocking_and_suspends_quick_overlay(
    qtbot, contract, tmp_path, monkeypatch
):
    gateway = FirmwareGateway(); vm = MainViewModel(gateway, contract)
    gateway.snapshot_ready.emit(_power_v2_snapshot(contract, read_only=False))
    path = custom_manifest(tmp_path, contract)
    window = MainWindow(vm); qtbot.addWidget(window)
    class FakeQuickViewport(QWidget):
        def setSource(self, _source):
            pass

    viewport = FakeQuickViewport(window)
    viewport.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop, True)
    window._boring_mist_quick_viewport = viewport
    static_calls = []
    opened = []
    monkeypatch.setattr(
        QFileDialog,
        'getOpenFileName',
        lambda *_args, **_kwargs: static_calls.append(True) or ('', ''),
    )
    monkeypatch.setattr(QFileDialog, 'open', lambda dialog: opened.append(dialog))
    vm.navigate('firmware');window.show()
    scroll = window.findChild(QScrollArea, 'firmwareMaintenanceScroll')
    toggle = window.findChild(QPushButton, 'firmwarePackageToggle')
    assert toggle.text().startswith('▸ ')
    tile = window.findChild(QWidget, 'firmwareInstallTile')
    before = tile.size()
    toggle.click()
    assert toggle.text().startswith('▾ ')
    # The options take the place of 手动安装 in the same tile; nothing grows or scrolls.
    assert tile.size() == before and scroll.verticalScrollBar().maximum() == 0
    button = window.findChild(QPushButton,'selectCustomFirmwarePackage')
    qtbot.waitUntil(button.isVisible)
    button.click()
    assert not static_calls
    assert not opened
    qtbot.waitUntil(lambda: bool(opened))
    dialog = opened[0]
    assert dialog.fileMode() is QFileDialog.FileMode.ExistingFile
    assert not viewport.testAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop)
    dialog.done(QDialog.DialogCode.Rejected)
    assert window._firmware_page.file_dialog is None
    assert viewport.testAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop)
    assert vm.firmware_update.package is None

    button.click()
    assert len(opened) == 1
    qtbot.waitUntil(lambda: len(opened) == 2)
    dialog = opened[1]
    assert not viewport.testAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop)
    dialog.selectFile(str(path))
    dialog.done(QDialog.DialogCode.Accepted)
    qtbot.waitUntil(lambda: vm.firmware_update.package is not None)
    assert vm.firmware_update.package.source_kind == 'custom'
    assert viewport.testAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop)
    assert not [command for command in gateway.commands if command.name.startswith('FW_')]
    vm.shutdown()
