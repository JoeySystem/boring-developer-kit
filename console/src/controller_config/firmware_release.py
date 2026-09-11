from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from importlib.resources import files
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from urllib.parse import quote

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from controller_config.firmware_update import (
    MANIFEST_FORMAT,
    FirmwarePackage,
    FirmwarePackageError,
)
from controller_config.models import DeviceSnapshot
from controller_config import __version__
from controller_config.protocol.contract import Contract


MAX_REMOTE_MANIFEST_BYTES = 64 * 1024


def default_firmware_source() -> dict[str, str]:
    """Packaged source for ordinary double-click startup."""
    return json.loads(files("controller_config.assets").joinpath("firmware-source.json").read_text(encoding="utf-8"))


class FirmwareReleaseError(ValueError):
    """An online release cannot be offered as a BORING firmware update."""


class RemoteFirmwareState(str, Enum):
    UNCONFIGURED = "unconfigured"
    IDLE = "idle"
    CHECKING = "checking"
    AVAILABLE = "available"
    DOWNLOADING = "downloading"
    DOWNLOADED = "downloaded"
    CURRENT = "current"
    FAILED = "failed"


@dataclass(frozen=True)
class RemoteFirmwareRelease:
    manifest: dict[str, object]
    manifest_url: str
    image_url: str

    @property
    def version(self) -> str:
        return str(self.manifest["version"])

    @property
    def build_id(self) -> str:
        return str(self.manifest["build_id"])

    @property
    def size(self) -> int:
        return int(self.manifest["size"])


@dataclass(frozen=True)
class RemoteFirmwareCheck:
    state: RemoteFirmwareState = RemoteFirmwareState.UNCONFIGURED
    message: str = "在线固件服务尚未配置"
    technical: str = ""
    release: RemoteFirmwareRelease | None = None
    received_size: int = 0
    total_size: int = 0

    @property
    def progress_percent(self) -> int:
        if self.total_size <= 0:
            return 0
        return min(100, int(self.received_size * 100 / self.total_size))


@dataclass(frozen=True)
class RemoteFirmwareBundle:
    manifest: dict[str, object]
    image_data: bytes
    manifest_url: str
    channel: str = "stable"


class FirmwareReleaseSource(QObject):
    release_found = Signal(object)
    download_completed = Signal(object)
    download_progress = Signal(int, int)
    failed = Signal(str)

    def check(self, snapshot: DeviceSnapshot) -> None:
        raise NotImplementedError

    def download(
        self,
        release: RemoteFirmwareRelease,
        snapshot: DeviceSnapshot,
    ) -> None:
        raise NotImplementedError

    def cancel(self) -> None:
        pass


