import pytest
"""The packaged app keeps only the QML modules the Console imports."""
import re
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "tools"))

import prune_staged_qml  # noqa: E402


def _make(root: Path, *relative: str) -> None:
    for name in relative:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")


def test_prune_keeps_quick3d_and_removes_webengine_pdf_and_controls(tmp_path):
    qml = tmp_path / "qml"
    _make(qml, "builtins.qmltypes", "QtQml/qmldir", "QtQml/Models/qmldir",
          "QtQml/WorkerScript/qmldir", "QtQml/StateMachine/qmldir",
          "QtQuick/qmldir", "QtQuick/libqtquick2plugin.dylib", "QtQuick/Window/qmldir",
          "QtQuick/Controls/Basic/qmldir", "QtQuick/Pdf/qmldir",
          "QtQuick/VirtualKeyboard/qmldir", "QtQuick3D/qmldir",
          "QtQuick3D/Effects/HDRBloomTonemap.qml", "QtQuick3D/Effects/designer/x.metainfo",
          "QtQuick3D/Particles3D/qmldir", "QtWebEngine/qmldir", "QtCharts/qmldir")

    removed = prune_staged_qml.prune(qml)

    assert {"QtWebEngine", "QtCharts", "QtQuick/Pdf", "QtQuick/Controls",
            "QtQuick/VirtualKeyboard", "QtQuick3D/Particles3D",
            "QtQuick3D/Effects/designer", "QtQml/StateMachine"} == set(removed)
    for kept in ("builtins.qmltypes", "QtQuick/libqtquick2plugin.dylib", "QtQuick/Window/qmldir",
                 "QtQuick3D/Effects/HDRBloomTonemap.qml", "QtQml/Models/qmldir"):
        assert (qml / kept).exists()


def test_prune_refuses_a_qt_without_the_modules_the_console_needs(tmp_path):
    qml = tmp_path / "qml"
    _make(qml, "QtQuick/qmldir")
    try:
        prune_staged_qml.prune(qml)
    except SystemExit as stop:
        assert "QtQuick3D" in str(stop)
    else:
        raise AssertionError("missing Quick3D must stop the build")


@pytest.mark.skip(reason="社区版不含 3D 设备模型的 QML")
def test_every_qml_import_in_the_app_is_kept():
    imports = set()
    for path in (PROJECT / "src").rglob("*.qml"):
        for line in path.read_text(encoding="utf-8").splitlines():
            match = re.match(r"\s*import\s+([A-Za-z][\w.]*)", line)
            if match:
                imports.add(match.group(1))
    assert imports, "expected the 3D view's QML files"
    missing = {name for name in imports if name.replace(".", "/") not in prune_staged_qml.KEEP_DIRS}
    assert not missing, f"QML imports pruned from the package: {missing}"


def test_build_prunes_only_the_staging_copy_before_nuitka():
    script = (PROJECT / "deploy/build_macos.sh").read_text(encoding="utf-8")
    prune = script.index("tools/prune_staged_qml.py")
    assert script.index('cp -cR "$PROJECT_DIR/.venv" "$STAGE_PROJECT/.venv"') < prune
    assert prune < script.index("-m nuitka")
    assert '"$STAGE_PROJECT"/.venv/*) ;;' in script
