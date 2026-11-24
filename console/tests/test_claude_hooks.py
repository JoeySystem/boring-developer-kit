from __future__ import annotations

import io
import json
from pathlib import Path
import shlex
import shutil
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


def test_desktop_only_install_without_cli_uses_exec_form_and_stopfailure(tmp_path, monkeypatch):
    # Real detection on a machine with only Claude desktop: no CLI on PATH or
    # in the known install locations, so no version can be read.
    monkeypatch.setattr(claude_hooks.shutil, "which", lambda _name: None)
    monkeypatch.setattr(Path, "is_file", lambda _path: False)
    monkeypatch.setattr(claude_hooks, "_host_platform", lambda: "macos")
    installation = ClaudeHookInstallation(tmp_path / "claude/settings.json", tmp_path / "boring/endpoint.json")
    status = installation.inspect()
    assert (status.executable, status.version, status.problem) == (None, None, "")
    assert claude_hooks.NO_CLI_NOTE in status.note
    status = installation.install()
    assert status.installed and "StopFailure" in status.events
    config = json.loads(installation.settings_path.read_text())
    for event in status.events:
        handler = config["hooks"][event][0]["hooks"][0]
        assert handler["command"] == claude_hooks._launcher_args(installation.endpoint_path)[0]
        assert "args" in handler


def test_unreadable_cli_version_installs_like_desktop(installation, monkeypatch):
    monkeypatch.setattr(claude_hooks, "_detect_claude",
                        lambda: ("/test/claude", None, "无法读取 Claude Code 版本。"))
    status = installation.install()
    assert status.installed and not status.problem
    assert claude_hooks.UNKNOWN_VERSION_NOTE in status.note
    config = json.loads(installation.settings_path.read_text())
    assert "args" in config["hooks"]["StopFailure"][0]["hooks"][0]


def test_source_and_native_commands_use_correct_entrypoints(monkeypatch, tmp_path):
    endpoint = tmp_path / "path with space/endpoint.json"
    source = claude_hooks._launcher_args(endpoint, frozen=False)
    assert Path(source[1]).name == "claude_hook.py"
    assert source[-2:] == ["--endpoint", str(endpoint)]
    program = tmp_path / "BORING Console.app/Contents/MacOS/console_entry"
    program.parent.mkdir(parents=True)
    program.write_text("", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [str(program)])
    assert claude_hooks._launcher_args(endpoint, frozen=True) == [
        str(program), "--claude-status-hook", str(endpoint)]
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


def _fake_nvm_claude(home: Path, node_version: str, cli_version: str) -> Path:
    # npm/nvm installs are `#!/usr/bin/env node` scripts; node sits next to
    # them and is not on the PATH a Finder-launched app gets.
    bin_dir = home / ".nvm/versions/node" / node_version / "bin"
    bin_dir.mkdir(parents=True)
    node = bin_dir / "node"
    node.write_text(f'#!/bin/sh\necho "{cli_version} (Claude Code)"\n', encoding="utf-8")
    claude = bin_dir / "claude"
    claude.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    for path in (node, claude):
        path.chmod(0o755)
    return claude


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX launch environment")
def test_finder_launch_finds_nvm_installed_cli_and_reads_its_version(tmp_path, monkeypatch):
    # M25: Finder gives the app PATH=/usr/bin:/bin:/usr/sbin:/sbin and no NVM_DIR.
    home = tmp_path / "home"
    _fake_nvm_claude(home, "v18.20.4", "2.1.70")
    newest = _fake_nvm_claude(home, "v22.11.0", "2.1.100")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
    monkeypatch.delenv("NVM_DIR", raising=False)

    def run(args, *, env=None, **_kwargs):
        # What `#!/usr/bin/env node` does: find node on the PATH it is given.
        node = shutil.which("node", path=(env or {}).get("PATH", ""))
        if node is None:
            return SimpleNamespace(returncode=127, stdout="")
        version = Path(node).read_text(encoding="utf-8").split('"')[1].split()[0]
        return SimpleNamespace(returncode=0, stdout=f"{version} (Claude Code)\n")

    monkeypatch.setattr(claude_hooks.subprocess, "run", run)
    assert claude_hooks._detect_claude() == (str(newest), "2.1.100", "")

    monkeypatch.setattr(claude_hooks, "_host_platform", lambda: "macos")
    installation = ClaudeHookInstallation(tmp_path / "claude/settings.json",
                                          tmp_path / "boring/endpoint.json")
    status = installation.install()
    assert status.installed and status.version == "2.1.100"
    handler = json.loads(installation.settings_path.read_text())["hooks"]["Stop"][0]["hooks"][0]
    # 2.1.100 predates exec-form args: the hook must use the shell form.
    assert "args" not in handler
    assert "claude_hook.py" in handler["command"] or "--claude-status-hook" in handler["command"]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlinks and modes")
