from __future__ import annotations

import json
import shutil
import sys
import time
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QTimer, Signal


APP_SERVER_TIMEOUT_MS = 10_000
DEFAULT_REFRESH_INTERVAL_MS = 300_000


class CodexUsageStatus(str, Enum):
    LOADING = "loading"
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


@dataclass(frozen=True)
class CodexQuotaWindow:
    used_percent: float
    window_minutes: int | None
    resets_at: int | None

    @property
    def remaining_percent(self) -> float:
        return max(0.0, 100.0 - self.used_percent)


@dataclass(frozen=True)
class CodexQuotaBucket:
    limit_id: str
    limit_name: str | None
    plan_type: str | None
    primary: CodexQuotaWindow | None
    secondary: CodexQuotaWindow | None

    @property
    def display_name(self) -> str:
        if self.limit_name:
            return self.limit_name
        if self.limit_id == "codex":
            return "Codex"
        return self.limit_id


@dataclass(frozen=True)
class CodexUsageSnapshot:
    status: CodexUsageStatus
    buckets: tuple[CodexQuotaBucket, ...] = ()
    source: str = "none"
    updated_at: float | None = None
    message: str = ""

    @classmethod
    def loading(
        cls, previous: CodexUsageSnapshot | None = None
    ) -> CodexUsageSnapshot:
        return cls(
            status=CodexUsageStatus.LOADING,
            buckets=previous.buckets if previous is not None else (),
            source=previous.source if previous is not None else "none",
            updated_at=previous.updated_at if previous is not None else None,
            message="正在读取 Codex 额度…",
        )

    @classmethod
    def unavailable(cls, message: str | None = None) -> CodexUsageSnapshot:
        return cls(
            status=CodexUsageStatus.UNAVAILABLE,
            message=message or "未找到 Codex 额度数据。请先登录并使用 Codex。",
        )


def snapshot_from_app_server_response(
    response: object,
    *,
    updated_at: float | None = None,
) -> CodexUsageSnapshot:
    if not isinstance(response, dict):
        raise ValueError("Codex app-server 返回了无效响应")
    result = response.get("result")
    if not isinstance(result, dict):
        raise ValueError("Codex app-server 响应中缺少 result")

    raw_buckets = result.get("rateLimitsByLimitId")
    candidates: list[tuple[str | None, object]] = []
    if isinstance(raw_buckets, dict):
        candidates.extend((str(key), value) for key, value in raw_buckets.items())
    if not candidates and isinstance(result.get("rateLimits"), dict):
        candidates.append((None, result["rateLimits"]))

    buckets = tuple(
        bucket
        for fallback_id, value in candidates
        if (bucket := _parse_bucket(value, fallback_id=fallback_id)) is not None
    )
    buckets = tuple(
        sorted(
            buckets,
            key=lambda bucket: (
                bucket.limit_id != "codex",
                bucket.display_name.lower(),
            ),
        )
    )
    if not buckets:
        raise ValueError("Codex app-server 尚未返回可用的额度窗口")
    return CodexUsageSnapshot(
        status=CodexUsageStatus.AVAILABLE,
        buckets=buckets,
        source="app_server",
        updated_at=time.time() if updated_at is None else updated_at,
        message="已通过 Codex 本地服务更新",
    )


