from __future__ import annotations

import os
import json
from pathlib import Path
import sys


AUTHORITY_FILENAME = "authority.json"
BUNDLED_AUTHORITY_FILENAME = "bundled-authority.json"


def resource_root() -> Path:
    """Return the source tree or PyInstaller resource directory."""
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        return Path(bundled).resolve()
    return Path(__file__).resolve().parents[1]


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def workspace_root() -> Path:
    """Return the main checkout when this tool runs from its worktree."""
    root = project_root()
    if root.parent.name == ".worktrees":
        return root.parent.parent.resolve()
    return root


def data_root() -> Path:
    override = os.environ.get("CODEX_MICRO_PROVISIONER_DATA")
    if override:
        return Path(override).expanduser().resolve()
    if sys_platform() == "darwin":
        return Path.home() / "Library" / "Application Support" / "BORING MIST Recovery"
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home()))
        return base / "BORING MIST Recovery"
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / (
        "codex-micro-firmware-provisioner"
    )


def history_root() -> Path:
    return data_root() / "history"


def factory_test_history_root() -> Path:
    return data_root() / "factory-tests"


def factory_identity_history_root() -> Path:
    return data_root() / "factory-identity-dry-runs"


def factory_tools_root() -> Path:
    override = os.environ.get("BORING_FACTORY_TOOLS_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    bundled = resource_root() / "factory-tools"
    if bundled.is_dir():
        return bundled.resolve()
    return workspace_root() / "software" / "firmware" / "tools"


def device_auth_fixture_path() -> Path:
    override = os.environ.get("BORING_DEVICE_AUTH_FIXTURE")
    if override:
        return Path(override).expanduser().resolve()
    bundled = resource_root() / "protocol" / "fixtures" / "device-auth-v1.json"
    if bundled.is_file():
        return bundled.resolve()
    return workspace_root() / "software" / "protocol" / "fixtures" / "device-auth-v1.json"


def managed_release_root() -> Path:
    return data_root() / "releases"


def authority_record_path() -> Path:
    return data_root() / AUTHORITY_FILENAME


def sys_platform() -> str:
    return sys.platform


def _read_authority_pointer(pointer_path: Path) -> Path | None:
    try:
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(pointer, dict):
        return None

    managed_file = pointer.get("managed_file")
    if isinstance(managed_file, str) and Path(managed_file).name == managed_file:
        candidate = (managed_release_root() / managed_file).resolve()
        if candidate.is_file():
            return candidate

    absolute_path = pointer.get("path")
    if isinstance(absolute_path, str) and absolute_path:
        candidate = Path(absolute_path).expanduser().resolve()
        if candidate.exists():
            return candidate
    return None


def _bundled_authority_path() -> Path | None:
    pointer_path = resource_root() / BUNDLED_AUTHORITY_FILENAME
    try:
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        relative = pointer.get("relative_path")
    except (OSError, json.JSONDecodeError, AttributeError):
        return None
    if not isinstance(relative, str) or not relative:
        return None
    relative_path = Path(relative)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        return None
    root = resource_root().resolve()
    candidate = (root / relative_path).resolve()
    if candidate.exists():
        return candidate
    return None


def discover_source_authority_path() -> Path | None:
    """Resolve the source checkout's single current-device package."""
    root = project_root()
    project_roots = [root]
    if root.parent.name == ".worktrees":
        project_roots.append(root.parent.parent)
    for candidate_root in project_roots:
        current_device = candidate_root / "software" / "firmware" / "current-device.json"
        if current_device.is_file():
            try:
                pointer = json.loads(current_device.read_text(encoding="utf-8"))
                package = pointer.get("package")
                if isinstance(package, str) and package:
                    firmware_root = current_device.parent.resolve()
                    candidate = (firmware_root / package).resolve()
                    package_roots = (
                        (firmware_root / "artifacts").resolve(),
                        (firmware_root / "releases").resolve(),
                    )
                    if (
                        any(candidate.is_relative_to(root) for root in package_roots)
                        and (candidate / "firmware-manifest.json").is_file()
                    ):
                        return candidate
            except (OSError, json.JSONDecodeError):
                pass
    return None


def discover_current_device_paths() -> set[Path]:
    selected = _read_authority_pointer(authority_record_path())
    if selected is not None:
        return {selected}

    source = discover_source_authority_path()
    if source is not None:
        return {source}
    bundled = _bundled_authority_path()
    return {bundled} if bundled is not None else set()


def discover_production_authority_path() -> Path | None:
    """Return the source current-device package, or its bundled App copy."""
    source = discover_source_authority_path()
    return source if source is not None else _bundled_authority_path()


def discover_release_paths() -> list[Path]:
    root = project_root()
    locations = (
        resource_root() / "releases",
        root / "software" / "firmware" / "releases",
        managed_release_root(),
    )
    found = discover_current_device_paths()
    production_authority = discover_production_authority_path()
    if production_authority is not None:
        found.add(production_authority)
    for location in locations:
        if not location.is_dir():
            continue
        if (location / "release-manifest.json").is_file():
            found.add(location.resolve())
        for manifest in location.glob("*/release-manifest.json"):
            found.add(manifest.parent.resolve())
        for archive in location.glob("*.zip"):
            found.add(archive.resolve())
    return sorted(found, key=lambda path: str(path))


def write_authority_pointer(path: Path, release_id: str) -> None:
    resolved = path.expanduser().resolve()
    managed_root = managed_release_root().resolve()
    if resolved.parent == managed_root and resolved.is_file():
        payload = {"release_id": release_id, "managed_file": resolved.name}
    else:
        payload = {"release_id": release_id, "path": str(resolved)}

    destination = authority_record_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, destination)
