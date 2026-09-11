from __future__ import annotations

import getpass
import json
import math
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from controller_config.codex_usage import (
    CodexQuotaBucket,
    CodexQuotaWindow,
    CodexUsageSnapshot,
    CodexUsageStatus,
)


CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CLAUDE_OAUTH_BETA = "oauth-2025-04-20"
CLAUDE_KEYCHAIN_SERVICE = "Claude Code-credentials"
DEFAULT_REFRESH_INTERVAL_MS = 300_000
MAX_USAGE_RESPONSE_BYTES = 64 * 1024


@dataclass(frozen=True)
class ClaudeCredential:
    access_token: str = field(repr=False)
    plan_type: str | None = None


def credential_from_payload(payload: object) -> ClaudeCredential | None:
    if not isinstance(payload, dict):
        return None
    token = next(
        (
            payload.get(key)
            for key in ("accessToken", "access_token", "oauth_access_token")
            if isinstance(payload.get(key), str) and payload.get(key)
        ),
        None,
    )
    plan = payload.get("subscriptionType", payload.get("subscription_type"))
    if isinstance(token, str):
        return ClaudeCredential(
            access_token=token,
            plan_type=plan if isinstance(plan, str) and plan else None,
        )
    for key in ("claudeAiOauth", "oauth"):
        nested = credential_from_payload(payload.get(key))
        if nested is not None:
            return nested
    return None


def credential_from_json(data: bytes | str) -> ClaudeCredential | None:
    try:
        payload = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
        return None
    return credential_from_payload(payload)


def snapshot_from_claude_usage_response(
    response: object,
    *,
    plan_type: str | None = None,
    updated_at: float | None = None,
) -> CodexUsageSnapshot:
    if not isinstance(response, dict):
        raise ValueError("Claude Code 额度服务返回了无效响应")
    limits = response.get("rate_limits", response)
    if not isinstance(limits, dict):
        raise ValueError("Claude Code 额度响应中缺少限额数据")

    buckets: list[CodexQuotaBucket] = []
    primary = _parse_window(limits.get("five_hour"), window_minutes=300)
    secondary = _parse_window(limits.get("seven_day"), window_minutes=10_080)
    if primary is not None or secondary is not None:
        buckets.append(
            CodexQuotaBucket(
                limit_id="claude",
                limit_name="Claude Code",
                plan_type=plan_type,
                primary=primary,
                secondary=secondary,
            )
        )

    for limit_id, name in (
        ("seven_day_sonnet", "Claude Code · Sonnet"),
        ("seven_day_opus", "Claude Code · Opus"),
    ):
        window = _parse_window(limits.get(limit_id), window_minutes=10_080)
        if window is not None:
            buckets.append(
                CodexQuotaBucket(
                    limit_id=f"claude_{limit_id}",
                    limit_name=name,
                    plan_type=plan_type,
                    primary=window,
                    secondary=None,
                )
            )

    if not buckets:
        raise ValueError("Claude Code 当前没有返回可显示的额度窗口")
    return CodexUsageSnapshot(
        status=CodexUsageStatus.AVAILABLE,
        buckets=tuple(buckets),
        source="claude_oauth",
        updated_at=time.time() if updated_at is None else updated_at,
        message="已通过 Claude Code 本地登录更新",
    )


