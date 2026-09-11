from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import tempfile
from pathlib import Path


_platform_plugin_temp: tempfile.TemporaryDirectory[str] | None = None


def prepare_qt_platform_plugins() -> None:
    """Keep Qt launchable when the macOS source path contains non-ASCII text."""
    global _platform_plugin_temp
    if sys.platform != "darwin" or _platform_plugin_temp is not None:
        return
    spec = importlib.util.find_spec("PySide6")
    if spec is None or not spec.submodule_search_locations:
        return
    package_dir = Path(next(iter(spec.submodule_search_locations)))
    source = package_dir / "Qt" / "plugins"
    if str(source).isascii() or not source.is_dir():
        return

    _platform_plugin_temp = tempfile.TemporaryDirectory(prefix="boring-qt-platforms-")
    destination = Path(_platform_plugin_temp.name)
    for category in ("platforms", "imageformats", "tls"):
        if (source / category).is_dir():
            shutil.copytree(source / category, destination / category, copy_function=shutil.copy)
    os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH", str(destination / "platforms"))
    existing = os.environ.get("QT_PLUGIN_PATH")
    os.environ["QT_PLUGIN_PATH"] = str(destination) + (os.pathsep + existing if existing else "")
