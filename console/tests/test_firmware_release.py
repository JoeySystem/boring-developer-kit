from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import struct

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from controller_config import firmware_signature
from controller_config.firmware_signature import sign_manifest, verify_manifest_signature, FirmwareSignatureError

import pytest
from PySide6.QtCore import QObject, QTimer, QUrl
from PySide6.QtNetwork import QNetworkReply
from PySide6.QtWidgets import QApplication, QMessageBox, QProgressBar, QPushButton

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


# Test-only ephemeral authority; no production signing key is loaded by these tests.
_TEST_SIGNING_KEY = Ed25519PrivateKey.generate()


@pytest.fixture(autouse=True)
def official_demo_release(monkeypatch, contract, trust_test_signing_key, isolated_firmware_release_history):
    from controller_config.firmware_origin import FirmwareReleaseHistory
    import controller_config.transport.demo as demo
    original = demo._power_v2_snapshot
    def official_snapshot(contract, *, read_only):
        snapshot = original(contract, read_only=read_only)
        return replace(snapshot, versions={**snapshot.versions, "build_id":"20260901.01-g12345678"})
    snapshot = official_snapshot(contract, read_only=False)
    FirmwareReleaseHistory().remember(sign_manifest({
        "product_id":snapshot.identity["product_id"], "hardware_id":snapshot.identity["hardware_id"],
        "version":snapshot.versions["firmware"], "build_id":snapshot.versions["build_id"],
    }, _TEST_SIGNING_KEY))
    monkeypatch.setattr(demo, "_power_v2_snapshot", official_snapshot)


@pytest.fixture(autouse=True)
def trust_test_signing_key(monkeypatch):
    monkeypatch.setattr(
        firmware_signature, "default_firmware_public_key",
        lambda: _TEST_SIGNING_KEY.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        ),
    )


def _resign(bundle):
    bundle.manifest.update(sign_manifest(bundle.manifest, _TEST_SIGNING_KEY))
    return bundle


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

    def attribute(self, _name):
        return None

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
        manifest=sign_manifest(manifest, _TEST_SIGNING_KEY),
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


def test_firmware_page_offers_one_install_action_and_confirms_after_download(
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

    qtbot.waitUntil(lambda: window.findChild(QPushButton, "installRemoteFirmware") is not None)
    install = window.findChild(QPushButton, "installRemoteFirmware")
    assert install is not None and install.text() == "安装最新版固件…"
    assert window.findChild(QPushButton, "checkRemoteFirmware") is None
    install.click()
    source.download_progress.emit(512, 1024)
    progress = window.findChild(QProgressBar, "remoteFirmwareProgress")
    assert progress is not None and progress.value() == 50

    def cancel_install() -> None:
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, QMessageBox)
        dialog.button(QMessageBox.Cancel).click()

    QTimer.singleShot(0, cancel_install)
    source.download_completed.emit(bundle)
    window._confirm_downloaded_remote_firmware()
    install = window.findChild(QPushButton, "startFirmwareUpdate")
    assert install is not None and install.text() == "安装最新版固件…"
    assert not any(command.name.startswith("FW_") for command in gateway.commands)


