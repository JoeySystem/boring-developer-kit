"""Exercise the real HTTPS client through package validation and the OTA view model."""
from datetime import datetime, timedelta, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import ssl
import threading

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
from PySide6.QtNetwork import QSslCertificate
import pytest

import controller_config.firmware_release as releases
from controller_config.i18n import translate_ui_text
from controller_config.firmware_update import FirmwareUpdateState
from controller_config.viewmodels.main import MainViewModel
from test_firmware_release import _bundle, _resign, RecordingDemoGateway, trust_test_signing_key, official_demo_release, remember_test_snapshot


@pytest.fixture
def https_server(tmp_path, monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(timezone.utc)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "BORING temporary test CA")])
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_certificate = (
        x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
        .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    certificate = (
        x509.CertificateBuilder().subject_name(name).issuer_name(ca_name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    certificate_pem = certificate.public_bytes(serialization.Encoding.PEM)
    (tmp_path / "server.crt").write_bytes(certificate_pem)
    (tmp_path / "server.key").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    web_root = tmp_path / "www"
    web_root.mkdir()
    requests = []

    class Handler(SimpleHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            super().do_GET()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(web_root)))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(tmp_path / "server.crt", tmp_path / "server.key")
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    original_request = releases._request

    def trusted_test_request(url):
        request = original_request(url)
        configuration = request.sslConfiguration()
        configuration.addCaCertificate(QSslCertificate(ca_certificate.public_bytes(serialization.Encoding.PEM)))
        request.setSslConfiguration(configuration)
        return request

    # Trust this test server only in this test's requests, never disable verification.
    monkeypatch.setattr(releases, "_request", trusted_test_request)
    try:
        yield web_root, f"https://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("corrupt", [False, True])
@pytest.mark.parametrize("origin", ["official", "custom", "unknown"])
def test_https_discovery_download_validation_install_and_reconnect(qtbot, contract, https_server, corrupt, origin):
    root, base_url, requests = https_server
    bundle = _bundle(contract, validation_state="sample-verified", git_dirty=True, build_id="0.3.0-alpha.2")
    bundle.manifest.update(
        device_model=contract.product_id, hardware_revision=bundle.manifest["hardware_id"],
        channel="sample", download_url=base_url + "/build/wired_macro_pad.bin",
        minimum_app_version="0.1.16", mandatory=False,
        release_notes="HTTPS integration test", published_at="2026-09-09T00:00:00Z",
    )
    _resign(bundle)
    (root / "firmware-manifest.json").write_text(json.dumps(bundle.manifest))
    (root / "build").mkdir()
    image = bytearray(bundle.image_data)
    if corrupt:
        image[-1] ^= 1
    (root / "build/wired_macro_pad.bin").write_bytes(image)
    source = releases.HttpFirmwareReleaseSource(base_url + "/firmware-manifest.json", channel="sample")
    gateway = RecordingDemoGateway(contract, "ready")
    model = MainViewModel(gateway, contract, firmware_release_source=source)
    if origin != "official":
        gateway._installed_firmware = {"firmware":"99.0.0", "build_id":"custom-https" if origin == "custom" else "local-unidentified"}
    try:
        model.start()
        qtbot.waitUntil(lambda: model.model.snapshot is not None)
        if origin != "official":
            assert requests == []
            model.check_remote_firmware()
        expected = releases.RemoteFirmwareState.AVAILABLE if origin == "official" else releases.RemoteFirmwareState.RESTORE_AVAILABLE
        qtbot.waitUntil(lambda: model.remote_firmware.state in {expected, releases.RemoteFirmwareState.FAILED}, timeout=5000)
        assert model.remote_firmware.state is expected, model.remote_firmware.technical
        assert requests == ["/firmware-manifest.json"]
        # Check the ordinary maintenance page exposes current and target data.
        from PySide6.QtWidgets import QLabel
        from controller_config.views.main_window import MainWindow
        window = MainWindow(model)
        qtbot.addWidget(window)
        model.navigate("firmware")
        texts = "\n".join(label.text() for label in window.findChildren(QLabel))
        current_text = window.findChild(QLabel, "firmwareCurrentVersion").text()
        release_text = window.findChild(QLabel, "remoteFirmwareReleaseSummary").text()
        assert model.model.snapshot.versions['firmware'] in current_text
        assert model.model.snapshot.versions['build_id'] in texts
        assert bundle.manifest['version'] in release_text
        assert bundle.manifest['build_id'] in release_text
        # The surrounding release block is translated; server-provided notes
        # must remain present and unchanged in the maintenance page.
        assert "HTTPS integration test" in texts
        model.download_remote_firmware()
        qtbot.waitUntil(lambda: model.remote_firmware.state in {releases.RemoteFirmwareState.DOWNLOADED, releases.RemoteFirmwareState.FAILED}, timeout=5000)
        assert requests == ["/firmware-manifest.json", "/build/wired_macro_pad.bin"]
        assert not any(command.name.startswith("FW_") for command in gateway.commands)
        if corrupt:
            assert model.remote_firmware.state is releases.RemoteFirmwareState.FAILED
            assert "SHA-256" in model.remote_firmware.technical
            assert model.remote_firmware.message == "固件校验未通过，未安装。"
            assert model.firmware_update.package is None
            return
        assert model.remote_firmware.state is releases.RemoteFirmwareState.DOWNLOADED, model.remote_firmware.technical
        model.start_firmware_update()
        qtbot.waitUntil(lambda: model.firmware_update.state in {FirmwareUpdateState.COMPLETED, FirmwareUpdateState.FAILED}, timeout=6000)
        assert model.firmware_update.state is FirmwareUpdateState.COMPLETED, model.firmware_update.message
        assert model.remote_firmware.state is releases.RemoteFirmwareState.CURRENT
        assert model.model.snapshot.versions["firmware"] == bundle.manifest["version"]
        assert model.model.snapshot.versions["build_id"] == bundle.manifest["build_id"]
        assert [command.name for command in gateway.commands if command.name.startswith("FW_")][-1] == "FW_STATUS"
    finally:
        model.shutdown()


def test_https_404_keeps_retry_available_without_claiming_current(qtbot, contract, https_server):
    from PySide6.QtWidgets import QLabel, QPushButton
    from controller_config.views.main_window import MainWindow
    _, base_url, requests = https_server
    source = releases.HttpFirmwareReleaseSource(base_url + '/not-published/firmware-manifest.json', channel='sample')
    gateway = RecordingDemoGateway(contract, 'ready')
    model = MainViewModel(gateway, contract, firmware_release_source=source)
    window = MainWindow(model)
    qtbot.addWidget(window)
    try:
        model.start()
        qtbot.waitUntil(lambda: model.remote_firmware.state is releases.RemoteFirmwareState.UNPUBLISHED, timeout=5000)
        model.navigate('firmware')
        assert 'HTTP 404' in model.remote_firmware.technical
        assert '尚未发布' in model.remote_firmware.technical
        assert model.remote_firmware.release is None
        assert model.firmware_update.package is None
        assert window.findChild(QPushButton, 'checkRemoteFirmware').isEnabled()
        assert not window.findChild(QPushButton, 'downloadRemoteFirmware')
        texts = [label.text() for label in window.findChildren(QLabel)]
        assert '暂无可用的固件发布' in model.remote_firmware.message
        assert '已连接更新服务' in model.remote_firmware.message
        assert '失败' not in model.remote_firmware.message
        technical = window.findChild(QLabel, 'remoteFirmwareTechnical')
        toggle = window.findChild(QPushButton, 'remoteFirmwareDetailsToggle')
        assert technical.isHidden()
        assert 'HTTP 404' in technical.text()
        assert window.findChild(QPushButton, 'checkRemoteFirmware').text() == '重新检查'
        toggle.click()
        assert not technical.isHidden()
        toggle.click()
        assert technical.isHidden()
        assert not any('已是最新' in text or '已安装并读回' in text for text in texts)
        window.findChild(QPushButton, 'checkRemoteFirmware').click()
        qtbot.waitUntil(lambda: len(requests) == 2 and model.remote_firmware.state is releases.RemoteFirmwareState.UNPUBLISHED, timeout=5000)
        assert not any(command.name.startswith('FW_') for command in gateway.commands)
    finally:
        model.shutdown()


def test_untrusted_https_certificate_is_rejected_before_manifest(qtbot, contract, https_server, monkeypatch):
    from PySide6.QtNetwork import QNetworkRequest
    _, base_url, requests = https_server
    # A normal request trusts the system CA set, not the fixture's temporary CA.
    monkeypatch.setattr(releases, '_request', QNetworkRequest)
    source = releases.HttpFirmwareReleaseSource(base_url + '/firmware-manifest.json', channel='sample')
    gateway = RecordingDemoGateway(contract, 'ready')
    model = MainViewModel(gateway, contract, firmware_release_source=source)
    try:
        model.start()
        qtbot.waitUntil(lambda: model.remote_firmware.state is releases.RemoteFirmwareState.FAILED, timeout=5000)
        assert model.remote_firmware.release is None
        assert model.firmware_update.package is None
        assert model.remote_firmware.message == "暂时无法连接更新服务，请检查网络后重试。"
        assert requests == []  # TLS failed before an HTTP request reached the server.
        assert not any(command.name.startswith('FW_') for command in gateway.commands)
    finally:
        model.shutdown()


def test_engineering_default_source_resolves_deployed_sample_entry(contract):
    from controller_config.transport.demo import _power_v2_snapshot
    config = releases.default_firmware_source()
    assert config['channel'] == 'sample'
    assert releases.manifest_url_for_snapshot(config['manifest_url'], _power_v2_snapshot(contract, read_only=False)).toString() == (
        'https://updates.boringconcept.com/firmware/wired-macro-pad-v1/'
        'WMP-S3-MATRIX12-POWER-V2/sample/firmware-manifest.json'
    )


def test_missing_image_is_download_failure_not_empty_channel(qtbot, contract, https_server):
    root, base_url, requests = https_server
    bundle = _bundle(contract, validation_state='sample-verified', git_dirty=True)
    bundle.manifest.update(channel='sample', download_url=base_url + '/missing.bin')
    _resign(bundle)
    (root / 'firmware-manifest.json').write_text(json.dumps(bundle.manifest))
    source = releases.HttpFirmwareReleaseSource(base_url + '/firmware-manifest.json', channel='sample')
    gateway = RecordingDemoGateway(contract, 'ready')
    model = MainViewModel(gateway, contract, firmware_release_source=source)
    try:
        model.start()
        qtbot.waitUntil(lambda: model.remote_firmware.state is releases.RemoteFirmwareState.AVAILABLE, timeout=5000)
        model.download_remote_firmware()
        qtbot.waitUntil(lambda: model.remote_firmware.state is releases.RemoteFirmwareState.FAILED, timeout=5000)
        assert model.remote_firmware.message == '固件下载暂不可用，未安装。请重新检查发布信息。'
        assert 'HTTP 404' in model.remote_firmware.technical
        assert model.remote_firmware.release is not None  # Explicit check/download retry remains possible.
        assert model.firmware_update.package is None
        assert not any(command.name.startswith('FW_') for command in gateway.commands)
    finally:
        model.shutdown()
