from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import struct

import pytest
from PySide6.QtCore import QObject, QUrl
from PySide6.QtNetwork import QNetworkReply
from PySide6.QtWidgets import QProgressBar, QPushButton

from controller_config.firmware_release import (
    FirmwareReleaseError,
    FirmwareReleaseSource,
    HttpFirmwareReleaseSource,
    RemoteFirmwareBundle,
    RemoteFirmwareRelease,
    RemoteFirmwareState,
    _abort_oversized_reply,
    load_remote_firmware_bundle,
    manifest_url_for_snapshot,
    _validate_release_manifest,
    release_is_newer,
)
from controller_config.firmware_update import (
    APP_DESC_OFFSET,
    CUSTOM_DESC_OFFSET,
    ESP_APP_DESC_MAGIC,
    FirmwareUpdateState,
    WMP_DESCRIPTOR,
    WMP_DESCRIPTOR_MAGIC,
    WMP_DESCRIPTOR_VERSION,
)
from controller_config.transport.demo import DemoGateway, _power_v2_snapshot
from controller_config.viewmodels.main import MainViewModel
from controller_config.views.main_window import MainWindow


class FakeReleaseSource(FirmwareReleaseSource):
    def __init__(self) -> None:
        super().__init__()
        self.snapshots = []
        self.downloads = []

    def check(self, snapshot) -> None:
        self.snapshots.append(snapshot)

    def download(self, release, snapshot) -> None:
        self.downloads.append((release, snapshot))


class RecordingDemoGateway(DemoGateway):
    def __init__(self, contract, state: str) -> None:
        super().__init__(contract, state)
        self.commands = []

    def execute_command(self, command) -> None:
        self.commands.append(command)
        super().execute_command(command)


