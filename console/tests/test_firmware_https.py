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
from controller_config.firmware_update import FirmwareUpdateState
from controller_config.viewmodels.main import MainViewModel
from test_firmware_release import _bundle, RecordingDemoGateway


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
def test_https_discovery_download_validation_install_and_reconnect(qtbot, contract, https_server, corrupt):
    root, base_url, requests = https_server
    bundle = _bundle(contract, validation_state="sample-verified", git_dirty=True, build_id="0.3.0-alpha.2")
    bundle.manifest.update(
        device_model=contract.product_id, hardware_revision=bundle.manifest["hardware_id"],
        channel="sample", download_url=base_url + "/build/wired_macro_pad.bin",
        minimum_app_version="0.1.0", mandatory=False, signature="",
        release_notes="HTTPS integration test", published_at="2026-09-09T00:00:00Z",
    )
    (root / "firmware-manifest.json").write_text(json.dumps(bundle.manifest))
    (root / "build").mkdir()
    image = bytearray(bundle.image_data)
    if corrupt:
        image[-1] ^= 1
    (root / "build/wired_macro_pad.bin").write_bytes(image)
    source = releases.HttpFirmwareReleaseSource(base_url + "/firmware-manifest.json", channel="sample")
    gateway = RecordingDemoGateway(contract, "ready")
    model = MainViewModel(gateway, contract, firmware_release_source=source)
    try:
        model.start()
        qtbot.waitUntil(lambda: model.remote_firmware.state in {releases.RemoteFirmwareState.AVAILABLE, releases.RemoteFirmwareState.FAILED}, timeout=5000)
        assert model.remote_firmware.state is releases.RemoteFirmwareState.AVAILABLE, model.remote_firmware.technical
        assert requests == ["/firmware-manifest.json"]
        model.download_remote_firmware()
        qtbot.waitUntil(lambda: model.remote_firmware.state in {releases.RemoteFirmwareState.DOWNLOADED, releases.RemoteFirmwareState.FAILED}, timeout=5000)
        assert requests == ["/firmware-manifest.json", "/build/wired_macro_pad.bin"]
        assert not any(command.name.startswith("FW_") for command in gateway.commands)
        if corrupt:
            assert model.remote_firmware.state is releases.RemoteFirmwareState.FAILED
            assert "SHA-256" in model.remote_firmware.technical
            assert model.firmware_update.package is None
            return
        assert model.remote_firmware.state is releases.RemoteFirmwareState.DOWNLOADED, model.remote_firmware.technical
        model.start_firmware_update()
        qtbot.waitUntil(lambda: model.firmware_update.state in {FirmwareUpdateState.COMPLETED, FirmwareUpdateState.FAILED}, timeout=6000)
        assert model.firmware_update.state is FirmwareUpdateState.COMPLETED, model.firmware_update.message
        assert model.remote_firmware.state is releases.RemoteFirmwareState.CURRENT
        assert model.model.snapshot.versions["firmware"] == bundle.manifest["version"]
        assert model.model.snapshot.versions["build_id"] == bundle.manifest["build_id"]
        assert gateway.commands[-1].name == "FW_STATUS"
    finally:
        model.shutdown()