def test_remote_install_waits_for_usb_after_bluetooth_download(qtbot, contract) -> None:
    source = FakeReleaseSource()
    gateway = RecordingDemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract, firmware_release_source=source)
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.show()
    view_model.start()
    qtbot.waitUntil(lambda: view_model.model.snapshot is not None, timeout=1000)
    bluetooth_snapshot = replace(
        view_model.model.snapshot,
        port_name="ble:test-device",
    )
    gateway.snapshot_ready.emit(bluetooth_snapshot)
    view_model.navigate("firmware")

    bundle = _bundle(contract)
    source.release_found.emit(_release(bundle))
    qtbot.waitUntil(lambda: window.findChild(QPushButton, "installRemoteFirmware") is not None)
    window.findChild(QPushButton, "installRemoteFirmware").click()
    source.download_completed.emit(bundle)
    qtbot.wait(20)

    continue_button = window.findChild(QPushButton, "startFirmwareUpdate")
    assert continue_button is not None
    assert continue_button.text() == "请连接 USB 后继续安装"
    assert not continue_button.isEnabled()
    assert QApplication.activeModalWidget() is None
    assert not any(command.name.startswith("FW_") for command in gateway.commands)
    downloaded_package = view_model.firmware_update.package

    def cancel_install() -> None:
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, QMessageBox)
        dialog.button(QMessageBox.Cancel).click()

    QTimer.singleShot(0, cancel_install)
    gateway.snapshot_ready.emit(replace(
        bluetooth_snapshot,
        port_name="/dev/cu.usbmodem-test",
    ))
    window._confirm_downloaded_remote_firmware()
    assert window.findChild(QPushButton, "startFirmwareUpdate").text() == "安装最新版固件…"
    assert window.findChild(QPushButton, "startFirmwareUpdate").isEnabled()
    assert view_model.firmware_update.package is downloaded_package
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
    bundle.manifest.update(channel="sample", minimum_app_version="0.1.16")
    _resign(bundle)
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
    ("mandatory", "false", "类型"),
])
def test_contract_rejects_unusable_online_metadata(contract, field, value, error):
    bundle = _bundle(contract)
    bundle.manifest[field] = value
    _resign(bundle)
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


def test_cancelled_request_cannot_consume_new_reply_or_emit_progress(qapp, contract):
    from PySide6.QtCore import Signal
    class Reply(FakeManifestReply):
        finished = Signal()
        downloadProgress = Signal(int, int)
        def abort(self):
            pass  # Model a completion already queued before cancellation.
    bundle = _bundle(contract)
    url = 'https://example.com/firmware-manifest.json'
    old, new = [Reply(json.dumps(bundle.manifest).encode(), url) for _ in range(2)]
    class Network:
        def __init__(self): self.replies = [old, new]
        def get(self, _request): return self.replies.pop(0)
    source = HttpFirmwareReleaseSource(url, network=Network())
    found, progress = [], []
    source.release_found.connect(found.append)
    source.download_progress.connect(lambda *_: progress.append(True))
    snapshot = _power_v2_snapshot(contract, read_only=False)
    source.check(snapshot)
    source.cancel()
    source.check(snapshot)
    old.finished.emit()
    source._on_download_progress(old, 100, 200, 200, '固件镜像')
    assert source._reply is new
    assert found == [] and progress == []
    new.finished.emit()
    assert len(found) == 1
    assert source._reply is None


@pytest.mark.parametrize("channel", ["stable", "sample"])
@pytest.mark.parametrize("signature", [None, "", "ed25519-v2:AAAA", "not-verified"])
def test_online_unsigned_or_unknown_signature_format_is_rejected(contract, channel, signature):
    bundle = _bundle(
        contract, validation_state="released" if channel == "stable" else "built",
        git_dirty=channel == "sample",
    )
    bundle = replace(bundle, channel=channel)
    bundle.manifest["channel"] = channel
    _resign(bundle)
    if signature is None:
        bundle.manifest.pop("signature")
    else:
        bundle.manifest["signature"] = signature
    with pytest.raises(FirmwareReleaseError):
        _validate_release_manifest(
            bundle.manifest, _power_v2_snapshot(contract, read_only=False), channel=channel,
        )
    # The downloaded-package entry point must independently enforce the same rule.
    with pytest.raises(FirmwareReleaseError):
        load_remote_firmware_bundle(bundle, contract)


def test_signature_from_untrusted_publisher_is_rejected(contract):
    bundle = _bundle(contract)
    unknown_key = Ed25519PrivateKey.generate()
    bundle.manifest.update(sign_manifest(bundle.manifest, unknown_key))
    with pytest.raises(FirmwareSignatureError):
        verify_manifest_signature(bundle.manifest)
    with pytest.raises(FirmwareReleaseError):
        _validate_release_manifest(bundle.manifest, _power_v2_snapshot(contract, read_only=False))
    with pytest.raises(FirmwareReleaseError):
        load_remote_firmware_bundle(bundle, contract)