class FakeDownloadReply(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.aborted = False

    def abort(self) -> None:
        self.aborted = True


class FakeManifestReply(QObject):
    def __init__(self, data: bytes, url: str) -> None:
        super().__init__()
        self._data = data
        self._url = QUrl(url)

    def property(self, name: str):
        return super().property(name)

    def error(self):
        return QNetworkReply.NetworkError.NoError

    def readAll(self) -> bytes:
        return self._data

    def url(self) -> QUrl:
        return self._url

    def deleteLater(self) -> None:
        pass


def _bundle(
    contract,
    *,
    validation_state: str = "released",
    git_dirty: bool = False,
    build_id: str = "remote-release-build",
) -> RemoteFirmwareBundle:
    version = "0.3.0-alpha.2"
    hardware_id = "WMP-S3-MATRIX12-POWER-V2"
    image = bytearray(1024)
    image[0] = 0xE9
    struct.pack_into("<I", image, APP_DESC_OFFSET, ESP_APP_DESC_MAGIC)
    image[APP_DESC_OFFSET + 16 : APP_DESC_OFFSET + 48] = _fixed(version, 32)
    image[APP_DESC_OFFSET + 48 : APP_DESC_OFFSET + 80] = _fixed(
        "wired_macro_pad", 32
    )
    WMP_DESCRIPTOR.pack_into(
        image,
        CUSTOM_DESC_OFFSET,
        WMP_DESCRIPTOR_MAGIC,
        WMP_DESCRIPTOR_VERSION,
        WMP_DESCRIPTOR.size,
        _fixed(contract.product_id, 32),
        _fixed(hardware_id, 32),
    )
    manifest = {
        "format": "wmp-firmware-update-v1",
        "product_id": contract.product_id,
        "hardware_id": hardware_id,
        "version": version,
        "image": "wired_macro_pad.bin",
        "size": len(image),
        "sha256": hashlib.sha256(image).hexdigest(),
        "build_id": build_id,
        "validation_state": validation_state,
        "git_dirty": git_dirty,
    }
    return RemoteFirmwareBundle(
        manifest=manifest,
        image_data=bytes(image),
        manifest_url="https://updates.example.test/firmware-manifest.json",
    )


def _release(bundle: RemoteFirmwareBundle) -> RemoteFirmwareRelease:
    return RemoteFirmwareRelease(
        manifest=bundle.manifest,
        manifest_url=bundle.manifest_url,
        image_url="https://updates.example.test/wired_macro_pad.bin",
    )


def _fixed(value: str, size: int) -> bytes:
    encoded = value.encode("ascii")
    return encoded + b"\0" + bytes(size - len(encoded) - 1)


def test_manifest_url_is_scoped_to_connected_product_and_hardware(contract) -> None:
    snapshot = _power_v2_snapshot(contract, read_only=False)
    url = manifest_url_for_snapshot(
        "https://updates.example.test/{product_id}/{hardware_id}/firmware-manifest.json",
        snapshot,
    )

    assert url.toString() == (
        "https://updates.example.test/wired-macro-pad-v1/"
        "WMP-S3-MATRIX12-POWER-V2/firmware-manifest.json"
    )
    with pytest.raises(FirmwareReleaseError, match="HTTPS"):
        manifest_url_for_snapshot("http://updates.example.test/latest.json", snapshot)


def test_oversized_download_is_aborted_before_full_response_is_buffered() -> None:
    reply = FakeDownloadReply()

    _abort_oversized_reply(reply, 1025, -1, 1024, "固件镜像")

    assert reply.aborted is True
    assert reply.property("boringSizeError") == "固件镜像超过允许大小"


def test_remote_bundle_reuses_package_validation_and_requires_clean_release(
    contract,
) -> None:
    package = load_remote_firmware_bundle(_bundle(contract), contract)
    assert package.build_id == "remote-release-build"
    assert package.validation_state == "released"

    with pytest.raises(FirmwareReleaseError, match="validation_state=released"):
        load_remote_firmware_bundle(
            _bundle(contract, validation_state="built"),
            contract,
        )
    with pytest.raises(FirmwareReleaseError, match="git_dirty=false"):
        load_remote_firmware_bundle(_bundle(contract, git_dirty=True), contract)


def test_http_check_stops_after_manifest_and_does_not_fetch_image(contract) -> None:
    snapshot = _power_v2_snapshot(contract, read_only=False)
    bundle = _bundle(contract)
    source = HttpFirmwareReleaseSource(
        "https://updates.example.test/firmware-manifest.json"
    )
    found = []
    source.release_found.connect(found.append)
    source._snapshot = snapshot
    source._reply = FakeManifestReply(
        json.dumps(bundle.manifest).encode("utf-8"),
        bundle.manifest_url,
    )

    source._manifest_finished()

    assert len(found) == 1
    assert found[0].image_url == "https://updates.example.test/wired_macro_pad.bin"
    assert source._reply is None
    assert source._snapshot is None


def test_remote_auto_check_then_explicit_download_never_starts_device_update(
    qtbot,
    contract,
) -> None:
    source = FakeReleaseSource()
    gateway = RecordingDemoGateway(contract, "ready")
    view_model = MainViewModel(
        gateway,
        contract,
        firmware_release_source=source,
    )
    view_model.start()
    qtbot.waitUntil(lambda: view_model.model.snapshot is not None, timeout=1000)

    assert view_model.remote_firmware.state is RemoteFirmwareState.CHECKING
    assert len(source.snapshots) == 1
    bundle = _bundle(contract)
    source.release_found.emit(_release(bundle))

    assert view_model.remote_firmware.state is RemoteFirmwareState.AVAILABLE
    assert view_model.firmware_update.package is None
    assert not any(command.name.startswith("FW_") for command in gateway.commands)

    view_model.download_remote_firmware()
    assert view_model.remote_firmware.state is RemoteFirmwareState.DOWNLOADING
    assert len(source.downloads) == 1
    source.download_progress.emit(512, 1024)
    assert view_model.remote_firmware.progress_percent == 50
    source.download_completed.emit(bundle)

    assert view_model.remote_firmware.state is RemoteFirmwareState.DOWNLOADED
    assert view_model.firmware_update.state is FirmwareUpdateState.PACKAGE_READY
    assert view_model.firmware_update.received_size == 0
    assert "尚未向设备发送" in view_model.firmware_update.message
    assert not any(command.name.startswith("FW_") for command in gateway.commands)

    view_model._finish_firmware_update(FirmwareUpdateState.COMPLETED, "verified")
    assert view_model.remote_firmware.state is RemoteFirmwareState.CURRENT


def test_remote_check_reports_current_build_and_network_failure(qtbot, contract) -> None:
    source = FakeReleaseSource()
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(
        gateway,
        contract,
        firmware_release_source=source,
    )
    view_model.start()
    qtbot.waitUntil(lambda: view_model.model.snapshot is not None, timeout=1000)
    snapshot = replace(
        view_model.model.snapshot,
        versions={**view_model.model.snapshot.versions, "build_id": "current-build", "firmware": "0.3.0-alpha.2"},
    )
    gateway.snapshot_ready.emit(snapshot)

    source.release_found.emit(_release(_bundle(contract, build_id="current-build")))
    assert view_model.remote_firmware.state is RemoteFirmwareState.CURRENT
    assert view_model.firmware_update.package is None

    view_model.check_remote_firmware()
    source.failed.emit("network unavailable")
    assert view_model.remote_firmware.state is RemoteFirmwareState.FAILED
    assert view_model.remote_firmware.technical == "network unavailable"


def test_remote_download_failure_keeps_release_for_explicit_retry(qtbot, contract) -> None:
    source = FakeReleaseSource()
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract, firmware_release_source=source)
    view_model.start()
    qtbot.waitUntil(lambda: view_model.model.snapshot is not None, timeout=1000)

    bundle = _bundle(contract)
    source.release_found.emit(_release(bundle))
    view_model.download_remote_firmware()
    source.failed.emit("connection reset")

    assert view_model.remote_firmware.state is RemoteFirmwareState.FAILED
    assert view_model.remote_firmware.release is not None
    assert "下载失败" in view_model.remote_firmware.message

    view_model.download_remote_firmware()
    assert len(source.downloads) == 2


