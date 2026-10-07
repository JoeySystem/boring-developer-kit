from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import secrets
import subprocess
import threading
from urllib.parse import parse_qs, urlparse
import uuid

from .devices import DeviceKind, detect_devices, load_ports, select_runtime_ports
from .errors import ProvisioningError
from .esptool_runner import EsptoolRunner
from .models import ProvisionMode, RecordKind
from .models import ProvisionRequest
from .paths import discover_production_authority_path, history_root, resource_root
from .queue import SequentialBatch
from .release import ReleasePackage
from .runtime import RuntimeVerifier
from .summaries import package_fingerprint, package_summary
from .workflow import Provisioner
from .official_recovery import verified_manifest, register_with_console


ALLOWED_AUTHORITY_STATES = {"sample-verified", "rc", "released"}
TERMINAL_JOB_STATUSES = {"passed", "failed", "action-required", "retryable"}


def _serial_port_holders(port: str) -> list[dict]:
    """Return processes that currently have the serial device open."""
    try:
        result = subprocess.run(
            ["lsof", "-Fpc", "--", port],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode not in {0, 1}:
        return []

    holders = []
    current_pid = None
    for line in result.stdout.splitlines():
        if line.startswith("p") and line[1:].isdigit():
            current_pid = int(line[1:])
        elif line.startswith("c") and current_pid is not None:
            holders.append({"pid": current_pid, "command": line[1:] or "其他程序"})
            current_pid = None
    return holders


def _port_busy_message(port: str, holders: list[dict]) -> str:
    if holders:
        programs = "、".join(
            f"{holder['command']}（PID {holder['pid']}）" for holder in holders
        )
        subject = f"串口被 {programs}占用"
    else:
        subject = f"串口 {port} 被其他程序占用"
    return (
        f"{subject}。请先退出占用串口的程序；"
        "工具检测到端口释放后会自动允许重试。"
    )


def _describe_serial_failure(
    port: str,
    error: str,
    *,
    holders: list[dict] | None = None,
) -> dict:
    lowered = error.lower()
    retryable = "resource busy" in lowered or "errno 16" in lowered
    if not retryable:
        return {"retryable": False, "message": error}
    active_holders = _serial_port_holders(port) if holders is None else holders
    return {
        "retryable": True,
        "message": _port_busy_message(port, active_holders),
    }


class AuthorityUpdateState:
    """Restricted state for one-device, runtime-only authority updates."""

    MAX_RETAINED_JOBS = 10
    MAX_EVENTS_PER_JOB = 500

    def __init__(self):
        self.token = secrets.token_urlsafe(24)
        self.jobs: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.busy_job: str | None = None
        self.worker: threading.Thread | None = None
        self.closing = False
        self.latest_job_id: str | None = None

    @staticmethod
    def _authority_path() -> Path:
        return resource_root() / "releases" / "recovery"

    def authority(self) -> dict:
        signed = verified_manifest()
        path = self._authority_path()
        with ReleasePackage.open(path) as package:
            if package.hardware_id != "WMP-S3-MATRIX12-POWER-V2":
                raise ProvisioningError("当前权威包不属于 Matrix12 Power V2")
            if package.build_id != signed["build_id"]:
                raise ProvisioningError("内置恢复包与签名构建不一致")
            if package.ota_application is None:
                raise ProvisioningError("当前权威包不支持免 BOOT 更新")
            return package_summary(package, path)

    def scan(self) -> dict:
        authority = self.authority()
        with ReleasePackage.open(Path(authority["path"])) as package:
            devices = [
                device.to_dict()
                for device in detect_devices(package.runtime_usb)
                if device.kind is not DeviceKind.OTHER
            ]
        runtime = [device for device in devices if device["kind"] == "runtime"]
        block_reason = None
        if len(devices) == 1 and len(runtime) == 1:
            holders = _serial_port_holders(runtime[0]["device"])
            if holders:
                readiness = "blocked"
                block_reason = "port-busy"
                message = _port_busy_message(runtime[0]["device"], holders)
            else:
                readiness = "ready"
                message = "已发现设备 USB 接口，点击恢复后检查是否能通信"
        elif not devices:
            readiness = "waiting"
            message = "请通过数据线直连 1 台设备。即使屏幕不亮也可检测；未发现接口时请换线或换 USB 口。"
        elif len(runtime) == 0:
            readiness = "blocked"
            block_reason = "boot-mode"
            message = "设备未提供本工具支持的 USB 恢复接口。请保存诊断信息并联系售后，不要拆机。"
        else:
            readiness = "blocked"
            block_reason = "multiple-devices"
            message = "一次只能连接 1 台设备，请拔下其他设备"
        return {
            "authority": authority,
            "devices": devices,
            "readiness": readiness,
            "block_reason": block_reason,
            "message": message,
        }

    def bootstrap(self) -> dict:
        result = self.scan()
        with self.lock:
            latest_job_id = self.latest_job_id
            busy = self.busy_job is not None
        result.update(
            {
                "token": self.token,
                "busy": busy,
                "latest_job": self.job(latest_job_id) if latest_job_id else None,
            }
        )
        return result

    def start_authority_update(self, requested_port: str) -> str:
        if not isinstance(requested_port, str) or not requested_port:
            raise ProvisioningError("请先接入并识别设备")
        scanned = self.scan()
        if scanned["readiness"] != "ready":
            raise ProvisioningError(scanned["message"])
        device = scanned["devices"][0]
        if device["device"] != requested_port:
            raise ProvisioningError("设备状态已变化，请重新扫描")
        authority = scanned["authority"]
        package = ReleasePackage.open(authority["path"])
        try:
            if package.release_id != authority["release_id"]:
                raise ProvisioningError("权威包版本在扫描后已变化，请重试")
            if package_fingerprint(package) != authority["fingerprint"]:
                raise ProvisioningError("权威包内容在扫描后已变化，请重试")
            selected = select_runtime_ports(load_ports(), package.runtime_usb, [requested_port])
            with self.lock:
                if self.closing:
                    raise ProvisioningError("工具正在关闭")
                if self.busy_job is not None:
                    raise ProvisioningError("已有更新任务正在运行")
                job_id = str(uuid.uuid4())
                self.jobs[job_id] = {
                    "id": job_id,
                    "status": "queued",
                    "progress": 0,
                    "stage": "queued",
                    "message": "等待开始",
                    "release_id": package.release_id,
                    "mode": ProvisionMode.UPDATE.value,
                    "record_kind": RecordKind.VALIDATION.value,
                    "ports": [requested_port],
                    "events": [],
                    "event_base": 0,
                    "results": [],
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
                self.busy_job = job_id
                self.latest_job_id = job_id
                self.worker = threading.Thread(
                    target=self._run_job,
                    args=(job_id, package, selected[0]),
                    daemon=False,
                    name=f"authority-update-{job_id[:8]}",
                )
                try:
                    self.worker.start()
                except RuntimeError:
                    self.jobs.pop(job_id, None)
                    self.busy_job = None
                    self.latest_job_id = None
                    self.worker = None
                    raise
        except Exception:
            package.close()
            raise
        return job_id

    def _update_job(self, job_id: str, **changes) -> None:
        with self.lock:
            self.jobs[job_id].update(changes)

    def _event(self, job_id: str, port: str):
        def callback(stage: str, message: str, progress: int) -> None:
            described = _describe_serial_failure(port, message)
            message = described["message"]
            event = {
                "time": datetime.now(timezone.utc).isoformat(),
                "port": port,
                "stage": stage,
                "message": message,
                "progress": progress,
            }
            with self.lock:
                job = self.jobs[job_id]
                job.update(
                    status="running",
                    stage=stage,
                    message=f"{port} · {message}",
                    progress=progress,
                )
                job["events"].append(event)
                overflow = len(job["events"]) - self.MAX_EVENTS_PER_JOB
                if overflow > 0:
                    del job["events"][:overflow]
                    job["event_base"] += overflow

        return callback

    def _run_job(self, job_id: str, package: ReleasePackage, device) -> None:
        try:
            with package:
                request = ProvisionRequest(
                    package=package,
                    port=device.device,
                    mode=ProvisionMode.UPDATE,
                    operator="customer-recovery-tool",
                    record_kind=RecordKind.VALIDATION,
                    verify_timeout_s=30.0,
                )
                provisioner = Provisioner(
                    EsptoolRunner(),
                    RuntimeVerifier(),
                    history_root(),
                )
                batch_result = SequentialBatch(
                    lambda selected: provisioner.run_ota(
                        request,
                        self._event(job_id, selected.device),
                        restart_mismatched=True,
                    )
                ).run([device])[0]
                if batch_result.success:
                    value = batch_result.value
                    results = [
                        {
                            "port": device.device,
                            "success": True,
                            "mac": value.runtime.serial.removeprefix("CP01-"),
                            "verification": "cdc-ota-runtime-wmp",
                            "runtime": asdict(value.runtime),
                            "runtime_port": value.runtime.port,
                            "receipt_path": str(value.receipt_path),
                        }
                    ]
                    status = "passed"
                    message = "固件恢复、重启与版本核对完成"
                    try:
                        registration = register_with_console(value.runtime.build_id)
                        results[0]["official_registration"] = registration
                        message += "；官方来源已登记，请退出工具再打开 Console"
                    except Exception as exc:
                        status = "action-required"
                        results[0]["firmware_recovered"] = True
                        results[0]["official_registration_error"] = str(exc)
                        message = "固件已恢复；官方来源登记未完成：" + str(exc)

                else:
                    described = _describe_serial_failure(
                        device.device,
                        batch_result.error or "更新未完成",
                    )
                    results = [
                        {
                            "port": device.device,
                            "success": False,
                            "error": described["message"],
                            "action_required": batch_result.action_required,
                        }
                    ]
                    if described["retryable"]:
                        status = "retryable"
                    elif batch_result.action_required:
                        status = "action-required"
                    else:
                        status = "failed"
                    message = described["message"] + "。请保存诊断信息交给售后；若换线、换接口后仍失败，请勿反复刷写。"
                with self.lock:
                    current_progress = self.jobs[job_id]["progress"]
                final_progress = current_progress
                final_stage = "done"
                if status == "passed":
                    final_progress = 100
                elif status == "action-required":
                    final_progress = 85
                    final_stage = "operator-action-required"
                elif status == "retryable":
                    final_stage = "port-busy"
                self._update_job(
                    job_id,
                    status=status,
                    progress=final_progress,
                    stage=final_stage,
                    message=message,
                    results=results,
                    completed_at=datetime.now(timezone.utc).isoformat(),
                )
        except Exception as exc:
            described = _describe_serial_failure(device.device, str(exc))
            with self.lock:
                current_progress = self.jobs[job_id]["progress"]
            self._update_job(
                job_id,
                status="retryable" if described["retryable"] else "failed",
                stage="port-busy" if described["retryable"] else "error",
                progress=current_progress,
                message=described["message"],
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
        finally:
            with self.lock:
                if self.busy_job == job_id:
                    self.busy_job = None
                if self.worker is threading.current_thread():
                    self.worker = None
                completed = sorted(
                    (
                        job for job in self.jobs.values()
                        if job["status"] in TERMINAL_JOB_STATUSES
                    ),
                    key=lambda job: job.get("completed_at", ""),
                    reverse=True,
                )
                for stale in completed[self.MAX_RETAINED_JOBS:]:
                    self.jobs.pop(stale["id"], None)

    def job(self, job_id: str | None, after_event: int = 0) -> dict | None:
        if not job_id:
            return None
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                return None
            snapshot = deepcopy(job)
            started_at = datetime.fromisoformat(snapshot["created_at"])
            finished_at = datetime.fromisoformat(
                snapshot.get("completed_at") or datetime.now(timezone.utc).isoformat()
            )
            snapshot["elapsed_seconds"] = max(
                0, int((finished_at - started_at).total_seconds())
            )
            event_base = snapshot.pop("event_base")
            if after_event < event_base:
                snapshot["events_reset"] = True
                start = 0
            else:
                snapshot["events_reset"] = False
                start = min(after_event - event_base, len(snapshot["events"]))
            snapshot["events"] = snapshot["events"][start:]
            snapshot["event_cursor"] = event_base + len(job["events"])
            return snapshot

    def require_shutdown_idle(self) -> None:
        with self.lock:
            if self.busy_job is not None:
                raise ProvisioningError("更新正在进行，完成后再退出工具")
            self.closing = True

    def shutdown(self) -> None:
        with self.lock:
            self.closing = True
            worker = self.worker
        if worker is not None and worker is not threading.current_thread():
            worker.join()


class AuthorityUpdateHandler(BaseHTTPRequestHandler):
    state: AuthorityUpdateState
    static_root: Path

    def log_message(self, format, *args):
        return

    def _headers(self, content_type: str, length: int, status=HTTPStatus.OK):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; frame-ancestors 'none'",
        )
        self.end_headers()

    def _json(self, value, status=HTTPStatus.OK):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self._headers("application/json; charset=utf-8", len(body), status)
        self.wfile.write(body)

    def _error(self, message: str, status=HTTPStatus.BAD_REQUEST):
        self._json({"error": message}, status)

    def _require_local_host(self) -> bool:
        hostname = urlparse(f"//{self.headers.get('Host', '')}").hostname
        if hostname not in {"127.0.0.1", "::1", "localhost"}:
            self._error("仅允许从本机地址访问", HTTPStatus.FORBIDDEN)
            return False
        return True

    def _require_token(self) -> bool:
        if self.headers.get("X-Authority-Updater-Token") != self.state.token:
            self._error("请求令牌无效", HTTPStatus.FORBIDDEN)
            return False
        return True

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ProvisioningError("请求长度无效") from exc
        if length <= 0 or length > 4096:
            raise ProvisioningError("请求内容为空或过大")
        try:
            value = json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProvisioningError("请求 JSON 无效") from exc
        if not isinstance(value, dict):
            raise ProvisioningError("请求必须是 JSON 对象")
        return value

    def do_GET(self):
        if not self._require_local_host():
            return
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/authority-update/bootstrap":
                self._json(self.state.bootstrap())
                return
            if parsed.path == "/api/authority-update/scan":
                self._json(self.state.scan())
                return
            if parsed.path.startswith("/api/authority-update/jobs/"):
                try:
                    after_event = int(parse_qs(parsed.query).get("after_event", ["0"])[0])
                except ValueError:
                    after_event = 0
                job_id = parsed.path.rsplit("/", 1)[-1]
                job = self.state.job(job_id, max(0, after_event))
                if job is None:
                    self._error("任务不存在", HTTPStatus.NOT_FOUND)
                else:
                    self._json(job)
                return
            if parsed.path == "/api/authority-update/report":
                report = {"scan": self.state.scan(), "job": self.state.job(self.state.latest_job_id)}
                body = json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Disposition", 'attachment; filename="BORING-recovery-diagnostic.json"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self._serve_static(parsed.path)
        except ProvisioningError as exc:
            self._error(str(exc))
        except Exception as exc:
            self._error(f"内部错误：{exc}", HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self):
        if not self._require_local_host() or not self._require_token():
            return
        try:
            payload = self._read_json()
            if self.path == "/api/authority-update/jobs":
                job_id = self.state.start_authority_update(str(payload.get("port", "")))
                self._json({"job_id": job_id}, HTTPStatus.ACCEPTED)
                return
            if self.path == "/api/authority-update/shutdown":
                self.state.require_shutdown_idle()
                self._json({"status": "shutting-down"})
                threading.Thread(
                    target=self.server.shutdown,
                    daemon=True,
                    name="authority-updater-shutdown",
                ).start()
                return
            self._error("接口不存在", HTTPStatus.NOT_FOUND)
        except ProvisioningError as exc:
            self._error(str(exc))
        except Exception as exc:
            self._error(f"内部错误：{exc}", HTTPStatus.INTERNAL_SERVER_ERROR)

    def _serve_static(self, request_path: str):
        aliases = {
            "": "authority-update.html",
            "/": "authority-update.html",
            "/authority-update.js": "authority-update.js",
            "/authority-update.css": "authority-update.css",
        }
        relative = aliases.get(request_path)
        if relative is None:
            self._error("页面不存在", HTTPStatus.NOT_FOUND)
            return
        candidate = (self.static_root / relative).resolve()
        if not candidate.is_relative_to(self.static_root.resolve()) or not candidate.is_file():
            self._error("页面不存在", HTTPStatus.NOT_FOUND)
            return
        body = candidate.read_bytes()
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type == "application/javascript":
            content_type += "; charset=utf-8"
        self._headers(content_type, len(body))
        self.wfile.write(body)


def create_authority_update_server(host: str = "127.0.0.1", port: int = 0):
    if host not in {"127.0.0.1", "::1", "localhost"}:
        raise ProvisioningError("权威包更新工具只允许监听本机回环地址")
    state = AuthorityUpdateState()
    handler = type(
        "BoundAuthorityUpdateHandler",
        (AuthorityUpdateHandler,),
        {"state": state, "static_root": resource_root() / "static"},
    )
    server = ThreadingHTTPServer((host, port), handler)
    return server, state
