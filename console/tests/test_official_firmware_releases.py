"""The bundled official release list verifies with the real Console public key."""
from dataclasses import replace
from importlib.resources import files
import json

import pytest
from PySide6.QtCore import QSettings

from controller_config.firmware_origin import FirmwareReleaseHistory
from controller_config.firmware_signature import verify_manifest_signature
from controller_config.transport.demo import _power_v2_snapshot

pytestmark = pytest.mark.skip(reason="社区版内置官方发布记录为空")


def _bundled():
    return json.loads(files("controller_config.assets").joinpath(
        "official-firmware-releases.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("manifest", _bundled(), ids=lambda m: m["build_id"])
def test_every_bundled_release_signature_verifies(manifest):
    # FirmwareReleaseHistory silently skips unverifiable entries; fail loudly here.
    verify_manifest_signature(manifest)


def test_bundled_20261030_01_is_official_without_cached_history(contract, tmp_path):
    settings = QSettings(str(tmp_path / "empty.ini"), QSettings.IniFormat)
    history = FirmwareReleaseHistory(settings)
    snapshot = _power_v2_snapshot(contract, read_only=False)
    snapshot = replace(
        snapshot,
        identity={**snapshot.identity, "product_id": "wired-macro-pad-v1",
                  "hardware_id": "WMP-S3-MATRIX12-POWER-V2"},
        versions={**snapshot.versions, "firmware": "0.3.0-alpha.1",
                  "build_id": "20261030.01-g97e49d25"},
    )
    assert history.classify(snapshot) == "official"
    assert history.classify(replace(
        snapshot, versions={**snapshot.versions, "build_id": "20261030.04-g97e49d25"})) == "unknown"


def test_bundled_20261102_01_is_official_without_cached_history(contract, tmp_path):
    settings = QSettings(str(tmp_path / "empty.ini"), QSettings.IniFormat)
    history = FirmwareReleaseHistory(settings)
    snapshot = _power_v2_snapshot(contract, read_only=False)
    snapshot = replace(
        snapshot,
        identity={**snapshot.identity, "product_id": "wired-macro-pad-v1",
                  "hardware_id": "WMP-S3-MATRIX12-POWER-V2"},
        versions={**snapshot.versions, "firmware": "0.3.0-alpha.1",
                  "build_id": "20261102.01-g995ca3a0"},
    )
    assert history.classify(snapshot) == "official"
    assert history.classify(replace(
        snapshot, versions={**snapshot.versions, "build_id": "20261102.01-g0bf9f639"})) == "unknown"


def test_bundled_20261110_01_is_official_without_cached_history(contract, tmp_path):
    settings = QSettings(str(tmp_path / "empty.ini"), QSettings.IniFormat)
    history = FirmwareReleaseHistory(settings)
    snapshot = _power_v2_snapshot(contract, read_only=False)
    snapshot = replace(
        snapshot,
        identity={**snapshot.identity, "product_id": "wired-macro-pad-v1",
                  "hardware_id": "WMP-S3-MATRIX12-POWER-V2"},
        versions={**snapshot.versions, "firmware": "0.3.0-alpha.1",
                  "build_id": "20261110.01-g81fac1af"},
    )
    assert history.classify(snapshot) == "official"
    assert history.classify(replace(
        snapshot, versions={**snapshot.versions, "build_id": "20261110.01-ge89bdb53"})) == "unknown"


def test_bundled_20261113_01_is_official_without_cached_history(contract, tmp_path):
    settings = QSettings(str(tmp_path / "empty.ini"), QSettings.IniFormat)
    history = FirmwareReleaseHistory(settings)
    snapshot = _power_v2_snapshot(contract, read_only=False)
    snapshot = replace(
        snapshot,
        identity={**snapshot.identity, "product_id": "wired-macro-pad-v1",
                  "hardware_id": "WMP-S3-MATRIX12-POWER-V2"},
        versions={**snapshot.versions, "firmware": "0.3.0-alpha.1",
                  "build_id": "20261113.01-g37dce764"},
    )
    assert history.classify(snapshot) == "official"
    assert history.classify(replace(
        snapshot, versions={**snapshot.versions, "build_id": "20261113.01-gfd225a30"})) == "unknown"


def test_bundled_20261118_01_is_official_without_cached_history(contract, tmp_path):
    settings = QSettings(str(tmp_path / "empty.ini"), QSettings.IniFormat)
    history = FirmwareReleaseHistory(settings)
    snapshot = _power_v2_snapshot(contract, read_only=False)
    snapshot = replace(
        snapshot,
        identity={**snapshot.identity, "product_id": "wired-macro-pad-v1",
                  "hardware_id": "WMP-S3-MATRIX12-POWER-V2"},
        versions={**snapshot.versions, "firmware": "0.3.0-alpha.1",
                  "build_id": "20261118.01-gf1f54815"},
    )
    assert history.classify(snapshot) == "official"
    assert history.classify(replace(
        snapshot, versions={**snapshot.versions, "build_id": "20261118.01-gdeadbeef"})) == "unknown"


def test_bundled_20261123_01_is_official_without_cached_history(contract, tmp_path):
    # Console 0.3.0 ships with firmware 20261123.01 (SETTINGS label fix).
    settings = QSettings(str(tmp_path / "empty.ini"), QSettings.IniFormat)
    history = FirmwareReleaseHistory(settings)
    snapshot = _power_v2_snapshot(contract, read_only=False)
    snapshot = replace(
        snapshot,
        identity={**snapshot.identity, "product_id": "wired-macro-pad-v1",
                  "hardware_id": "WMP-S3-MATRIX12-POWER-V2"},
        versions={**snapshot.versions, "firmware": "0.3.0-alpha.1",
                  "build_id": "20261123.01-ga7323e48"},
    )
    assert history.classify(snapshot) == "official"
    assert history.classify(replace(
        snapshot, versions={**snapshot.versions, "build_id": "20261123.01-gdeadbeef"})) == "unknown"