def test_firmware_page_exposes_check_download_and_install_as_separate_actions(
    qtbot,
    contract,
) -> None:
    source = FakeReleaseSource()
    gateway = RecordingDemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract, firmware_release_source=source)
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.show()
    view_model.start()
    qtbot.waitUntil(lambda: view_model.model.snapshot is not None, timeout=1000)
    view_model.navigate("firmware")

    assert view_model.remote_firmware.state is RemoteFirmwareState.CHECKING
    bundle = _bundle(contract)
    source.release_found.emit(_release(bundle))

    check = window.findChild(QPushButton, "checkRemoteFirmware")
    assert check is not None and check.isEnabled()
    download = window.findChild(QPushButton, "downloadRemoteFirmware")
    assert download is not None and download.text() == "下载新固件"
    download.click()
    source.download_progress.emit(512, 1024)
    progress = window.findChild(QProgressBar, "remoteFirmwareProgress")
    assert progress is not None and progress.value() == 50

    source.download_completed.emit(bundle)
    install = window.findChild(QPushButton, "startFirmwareUpdate")
    assert install is not None and install.text() == "安装新固件"
    assert not any(command.name.startswith("FW_") for command in gateway.commands)


def test_remote_check_requires_a_write_compatible_device(qtbot, contract) -> None:
    source = FakeReleaseSource()
    gateway = DemoGateway(contract, "read-only")
    view_model = MainViewModel(
        gateway,
        contract,
        firmware_release_source=source,
    )
    view_model.start()
    qtbot.waitUntil(lambda: view_model.model.snapshot is not None, timeout=1000)

    with pytest.raises(ValueError, match="可写兼容"):
        view_model.check_remote_firmware()
    assert source.snapshots == []


@pytest.mark.parametrize(("offered", "installed", "newer"), [
    ("0.3.0-alpha.2", "0.3.0-alpha.1", True),
    ("0.3.0-alpha.2", "0.3.0", False),
    ("0.3.0", "0.3.0-alpha.2", True),
    ("0.3.0-alpha.10", "0.3.0-alpha.9", True),
    ("0.2.0", "0.3.0", False),
])
def test_online_versions_never_offer_downgrades(contract, offered, installed, newer):
    bundle = _bundle(contract)
    bundle.manifest["version"] = offered
    snapshot = _power_v2_snapshot(contract, read_only=False)
    snapshot = replace(snapshot, versions={**snapshot.versions, "firmware": installed})
    assert release_is_newer(_release(bundle), snapshot) is newer


