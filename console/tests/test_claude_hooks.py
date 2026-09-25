from __future__ import annotations

import io
import json
from pathlib import Path
import shlex
import socket
import subprocess
import sys
from types import SimpleNamespace

import pytest

from controller_config import claude_hook, claude_hooks
from controller_config.claude_hooks import ClaudeHookInstallation


@pytest.fixture
def installation(tmp_path, monkeypatch):
    monkeypatch.setattr(claude_hooks, "_detect_claude", lambda: ("/test/claude", "2.1.63", ""))
    monkeypatch.setattr(claude_hooks, "_host_platform", lambda: "macos")
    return ClaudeHookInstallation(tmp_path / "claude/settings.json", tmp_path / "boring/endpoint.json")


def _write(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_inspection_never_installs_or_writes(installation):
    status = installation.inspect()
    assert status.executable == "/test/claude"
    assert status.version == "2.1.63"
    assert not status.installed
    assert "StopFailure" not in status.events
    assert not installation.settings_path.exists()
    assert not installation.record_path.exists()


def test_install_merges_and_uninstall_preserves_other_hooks(installation):
    original = {"model": "sonnet", "permissions": {"allow": ["Read"]},
                "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo unrelated"}]}]}}
    _write(installation.settings_path, original)
    assert installation.install().installed
    once = json.loads(installation.settings_path.read_text())
    assert once["permissions"] == original["permissions"]
    assert once["hooks"]["Stop"][0] == original["hooks"]["Stop"][0]
    assert "StopFailure" not in once["hooks"]
    assert installation.install().installed
    assert json.loads(installation.settings_path.read_text()) == once
    assert not installation.uninstall().installed
    assert json.loads(installation.settings_path.read_text()) == original


def test_install_record_identifies_owner_origin_and_endpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(claude_hooks, "_detect_claude", lambda: ("/test/claude", "2.1.63", ""))
    monkeypatch.setattr(claude_hooks, "_host_platform", lambda: "macos")
    endpoint = tmp_path / "boring-diy/endpoint.json"
    install = ClaudeHookInstallation(
        tmp_path / "claude/settings.json",
        endpoint,
        owner_id="com.boring.controller-config.diy",
        origin="custom",
        official_build_probe=lambda: (("0.1.52", "official"),),
    )

    assert install.install().installed
    record = json.loads(install.record_path.read_text())
    assert record["owner"] == "com.boring.controller-config.diy"
    assert record["origin"] == "custom"
    assert record["endpoint_path"] == str(endpoint.absolute())


def test_other_owner_hooks_are_not_overwritten_or_duplicated(tmp_path, monkeypatch):
    monkeypatch.setattr(claude_hooks, "_detect_claude", lambda: ("/test/claude", "2.1.63", ""))
    monkeypatch.setattr(claude_hooks, "_host_platform", lambda: "macos")
    settings_path = tmp_path / "claude/settings.json"
    official_endpoint = tmp_path / "official/endpoint.json"
    diy_endpoint = tmp_path / "diy/endpoint.json"
    official = ClaudeHookInstallation(
        settings_path,
        official_endpoint,
        owner_id="com.boring.controller-config",
        origin="official",
    )
    diy = ClaudeHookInstallation(
        settings_path,
        diy_endpoint,
        owner_id="com.boring.controller-config.diy",
        origin="custom",
        official_build_probe=lambda: (("0.1.52", "official"),),
    )
    official.install()
    before = settings_path.read_text()

    with pytest.raises(ValueError, match="另一版本"):
        diy.install()

    assert settings_path.read_text() == before
    assert not diy.record_path.exists()

    official.uninstall()
    assert diy.install().installed
    settings = json.loads(settings_path.read_text())
    commands = json.dumps(settings)
    assert str(diy_endpoint.absolute()) in commands
    assert str(official_endpoint.absolute()) not in commands


