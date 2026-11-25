"""Refuse a macOS app bundle whose Qt libraries cannot load.

Run by deploy/build_macos.sh before signing (task C6). A bundle
built on 2026-11-16 shipped Qt plugins that still pointed at
``@rpath/Qt*.framework`` and only 20 of the Qt libraries, so the app aborted
before showing a window; the old script signed and packaged it anyway.

Checks every Mach-O file below ``Contents/MacOS``:
* no dependency may remain ``@rpath/Qt…`` (Nuitka rewrites them to
  ``@executable_path/Qt…``);
* every ``@executable_path/…`` and ``@loader_path/…`` dependency must exist;
* other absolute dependencies must be system libraries;
and that at least ``--min-qt`` Qt libraries were bundled.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Callable, Iterable

# Present in the Qt wheel but never loaded at run time; Nuitka deliberately
# skips it ("forbidden DLL"), see the build log.
IGNORED = ("qml/QtQuick/Shapes/DesignHelpers/",)
SYSTEM_PREFIXES = ("/System/Library/", "/usr/lib/")
MACHO_MAGIC = {b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"}


def _is_macho(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(4) in MACHO_MAGIC
    except OSError:
        return False


def _otool(path: Path) -> list[str]:
    output = subprocess.run(["otool", "-L", str(path)], check=True,
                            capture_output=True, text=True).stdout
    # First line names the file itself; each dependency line is
    # "\t<path> (compatibility version …)". A library also lists its own
    # install name there (otool -D); that is an identity, not a dependency.
    own = subprocess.run(["otool", "-D", str(path)], check=True,
                         capture_output=True, text=True).stdout.splitlines()[1:]
    return [entry for entry in (line.strip().split(" (", 1)[0]
                                for line in output.splitlines()[1:] if line.strip())
            if entry not in {name.strip() for name in own}]


def problems(macos_dir: Path, *, min_qt: int,
             dependencies: Callable[[Path], list[str]] = _otool,
             files: Iterable[Path] | None = None) -> list[str]:
    found: list[str] = []
    qt_count = sum(1 for entry in macos_dir.iterdir() if entry.name.startswith("Qt"))
    if qt_count < min_qt:
        found.append(f"only {qt_count} Qt libraries in {macos_dir} (expected at least {min_qt})")
    candidates = files if files is not None else (
        path for path in macos_dir.rglob("*")
        if path.is_file() and not path.is_symlink() and _is_macho(path))
    for path in candidates:
        relative = path.relative_to(macos_dir).as_posix()
        if any(part in relative for part in IGNORED):
            continue
        for dependency in dependencies(path):
            if dependency.startswith("@rpath/Qt"):
                found.append(f"{relative}: unresolved {dependency}")
            elif dependency.startswith("@executable_path/"):
                if not (macos_dir / dependency[len("@executable_path/"):]).exists():
                    found.append(f"{relative}: missing {dependency}")
            elif dependency.startswith("@loader_path/"):
                if not (path.parent / dependency[len("@loader_path/"):]).exists():
                    found.append(f"{relative}: missing {dependency}")
            elif dependency.startswith("/") and not dependency.startswith(SYSTEM_PREFIXES):
                found.append(f"{relative}: non-system absolute dependency {dependency}")
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path, help="path to the .app bundle")
    # 0.2.1 bundle: 119 Qt libraries, most pulled in by unused QML modules.
    # 0.2.10 after the QML allowlist and QtPdf exclusion: 21. The
    # broken 11-16 bundle had 20; the dependency checks above are what proves
    # loadability, this count only catches a near-empty Qt set.
    parser.add_argument("--min-qt", type=int, default=20)
    args = parser.parse_args(argv)
    macos_dir = args.app / "Contents/MacOS"
    if not macos_dir.is_dir():
        print(f"Not an app bundle: {args.app}", file=sys.stderr)
        return 2
    found = problems(macos_dir, min_qt=args.min_qt)
    if found:
        print(f"macOS bundle check failed ({len(found)} problems):", file=sys.stderr)
        for line in found[:40]:
            print(f"  {line}", file=sys.stderr)
        return 1
    print("macOS bundle check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