def test_symlinked_settings_stay_linked_and_keep_permissions(tmp_path, monkeypatch):
    monkeypatch.setattr(claude_hooks, "_detect_claude", lambda: ("/test/claude", "2.1.63", ""))
    monkeypatch.setattr(claude_hooks, "_host_platform", lambda: "macos")
    real = tmp_path / "dotfiles/claude-settings.json"
    user = {"model": "opus", "permissions": {"allow": ["Bash(ls:*)"]},
            "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "say 好"}]}]},
            "env": {"中文": "保留"}}
    _write(real, user)
    real.chmod(0o644)
    link = tmp_path / "claude/settings.json"
    link.parent.mkdir(parents=True)
    link.symlink_to(real)
    installation = ClaudeHookInstallation(link, tmp_path / "boring/endpoint.json")

    assert installation.install().installed
    assert link.is_symlink() and link.resolve() == real.resolve()
    assert oct(real.stat().st_mode & 0o777) == "0o644"
    config = json.loads(real.read_text(encoding="utf-8"))
    assert {k: v for k, v in config.items() if k != "hooks"} == {
        k: v for k, v in user.items() if k != "hooks"}
    assert config["hooks"]["Stop"][0] == user["hooks"]["Stop"][0]
    assert not list(link.parent.glob(".settings.json.*"))

    assert not installation.uninstall().installed
    assert link.is_symlink()
    assert oct(real.stat().st_mode & 0o777) == "0o644"
    assert json.loads(real.read_text(encoding="utf-8")) == user


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
def test_plain_settings_file_keeps_its_permissions(installation):
    _write(installation.settings_path, {"theme": "dark"})
    installation.settings_path.chmod(0o640)
    installation.install()
    assert oct(installation.settings_path.stat().st_mode & 0o777) == "0o640"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlinks")
def test_dangling_settings_link_is_reported_and_never_replaced(installation, tmp_path):
    link = installation.settings_path
    link.parent.mkdir(parents=True)
    link.symlink_to(tmp_path / "missing/settings.json")
    status = installation.inspect()
    assert status.problem == claude_hooks.DANGLING_LINK_PROBLEM
    with pytest.raises(ValueError):
        installation.install()
    assert link.is_symlink() and not (tmp_path / "missing").exists()
    assert not installation.record_path.exists()


def _place_console(monkeypatch, app: Path) -> None:
    # Stand-in for the packaged Console executable at a given location.
    app.parent.mkdir(parents=True, exist_ok=True)
    app.write_text("", encoding="utf-8")
    monkeypatch.setattr(claude_hooks, "_launcher_args", lambda endpoint, frozen=None: [
        str(app), "--claude-status-hook", str(endpoint)])


def _user_settings() -> dict:
    return {
        "model": "opus", "env": {"中文": "保留"},
        "permissions": {"allow": ["Bash(ls:*)"]},
        "hooks": {
            "Stop": [{"hooks": [{"type": "command", "command": "say done"}]}],
            "PreToolUse": [{"matcher": "Bash", "hooks": [
                {"type": "command", "command": "/usr/local/bin/audit"}]}],
        },
    }