def test_legacy_hook_record_is_rejected_without_mutating_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(claude_hooks, "_detect_claude", lambda: ("/test/claude", "2.1.63", ""))
    monkeypatch.setattr(claude_hooks, "_host_platform", lambda: "macos")
    settings_path = tmp_path / "claude/settings.json"
    endpoint = tmp_path / "legacy/endpoint.json"
    install = ClaudeHookInstallation(
        settings_path,
        endpoint,
        owner_id="com.boring.controller-config.diy",
        origin="custom",
        official_build_probe=lambda: (("0.1.52", "official"),),
    )
    handler = claude_hooks._handler(endpoint, "2.1.63", "macos")
    settings = {"hooks": {"Stop": [{"hooks": [handler]}]}}
    _write(settings_path, settings)
    _write(install.record_path, {"settings_path": str(settings_path.absolute()), "hooks": settings["hooks"]})
    before = settings_path.read_text()

    with pytest.raises(ValueError, match="旧版"):
        install.install()

    assert settings_path.read_text() == before


def test_official_owner_can_remove_its_legacy_default_hooks(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(claude_hooks, "_detect_claude", lambda: ("/test/claude", "2.1.63", ""))
    monkeypatch.setattr(claude_hooks, "_host_platform", lambda: "macos")
    claude_hooks.reset_default_identity()
    endpoint = tmp_path / ".boring/claude-status/endpoint.json"
    settings_path = tmp_path / "claude/settings.json"
    install = ClaudeHookInstallation(settings_path)
    handler = claude_hooks._handler(endpoint, "2.1.63", "macos")
    settings = {"hooks": {"Stop": [{"hooks": [handler]}]}}
    _write(settings_path, settings)
    _write(
        install.record_path,
        {"settings_path": str(settings_path.absolute()), "hooks": settings["hooks"]},
    )
    legacy_record = install.record_path.read_text()

    assert install.inspect().installed
    assert install.record_path.read_text() == legacy_record
    assert not install.uninstall().installed
    assert json.loads(settings_path.read_text()) == {}
    migrated = json.loads(install.record_path.read_text())
    assert migrated["owner"] == "com.boring.controller-config"
    assert migrated["origin"] == "official"


@pytest.mark.parametrize(
    "installed_builds",
    [(("0.1.50", None),), (("0.1.51", "official"),), ((None, None),)],
)
def test_diy_rejects_legacy_installed_official_even_when_hooks_are_empty(
    tmp_path, monkeypatch, installed_builds
):
    monkeypatch.setattr(
        claude_hooks, "_detect_claude", lambda: ("/test/claude", "2.1.63", "")
    )
    monkeypatch.setattr(claude_hooks, "_host_platform", lambda: "macos")
    settings_path = tmp_path / "claude/settings.json"
    endpoint = tmp_path / "diy/endpoint.json"
    install = ClaudeHookInstallation(
        settings_path,
        endpoint,
        owner_id="com.boring.controller-config.diy",
        origin="custom",
        official_build_probe=lambda: installed_builds,
    )

    with pytest.raises(ValueError, match="先升级官方版"):
        install.install()

    assert not settings_path.exists()
    assert not install.record_path.exists()


def test_diy_allows_hooks_after_owner_aware_official_upgrade(tmp_path, monkeypatch):
    monkeypatch.setattr(
        claude_hooks, "_detect_claude", lambda: ("/test/claude", "2.1.63", "")
    )
    monkeypatch.setattr(claude_hooks, "_host_platform", lambda: "macos")
    install = ClaudeHookInstallation(
        tmp_path / "claude/settings.json",
        tmp_path / "diy/endpoint.json",
        owner_id="com.boring.controller-config.diy",
        origin="custom",
        official_build_probe=lambda: (("0.1.52", "official"),),
    )

    assert install.install().installed


def test_configured_default_hook_identity_uses_separate_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    claude_hooks.configure_default_identity(
        owner_id="com.boring.controller-config.diy",
        origin="custom",
        endpoint_path=tmp_path / ".boring/claude-status-diy/endpoint.json",
    )
    try:
        install = ClaudeHookInstallation(tmp_path / "claude/settings.json")
        assert install.owner_id == "com.boring.controller-config.diy"
        assert install.origin == "custom"
        assert install.endpoint_path == tmp_path / ".boring/claude-status-diy/endpoint.json"
    finally:
        claude_hooks.reset_default_identity()


def test_uninstall_preserves_later_edits_inside_own_group(installation):
    installation.install()
    configured = json.loads(installation.settings_path.read_text())
    later_handler = {"type": "command", "command": "echo collaborator"}
    configured["hooks"]["Stop"][0]["hooks"].append(later_handler)
    configured["theme"] = "dark"
    _write(installation.settings_path, configured)
    installation.uninstall()
    assert json.loads(installation.settings_path.read_text()) == {
        "theme": "dark", "hooks": {"Stop": [{"hooks": [later_handler]}]}}


def test_old_or_unknown_versions_and_disabled_hooks_do_not_write(installation, monkeypatch):
    monkeypatch.setattr(claude_hooks, "_detect_claude", lambda: ("/test/claude", "2.0.1", ""))
    with pytest.raises(ValueError, match="2.1.63"):
        installation.install()
    assert not installation.settings_path.exists()
    monkeypatch.setattr(claude_hooks, "_detect_claude", lambda: ("/test/claude", "2.1.63", ""))
    original = {"disableAllHooks": True, "custom": "keep"}
    _write(installation.settings_path, original)
    with pytest.raises(ValueError, match="禁用"):
        installation.install()
    assert json.loads(installation.settings_path.read_text()) == original


def test_malformed_settings_are_not_overwritten(installation):
    installation.settings_path.parent.mkdir(parents=True)
    installation.settings_path.write_text("{unfinished", encoding="utf-8")
    with pytest.raises(ValueError):
        installation.install()
    assert installation.settings_path.read_text() == "{unfinished"


def test_stopfailure_requires_supported_version_and_new_exec_form(installation, monkeypatch):
    monkeypatch.setattr(claude_hooks, "_detect_claude", lambda: ("/test/claude", "2.1.78", ""))
    assert "StopFailure" in installation.install().events
    config = json.loads(installation.settings_path.read_text())
    assert "args" not in config["hooks"]["StopFailure"][0]["hooks"][0]
    monkeypatch.setattr(claude_hooks, "_detect_claude", lambda: ("/test/claude", "2.1.139", ""))
    installation.install()
    config = json.loads(installation.settings_path.read_text())
    assert "args" in config["hooks"]["StopFailure"][0]["hooks"][0]


def test_source_and_native_commands_use_correct_entrypoints(monkeypatch, tmp_path):
    endpoint = tmp_path / "path with space/endpoint.json"
    source = claude_hooks._launcher_args(endpoint, frozen=False)
    assert Path(source[1]).name == "claude_hook.py"
    assert source[-2:] == ["--endpoint", str(endpoint)]
    assert claude_hooks._launcher_args(endpoint, frozen=True) == [
        sys.executable, "--claude-status-hook", str(endpoint)]
    monkeypatch.setattr(claude_hooks, "_launcher_args", lambda _: [
        r"C:\Program Files\BORING\console.exe", "--claude-status-hook", r"C:\Users\A B\endpoint.json"])
    handler = claude_hooks._handler(endpoint, "2.1.63", "windows-native")
    assert shlex.split(handler["command"]) == [
        "C:/Program Files/BORING/console.exe", "--claude-status-hook", "C:/Users/A B/endpoint.json"]
    assert "wsl" not in handler["command"].lower()


def test_windows_and_wsl_are_marked_unverified(installation, monkeypatch):
    for target in ("windows-native", "wsl"):
        monkeypatch.setattr(claude_hooks, "_host_platform", lambda: target)
        status = installation.inspect()
        assert status.platform == target
        assert "未验证" in status.note


def test_event_projection_drops_all_content_and_limits_error_types():
    original = {
        "session_id": "session-1", "hook_event_name": "Stop", "stop_hook_active": False,
        "tool_name": "AskUserQuestion", "agent_id": "child-1", "notification_type": "idle_prompt",
        "is_interrupt": True, "prompt": "private prompt", "last_assistant_message": "private reply",
        "tool_input": {"question": "secret"}, "tool_response": {"token": "secret"},
        "cwd": "/private/project", "transcript_path": "/private/transcript", "error": "secret error",
        "background_tasks": [{"command": "private command"}],
        "session_crons": [{"prompt": "private cron prompt"}],
    }
    result = claude_hook.extract_event(original)
    assert result == {"session_id": "session-1", "hook_event_name": "Stop", "stop_hook_active": False,
                      "tool_name": "AskUserQuestion", "agent_id": "child-1", "notification_type": "idle_prompt",
                      "is_interrupt": True, "has_background_tasks": True, "has_session_crons": True}
    assert "private" not in json.dumps(result)
    assert claude_hook.extract_event({**original, "hook_event_name": "StopFailure", "error": "rate_limit"})["error"] == "rate_limit"
    assert "error" not in claude_hook.extract_event({**original, "hook_event_name": "StopFailure"})
    assert claude_hook.extract_event({**original, "hook_event_name": "unknown"}) is None


class _Process:
    def __init__(self, name, exe, parent=None, pid=12, args=()):
        self._name, self._exe, self._parent, self.pid, self._args = name, exe, parent, pid, args

    def name(self):
        return self._name

    def exe(self):
        return self._exe

    def parent(self):
        return self._parent

    def create_time(self):
        return 1000.25

    def cmdline(self):
        return self._args


def test_owner_is_claude_ancestor_never_transient_shell(monkeypatch):
    claude = _Process("claude", "/Users/test/.local/share/claude/versions/2.1.63", pid=123)
    shell = _Process("sh", "/bin/sh", parent=claude)
    hook = _Process("python", "/bin/python", parent=shell)
    monkeypatch.setattr(claude_hook.psutil, "Process", lambda: hook)
    assert claude_hook.claude_owner() == (123, 1000.25)
    claude._name = "2.1.63"
    assert claude_hook.claude_owner() == (123, 1000.25)
    shell._parent = None
    assert claude_hook.claude_owner() == (None, None)
    shell._parent = _Process("node", "/bin/node", pid=124,
                            args=["node", "/lib/node_modules/@anthropic-ai/claude-code/cli.js"])
    assert claude_hook.claude_owner() == (124, 1000.25)


def test_adapter_delivers_only_metadata_to_loopback(tmp_path, monkeypatch, capsys):
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        server.settimeout(1)
        endpoint = tmp_path / "endpoint.json"
        _write(endpoint, {"host": "127.0.0.1", "port": server.getsockname()[1], "token": "local-token"})
        payload = {"session_id": "session-1", "hook_event_name": "UserPromptSubmit", "prompt": "DO NOT SEND"}
        monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(json.dumps(payload).encode())))
        monkeypatch.setattr(claude_hook, "claude_owner", lambda: (321, 1000.5))
        assert claude_hook.run_hook(endpoint) == 0
        client, _ = server.accept()
        with client:
            data = client.recv(claude_hook.MAX_MESSAGE_BYTES)
        assert data.endswith(b"\n")
        assert json.loads(data) == {"token": "local-token", "event": {
            "session_id": "session-1", "hook_event_name": "UserPromptSubmit",
            "owner_pid": 321, "owner_started": 1000.5}}
        assert capsys.readouterr() == ("", "")


def test_offline_hook_exit_is_silent_and_fast_from_other_project(tmp_path):
    args = claude_hooks._launcher_args(tmp_path / "nonexistent/endpoint.json", frozen=False)
    completed = subprocess.run(args, input=b'{"session_id":"s","hook_event_name":"Stop"}',
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=tmp_path,
                               timeout=2, check=False)
    assert completed.returncode == 0
    assert completed.stdout == completed.stderr == b""


def test_adapter_rejects_lan_and_oversized_input_without_sending(tmp_path, monkeypatch):
    endpoint = tmp_path / "endpoint.json"
    monkeypatch.setattr(claude_hook.socket, "create_connection", lambda *a, **kw: pytest.fail("must not connect"))
    _write(endpoint, {"host": "192.168.1.1", "port": 10000, "token": "token"})
    assert claude_hook.run_hook(endpoint) == 0
    _write(endpoint, {"host": "127.0.0.1", "port": 10000, "token": "token"})
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b"x" * (claude_hook.MAX_INPUT_BYTES + 1))))
    assert claude_hook.run_hook(endpoint) == 0
