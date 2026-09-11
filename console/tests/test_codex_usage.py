from __future__ import annotations

import json

from PySide6.QtCore import QProcess

import controller_config.codex_usage as codex_usage
from controller_config.codex_usage import (
    CodexUsageMonitor,
    CodexUsageStatus,
    load_latest_session_usage,
    snapshot_from_app_server_response,
)


class _FinishedProcess:
    def __init__(self) -> None:
        self.deleted = False

    def state(self):
        return QProcess.ProcessState.NotRunning

    def deleteLater(self) -> None:  # noqa: N802 - Qt-compatible test double
        self.deleted = True


def test_parses_official_app_server_rate_limit_buckets() -> None:
    response = {
        "id": 2,
        "result": {
            "rateLimits": {
                "limitId": "codex",
                "limitName": None,
                "planType": "pro",
                "primary": {
                    "usedPercent": 12,
                    "windowDurationMins": 300,
                    "resetsAt": 1_800_000_000,
                },
                "secondary": {
                    "usedPercent": 34.5,
                    "windowDurationMins": 10_080,
                    "resetsAt": 1_800_086_400,
                },
            },
            "rateLimitsByLimitId": {
                "codex": {
                    "limitId": "codex",
                    "limitName": None,
                    "planType": "pro",
                    "primary": {
                        "usedPercent": 12,
                        "windowDurationMins": 300,
                        "resetsAt": 1_800_000_000,
                    },
                    "secondary": {
                        "usedPercent": 34.5,
                        "windowDurationMins": 10_080,
                        "resetsAt": 1_800_086_400,
                    },
                },
                "codex_bengalfox": {
                    "limitId": "codex_bengalfox",
                    "limitName": "GPT-5.3-Codex-Spark",
                    "planType": "pro",
                    "primary": {
                        "usedPercent": 4,
                        "windowDurationMins": 300,
                        "resetsAt": 1_800_000_000,
                    },
                    "secondary": None,
                },
            },
        },
    }

    snapshot = snapshot_from_app_server_response(response, updated_at=1234.0)

    assert snapshot.status is CodexUsageStatus.AVAILABLE
    assert snapshot.source == "app_server"
    assert snapshot.updated_at == 1234.0
    assert [bucket.limit_id for bucket in snapshot.buckets] == [
        "codex",
        "codex_bengalfox",
    ]
    assert snapshot.buckets[0].primary is not None
    assert snapshot.buckets[0].primary.used_percent == 12
    assert snapshot.buckets[0].primary.remaining_percent == 88
    assert snapshot.buckets[0].secondary is not None
    assert snapshot.buckets[0].secondary.window_minutes == 10_080
    assert snapshot.buckets[1].display_name == "GPT-5.3-Codex-Spark"


def test_local_session_log_is_a_clearly_marked_fallback(tmp_path) -> None:
    session = tmp_path / "2026" / "08" / "31" / "rollout.jsonl"
    session.parent.mkdir(parents=True)
    session.write_text(
        "\n".join(
            [
                json.dumps({"type": "event_msg", "payload": {"type": "other"}}),
                json.dumps(
                    {
                        "timestamp": "2026-08-31T07:30:00Z",
                        "type": "event_msg",
                        "payload": {
                            "type": "token_count",
                            "rate_limits": {
                                "limit_id": "codex",
                                "limit_name": None,
                                "plan_type": "pro",
                                "primary": {
                                    "used_percent": 21,
                                    "window_minutes": 300,
                                    "resets_at": 1_800_000_000,
                                },
                                "secondary": {
                                    "used_percent": 43,
                                    "window_minutes": 10_080,
                                    "resets_at": 1_800_086_400,
                                },
                            },
                        },
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    snapshot = load_latest_session_usage(tmp_path)

    assert snapshot.status is CodexUsageStatus.AVAILABLE
    assert snapshot.source == "session_log"
    assert snapshot.buckets[0].limit_id == "codex"
    assert snapshot.buckets[0].secondary is not None
    assert snapshot.buckets[0].secondary.used_percent == 43
    assert "可能滞后" in snapshot.message


def test_missing_or_invalid_session_logs_return_unavailable(tmp_path) -> None:
    invalid = tmp_path / "invalid.jsonl"
    invalid.write_text("not-json\n", encoding="utf-8")

    snapshot = load_latest_session_usage(tmp_path)

    assert snapshot.status is CodexUsageStatus.UNAVAILABLE
    assert snapshot.buckets == ()
    assert snapshot.source == "none"


def test_monitor_releases_finished_process_objects(qapp) -> None:
    monitor = CodexUsageMonitor()
    finished = _FinishedProcess()
    monitor._process = finished
    monitor._request_completed = True

    monitor._app_server_finished(0, QProcess.ExitStatus.NormalExit)

    assert monitor._process is None
    assert finished.deleted

    stopped = _FinishedProcess()
    monitor._process = stopped
    monitor.stop()

    assert monitor._process is None
    assert stopped.deleted


def test_monitor_repeated_missing_executable_refreshes_do_not_stick_loading(
    qapp, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(codex_usage, "resolve_codex_executable", lambda: None)
    monitor = CodexUsageMonitor(sessions_root=tmp_path)

    monitor.refresh()
    assert monitor.snapshot.status is CodexUsageStatus.ERROR

    monitor.refresh()
    assert monitor.snapshot.status is CodexUsageStatus.ERROR
    assert monitor._process is None