class ClaudeUsageMonitor(QObject):
    """Read Claude subscription quota metadata using Claude Code's local login."""

    changed = Signal(object)

    def __init__(
        self,
        *,
        refresh_interval_ms: int = DEFAULT_REFRESH_INTERVAL_MS,
        credentials_path: Path | None = None,
        network: QNetworkAccessManager | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._credentials_path = credentials_path
        self._network = network or QNetworkAccessManager(self)
        self._snapshot = _unavailable_snapshot()
        self._credential_process: QProcess | None = None
        self._credential_stdout = b""
        self._reply: QNetworkReply | None = None
        self._plan_type: str | None = None
        self._credential_timer = QTimer(self)
        self._credential_timer.setSingleShot(True)
        self._credential_timer.setInterval(15_000)
        self._credential_timer.timeout.connect(self._keychain_timed_out)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(refresh_interval_ms)
        self._refresh_timer.timeout.connect(self.refresh)

    @property
    def snapshot(self) -> CodexUsageSnapshot:
        return self._snapshot

    def start(self) -> None:
        if not self._refresh_timer.isActive():
            self._refresh_timer.start()
        self.refresh()

    def refresh(self) -> None:
        if self._credential_process is not None or self._reply is not None:
            return
        previous = self._snapshot if self._snapshot.buckets else None
        self._set_snapshot(_loading_snapshot(previous))

        token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
        if token:
            self._request_usage(ClaudeCredential(token))
            return

        if self._credentials_path is not None:
            self._use_legacy_credentials_or_unavailable()
            return

        if sys.platform == "darwin":
            self._read_keychain_credentials()
            return
        self._use_legacy_credentials_or_unavailable()

    def stop(self) -> None:
        self._refresh_timer.stop()
        self._credential_timer.stop()
        self._credential_stdout = b""
        process = self._credential_process
        self._credential_process = None
        if process is not None:
            if process.state() != QProcess.ProcessState.NotRunning:
                process.kill()
                process.waitForFinished(1_000)
            process.deleteLater()
        reply = self._reply
        self._reply = None
        if reply is not None:
            reply.abort()
            reply.deleteLater()

    def _read_legacy_credentials(self) -> ClaudeCredential | None:
        path = self._credentials_path or (Path.home() / ".claude" / ".credentials.json")
        try:
            return credential_from_json(path.read_bytes())
        except OSError:
            return None

    def _read_keychain_credentials(self) -> None:
        process = QProcess(self)
        process.setProgram("/usr/bin/security")
        process.setArguments(
            [
                "find-generic-password",
                "-a",
                os.environ.get("USER") or getpass.getuser(),
                "-w",
                "-s",
                CLAUDE_KEYCHAIN_SERVICE,
            ]
        )
        process.readyReadStandardOutput.connect(
            lambda: self._append_keychain_output(process)
        )
        process.finished.connect(
            lambda code, status: self._keychain_finished(process, code, status)
        )
        process.errorOccurred.connect(lambda _error: self._keychain_failed(process))
        self._credential_stdout = b""
        self._credential_process = process
        self._credential_timer.start()
        process.start()

    def _append_keychain_output(self, process: QProcess) -> None:
        if process is self._credential_process:
            self._credential_stdout += bytes(process.readAllStandardOutput())

    def _keychain_finished(self, process: QProcess, exit_code: int, _status) -> None:
        if process is not self._credential_process:
            return
        self._credential_timer.stop()
        self._credential_stdout += bytes(process.readAllStandardOutput())
        self._credential_process = None
        process.deleteLater()
        data = self._credential_stdout
        self._credential_stdout = b""
        if exit_code != 0:
            self._use_legacy_credentials_or_unavailable()
            return
        credential = credential_from_json(data.strip())
        if credential is None:
            self._set_snapshot(
                _unavailable_snapshot(
                    "Claude Code 本地登录信息无法读取，请重新登录。"
                )
            )
            return
        self._request_usage(credential)

    def _keychain_failed(self, process: QProcess) -> None:
        if process is not self._credential_process:
            return
        self._credential_timer.stop()
        self._credential_process = None
        self._credential_stdout = b""
        process.deleteLater()
        self._use_legacy_credentials_or_unavailable()

    def _keychain_timed_out(self) -> None:
        process = self._credential_process
        if process is None:
            return
        self._credential_process = None
        self._credential_stdout = b""
        process.kill()
        process.deleteLater()
        self._set_snapshot(_error_snapshot(
            "Claude Code 钥匙串读取超时，请完成系统授权后重新刷新。", self._snapshot
        ))

    def _use_legacy_credentials_or_unavailable(self) -> None:
        credential = self._read_legacy_credentials()
        if credential is None:
            self._set_snapshot(_unavailable_snapshot())
            return
        self._request_usage(credential)

    def _request_usage(self, credential: ClaudeCredential) -> None:
        self._plan_type = credential.plan_type
        request = QNetworkRequest(QUrl(CLAUDE_USAGE_URL))
        request.setRawHeader(
            b"Authorization", f"Bearer {credential.access_token}".encode("utf-8")
        )
        request.setRawHeader(b"anthropic-beta", CLAUDE_OAUTH_BETA.encode("ascii"))
        request.setRawHeader(b"Content-Type", b"application/json")
        request.setHeader(
            QNetworkRequest.KnownHeaders.UserAgentHeader,
            "BORING-Console Claude-Code-Usage",
        )
        request.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy,
        )
        request.setTransferTimeout(10_000)
        reply = self._network.get(request)
        self._reply = reply
        reply.finished.connect(self._usage_finished)

    def _usage_finished(self) -> None:
        reply = self._reply
        self._reply = None
        if reply is None:
            return
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
                if status in (401, 403):
                    message = "Claude Code 登录已失效，请在 Claude Code 中重新登录。"
                else:
                    message = "Claude Code 额度服务暂时不可用。"
                self._set_snapshot(_error_snapshot(message, self._snapshot))
                return
            data = bytes(reply.readAll())
            if not data or len(data) > MAX_USAGE_RESPONSE_BYTES:
                raise ValueError("Claude Code 额度服务返回了无效响应")
            payload = json.loads(data.decode("utf-8"))
            snapshot = snapshot_from_claude_usage_response(
                payload,
                plan_type=self._plan_type,
            )
            self._set_snapshot(snapshot)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            self._set_snapshot(_error_snapshot(str(exc), self._snapshot))
        finally:
            reply.deleteLater()

    def _set_snapshot(self, snapshot: CodexUsageSnapshot) -> None:
        if snapshot == self._snapshot:
            return
        self._snapshot = snapshot
        self.changed.emit(snapshot)


