from __future__ import annotations

import json
import sys
import pytest

from PySide6.QtCore import QObject, QProcess, Signal
from PySide6.QtNetwork import QNetworkReply, QNetworkRequest

from controller_config.claude_usage import (
    CLAUDE_OAUTH_BETA,
    CLAUDE_USAGE_URL,
    ClaudeCredential,
    ClaudeUsageMonitor,
    credential_from_json,
    snapshot_from_claude_usage_response,
)
from controller_config.codex_usage import CodexUsageStatus


class _Reply(QObject):
    finished = Signal()

    def __init__(
        self,
        payload: object,
        *,
        error=QNetworkReply.NetworkError.NoError,
        status: int = 200,
    ) -> None:
        super().__init__()
        self._data = json.dumps(payload).encode("utf-8")
        self._error = error
        self._status = status

    def error(self):
        return self._error

    def attribute(self, attribute):
        if attribute == QNetworkRequest.Attribute.HttpStatusCodeAttribute:
            return self._status
        return None

    def readAll(self):  # noqa: N802 - Qt-compatible test double
        return self._data


class _Network:
    def __init__(self, reply: _Reply) -> None:
        self.reply = reply
        self.requests = []

    def get(self, request):
        self.requests.append(request)
        return self.reply


class _WaitingCredentialProcess(QProcess):
    """Exercise real Qt process cleanup, but never invoke security/Keychain."""

    def setProgram(self, _program):
        super().setProgram(sys.executable)

    def setArguments(self, _arguments):
        super().setArguments(["-c", "import time; time.sleep(60)"])

    def kill(self):
        self.killed = True
        super().kill()


def test_reads_nested_claude_code_oauth_credentials_without_exposing_token() -> None:
    credential = credential_from_json(
        json.dumps(
            {
                "claudeAiOauth": {
                    "accessToken": "secret-token",
                    "subscriptionType": "max",
                }
            }
        )
    )

    assert credential == ClaudeCredential("secret-token", "max")
    assert "secret-token" not in repr(credential)
    assert credential_from_json("not-json") is None
    assert credential_from_json("{}") is None


def test_parses_claude_session_weekly_and_model_specific_windows() -> None:
    snapshot = snapshot_from_claude_usage_response(
        {
            "five_hour": {
                "utilization": 23.5,
                "resets_at": "2026-09-02T12:30:00Z",
            },
            "seven_day": {
                "utilization": 61,
                "resets_at": "2026-09-07T08:00:00Z",
            },
            "seven_day_sonnet": {
                "utilization": 18,
                "resets_at": "2026-09-07T08:00:00Z",
            },
            "seven_day_opus": None,
        },
        plan_type="max",
        updated_at=1234.0,
    )

    assert snapshot.status is CodexUsageStatus.AVAILABLE
    assert snapshot.source == "claude_oauth"
    assert snapshot.updated_at == 1234.0
    assert [bucket.limit_id for bucket in snapshot.buckets] == [
        "claude",
        "claude_seven_day_sonnet",
    ]
    assert snapshot.buckets[0].display_name == "Claude Code"
    assert snapshot.buckets[0].plan_type == "max"
    assert snapshot.buckets[0].primary is not None
    assert snapshot.buckets[0].primary.remaining_percent == 76.5
    assert snapshot.buckets[0].secondary is not None
    assert snapshot.buckets[0].secondary.remaining_percent == 39
    assert snapshot.buckets[1].display_name == "Claude Code · Sonnet"


def test_parses_statusline_percentage_without_treating_one_percent_as_full() -> None:
    snapshot = snapshot_from_claude_usage_response(
        {
            "rate_limits": {
                "five_hour": {
                    "used_percentage": 1,
                    "resets_at_epoch": 1_800_000_000,
                }
            }
        }
    )

    window = snapshot.buckets[0].primary
    assert window is not None
    assert window.used_percent == 1
    assert window.remaining_percent == 99


@pytest.mark.parametrize("used", [0, 0.235, 1, 2, 100])
def test_oauth_utilization_is_always_a_percentage(used) -> None:
    window = snapshot_from_claude_usage_response(
        {"five_hour": {"utilization": used}}
    ).buckets[0].primary
    assert window.used_percent == used
    assert window.remaining_percent == 100 - used


def test_keychain_timeout_can_retry_without_old_callback_overwriting_new_request(
    qapp, qtbot, monkeypatch
) -> None:
    import controller_config.claude_usage as usage
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setattr(usage.sys, "platform", "darwin")
    monkeypatch.setattr(usage, "QProcess", _WaitingCredentialProcess)
    monitor = ClaudeUsageMonitor()
    monitor._credential_timer.setInterval(200)
    monitor.refresh()
    old = monitor._credential_process
    qtbot.waitUntil(lambda: monitor.snapshot.status is CodexUsageStatus.ERROR)
    assert old.killed
    assert monitor._credential_process is None
    assert "超时" in monitor.snapshot.message
    monitor._credential_timer.setInterval(15_000)
    monitor.refresh()
    new = monitor._credential_process
    monitor._keychain_finished(old, 1, None)
    assert monitor._credential_process is new
    assert monitor.snapshot.status is CodexUsageStatus.LOADING
    monitor.stop()
    assert not monitor._credential_timer.isActive()
    monitor.deleteLater()
    qtbot.wait(1)


def test_rejects_claude_usage_response_without_displayable_windows() -> None:
    try:
        snapshot_from_claude_usage_response({"extra_usage": {}})
    except ValueError as exc:
        assert "没有返回可显示的额度窗口" in str(exc)
    else:
        raise AssertionError("missing Claude quota windows should fail")


def test_monitor_requests_only_claude_usage_metadata(qapp, monkeypatch) -> None:
    reply = _Reply(
        {
            "five_hour": {"utilization": 9, "resets_at": None},
            "seven_day": {"utilization": 27, "resets_at": None},
        }
    )
    network = _Network(reply)
    monitor = ClaudeUsageMonitor(network=network)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "secret-token")

    monitor.refresh()

    assert len(network.requests) == 1
    request = network.requests[0]
    assert request.url().toString() == CLAUDE_USAGE_URL
    assert bytes(request.rawHeader("Authorization")) == b"Bearer secret-token"
    assert bytes(request.rawHeader("anthropic-beta")) == CLAUDE_OAUTH_BETA.encode()
    reply.finished.emit()
    assert monitor.snapshot.status is CodexUsageStatus.AVAILABLE
    assert monitor.snapshot.buckets[0].primary.remaining_percent == 91
    assert monitor.snapshot.buckets[0].secondary.remaining_percent == 73


def test_monitor_preserves_last_values_when_claude_request_fails(
    qapp, monkeypatch
) -> None:
    first = _Reply({"five_hour": {"utilization": 20, "resets_at": None}})
    network = _Network(first)
    monitor = ClaudeUsageMonitor(network=network)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "secret-token")
    monitor.refresh()
    first.finished.emit()
    previous_bucket = monitor.snapshot.buckets[0]

    failed = _Reply(
        {},
        error=QNetworkReply.NetworkError.AuthenticationRequiredError,
        status=401,
    )
    network.reply = failed
    monitor.refresh()
    failed.finished.emit()

    assert monitor.snapshot.status is CodexUsageStatus.ERROR
    assert monitor.snapshot.buckets == (previous_bucket,)
    assert "重新登录" in monitor.snapshot.message
