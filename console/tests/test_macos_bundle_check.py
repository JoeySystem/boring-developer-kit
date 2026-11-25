"""Mac packaging refuses bundles whose Qt libraries cannot load (C5, C6).

The 11-16 package from the designer's Mac kept 121 Qt plugins pointing at
``@rpath/Qt*.framework`` and shipped 20 of the Qt libraries; the script still
signed it. See task.
"""
from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import check_macos_bundle  # noqa: E402


def test_bundle_check_rejects_unrewritten_qt_plugins_and_missing_libraries(tmp_path):
    macos = tmp_path / "Contents/MacOS"
    plugin = macos / "PySide6/qt-plugins/platforms/libqcocoa.dylib"
    plugin.parent.mkdir(parents=True)
    plugin.write_bytes(b"")
    (macos / "QtCore").write_bytes(b"")
    deps = {
        plugin: ["@rpath/QtGui.framework/Versions/A/QtGui", "@executable_path/QtCore",
                 "@executable_path/QtWidgets", "/usr/lib/libSystem.B.dylib"],
    }
    found = check_macos_bundle.problems(macos, min_qt=2, dependencies=deps.__getitem__,
                                        files=[plugin])
    assert any("only 1 Qt libraries" in line for line in found)
    assert any("unresolved @rpath/QtGui.framework" in line for line in found)
    assert any("missing @executable_path/QtWidgets" in line for line in found)
    assert not any("QtCore" in line and "missing" in line for line in found)

    (macos / "QtGui").write_bytes(b"")
    (macos / "QtWidgets").write_bytes(b"")
    deps[plugin] = ["@executable_path/QtGui", "@executable_path/QtCore", "/usr/lib/libSystem.B.dylib"]
    assert check_macos_bundle.problems(macos, min_qt=2, dependencies=deps.__getitem__,
                                       files=[plugin]) == []


def test_build_script_stages_on_resolved_path_and_checks_before_signing():
    script = (Path(__file__).resolve().parents[1] / "deploy/build_macos.sh").read_text(encoding="utf-8")
    assert 'pwd -P' in script and '"$STAGE_BASE/boring-console-build.XXXXXX"' in script
    check = script.index("tools/check_macos_bundle.py")
    assert check < script.index("/usr/bin/codesign") and check < script.index("hdiutil create")
