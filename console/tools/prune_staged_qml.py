"""Keep only the QML modules the Console imports in the staged PySide6 copy.

Run by deploy/build_macos.sh on the disposable staging venv, never on the
workspace. Nuitka's PySide6 plugin collects the whole ``PySide6/Qt/qml`` tree
once QtQuick is used, then follows every QML plugin's library dependencies.
That pulled QtWebEngineCore (216 MB), QtPdf, QtCharts, Qt3D, VirtualKeyboard
and every Quick Controls style into the 0.2.x bundles although nothing loads
them (docs/macos-package-size-optimization.md). Excluding a plugin with
``--noinclude-dlls`` does not help: Nuitka still scans the excluded plugin's
dependencies. Removing the unused modules before Nuitka runs does.

The allowlist follows the imports in ``assets/boring_mist_3d/*.qml``
(QtQuick, QtQuick3D, QtQuick3D.Effects) plus what their ``qmldir`` files pull
in (QtQml with Models and WorkerScript, QtQuick.Window). A test checks that
every QML import in ``src/`` is covered, so a new import fails before packaging.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

# Relative directories below PySide6/Qt/qml. Files directly inside a kept
# directory stay; any subdirectory not listed here is removed.
KEEP_DIRS = frozenset({
    "",
    "QtQml",
    "QtQml/Models",
    "QtQml/WorkerScript",
    "QtQuick",
    "QtQuick/Window",
    "QtQuick3D",
    "QtQuick3D/Effects",
})


def prune(qml_dir: Path) -> list[str]:
    """Remove every directory below ``qml_dir`` that is not kept; return them."""
    removed: list[str] = []
    pending = [qml_dir]
    while pending:
        directory = pending.pop()
        for child in sorted(directory.iterdir()):
            if not child.is_dir() or child.is_symlink():
                continue
            relative = child.relative_to(qml_dir).as_posix()
            if relative in KEEP_DIRS:
                pending.append(child)
            else:
                shutil.rmtree(child)
                removed.append(relative)
    missing = sorted(name for name in KEEP_DIRS if not (qml_dir / name).is_dir())
    if missing:
        raise SystemExit(f"QML modules the Console needs are missing in {qml_dir}: {missing}")
    return removed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("qml_dir", type=Path, help="PySide6/Qt/qml of the staging venv")
    args = parser.parse_args(argv)
    if not args.qml_dir.is_dir():
        print(f"Not a QML directory: {args.qml_dir}", file=sys.stderr)
        return 2
    removed = prune(args.qml_dir)
    print(f"Removed {len(removed)} unused QML module directories from {args.qml_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