class HttpFirmwareReleaseSource(FirmwareReleaseSource):
    """Check one HTTPS manifest, then download its image only on request."""

    def __init__(
        self,
        manifest_url_template: str,
        parent: QObject | None = None,
        network: QNetworkAccessManager | None = None,
        *,
        channel: str = "stable",
    ) -> None:
        super().__init__(parent)
        self._template = manifest_url_template.strip()
        if channel not in {"stable", "sample"}:
            raise FirmwareReleaseError("不支持的固件渠道")
        self._channel = channel
        self._network = network or QNetworkAccessManager(self)
        self._reply: QNetworkReply | None = None
        self._snapshot: DeviceSnapshot | None = None
        self._manifest: dict[str, object] | None = None
        self._manifest_url = QUrl()
        self._release: RemoteFirmwareRelease | None = None

    def check(self, snapshot: DeviceSnapshot) -> None:
        if self._reply is not None:
            raise FirmwareReleaseError("在线固件检查正在进行")
        url = manifest_url_for_snapshot(self._template, snapshot)
        self._snapshot = snapshot
        self._manifest = None
        self._release = None
        self._manifest_url = url
        self._start_request(
            url,
            MAX_REMOTE_MANIFEST_BYTES,
            "固件发布 manifest",
            self._manifest_finished,
        )

    def download(
        self,
        release: RemoteFirmwareRelease,
        snapshot: DeviceSnapshot,
    ) -> None:
        if self._reply is not None:
            raise FirmwareReleaseError("在线固件请求正在进行")
        manifest = _validate_release_manifest(release.manifest, snapshot, channel=self._channel)
        manifest_url = QUrl(release.manifest_url)
        image_url = manifest_url.resolved(QUrl(str(manifest.get("download_url", manifest["image"]))))
        _require_https(manifest_url)
        _require_https(image_url)
        if image_url.toString() != release.image_url:
            raise FirmwareReleaseError("在线固件下载地址与检查结果不一致")
        self._snapshot = snapshot
        self._manifest = manifest
        self._manifest_url = manifest_url
        self._release = release
        self._start_request(
            image_url,
            release.size,
            "固件镜像",
            self._image_finished,
        )

    def cancel(self) -> None:
        reply = self._reply
        self._reply = None
        if reply is not None:
            reply.abort()
            reply.deleteLater()
        self._snapshot = None
        self._manifest = None
        self._release = None

    def _manifest_finished(self) -> None:
        reply = self._take_reply()
        if reply is None:
            return
        try:
            data = _reply_bytes(reply, MAX_REMOTE_MANIFEST_BYTES, "固件发布 manifest")
            manifest = json.loads(data.decode("utf-8"))
            snapshot = self._snapshot
            if snapshot is None:
                raise FirmwareReleaseError("在线固件检查上下文已失效")
            manifest = _validate_release_manifest(manifest, snapshot, channel=self._channel)
            self._manifest_url = reply.url()
            image_url = self._manifest_url.resolved(QUrl(str(manifest.get("download_url", manifest["image"]))))
            _require_https(image_url)
            release = RemoteFirmwareRelease(
                manifest=manifest,
                manifest_url=self._manifest_url.toString(),
                image_url=image_url.toString(),
            )
            self._snapshot = None
            self.release_found.emit(release)
        except (FirmwareReleaseError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._fail(str(exc))
        finally:
            reply.deleteLater()

    def _image_finished(self) -> None:
        reply = self._take_reply()
        if reply is None:
            return
        try:
            manifest = self._manifest
            snapshot = self._snapshot
            release = self._release
            if manifest is None or snapshot is None or release is None:
                raise FirmwareReleaseError("在线固件下载上下文已失效")
            image_limit = int(manifest["size"])
            image_data = _reply_bytes(reply, image_limit, "固件镜像")
            if len(image_data) != manifest["size"]:
                raise FirmwareReleaseError("下载的固件镜像大小与 manifest 不一致")
            bundle = RemoteFirmwareBundle(
                manifest=manifest,
                image_data=image_data,
                manifest_url=release.manifest_url,
                channel=self._channel,
            )
            self._snapshot = None
            self._manifest = None
            self._release = None
            self.download_completed.emit(bundle)
        except FirmwareReleaseError as exc:
            self._fail(str(exc))
        finally:
            reply.deleteLater()

    def _take_reply(self) -> QNetworkReply | None:
        reply = self._reply
        self._reply = None
        return reply

    def _start_request(
        self,
        url: QUrl,
        limit: int,
        label: str,
        finished: Callable[[], None],
    ) -> None:
        reply = self._network.get(_request(url))
        self._reply = reply
        reply.downloadProgress.connect(
            lambda received, total: self._on_download_progress(
                reply, received, total, limit, label
            )
        )
        reply.finished.connect(finished)

    def _on_download_progress(
        self,
        reply: QNetworkReply,
        received: int,
        total: int,
        limit: int,
        label: str,
    ) -> None:
        _abort_oversized_reply(reply, received, total, limit, label)
        if label == "固件镜像" and not reply.property("boringSizeError"):
            self.download_progress.emit(received, total)

    def _fail(self, message: str) -> None:
        self._snapshot = None
        self._manifest = None
        self._release = None
        self.failed.emit(message)


def manifest_url_for_snapshot(template: str, snapshot: DeviceSnapshot) -> QUrl:
    values = {
        "product_id": snapshot.identity.get("product_id"),
        "hardware_id": snapshot.identity.get("hardware_id"),
    }
    if not template:
        raise FirmwareReleaseError("尚未配置在线固件 manifest 地址")
    rendered = template
    for name, value in values.items():
        if not isinstance(value, str) or not value:
            raise FirmwareReleaseError(f"设备没有返回 {name}")
        rendered = rendered.replace("{" + name + "}", quote(value, safe=""))
    if "{" in rendered or "}" in rendered:
        raise FirmwareReleaseError("在线固件地址包含未知占位符")
    url = QUrl(rendered)
    _require_https(url)
    return url


def load_remote_firmware_bundle(
    bundle: RemoteFirmwareBundle,
    contract: Contract,
) -> FirmwarePackage:
    image_name = bundle.manifest.get("image")
    if not isinstance(image_name, str) or Path(image_name).name != image_name:
        raise FirmwareReleaseError("在线固件 manifest 的 image 字段无效")
    with TemporaryDirectory(prefix="boring-firmware-") as directory:
        root = Path(directory)
        manifest_path = root / "firmware-manifest.json"
        manifest_path.write_text(
            json.dumps(bundle.manifest, ensure_ascii=False),
            encoding="utf-8",
        )
        (root / image_name).write_bytes(bundle.image_data)
        try:
            package = FirmwarePackage.load(manifest_path, contract)
        except FirmwarePackageError as exc:
            raise FirmwareReleaseError(str(exc)) from exc
    _validate_channel(bundle.manifest, bundle.channel)
    validate_minimum_app_version(bundle.manifest)
    if not package.build_id:
        raise FirmwareReleaseError("在线发布固件必须提供 build_id")
    return package


def _validate_release_manifest(
    value: object,
    snapshot: DeviceSnapshot,
    *,
    channel: str = "stable",
) -> dict[str, object]:
    if not isinstance(value, dict):
        raise FirmwareReleaseError("在线固件 manifest 根节点必须是 object")
    if value.get("format") != MANIFEST_FORMAT:
        raise FirmwareReleaseError("在线固件 manifest 格式无效")
    image = value.get("image")
    size = value.get("size")
    if not isinstance(image, str) or Path(image).name != image:
        raise FirmwareReleaseError("在线固件镜像必须与 manifest 位于同一目录")
    if type(size) is not int or size <= 0 or size > _device_image_limit(snapshot):
        raise FirmwareReleaseError("在线固件镜像大小超过设备声明的 OTA 上限")
    if value.get("product_id") != snapshot.identity.get("product_id"):
        raise FirmwareReleaseError("在线固件 product_id 与当前设备不匹配")
    if value.get("hardware_id") != snapshot.identity.get("hardware_id"):
        raise FirmwareReleaseError("在线固件 hardware_id 与当前设备不匹配")
    _validate_channel(value, channel)
    validate_minimum_app_version(value)
    for alias, field in (("device_model", "product_id"), ("hardware_revision", "hardware_id")):
        if alias in value and value[alias] != value[field]:
            raise FirmwareReleaseError(f"在线固件 {alias} 与 {field} 不一致")
    for field, kind in (("mandatory", bool), ("release_notes", str), ("published_at", str), ("signature", str)):
        if field in value and type(value[field]) is not kind:
            raise FirmwareReleaseError(f"在线固件 {field} 类型错误")
    if "download_url" in value:
        if not isinstance(value["download_url"], str):
            raise FirmwareReleaseError("在线固件 download_url 类型错误")
        _require_https(QUrl(value["download_url"]))
    if value.get("signature"):
        raise FirmwareReleaseError("当前 MVP 尚不支持发布签名验签，不能把签名视为已验证")
    version = value.get("version")
    if not isinstance(version, str) or re.fullmatch(r"[A-Za-z0-9._+-]{1,31}", version) is None:
        raise FirmwareReleaseError("在线固件版本号无效")
    sha256 = value.get("sha256")
    if not isinstance(sha256, str) or re.fullmatch(r"[0-9a-f]{64}", sha256) is None:
        raise FirmwareReleaseError("在线固件 sha256 无效")
    if not isinstance(value.get("build_id"), str) or not value["build_id"]:
        raise FirmwareReleaseError("在线发布固件必须提供 build_id")
    return dict(value)


def _validate_channel(manifest: dict, channel: str) -> None:
    if channel not in {"stable", "sample"} or manifest.get("channel", "stable") != channel:
        raise FirmwareReleaseError("在线固件渠道与桌面端配置不一致")
    if channel == "stable":
        if manifest.get("validation_state") != "released":
            raise FirmwareReleaseError("在线 stable 渠道只接受 validation_state=released 的固件")
        if manifest.get("git_dirty") is not False:
            raise FirmwareReleaseError("在线 stable 渠道只接受 git_dirty=false 的固件")
    elif manifest.get("validation_state") not in {"built", "sample-verified"}:
        raise FirmwareReleaseError("sample 渠道只接受 built 或 sample-verified 样品固件")


def _version_key(value: str) -> tuple:
    match = re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?", value)
    if match is None:
        raise FirmwareReleaseError(f"无法比较版本号：{value}")
    major, minor, patch, prerelease = match.groups()
    identifiers = tuple((0, int(part)) if part.isdigit() else (1, part) for part in (prerelease or "").split("."))
    return int(major), int(minor), int(patch), prerelease is None, identifiers


def validate_minimum_app_version(manifest: dict) -> None:
    minimum = manifest.get("minimum_app_version", "0.1.0")
    if not isinstance(minimum, str) or _version_key(__version__) < _version_key(minimum):
        raise FirmwareReleaseError(f"请先升级 BORING 桌面端至 {minimum} 或更新版本")


def release_is_newer(release: RemoteFirmwareRelease, snapshot: DeviceSnapshot) -> bool:
    """Compare product version, then this project's dated build sequence."""
    current_version = str(snapshot.versions.get("firmware", ""))
    current_build = str(snapshot.versions.get("build_id", ""))
    offered, current = _version_key(release.version), _version_key(current_version)
    if offered != current:
        return offered > current
    if release.build_id == current_build:
        return False
    build_pattern = r"(\d{8})\.(\d+)-g[0-9a-f]+(?:-dirty)?"
    offered_build = re.fullmatch(build_pattern, release.build_id)
    installed_build = re.fullmatch(build_pattern, current_build)
    if not offered_build or not installed_build:
        raise FirmwareReleaseError("同版本的 build_id 无法确定先后；请使用本地维护包进行人工维护")
    return tuple(map(int, offered_build.groups())) > tuple(map(int, installed_build.groups()))


def _device_image_limit(snapshot: DeviceSnapshot) -> int:
    limits = snapshot.capabilities.get("limits")
    value = limits.get("firmware_image_bytes") if isinstance(limits, dict) else None
    if type(value) is not int or value <= 0:
        raise FirmwareReleaseError("设备没有返回有效的 OTA 镜像上限")
    return value


def _request(url: QUrl) -> QNetworkRequest:
    request = QNetworkRequest(url)
    request.setHeader(QNetworkRequest.KnownHeaders.UserAgentHeader, "BORING-Configurator")
    request.setAttribute(
        QNetworkRequest.Attribute.RedirectPolicyAttribute,
        QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy,
    )
    request.setTransferTimeout(15_000)
    return request


def _reply_bytes(reply: QNetworkReply, limit: int, label: str) -> bytes:
    size_error = reply.property("boringSizeError")
    if isinstance(size_error, str) and size_error:
        raise FirmwareReleaseError(size_error)
    if reply.error() != QNetworkReply.NetworkError.NoError:
        raise FirmwareReleaseError(f"{label}下载失败：{reply.errorString()}")
    data = bytes(reply.readAll())
    if not data:
        raise FirmwareReleaseError(f"{label}为空")
    if len(data) > limit:
        raise FirmwareReleaseError(f"{label}超过允许大小")
    return data


def _abort_oversized_reply(
    reply: QNetworkReply,
    received: int,
    total: int,
    limit: int,
    label: str,
) -> None:
    if received <= limit and (total < 0 or total <= limit):
        return
    reply.setProperty("boringSizeError", f"{label}超过允许大小")
    reply.abort()


def _require_https(url: QUrl) -> None:
    if not url.isValid() or url.scheme().lower() != "https" or not url.host():
        raise FirmwareReleaseError("在线固件地址必须是有效的 HTTPS URL")
