from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtNetwork import QNetworkReply

from controller_config import desktop_update_windows as updater


class Reply(QObject):
    readyRead = Signal()
    finished = Signal()

    def __init__(self, url):
        super().__init__()
        self.data = b""
        self.address = url
        self.aborted = False
        self.code = 200
        self.network_error = QNetworkReply.NetworkError.NoError

    def readAll(self):
        data, self.data = self.data, b""
        return data

    def url(self):
        return QUrl(self.address)

    def attribute(self, _):
        return self.code

    def error(self):
        return self.network_error

    def errorString(self):
        return "connection failed"

    def abort(self):
        self.aborted = True
        self.finished.emit()

    def deliver(self, data):
        self.data = data
        self.readyRead.emit()
        self.finished.emit()


class Network:
    def get(self, request):
        self.request = request
        self.reply = Reply(request.url().toString())
        return self.reply


@pytest.fixture
def update(qtbot, monkeypatch, tmp_path):
    monkeypatch.setattr(updater, "_installed_directory", lambda: tmp_path / "installed")
    monkeypatch.setattr(updater.platform, "machine", lambda: "AMD64")
    key = Ed25519PrivateKey.generate()
    public_key = base64.b64encode(key.public_key().public_bytes_raw()).decode()
    instance = updater.WindowsDesktopUpdater({"feed_url": "https://updates.example.com/windows.json", "public_key": public_key}, lambda: True)
    instance._network = Network()
    payload = b"example signed installer contents"
    release = dict(version="99.0.0", url="https://updates.example.com/installer.exe", size=len(payload), arch="x86_64", signature=base64.b64encode(key.sign(payload)).decode())
    yield instance, release, payload
    instance.shutdown()


def available(instance, release):
    instance.check()
    assert instance.status.state == "checking"
    instance._network.reply.deliver(json.dumps(release).encode())
    assert instance.status.state == "available"


def test_background_update_checks_run_hourly(update):
    instance, _, _ = update
    assert instance._periodic.interval() == 60 * 60 * 1000


def ready(qtbot, instance, release, payload):
    available(instance, release)
    instance.download()
    assert instance.status.state == "downloading"
    instance._network.reply.deliver(payload)
    qtbot.waitUntil(lambda: instance.status.state != "verifying")


def test_download_signature_then_install_after_prepare(qtbot, update, monkeypatch):
    instance, release, payload = update
    ready(qtbot, instance, release, payload)
    assert instance.status.state == "ready"
    assert instance._installer.read_bytes() == payload
    events = []
    instance.prepare_restart = lambda: events.append("prepare") or True
    monkeypatch.setattr(updater, "_launch_installer", lambda path, directory: events.append("launch"))
    instance.quit_requested.connect(lambda: events.append("quit"))
    instance.install()
    assert events == ["prepare", "launch", "quit"]
    assert instance.status.state == "installing"
    # Handoff must retain the verified installer until the external helper runs.
    instance.shutdown()
    assert instance._installer.exists()
    instance._cleanup()


@pytest.mark.parametrize("corruption", ["truncated", "modified", "wrong_key"])
def test_bad_installer_never_becomes_ready(qtbot, update, corruption):
    instance, release, payload = update
    if corruption == "truncated":
        payload = payload[:-1]
    elif corruption == "modified":
        payload = b"X" + payload[1:]
    else:
        instance.config["public_key"] = base64.b64encode(Ed25519PrivateKey.generate().public_key().public_bytes_raw()).decode()
    ready(qtbot, instance, release, payload)
    assert instance.status.state == "failed"
    assert instance._installer is None


@pytest.mark.parametrize("change", [{"url": "http://example.com/app.exe"}, {"arch": "arm64"}, {"signature": "bad"}, {"size": 0}, {"version": "invalid"}])
def test_bad_feed_is_rejected(update, change):
    instance, release, _ = update
    release.update(change)
    instance.check()
    instance._network.reply.deliver(json.dumps(release).encode())
    assert instance.status.state == "failed"


@pytest.mark.parametrize("version", ["0.0.1", updater.__version__])
def test_equal_or_old_versions_not_offered(update, version):
    instance, release, _ = update
    release["version"] = version
    instance.check()
    instance._network.reply.deliver(json.dumps(release).encode())
    assert instance.status.state == "current"


@pytest.mark.parametrize("state", ["checking", "downloading"])
def test_cancel_aborts_and_discards_partial_file(update, state):
    instance, release, payload = update
    if state == "downloading":
        available(instance, release)
        instance.download()
        instance._network.reply.data = payload[:4]
        instance._network.reply.readyRead.emit()
        directory = instance._directory
    else:
        instance.check()
        directory = None
    reply = instance._network.reply
    instance.cancel()
    assert reply.aborted
    assert instance.status.state == ("available" if state == "downloading" else "idle")
    if directory:
        assert not directory.exists()


@pytest.mark.parametrize("failure", ["http", "tls", "redirect", "oversize"])
def test_transport_failure_never_verifies(update, failure):
    instance, release, payload = update
    available(instance, release)
    instance.download()
    reply = instance._network.reply
    if failure == "http":
        reply.code = 404
    elif failure == "tls":
        reply.network_error = QNetworkReply.NetworkError.SslHandshakeFailedError
    elif failure == "redirect":
        reply.address = "http://updates.example.com/installer.exe"
    else:
        payload += b"extra"
    reply.deliver(payload)
    assert instance.status.state == "failed"
    assert instance._installer is None


