from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from controller_config.protocol.contract import Contract
from controller_config.qt_bootstrap import prepare_qt_platform_plugins


prepare_qt_platform_plugins()


@pytest.fixture(autouse=True)
def isolated_console_settings(tmp_path):
    """Language and setup choices must not leak between tests or into user prefs."""
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    from controller_config.i18n import LanguageManager, SIMPLIFIED_CHINESE

    previous_format = QSettings.defaultFormat()
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path / "settings"))
    application = QApplication.instance()
    language = (LanguageManager(application, initial_language=SIMPLIFIED_CHINESE, persist=False)
                if application is not None else None)
    yield
    QSettings.setDefaultFormat(previous_format)


@pytest.fixture(scope="session")
def protocol_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "protocol"


@pytest.fixture(scope="session")
def contract(protocol_dir: Path) -> Contract:
    return Contract.load(protocol_dir)


@pytest.fixture(scope="session")
def load_fixture(protocol_dir: Path):
    def load(name: str) -> dict[str, Any]:
        with (protocol_dir / "fixtures" / name).open("r", encoding="utf-8") as handle:
            value = json.load(handle)
        assert isinstance(value, dict)
        return value

    return load


@pytest.fixture(autouse=True)
def isolated_firmware_release_history(monkeypatch, tmp_path):
    # Newly remembered test releases must never enter the user's application settings.
    from PySide6.QtCore import QSettings
    import controller_config.firmware_origin as origin
    monkeypatch.setattr(origin, 'QSettings', lambda *_: QSettings(
        str(tmp_path / 'firmware-history.ini'), QSettings.IniFormat))


@pytest.fixture(autouse=True)
def notice_log(monkeypatch):
    """Messages in the dialog shell never block a test: each one is recorded
    (title and text) and closed as if the user pressed Esc. A test that needs
    an answer replaces notice.ask / notice.choose itself."""
    from controller_config.views.ui import notice

    log: list[tuple[str, str]] = []

    def record(dialog):
        texts = [label.text() for label in dialog.findChildren(type(dialog.title_label))
                 if label.objectName() == "boringNoticeText"]
        log.append((dialog.title_label.text(), texts[0] if texts else ""))
        return 0

    monkeypatch.setattr(notice, "_run", record)
    return log
