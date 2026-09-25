"""Runtime policy derived from the build origin bundled with the application."""
from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
import json
from pathlib import Path


@dataclass(frozen=True)
class BuildIdentity:
    """The declared source of this application build.

    ``None`` is fail-closed: a missing or malformed resource must never gain the
    official application's replacement capability.
    """

    origin: str | None
    issue: str = ""

    @property
    def allows_official_updates(self) -> bool:
        return self.origin == "official"

    @property
    def update_state(self) -> str:
        return "custom" if self.origin == "custom" else "unavailable"

    @property
    def update_message(self) -> str:
        if self.origin == "custom":
            return "自定义版本不使用官方软件自动更新。"
        return "此构建缺少有效的来源信息，已停用官方软件更新。"

    @property
    def runtime_origin(self) -> str:
        """Return the runtime identity; unknown builds stay outside official data."""

        return "official" if self.origin == "official" else "custom"

    @property
    def application_name(self) -> str:
        if self.runtime_origin == "official":
            return "BORING Controller Config"
        return "BORING Console Community"

    @property
    def application_display_name(self) -> str:
        if self.runtime_origin == "official":
            return "BORING 控制台"
        return "BORING Console Community"

    @property
    def launch_agent_label(self) -> str:
        if self.runtime_origin == "official":
            return "com.boring.controller-config.prompt-helper"
        return "com.boring.controller-config.community.prompt-helper"

    @property
    def other_launch_agent_label(self) -> str:
        if self.runtime_origin == "official":
            return "com.boring.controller-config.community.prompt-helper"
        return "com.boring.controller-config.prompt-helper"

    @property
    def claude_owner_id(self) -> str:
        if self.runtime_origin == "official":
            return "com.boring.controller-config"
        return "com.boring.controller-config.community"

    @property
    def claude_endpoint_path(self) -> Path:
        directory = (
            "claude-status"
            if self.runtime_origin == "official"
            else "community/claude-status"
        )
        return Path.home() / ".boring" / directory / "endpoint.json"


def load_build_identity(path: Path | None = None) -> BuildIdentity:
    """Load the immutable build origin bundled by the packaging process."""

    resource = (
        path
        if path is not None
        else files("controller_config.assets").joinpath("app-build.json")
    )
    try:
        payload = json.loads(resource.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        return BuildIdentity(origin=None, issue=str(exc))
    if not isinstance(payload, dict) or payload.get("origin") not in {
        "official",
        "custom",
    }:
        return BuildIdentity(origin=None, issue="origin must be official or custom")
    return BuildIdentity(origin=payload["origin"])
