"""Packaged builds must start the real program, not Nuitka's "python" stand-in.

the firmware owner 11-16 (design ticket): the packaged Console wrote Claude Hooks that
ran ``Contents/MacOS/python``, a file Nuitka standalone builds name in
``sys.executable`` but never ship; "修复状态灯" wrote the same path again.
These tests reproduce a packaged environment: ``sys.executable`` points at a
missing file, ``sys.argv[0]`` at the real binary.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from controller_config import background_helper, claude_hooks, icon_material_check
from controller_config.claude_hooks import ClaudeHookInstallation
from controller_config.program_path import program_executable


@pytest.fixture
def packaged(tmp_path, monkeypatch):
    macos = tmp_path / "Applications/BORING Console Dev.app/Contents/MacOS"
    macos.mkdir(parents=True)
    program = macos / "console_entry"
    program.write_text("", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(macos / "python"))  # Nuitka: never shipped
    monkeypatch.setattr(sys, "argv", [str(program)])
    return program


def test_program_path_prefers_the_compiled_binary_over_missing_sys_executable(tmp_path):
    binary_dir = tmp_path / "MacOS"
    binary_dir.mkdir()
    (binary_dir / "console_entry").write_text("", encoding="utf-8")
    compiled = SimpleNamespace(containing_dir=str(binary_dir))
    # Started with a relative argv[0] from another directory.
    assert program_executable(compiled=compiled, argv0="./console_entry",
                              executable=str(binary_dir / "python")) == str(binary_dir / "console_entry")
    # Nothing better exists: keep the old value instead of inventing one.
    assert program_executable(compiled=None, argv0="", executable="/x/python") == "/x/python"


def test_packaged_hook_command_points_at_an_existing_program(packaged, tmp_path):
    args = claude_hooks._launcher_args(tmp_path / "endpoint.json")
    assert args[0] == str(packaged) and Path(args[0]).is_file()
    assert args[1:] == ["--claude-status-hook", str(tmp_path / "endpoint.json")]


def test_packaged_background_and_icon_worker_start_an_existing_program(packaged):
    assert background_helper.background_program_arguments() == (str(packaged), "--background")
    assert icon_material_check.worker_arguments("/tmp/a.png")[0] == str(packaged)


def test_repair_replaces_hooks_written_by_the_broken_package(packaged, tmp_path, monkeypatch):
    monkeypatch.setattr(claude_hooks, "_detect_claude", lambda: (None, None, claude_hooks.NO_CLI_NOTE))
    monkeypatch.setattr(claude_hooks, "_host_platform", lambda: "macos")
    settings = tmp_path / "claude/settings.json"
    settings.parent.mkdir(parents=True)
    user = {"model": "opus", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "say done"}]}]}}
    settings.write_text(json.dumps(user), encoding="utf-8")
    installation = ClaudeHookInstallation(settings, tmp_path / "boring/endpoint.json")

    # The 255034b package: argv[0] gave nothing usable, sys.executable won.
    broken = Path(sys.executable)
    monkeypatch.setattr(sys, "argv", [""])
    installation.install()
    assert str(broken) in settings.read_text(encoding="utf-8")
    status = installation.inspect()
    assert status.needs_repair and status.missing_targets == (str(broken),)

    # This package: "修复状态灯" (bridge.repair == install) points at the binary.
    monkeypatch.setattr(sys, "argv", [str(packaged)])
    repaired = installation.install()
    assert repaired.installed and not repaired.needs_repair
    config = json.loads(settings.read_text(encoding="utf-8"))
    rendered = json.dumps(config)
    assert str(broken) not in rendered and str(packaged) in rendered
    boring = [h for groups in config["hooks"].values() for g in groups for h in g["hooks"]
              if claude_hooks._is_boring_handler(h)]
    assert boring and all(h["command"] == str(packaged) for h in boring)
    assert len(boring) == len(repaired.events)  # one per event, none left behind
    assert {"type": "command", "command": "say done"} in [
        h for g in config["hooks"]["Stop"] for h in g["hooks"]]
    assert config["model"] == "opus"
