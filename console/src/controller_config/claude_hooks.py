"""Explicit installation of BORING's lifecycle-only Claude Code hooks."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Callable


BASE_EVENTS = (
    "SessionStart", "UserPromptSubmit", "PreToolUse", "PermissionRequest",
    "PostToolUse", "PostToolUseFailure", "Stop", "SessionEnd", "Notification",
    "SubagentStart", "SubagentStop",
)
# The first supported baseline is the locally inspected release. StopFailure
# was introduced in 2.1.78, exec-form hook args in 2.1.139 (official changelog).
MINIMUM_VERSION = (2, 1, 63)
STOP_FAILURE_VERSION = (2, 1, 78)
EXEC_ARGS_VERSION = (2, 1, 139)
OWNER_AWARE_CONSOLE_VERSION = (0, 1, 52)

_DEFAULT_OWNER_ID = "com.boring.controller-config"
_DEFAULT_ORIGIN = "official"
_DEFAULT_ENDPOINT_PATH: Path | None = None


@dataclass(frozen=True)
class ClaudeHookInspection:
    executable: str | None
    version: str | None
    platform: str
    installed: bool
    events: tuple[str, ...]
    problem: str = ""
    note: str = ""


def default_endpoint_path() -> Path:
    if _DEFAULT_ENDPOINT_PATH is not None:
        return _DEFAULT_ENDPOINT_PATH
    return Path.home() / ".boring" / "claude-status" / "endpoint.json"


def configure_default_identity(
    *, owner_id: str, origin: str, endpoint_path: Path | str
) -> None:
    """Configure the Hooks owner before the view model creates its bridge."""

    if not owner_id or origin not in {"official", "custom"}:
        raise ValueError("BORING Hooks 构建身份无效")
    global _DEFAULT_OWNER_ID, _DEFAULT_ORIGIN, _DEFAULT_ENDPOINT_PATH
    _DEFAULT_OWNER_ID = owner_id
    _DEFAULT_ORIGIN = origin
    _DEFAULT_ENDPOINT_PATH = Path(endpoint_path).expanduser().absolute()


def reset_default_identity() -> None:
    """Restore source-compatible defaults. Intended for isolated tests."""

    global _DEFAULT_OWNER_ID, _DEFAULT_ORIGIN, _DEFAULT_ENDPOINT_PATH
    _DEFAULT_OWNER_ID = "com.boring.controller-config"
    _DEFAULT_ORIGIN = "official"
    _DEFAULT_ENDPOINT_PATH = None


def _host_platform() -> str:
    if sys.platform == "win32":
        return "windows-native"
    if sys.platform == "darwin":
        return "macos"
    if "microsoft" in platform.release().lower() or os.environ.get("WSL_DISTRO_NAME"):
        return "wsl"
    return "linux"


def _detect_claude() -> tuple[str | None, str | None, str]:
    executable = shutil.which("claude")
    if executable is None:
        # Finder-launched apps do not inherit the user's interactive-shell PATH.
        candidates = (Path.home() / ".local/bin/claude", Path("/opt/homebrew/bin/claude"),
                      Path("/usr/local/bin/claude")) if sys.platform != "win32" else (
            Path.home() / ".local/bin/claude.exe",
            Path(os.environ.get("APPDATA", str(Path.home()))) / "npm/claude.cmd",
        )
        executable = next((str(path) for path in candidates if path.is_file()), None)
    if executable is None:
        return None, None, "未找到本机 Claude Code；请先安装并确认 claude --version 可运行。"
    try:
        result = subprocess.run([executable, "--version"], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                timeout=2, check=False, text=True)
        match = re.search(r"(?<!\d)(\d+\.\d+\.\d+)(?!\d)", result.stdout)
        if result.returncode != 0 or match is None:
            return executable, None, "无法读取 Claude Code 版本。"
        return executable, match.group(1), ""
    except (OSError, subprocess.SubprocessError):
        return executable, None, "读取 Claude Code 版本失败或超时。"


def _version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def _installed_official_builds() -> tuple[tuple[str | None, str | None], ...]:
    """Return installed official identities relevant to Hooks ownership."""

    if sys.platform == "darwin":
        results: list[tuple[str | None, str | None]] = []
        for bundle in (
            Path("/Applications/BORING Console.app"),
            Path.home() / "Applications/BORING Console.app",
        ):
            plist_path = bundle / "Contents/Info.plist"
            if not plist_path.is_file():
                continue
            try:
                info = plistlib.loads(plist_path.read_bytes())
                version = info.get("CFBundleVersion")
            except (OSError, ValueError, TypeError):
                version = None
            marker_path = (
                bundle
                / "Contents/MacOS/controller_config/assets/app-build.json"
            )
            try:
                marker = json.loads(marker_path.read_text(encoding="utf-8"))
                origin = marker.get("origin") if isinstance(marker, dict) else None
            except (OSError, UnicodeError, ValueError, TypeError):
                origin = None
            results.append((str(version) if version else None, origin))
        return tuple(results)
    if sys.platform != "win32":
        return ()

    try:
        import winreg
    except ImportError:
        return ()
    subkey = (
        "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\"
        "{7D499724-C0AE-438B-93C1-59E2D01785D7}_is1"
    )
    results = []
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for access in (
            winreg.KEY_READ,
            winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0),
            winreg.KEY_READ | getattr(winreg, "KEY_WOW64_32KEY", 0),
        ):
            try:
                with winreg.OpenKey(root, subkey, 0, access) as key:
                    version = winreg.QueryValueEx(key, "DisplayVersion")[0]
            except OSError:
                continue
            item = (str(version) if version else None, "official")
            if item not in results:
                results.append(item)
    return tuple(results)


def _requires_owner_aware_official_upgrade(
    builds: tuple[tuple[str | None, str | None], ...],
) -> bool:
    for version, origin in builds:
        try:
            compatible_version = (
                version is not None
                and _version_tuple(version) >= OWNER_AWARE_CONSOLE_VERSION
            )
        except (AttributeError, ValueError):
            compatible_version = False
        if origin != "official" or not compatible_version:
            return True
    return False


def _read_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} 必须是 JSON 对象；未修改原文件。")
    return data


def _write_object(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _hook_groups(settings: dict[str, Any]) -> dict[str, Any]:
    hooks = settings.get("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("Claude Code hooks 格式不正确；未修改原设置。")
    return hooks


def _launcher_args(endpoint: Path, *, frozen: bool | None = None) -> list[str]:
    if frozen is None:
        frozen = bool(getattr(sys, "frozen", False) or "__compiled__" in globals())
    if frozen:
        return [sys.executable, "--claude-status-hook", str(endpoint)]
    # Absolute script path also works when Claude's project cwd differs from the
    # source checkout. This module has no package-relative imports or Qt startup.
    return [sys.executable, str(Path(__file__).with_name("claude_hook.py")),
            "--endpoint", str(endpoint)]


def _handler(endpoint: Path, version: str, host_platform: str) -> dict[str, Any]:
    args = _launcher_args(endpoint)
    result: dict[str, Any] = {"type": "command", "timeout": 2}
    if _version_tuple(version) >= EXEC_ARGS_VERSION:
        result.update(command=args[0], args=args[1:])
    else:
        # <=2.1.138 command hooks run through sh / Git Bash (native Windows),
        # not cmd.exe. Native Windows paths must retain drive letters and use
        # forward slashes; never redirect them into a WSL distribution.
        if host_platform == "windows-native":
            args = [value.replace("\\", "/") for value in args]
        result["command"] = shlex.join(args)
    return result


def _remove_recorded(settings: dict[str, Any], recorded: dict[str, Any]) -> None:
    hooks = _hook_groups(settings)
    for event, groups in recorded.items():
        current = hooks.get(event)
        if not isinstance(current, list) or not isinstance(groups, list):
            continue
        for installed_group in groups:
            # If a user added another handler to our group, keep it and remove
            # only our exact recorded handler. Never remove by event name alone.
            for group in list(current):
                if not isinstance(group, dict) or not isinstance(installed_group, dict):
                    continue
                if {k: v for k, v in group.items() if k != "hooks"} != {
                        k: v for k, v in installed_group.items() if k != "hooks"}:
                    continue
                handlers = group.get("hooks")
                if not isinstance(handlers, list):
                    continue
                for handler in installed_group.get("hooks", []):
                    if handler in handlers:
                        handlers.remove(handler)
                if not handlers:
                    current.remove(group)
        if not current:
            hooks.pop(event, None)
    if not hooks:
        settings.pop("hooks", None)


def _recorded_handlers(recorded: dict[str, Any]) -> list[dict[str, Any]]:
    handlers: list[dict[str, Any]] = []
    for groups in recorded.values():
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                continue
            handlers.extend(
                handler for handler in group["hooks"] if isinstance(handler, dict)
            )
    return handlers


def _is_boring_handler(handler: dict[str, Any]) -> bool:
    command = str(handler.get("command", ""))
    args = handler.get("args", [])
    if not isinstance(args, list):
        args = []
    rendered = " ".join([command, *(str(item) for item in args)])
    return "--claude-status-hook" in rendered or (
        "claude_hook.py" in rendered and "--endpoint" in rendered
    )


def _foreign_boring_handlers(
    settings: dict[str, Any], recorded: dict[str, Any]
) -> bool:
    owned = _recorded_handlers(recorded)
    for groups in _hook_groups(settings).values():
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                continue
            for handler in group["hooks"]:
                if (
                    isinstance(handler, dict)
                    and _is_boring_handler(handler)
                    and handler not in owned
                ):
                    return True
    return False


class ClaudeHookInstallation:
    """No writes during construction or inspection; install/uninstall are explicit."""

    def __init__(self, settings_path: Path | str | None = None,
                 endpoint_path: Path | str | None = None, *,
                 owner_id: str | None = None,
                 origin: str | None = None,
                 official_build_probe: Callable[
                     [], tuple[tuple[str | None, str | None], ...]
                 ] | None = None) -> None:
        config_dir = Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude")))
        self.settings_path = (Path(settings_path) if settings_path is not None else config_dir / "settings.json").expanduser().absolute()
        self.endpoint_path = (Path(endpoint_path) if endpoint_path is not None else default_endpoint_path()).expanduser().absolute()
        self.record_path = self.endpoint_path.parent / "installation.json"

        self.owner_id = owner_id or _DEFAULT_OWNER_ID
        self.origin = origin or _DEFAULT_ORIGIN
        self._official_build_probe = (
            official_build_probe or _installed_official_builds
        )
        if self.origin not in {"official", "custom"}:
            raise ValueError("BORING Hooks 构建来源无效")

    def _record_payload(self, hooks: dict[str, Any]) -> dict[str, Any]:
        return {
            "settings_path": str(self.settings_path),
            "owner": self.owner_id,
            "origin": self.origin,
            "endpoint_path": str(self.endpoint_path),
            "hooks": hooks,
        }

    def _recorded(self) -> dict[str, Any]:
        record = _read_object(self.record_path)
        accepted_legacy_official = False
        if record and record.get("settings_path") != str(self.settings_path):
            raise ValueError("BORING Hooks 的安装记录属于另一份 Claude 配置；请先从原环境卸载。")
        if record and not record.get("owner"):
            legacy_official_endpoint = (
                Path.home() / ".boring" / "claude-status" / "endpoint.json"
            ).absolute()
            is_legacy_official_owner = (
                self.origin == "official"
                and self.owner_id == "com.boring.controller-config"
                and self.endpoint_path == legacy_official_endpoint
            )
            if not is_legacy_official_owner:
                raise ValueError(
                    "发现旧版 BORING Hooks。请先使用安装它的原版控制台卸载，"
                    "并将原版升级后再切换。"
                )
            accepted_legacy_official = True
        if record and not accepted_legacy_official and (
            record.get("owner") != self.owner_id
            or record.get("origin") != self.origin
            or record.get("endpoint_path") != str(self.endpoint_path)
        ):
            raise ValueError(
                "BORING Hooks 属于另一版本的控制台；请先在原版中卸载。"
            )
        hooks = record.get("hooks", {})
        if not isinstance(hooks, dict):
            raise ValueError("BORING Hooks 安装记录无法读取；未修改 Claude 设置。")
        return hooks

    def inspect(self) -> ClaudeHookInspection:
        executable, version, problem = _detect_claude()
        host_platform = _host_platform()
        events = BASE_EVENTS
        if version and _version_tuple(version) >= STOP_FAILURE_VERSION:
            events += ("StopFailure",)
        if version and _version_tuple(version) < MINIMUM_VERSION:
            problem = "需要 Claude Code 2.1.63 或更新版本；当前版本未安装 Hooks。"
        installed = False
        try:
            settings = _read_object(self.settings_path)
            hooks = _hook_groups(settings)
            recorded = self._recorded()
            if (
                self.origin == "custom"
                and _requires_owner_aware_official_upgrade(
                    self._official_build_probe()
                )
            ):
                raise ValueError(
                    "检测到不支持 Hooks 归属切换的旧官方版。请先升级官方版，"
                    "再在 DIY 版中启用 Claude Hooks。"
                )
            if _foreign_boring_handlers(settings, recorded):
                raise ValueError(
                    "另一版本或旧版 BORING 控制台已安装 Claude Hooks；"
                    "请先在原版中卸载。"
                )
            installed = bool(recorded) and all(
                any(isinstance(group, dict) and all(handler in group.get("hooks", [])
                    for handler in item.get("hooks", [])) for group in hooks.get(event, []))
                for event, items in recorded.items() for item in items
            )
            if settings.get("disableAllHooks") is True or settings.get("allowManagedHooksOnly") is True:
                problem = "Claude Code 设置禁用了用户 Hooks；BORING 不会修改此策略。"
        except (OSError, ValueError, TypeError) as error:
            problem = str(error)
        notes = ["开发中，待真实 Hook → USB → 灯光/震动联合验收。"]
        if version and _version_tuple(version) < STOP_FAILURE_VERSION:
            notes.append("此版本不支持 StopFailure；不会把普通工具失败误报为终止错误。")
        if host_platform == "windows-native":
            notes.append("Windows 原生链路未验证；旧版 Claude 需 Git Bash，不能改用 WSL 代跑。")
        elif host_platform == "wsl":
            notes.append("WSL 链路未验证；仅连接本 WSL 的本地接收端，不转发到 Windows 控制台。")
        elif host_platform == "linux":
            notes.append("Linux 链路未验证。")
        return ClaudeHookInspection(executable, version, host_platform, installed, events,
                                    problem, "\n".join(notes))

    def install(self) -> ClaudeHookInspection:
        inspection = self.inspect()
        if inspection.problem or not inspection.version:
            raise ValueError(inspection.problem or "无法确认 Claude Code 版本。")
        settings = _read_object(self.settings_path)
        previous = self._recorded()
        _remove_recorded(settings, previous)
        hooks = settings.setdefault("hooks", {})
        handler = _handler(self.endpoint_path, inspection.version, inspection.platform)
        recorded: dict[str, Any] = {}
        for event in inspection.events:
            if event in hooks and not isinstance(hooks[event], list):
                raise ValueError(f"Claude Code {event} Hooks 格式不正确；未修改原设置。")
            group = {"hooks": [deepcopy(handler)]}
            if event not in {"UserPromptSubmit", "Stop", "SubagentStop"}:
                group["matcher"] = ""
            hooks.setdefault(event, []).append(group)
            recorded[event] = [group]
        # Record exact owned entries first, so an interrupted install can still
        # uninstall either the previous or the new command without guessing.
        owned = deepcopy(previous)
        for event, groups in recorded.items():
            for group in groups:
                if group not in owned.setdefault(event, []):
                    owned[event].append(group)
        _write_object(self.record_path, self._record_payload(owned))
        _write_object(self.settings_path, settings)
        _write_object(self.record_path, self._record_payload(recorded))
        return self.inspect()

    def uninstall(self) -> ClaudeHookInspection:
        recorded = self._recorded()
        if recorded:
            settings = _read_object(self.settings_path)
            _remove_recorded(settings, recorded)
            _write_object(self.settings_path, settings)
            _write_object(self.record_path, self._record_payload({}))
        return self.inspect()