@pytest.mark.parametrize(("offered", "installed", "expected"), [
    ("20260909.10-g7989ccbf-dirty", "20260909.09-g7989ccbf-dirty", True),
    ("20260908.02-g7989ccbf-dirty", "20260909.01-g7989ccbf-dirty", False),
    ("20260909.01-g7989ccbf", "20260909.01-g7989ccbf", False),
])
def test_same_version_uses_numeric_project_build_sequence(contract, offered, installed, expected):
    bundle = _bundle(contract, build_id=offered)
    snapshot = _power_v2_snapshot(contract, read_only=False)
    snapshot = replace(snapshot, versions={"firmware": bundle.manifest["version"], "build_id": installed})
    assert release_is_newer(_release(bundle), snapshot) is expected


def test_sample_channel_preserves_real_package_state_and_cannot_enter_stable(contract):
    bundle = _bundle(contract, validation_state="sample-verified", git_dirty=True)
    bundle.manifest.update(channel="sample", minimum_app_version="0.1.0", signature="")
    snapshot = _power_v2_snapshot(contract, read_only=False)
    assert _validate_release_manifest(bundle.manifest, snapshot, channel="sample")["git_dirty"] is True
    package = load_remote_firmware_bundle(replace(bundle, channel="sample"), contract)
    assert package.validation_state == "sample-verified"
    with pytest.raises(FirmwareReleaseError, match="渠道"):
        _validate_release_manifest(bundle.manifest, snapshot)
    with pytest.raises(FirmwareReleaseError, match="渠道"):
        load_remote_firmware_bundle(bundle, contract)


@pytest.mark.parametrize(("field", "value", "error"), [
    ("minimum_app_version", "9.0.0", "先升级"),
    ("hardware_revision", "REV_A", "不一致"),
    ("device_model", "BORING-01", "不一致"),
    ("download_url", "http://example.test/app.bin", "HTTPS"),
    ("signature", "not-verified", "验签"),
    ("mandatory", "false", "类型"),
])
def test_contract_rejects_unusable_online_metadata(contract, field, value, error):
    bundle = _bundle(contract)
    bundle.manifest[field] = value
    with pytest.raises(FirmwareReleaseError, match=error):
        _validate_release_manifest(bundle.manifest, _power_v2_snapshot(contract, read_only=False))


def test_online_download_cannot_replace_an_active_local_install(qtbot, contract, tmp_path):
    from test_firmware_transaction import _package
    source = FakeReleaseSource()
    gateway = RecordingDemoGateway(contract, "ready")
    model = MainViewModel(gateway, contract, firmware_release_source=source)
    try:
        model.start()
        qtbot.waitUntil(lambda: model.model.snapshot is not None, timeout=1000)
        source.release_found.emit(_release(_bundle(contract)))
        package = model.load_firmware_package(_package(tmp_path, contract))
        model.check_remote_firmware()
        with pytest.raises(ValueError, match="在线固件请求"):
            model.start_firmware_update()
        source.release_found.emit(_release(_bundle(contract)))
        model.download_remote_firmware()
        with pytest.raises(ValueError, match="在线固件请求"):
            model.start_firmware_update()
        assert model.firmware_update.package is package
        assert not any(command.name.startswith("FW_") for command in gateway.commands)
    finally:
        model.shutdown()


def test_release_for_previous_hardware_is_not_offered(qtbot, contract):
    source = FakeReleaseSource()
    gateway = DemoGateway(contract, "ready")
    model = MainViewModel(gateway, contract, firmware_release_source=source)
    try:
        model.start()
        qtbot.waitUntil(lambda: model.model.snapshot is not None, timeout=1000)
        bundle = _bundle(contract)
        bundle.manifest["hardware_id"] = "WMP-S3-REV-A"
        source.release_found.emit(_release(bundle))
        assert model.remote_firmware.state is RemoteFirmwareState.FAILED
        assert model.remote_firmware.release is None
        assert "型号已改变" in model.remote_firmware.technical
    finally:
        model.shutdown()
