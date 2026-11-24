"""Runtime policy derived from the build origin bundled with the application."""
from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
import json
from pathlib import Path
import re

# Internal development packages (build switch BORING_CONSOLE_DEV_BUILD): a
# label naming what is under test and the build date, e.g. 豆包联动 · 20261106.
DEV_LABEL_MAX = 32
_DEV_DATE = re.compile(r"\d{8}")


@dataclass(frozen=True)
class BuildIdentity:
    """The declared source of this application build.

    ``None`` is fail-closed: a missing or malformed resource must never gain the
    official application's replacement capability.
    """

    origin: str | None
    issue: str = ""
    # Non-empty only for internal development packages: never updated in-app,
    # so an official update cannot replace the feature under test.
    dev_label: str = ""
    dev_date: str = ""

    @property
    def is_dev_build(self) -> bool:
        return bool(self.dev_label)

    @property
    def allows_official_updates(self) -> bool:
        return self.origin == "official" and not self.is_dev_build

    @property
    def update_state(self) -> str:
        if self.is_dev_build:
            return "dev"
        return "custom" if self.origin == "custom" else "unavailable"

    @property
    def update_message(self) -> str:
        if self.is_dev_build:
            return "开发版不检查、不下载软件更新。"
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
        name = "BORING 控制台" if self.runtime_origin == "official" else "BORING Console Community"
        return f"{name} 开发版" if self.is_dev_build else name

    def display_version(self, version: str) -> str:
        """Version as shown in Settings; development builds are unmistakable."""
        if not self.is_dev_build:
            return version
        text = f"{version} 开发版（{self.dev_label}）"
        return f"{text}· {self.dev_date}" if self.dev_date else text

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
    dev = payload.get("dev")
    if dev is None:
        return BuildIdentity(origin=payload["origin"])
    issue = dev_build_issue(dev)
    if issue:
        return BuildIdentity(origin=None, issue=issue)
    return BuildIdentity(origin=payload["origin"], dev_label=dev["label"],
                         dev_date=dev.get("date", ""))


def dev_build_issue(dev) -> str:
    """Why a development-build marker is malformed ("" when it is valid)."""

    if not isinstance(dev, dict) or set(dev) - {"label", "date"}:
        return "dev must be an object with label and date"
    label, date = dev.get("label"), dev.get("date", "")
    if not isinstance(label, str) or not label.strip() or len(label) > DEV_LABEL_MAX or label != label.strip():
        return f"dev label must be 1–{DEV_LABEL_MAX} characters"
    if not isinstance(date, str) or (date and not _DEV_DATE.fullmatch(date)):
        return "dev date must be YYYYMMDD"
    return ""