def _parse_window(value: object, *, window_minutes: int) -> CodexQuotaWindow | None:
    if not isinstance(value, dict):
        return None
    utilization = value.get("utilization", value.get("used_percentage"))
    if not isinstance(utilization, (int, float)) or isinstance(utilization, bool):
        return None
    used_percent = float(utilization)
    if not math.isfinite(used_percent) or used_percent < 0:
        return None
    # Both OAuth utilization and statusline used_percentage are percentages.
    resets_at = _reset_epoch(
        value.get(
            "resets_at",
            value.get("resetsAt", value.get("resets_at_epoch")),
        )
    )
    return CodexQuotaWindow(
        used_percent=min(100.0, used_percent),
        window_minutes=window_minutes,
        resets_at=resets_at,
    )


def _reset_epoch(value: object) -> int | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    if not isinstance(value, str) or not value:
        return None
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def _loading_snapshot(previous: CodexUsageSnapshot | None) -> CodexUsageSnapshot:
    return CodexUsageSnapshot(
        status=CodexUsageStatus.LOADING,
        buckets=previous.buckets if previous is not None else (),
        source=previous.source if previous is not None else "none",
        updated_at=previous.updated_at if previous is not None else None,
        message="正在读取 Claude Code 额度…",
    )


def _unavailable_snapshot(message: str | None = None) -> CodexUsageSnapshot:
    return CodexUsageSnapshot(
        status=CodexUsageStatus.UNAVAILABLE,
        message=message
        or "未找到 Claude Code 登录信息。请先在 Claude Code 中登录订阅账户。",
    )


def _error_snapshot(
    message: str,
    previous: CodexUsageSnapshot,
) -> CodexUsageSnapshot:
    return CodexUsageSnapshot(
        status=CodexUsageStatus.ERROR,
        buckets=previous.buckets,
        source=previous.source,
        updated_at=previous.updated_at,
        message=message,
    )