@pytest.mark.parametrize("field,value", [
    ("version", "0.3.0-alpha.3"),
    ("product_id", "other-product"),
    ("hardware_id", "WMP-S3-MATRIX12-V1"),
    ("build_id", "20260912.99-attacker"),
    ("download_url", "https://attacker.example/app.bin"),
    ("sha256", "0" * 64),
    ("minimum_app_version", "0.1.0"),
    ("release_notes", "替换后的发布说明"),
])
def test_signature_binds_firmware_identity_download_and_integrity(contract, field, value):
    bundle = _bundle(contract)
    bundle.manifest[field] = value
    with pytest.raises(FirmwareSignatureError):
        verify_manifest_signature(bundle.manifest)
    with pytest.raises(FirmwareReleaseError):
        _validate_release_manifest(bundle.manifest, _power_v2_snapshot(contract, read_only=False))
    with pytest.raises(FirmwareReleaseError):
        load_remote_firmware_bundle(bundle, contract)


def test_replacing_image_and_recalculating_digest_cannot_forge_release(contract):
    bundle = _bundle(contract)
    changed_image = bytearray(bundle.image_data)
    changed_image[-1] ^= 1  # Keep all image headers/identities valid.
    bundle = replace(bundle, image_data=bytes(changed_image))
    bundle.manifest["sha256"] = hashlib.sha256(bundle.image_data).hexdigest()
    bundle.manifest["size"] = len(bundle.image_data)
    with pytest.raises(FirmwareReleaseError):
        load_remote_firmware_bundle(bundle, contract)


def test_signing_is_stable_across_json_formatting_and_does_not_mutate_input(contract):
    bundle = _bundle(contract)
    manifest = {key: value for key, value in bundle.manifest.items() if key != "signature"}
    manifest["release_notes"] = "固件发布说明"
    signed = sign_manifest(manifest, _TEST_SIGNING_KEY)
    assert "signature" not in manifest
    roundtrip = json.loads(json.dumps(dict(reversed(list(signed.items()))), ensure_ascii=True, indent=4))
    verify_manifest_signature(roundtrip)


def test_prepared_release_passes_real_online_validation_and_package_loader(contract, tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "firmware" / "tools"))
    from prepare_online_firmware import prepare_online_firmware

    bundle = _bundle(contract, validation_state="built", git_dirty=True)
    local_manifest = {key: value for key, value in bundle.manifest.items() if key != "signature"}
    source = tmp_path / "local-maintenance"
    source.mkdir()
    (source / local_manifest["image"]).write_bytes(bundle.image_data)
    local_path = source / "firmware-manifest.json"
    local_path.write_text(json.dumps(local_manifest), encoding="utf-8")
    key_path = tmp_path / "temporary-test-authority.pem"
    key_path.write_bytes(_TEST_SIGNING_KEY.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
    online_path = prepare_online_firmware(
        local_path, tmp_path / "online", base_url="https://updates.example.test",
        channel="sample", release_notes="签名发布工具到控制台集成",
        private_key_file=key_path,
    )
    online = json.loads(online_path.read_text(encoding="utf-8"))
    checked = _validate_release_manifest(
        online, _power_v2_snapshot(contract, read_only=False), channel="sample",
    )
    package = load_remote_firmware_bundle(RemoteFirmwareBundle(
        manifest=checked, image_data=(online_path.parent / checked["image"]).read_bytes(),
        manifest_url="https://updates.example.test/firmware-manifest.json", channel="sample",
    ), contract)
    assert package.build_id == local_manifest["build_id"]
    assert package.validation_state == "built"
    # Manual local maintenance continues accepting pre-signature local packages.
    from controller_config.firmware_update import FirmwarePackage
    assert FirmwarePackage.load(local_path, contract).build_id == package.build_id


def remember_test_snapshot(vm, snapshot):
    snapshot = replace(snapshot, versions={**snapshot.versions, "build_id":snapshot.versions.get("build_id", "20260901.01-g12345678")})
    vm._firmware_release_history.remember(sign_manifest({
        "product_id":snapshot.identity["product_id"], "hardware_id":snapshot.identity["hardware_id"],
        "version":snapshot.versions["firmware"], "build_id":snapshot.versions["build_id"],
    }, _TEST_SIGNING_KEY))
    return snapshot
