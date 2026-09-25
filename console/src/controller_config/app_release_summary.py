"""Short, customer-facing highlights bundled with each Console release."""
from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
import json
from pathlib import Path

from controller_config import __version__


_LANGUAGES = ("zh_CN", "en_US", "ja_JP")


@dataclass(frozen=True)
class AppReleaseSummary:
    version: str
    from_version: str
    highlights: tuple[str, ...]


def load_app_release_summary(
    language: str,
    path: Path | None = None,
    *,
    expected_version: str = __version__,
) -> AppReleaseSummary:
    """Load the concise highlights for the installed application version."""

    resource = (
        path
        if path is not None
        else files("controller_config.assets").joinpath("app-release-summary.json")
    )
    payload = json.loads(resource.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("app release summary must be an object")
    version = payload.get("version")
    if version != expected_version:
        raise ValueError(
            f"app release summary version {version!r} does not match {expected_version!r}"
        )
    from_version = payload.get("from_version")
    if not isinstance(from_version, str) or not from_version.strip():
        raise ValueError("app release summary from_version must be a non-empty string")

    localized = payload.get("highlights")
    if not isinstance(localized, dict):
        raise ValueError("app release summary highlights must be an object")
    for key in _LANGUAGES:
        values = localized.get(key)
        if not isinstance(values, list) or not 1 <= len(values) <= 4:
            raise ValueError(f"{key} must contain 1 to 4 release highlights")
        if any(
            not isinstance(value, str)
            or value != value.strip()
            or not value
            or "\n" in value
            or len(value) > 160
            for value in values
        ):
            raise ValueError(f"{key} release highlights must be concise single lines")

    selected = language if language in _LANGUAGES else "en_US"
    return AppReleaseSummary(
        version=version,
        from_version=from_version,
        highlights=tuple(localized[selected]),
    )
