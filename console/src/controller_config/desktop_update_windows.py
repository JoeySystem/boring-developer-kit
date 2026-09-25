"""Windows desktop updates: HTTPS staging, Ed25519 provenance, then Inno Setup.

The external helper lives in TEMP, not the installation it replaces. Portable
and source launches are deliberately unsupported; they have no installed target.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from . import __version__
from .desktop_update import DESKTOP_UPDATE_CHECK_INTERVAL_SECONDS, UpdateStatus

_UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{7D499724-C0AE-438B-93C1-59E2D01785D7}_is1"


def _https(url: str) -> bool:
    parsed = QUrl(url)
    return parsed.isValid() and parsed.scheme() == "https" and bool(parsed.host()) and not parsed.userInfo()


def _version(value: str) -> tuple[int, int, int]:
    if not isinstance(value, str) or not re.fullmatch(r"\d+\.\d+\.\d+", value):
        raise ValueError("更新版本必须为 major.minor.patch")
    return tuple(map(int, value.split(".")))


def _installed_directory() -> Path | None:
    if sys.platform != "win32" or Path(sys.executable).name.lower() != "boring console.exe":
        return None
    import winreg
    for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _UNINSTALL_KEY, 0, winreg.KEY_READ | view) as key:
                directory = Path(winreg.QueryValueEx(key, "InstallLocation")[0])
            if directory.resolve() == Path(sys.executable).resolve().parent:
                return directory
        except OSError:
            continue
    return None


def _release(payload: bytes, current: str = __version__) -> dict:
    release = json.loads(payload)
    if not isinstance(release, dict):
        raise ValueError("更新信息格式错误")
    version = _version(release.get("version"))
    if version <= _version(current):
        return {}
    if release.get("arch") != "x86_64" or platform.machine().lower() not in ("amd64", "x86_64"):
        raise ValueError("此更新不适用于当前 Windows 架构")
    if not isinstance(release.get("url"), str) or not _https(release["url"]):
        raise ValueError("更新安装包必须使用 HTTPS")
    if type(release.get("size")) is not int or release["size"] <= 0:
        raise ValueError("更新信息缺少有效的安装包大小")
    signature = base64.b64decode(release.get("signature", ""), validate=True)
    if len(signature) != 64:
        raise ValueError("更新信息缺少有效签名")
    return release


def _verify(path: Path, release: dict, public_key: str) -> None:
    if path.stat().st_size != release["size"]:
        raise ValueError("安装包下载不完整，请重新下载")
    key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key, validate=True))
    key.verify(base64.b64decode(release["signature"], validate=True), path.read_bytes())


def _powershell_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _launch_installer(installer: Path, directory: Path) -> None:
    script = installer.parent / "apply-update.ps1"
    # Inno's argument parser needs explicit quotes inside ArgumentList for /DIR.
    arguments = '/VERYSILENT /SUPPRESSMSGBOXES /SP- /NORESTART /NOCLOSEAPPLICATIONS /NORESTARTAPPLICATIONS /DIR="' + str(directory) + '" /LOG="' + str(installer.parent / "install.log") + '"'
    script.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        f"$OriginalProcess = {os.getpid()}\n"
        "$Deadline = (Get-Date).AddMinutes(2)\n"
        "while (Get-Process -Id $OriginalProcess -ErrorAction SilentlyContinue) {\n"
        "  if ((Get-Date) -gt $Deadline) { exit 2 }\n"
        "  Start-Sleep -Milliseconds 250\n"
        "}\n"
        "try {\n"
        f"  $Result = Start-Process -FilePath {_powershell_literal(str(installer))} -ArgumentList {_powershell_literal(arguments)} -Wait -PassThru\n"
        "  if ($Result.ExitCode -ne 0) { throw ('Installer exit code: ' + $Result.ExitCode) }\n"
        f"  Start-Process -FilePath {_powershell_literal(str(directory / 'BORING Console.exe'))} -WorkingDirectory {_powershell_literal(str(directory))}\n"
        "} catch {\n"
        f"  $_ | Out-File -LiteralPath {_powershell_literal(str(installer.parent / 'update-error.txt'))}\n"
        "  Add-Type -AssemblyName System.Windows.Forms\n"
        "  [System.Windows.Forms.MessageBox]::Show('BORING Console 更新未完成。请重新打开软件，或使用安装包重新安装。', 'BORING Console') | Out-Null\n"
        "  exit 1\n"
        "}\n"
        "Set-Location -LiteralPath $env:TEMP\n"
        f"Remove-Item -LiteralPath {_powershell_literal(str(installer.parent))} -Recurse -Force -ErrorAction SilentlyContinue\n",
        encoding="utf-8-sig",
    )
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    subprocess.Popen(
        [str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        cwd=str(installer.parent), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
    )


class WindowsDesktopUpdater(QObject):
    changed = Signal(object)
    quit_requested = Signal()

    def __init__(self, config: dict, prepare_restart: Callable[[], bool], parent=None):
        super().__init__(parent)
        self.config = config
        self.prepare_restart = prepare_restart
        self.status = UpdateStatus()
        self._network = QNetworkAccessManager(self)
        self._reply = None
        self._file = None
        self._directory = None
        self._installer = None
        self._release = {}
        self._feed = bytearray()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="desktop-update-signature")
        self._verification = None
        self._poll = QTimer(self)
        self._poll.setInterval(50)
        self._poll.timeout.connect(self._verified)
        self._periodic = QTimer(self)
        self._periodic.setInterval(
            int(config.get(
                "check_interval_seconds", DESKTOP_UPDATE_CHECK_INTERVAL_SECONDS
            )) * 1000
        )
        self._periodic.timeout.connect(self.check)
        self._closed = False

    def _set(self, state: str, message: str = "", received: int = 0, total: int = 0, *, retry_action: str = ""):
        self.status = UpdateStatus(state=state, version=self._release.get("version", ""), received=received, total=total, message=message, retry_action=retry_action)
        self.changed.emit(self.status)

    def _configured(self) -> bool:
        if not self.config.get("feed_url") or not self.config.get("public_key"):
            self._set("unconfigured", "尚未配置 Windows 更新服务")
            return False
        try:
            if not _https(self.config["feed_url"]):
                raise ValueError("更新服务必须使用 HTTPS")
            Ed25519PublicKey.from_public_bytes(base64.b64decode(self.config["public_key"], validate=True))
            if _installed_directory() is None:
                raise ValueError("自动更新仅支持安装版，请先使用 Windows 安装程序安装")
        except (ValueError, TypeError) as error:
            self._set("unconfigured", str(error))
            return False
        return True

    def start(self):
        if self._configured():
            self._periodic.start()
            self.check()

    def check(self):
        if self._closed or self.status.state in ("checking", "downloading", "verifying", "ready", "installing"):
            return
        if not self._configured():
            return
        self._release = {}
        self._feed.clear()
        self._set("checking")
        self._request(self.config["feed_url"])

    def _request(self, url: str):
        request = QNetworkRequest(QUrl(url))
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute, QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
        request.setTransferTimeout(30000)
        self._reply = self._network.get(request)
        self._reply.readyRead.connect(self._consume)
        self._reply.finished.connect(self._finished)

    def _consume(self):
        if self._reply is None:
            return
        data = bytes(self._reply.readAll())
        try:
            if self.status.state == "checking":
                self._feed.extend(data)
                if len(self._feed) > 1024 * 1024:
                    raise ValueError("更新信息过大")
            elif self.status.state == "downloading":
                self._file.write(data)
                received = self._file.tell()
                if received > self._release["size"]:
                    raise ValueError("安装包大小与更新信息不一致")
                self._set("downloading", received=received, total=self._release["size"])
        except (OSError, ValueError) as error:
            self._fail(str(error))

    def _finished(self):
        if self._reply is None:
            return
        self._consume()
        if self._reply is None:
            return
        reply, self._reply = self._reply, None
        error = reply.error()
        http = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        reason = reply.errorString()
        secure = _https(reply.url().toString())
        reply.deleteLater()
        if error != QNetworkReply.NetworkError.NoError or http != 200 or not secure:
            if not secure:
                message = "更新连接未使用 HTTPS，已停止下载"
            elif error != QNetworkReply.NetworkError.NoError:
                message = f"检查更新失败：{reason}" if self.status.state == "checking" else f"更新下载失败：{reason}"
            else:
                message = f"更新服务返回 HTTP {http}"
            self._fail(message)
            return
        if self.status.state == "checking":
            try:
                self._release = _release(bytes(self._feed))
                self._set("available" if self._release else "current")
            except (ValueError, TypeError) as error:
                self._fail(str(error))
        elif self.status.state == "downloading":
            output, self._file = self._file, None
            try:
                output.close()
            except OSError as error:
                self._fail(f"无法保存安装包：{error}")
                return
            self._set("verifying")
            self._verification = self._pool.submit(_verify, self._installer, self._release, self.config["public_key"])
            self._poll.start()

    def download(self):
        if self._closed or not self._release or self.status.state not in ("available", "failed"):
            return
        self._cleanup()
        try:
            self._directory = Path(tempfile.mkdtemp(prefix="boring-console-update-"))
            self._installer = self._directory / "BORING-Console-Setup.exe"
            self._file = self._installer.open("wb")
            self._set("downloading", total=self._release["size"])
            self._request(self._release["url"])
        except OSError as error:
            self._fail(str(error))

    def _verified(self):
        if self._verification is None or not self._verification.done():
            return
        self._poll.stop()
        try:
            self._verification.result()
            self._set("ready")
        except Exception:
            self._fail("安装包签名验证失败或下载不完整，未执行安装")
        self._verification = None

    def install(self):
        if self._closed or self.status.state != "ready":
            return
        directory = _installed_directory()
        if directory is None:
            self._set("unconfigured", "找不到当前安装位置，请使用 Windows 安装程序重新安装")
            return
        try:
            if not self.prepare_restart():
                return
            _launch_installer(self._installer, directory)
        except Exception as error:
            self._set("ready", f"无法启动更新程序：{error}", retry_action="install")
            return
        self._set("installing")
        self.quit_requested.emit()

    def retry(self):
        action = self.status.retry_action
        if action == "download":
            self.download()
        elif action == "install":
            self.install()
        elif action == "check":
            self.check()

    def _abort(self):
        if self._reply is not None:
            reply, self._reply = self._reply, None
            reply.abort()
            reply.deleteLater()
        if self._file is not None:
            output, self._file = self._file, None
            try:
                output.close()
            except OSError:
                # The failed or cancelled partial download is discarded below.
                pass

    def _cleanup(self):
        if self._directory is not None:
            shutil.rmtree(self._directory, ignore_errors=True)
            self._directory = self._installer = None

    def _fail(self, message: str):
        self._abort()
        self._cleanup()
        self._set("failed", message, retry_action="download" if self._release else "check")

    def cancel(self):
        if self.status.state not in ("checking", "downloading"):
            return
        self._abort()
        self._cleanup()
        self._set("available" if self._release else "idle")

    def shutdown(self):
        self._closed = True
        self._periodic.stop()
        self._poll.stop()
        self._abort()
        self._pool.shutdown(wait=True)
        if self.status.state != "installing":
            self._cleanup()