def test_declined_restart_does_not_launch_or_quit(qtbot, update, monkeypatch):
    instance, release, payload = update
    ready(qtbot, instance, release, payload)
    instance.prepare_restart = lambda: False
    monkeypatch.setattr(updater, "_launch_installer", lambda *_: pytest.fail("must not launch"))
    instance.quit_requested.connect(lambda: pytest.fail("must not quit"))
    instance.install()
    assert instance.status.state == "ready"


def test_helper_launch_failure_does_not_quit(qtbot, update, monkeypatch):
    instance, release, payload = update
    ready(qtbot, instance, release, payload)
    def fail(*_):
        raise OSError("PowerShell unavailable")
    monkeypatch.setattr(updater, "_launch_installer", fail)
    instance.quit_requested.connect(lambda: pytest.fail("must not quit"))
    instance.install()
    assert instance.status.state == "ready"
    assert "PowerShell unavailable" in instance.status.message


def test_portable_install_disabled(update, monkeypatch):
    instance, _, _ = update
    monkeypatch.setattr(updater, "_installed_directory", lambda: None)
    instance.start()
    assert instance.status.state == "unconfigured"
    assert "安装版" in instance.status.message


def test_helper_preserves_installed_path_and_does_not_kill_processes(tmp_path, monkeypatch):
    installer = tmp_path / "BORING-Console-Setup.exe"
    installer.write_bytes(b"installer")
    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    monkeypatch.setattr(updater.subprocess, "DETACHED_PROCESS", 8, raising=False)
    monkeypatch.setattr(updater.subprocess, "CREATE_NEW_PROCESS_GROUP", 512, raising=False)
    calls = []
    monkeypatch.setattr(updater.subprocess, "Popen", lambda args, **kwargs: calls.append((args, kwargs)))
    updater._launch_installer(installer, Path("C:/Users/Tester's Account/BORING Console"))
    script = (tmp_path / "apply-update.ps1").read_text(encoding="utf-8-sig")
    assert "Tester''s Account/BORING Console" in script
    assert '/DIR="C:/Users/' in script
    assert "Get-Process -Id $OriginalProcess" in script
    assert "Stop-Process" not in script
    assert "/NORESTART" in script and "/NOCLOSEAPPLICATIONS" in script
    assert calls[0][1]["cwd"] == str(tmp_path)
    assert "-WorkingDirectory 'C:/Users/Tester''s Account/BORING Console'" in script
    assert "Set-Location -LiteralPath $env:TEMP" in script
    # Failed installation exits before cleanup, retaining installer/logs for repair.
    assert script.index("exit 1") < script.index("Remove-Item -LiteralPath")
    assert "-Recurse -Force -ErrorAction SilentlyContinue" in script


@pytest.mark.parametrize("config", [
    {"feed_url": "http://updates.example.com/feed.json", "public_key": "bad"},
    {"feed_url": "https://updates.example.com/feed.json", "public_key": "bad"},
    {"feed_url": "", "public_key": ""},
])
def test_invalid_configuration_never_requests_network(update, config):
    instance, _, _ = update
    instance.config = config
    instance.start()
    assert instance.status.state == "unconfigured"
    assert not hasattr(instance._network, "reply")


def test_disk_flush_failure_is_reported_without_verification(update):
    instance, release, _ = update
    available(instance, release)
    instance.download()
    instance._file.close()
    class FullDisk:
        def write(self, data):
            return len(data)
        def tell(self):
            return release["size"]
        def close(self):
            raise OSError("disk full")
    instance._file = FullDisk()
    instance._network.reply.deliver(b"")
    assert instance.status.state == "failed"
    assert "disk full" in instance.status.message
    assert instance._verification is None


def test_download_retry_requests_same_release_and_verifies_again(qtbot, update):
    instance, release, payload = update
    available(instance, release)
    instance.download()
    previous = instance._directory
    instance._network.reply.network_error = QNetworkReply.NetworkError.TimeoutError
    instance._network.reply.deliver(payload[:4])
    assert instance.status.retry_action == "download"
    assert not previous.exists()
    instance.retry()
    assert instance._network.request.url().toString() == release["url"]
    instance._network.reply.deliver(payload)
    qtbot.waitUntil(lambda: instance.status.state != "verifying")
    assert instance.status.state == "ready"
    assert instance.status.retry_action == ""


def test_failed_feed_retry_checks_feed_not_installer(update):
    instance, _, _ = update
    instance.check()
    instance._network.reply.code = 503
    instance._network.reply.deliver(b"")
    assert instance.status.retry_action == "check"
    instance.retry()
    assert instance.status.state == "checking"
    assert instance._network.request.url().toString() == instance.config["feed_url"]


def test_install_retry_keeps_verified_package_and_checks_restart_again(qtbot, update, monkeypatch):
    instance, release, payload = update
    ready(qtbot, instance, release, payload)
    installer = instance._installer
    events = []
    instance.prepare_restart = lambda: events.append("prepare") or True
    def fail(*_):
        raise OSError("PowerShell unavailable")
    monkeypatch.setattr(updater, "_launch_installer", fail)
    instance.install()
    assert instance.status.retry_action == "install"
    assert installer.read_bytes() == payload
    monkeypatch.setattr(updater, "_launch_installer", lambda path, _: events.append(path))
    instance.retry()
    assert events == ["prepare", "prepare", installer]
    assert instance.status.state == "installing"
    instance._cleanup()