def _without_boring(config: dict) -> dict:
    result = {k: v for k, v in config.items() if k != "hooks"}
    hooks = {}
    for event, groups in config.get("hooks", {}).items():
        kept = []
        for group in groups:
            handlers = [h for h in group["hooks"] if not claude_hooks._is_boring_handler(h)]
            if handlers:
                kept.append({**group, "hooks": handlers})
        if kept:
            hooks[event] = kept
    if hooks:
        result["hooks"] = hooks
    return result


@pytest.mark.parametrize("version", ["2.1.63", None])  # shell form, exec form
def test_moved_console_is_detected_and_one_repair_keeps_user_settings(
        installation, tmp_path, monkeypatch, version):
    # H20: Console installed Hooks from a DMG / old folder, then was moved and
    # the old copy disappeared. Before the fix inspect() still said installed.
    monkeypatch.setattr(claude_hooks, "_detect_claude", lambda: ("/test/claude", version, ""))
    _write(installation.settings_path, _user_settings())
    old_app = tmp_path / "Volumes/BORING/BORING Console.app/Contents/MacOS/BORING Console"
    _place_console(monkeypatch, old_app)
    assert not installation.install().needs_repair

    new_app = tmp_path / "Applications/BORING Console.app/Contents/MacOS/BORING Console"
    old_app.unlink()
    _place_console(monkeypatch, new_app)
    status = installation.inspect()
    assert status.installed and status.needs_repair and not status.problem
    assert status.missing_targets == (str(old_app),)
    assert claude_hooks.MISSING_TARGET_WARNING in status.note
    before = installation.settings_path.read_text(encoding="utf-8")
    assert installation.settings_path.read_text(encoding="utf-8") == before  # inspect never writes

    repaired = installation.install()
    assert repaired.installed and not repaired.needs_repair
    config = json.loads(installation.settings_path.read_text(encoding="utf-8"))
    assert _without_boring(config) == _user_settings()
    rendered = json.dumps(config, ensure_ascii=False)
    assert str(old_app) not in rendered and str(new_app) in rendered
    assert sum(claude_hooks._is_boring_handler(h) for g in config["hooks"]["Stop"]
               for h in g["hooks"]) == 1
    assert not installation.uninstall().installed
    assert json.loads(installation.settings_path.read_text(encoding="utf-8")) == _user_settings()


def test_hooks_of_another_existing_copy_offer_repair_without_warning(installation, tmp_path, monkeypatch):
    _place_console(monkeypatch, tmp_path / "old/BORING Console")
    installation.install()
    _place_console(monkeypatch, tmp_path / "new/BORING Console")
    status = installation.inspect()
    assert status.points_elsewhere and status.needs_repair and not status.missing_targets
    assert claude_hooks.ELSEWHERE_NOTE in status.note
    assert not installation.install().needs_repair


def test_missing_source_hook_script_is_detected(installation, tmp_path, monkeypatch):
    script = tmp_path / "checkout/src/controller_config/claude_hook.py"
    script.parent.mkdir(parents=True)
    script.write_text("", encoding="utf-8")
    monkeypatch.setattr(claude_hooks, "_launcher_args", lambda endpoint, frozen=None: [
        sys.executable, str(script), "--endpoint", str(endpoint)])
    installation.install()
    script.unlink()  # development checkout deleted
    assert installation.inspect().missing_targets == (str(script),)


def test_windows_shell_form_paths_compare_equal_to_native_paths():
    native = {"type": "command", "command": "C:\\BORING\\BORING Console.exe",
              "args": ["--claude-status-hook", "C:\\Users\\u\\endpoint.json"]}
    shell = {"type": "command",
             "command": shlex.join(["C:/BORING/BORING Console.exe", "--claude-status-hook", "x"])}
    if sys.platform == "win32":
        assert claude_hooks._handler_targets(native) == claude_hooks._handler_targets(shell)
    assert claude_hooks._handler_targets({"type": "command"}) == ()
