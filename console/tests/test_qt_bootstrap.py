from types import SimpleNamespace

from controller_config import qt_bootstrap


def test_non_ascii_source_copies_image_plugins_and_preserves_platform_override(tmp_path, monkeypatch):
    package = tmp_path / "中文" / "PySide6"
    for category in ("platforms", "imageformats", "tls"):
        source = package / "Qt" / "plugins" / category
        source.mkdir(parents=True)
        (source / "plugin.dylib").write_bytes(b"test")
    monkeypatch.setattr(qt_bootstrap.sys, "platform", "darwin")
    monkeypatch.setattr(qt_bootstrap, "_platform_plugin_temp", None)
    monkeypatch.setattr(qt_bootstrap.importlib.util, "find_spec", lambda name:
                        SimpleNamespace(submodule_search_locations=[str(package)]))
    monkeypatch.setenv("QT_QPA_PLATFORM_PLUGIN_PATH", "/existing/platforms")
    monkeypatch.setenv("QT_PLUGIN_PATH", "/existing/plugins")
    qt_bootstrap.prepare_qt_platform_plugins()
    temporary = qt_bootstrap._platform_plugin_temp
    try:
        copied = qt_bootstrap.Path(temporary.name)
        assert (copied / "imageformats" / "plugin.dylib").read_bytes() == b"test"
        assert (copied / "tls" / "plugin.dylib").read_bytes() == b"test"
        assert qt_bootstrap.os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] == "/existing/platforms"
        expected = str(copied) + qt_bootstrap.os.pathsep + "/existing/plugins"
        assert qt_bootstrap.os.environ["QT_PLUGIN_PATH"] == expected
        qt_bootstrap.prepare_qt_platform_plugins()
        assert qt_bootstrap.os.environ["QT_PLUGIN_PATH"] == expected
    finally:
        temporary.cleanup()
