"""Path of the program to start again (Hooks, LaunchAgent, helper processes).

A Nuitka standalone build sets ``sys.executable`` to "<program dir>/python"
(``getStandaloneSysExecutablePath`` in Nuitka's CompiledCodeHelpers.c) so that
``sys.executable -m …`` style code keeps a plausible value, but that file does
not exist: the real program is ``Contents/MacOS/console_entry`` on macOS and
``BORING Console.exe`` on Windows. Nuitka documents ``sys.argv[0]`` as the
program and ``__compiled__.containing_dir`` as its directory; together they
name the binary even when it was started with a relative path.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def is_packaged() -> bool:
    return bool(getattr(sys, "frozen", False) or "__compiled__" in globals())


def program_executable(*, compiled=None, argv0: str | None = None,
                       executable: str | None = None) -> str:
    """Return an existing program path; fall back to ``sys.executable``."""

    compiled = compiled if compiled is not None else globals().get("__compiled__")
    argv0 = sys.argv[0] if argv0 is None and sys.argv else (argv0 or "")
    executable = executable if executable is not None else sys.executable
    candidates: list[Path] = []
    if argv0:
        if compiled is not None and getattr(compiled, "containing_dir", None):
            candidates.append(Path(compiled.containing_dir) / Path(argv0).name)
        candidates.append(Path(os.path.abspath(argv0)))
    candidates.append(Path(executable))
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return executable