def load_latest_session_usage(
    sessions_root: Path | None = None,
) -> CodexUsageSnapshot:
    root = sessions_root or (Path.home() / ".codex" / "sessions")
    if not root.exists():
        return CodexUsageSnapshot.unavailable()
    try:
        candidates = sorted(
            root.rglob("*.jsonl"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )[:20]
    except OSError:
        return CodexUsageSnapshot.unavailable("无法读取 Codex 本地会话日志。")

    for path in candidates:
        try:
            lines = _tail_lines(path)
        except OSError:
            continue
        for line in reversed(lines):
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            bucket = _bucket_from_session_record(record)
            if bucket is None:
                continue
            updated_at = _record_timestamp(record) or path.stat().st_mtime
            return CodexUsageSnapshot(
                status=CodexUsageStatus.AVAILABLE,
                buckets=(bucket,),
                source="session_log",
                updated_at=updated_at,
                message="来自 Codex 本地会话日志，可能滞后",
            )
    return CodexUsageSnapshot.unavailable()


def resolve_codex_executable() -> str | None:
    executable = shutil.which("codex")
    if executable:
        return executable
    if sys.platform == "darwin":
        bundled = Path("/Applications/ChatGPT.app/Contents/Resources/codex")
        if bundled.is_file():
            return str(bundled)
    return None


class CodexUsageMonitor(QObject):
    """Periodically reads subscription quota metadata from the local Codex app-server."""

    changed = Signal(object)

    def __init__(
        self,
        *,
        refresh_interval_ms: int = DEFAULT_REFRESH_INTERVAL_MS,
        sessions_root: Path | None = None,
        executable: str | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._sessions_root = sessions_root
        self._executable = executable
        self._snapshot = CodexUsageSnapshot.unavailable()
        self._process: QProcess | None = None
        self._stdout_buffer = b""
        self._request_completed = False

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(refresh_interval_ms)
        self._refresh_timer.timeout.connect(self.refresh)
        self._deadline = QTimer(self)
        self._deadline.setSingleShot(True)
        self._deadline.setInterval(APP_SERVER_TIMEOUT_MS)
        self._deadline.timeout.connect(
            lambda: self._use_log_fallback("Codex 本地服务读取超时")
        )

    @property
    def snapshot(self) -> CodexUsageSnapshot:
        return self._snapshot

    def start(self) -> None:
        if not self._refresh_timer.isActive():
            self._refresh_timer.start()
        self.refresh()

    def refresh(self) -> None:
        if self._process is not None:
            return
        self._stdout_buffer = b""
        self._request_completed = False
        previous = self._snapshot if self._snapshot.buckets else None
        self._set_snapshot(CodexUsageSnapshot.loading(previous))
        executable = self._executable or resolve_codex_executable()
        if executable is None:
            self._use_log_fallback("未找到可调用的 Codex 本地程序")
            return

        process = QProcess(self)
        process.setProgram(executable)
        process.setArguments(["app-server", "--stdio"])
        process.started.connect(self._initialize_app_server)
        process.readyReadStandardOutput.connect(self._read_app_server_output)
        process.errorOccurred.connect(
            lambda _error: self._use_log_fallback("Codex 本地服务无法启动")
        )
        process.finished.connect(self._app_server_finished)
        self._process = process
        self._deadline.start()
        process.start()

    def stop(self) -> None:
        self._refresh_timer.stop()
        self._deadline.stop()
        process = self._process
        self._process = None
        self._request_completed = True
        if process is not None:
            if process.state() != QProcess.ProcessState.NotRunning:
                process.kill()
                process.waitForFinished(1_000)
            process.deleteLater()

    def _initialize_app_server(self) -> None:
        self._write_message(
            {
                "id": 1,
                "method": "initialize",
                "params": {
                    "clientInfo": {
                        "name": "boring-console",
                        "title": "BORING Console",
                        "version": "1.0",
                    }
                },
            }
        )

    def _read_app_server_output(self) -> None:
        process = self._process
        if process is None:
            return
        self._stdout_buffer += bytes(process.readAllStandardOutput())
        while b"\n" in self._stdout_buffer:
            line, self._stdout_buffer = self._stdout_buffer.split(b"\n", 1)
            if not line.strip():
                continue
            try:
                message = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if message.get("id") == 1:
                if "error" in message:
                    self._use_log_fallback("Codex 本地服务初始化失败")
                    return
                self._write_message({"method": "initialized", "params": {}})
                self._write_message(
                    {"id": 2, "method": "account/rateLimits/read", "params": {}}
                )
            elif message.get("id") == 2:
                try:
                    snapshot = snapshot_from_app_server_response(message)
                except ValueError as exc:
                    self._use_log_fallback(str(exc))
                    return
                self._request_completed = True
                self._deadline.stop()
                self._set_snapshot(snapshot)
                self._close_process()
                return

    def _write_message(self, payload: dict) -> None:
        process = self._process
        if process is None:
            return
        process.write(json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n")

    def _app_server_finished(self, _exit_code: int, _status) -> None:
        process = self._process
        self._process = None
        if process is not None:
            process.deleteLater()
        if self._request_completed:
            return
        self._use_log_fallback("Codex 本地服务在返回额度前结束")

    def _use_log_fallback(self, reason: str) -> None:
        if self._request_completed:
            return
        self._request_completed = True
        self._deadline.stop()
        fallback = load_latest_session_usage(self._sessions_root)
        if fallback.status is CodexUsageStatus.UNAVAILABLE:
            fallback = replace(
                fallback,
                status=CodexUsageStatus.ERROR,
                message=f"{reason}；本地会话日志也没有可用额度。",
            )
        self._set_snapshot(fallback)
        self._close_process()

    def _close_process(self) -> None:
        process = self._process
        if process is None:
            return
        if process.state() != QProcess.ProcessState.NotRunning:
            process.kill()
        else:
            self._process = None
            process.deleteLater()

    def _set_snapshot(self, snapshot: CodexUsageSnapshot) -> None:
        if snapshot == self._snapshot:
            return
        self._snapshot = snapshot
        self.changed.emit(snapshot)


def _parse_bucket(value: object, *, fallback_id: str | None) -> CodexQuotaBucket | None:
    if not isinstance(value, dict):
        return None
    raw_limit_id = value.get("limitId", value.get("limit_id", fallback_id))
    if not isinstance(raw_limit_id, str) or not raw_limit_id:
        return None
    limit_name = value.get("limitName", value.get("limit_name"))
    plan_type = value.get("planType", value.get("plan_type"))
    return CodexQuotaBucket(
        limit_id=raw_limit_id,
        limit_name=limit_name if isinstance(limit_name, str) and limit_name else None,
        plan_type=plan_type if isinstance(plan_type, str) and plan_type else None,
        primary=_parse_window(value.get("primary")),
        secondary=_parse_window(value.get("secondary")),
    )


def _parse_window(value: object) -> CodexQuotaWindow | None:
    if not isinstance(value, dict):
        return None
    used = value.get("usedPercent", value.get("used_percent"))
    if not isinstance(used, (int, float)) or isinstance(used, bool):
        return None
    duration = value.get(
        "windowDurationMins",
        value.get("window_minutes", value.get("window_duration_minutes")),
    )
    resets_at = value.get("resetsAt", value.get("resets_at"))
    return CodexQuotaWindow(
        used_percent=min(100.0, max(0.0, float(used))),
        window_minutes=(
            int(duration)
            if isinstance(duration, (int, float)) and not isinstance(duration, bool)
            else None
        ),
        resets_at=(
            int(resets_at)
            if isinstance(resets_at, (int, float)) and not isinstance(resets_at, bool)
            else None
        ),
    )


def _bucket_from_session_record(record: object) -> CodexQuotaBucket | None:
    if not isinstance(record, dict):
        return None
    payload = record.get("payload")
    if not isinstance(payload, dict) or payload.get("type") != "token_count":
        return None
    return _parse_bucket(payload.get("rate_limits"), fallback_id="codex")


def _record_timestamp(record: object) -> float | None:
    if not isinstance(record, dict) or not isinstance(record.get("timestamp"), str):
        return None
    try:
        return datetime.fromisoformat(record["timestamp"].replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _tail_lines(path: Path, maximum_bytes: int = 1_000_000) -> list[str]:
    with path.open("rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        handle.seek(max(0, size - maximum_bytes))
        data = handle.read()
    if size > maximum_bytes and b"\n" in data:
        data = data.split(b"\n", 1)[1]
    return data.decode("utf-8").splitlines()
