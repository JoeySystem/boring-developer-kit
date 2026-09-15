from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from controller_config.protocol.contract import Contract
from controller_config.qt_bootstrap import prepare_qt_platform_plugins


prepare_qt_platform_plugins()


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
