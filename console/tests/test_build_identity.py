from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

from controller_config.build_identity import BuildIdentity, load_build_identity
from controller_config.desktop_update import UnavailableUpdater, create_desktop_updater


OFFICIAL_UPDATE_CONFIG = {
    "macos": {
        "feed_url": "https://updates.example.test/appcast.xml",
        "public_key": "test-public-key",
    },
    "windows": {
        "feed_url": "https://updates.example.test/update.json",
        "public_key": "test-public-key",
    },
}


def test_packaged_source_defaults_to_custom() -> None:
    identity = load_build_identity()

    assert identity == BuildIdentity(origin="custom")
    assert identity.allows_official_updates is False


@pytest.mark.parametrize(
    "payload",
    (
        None,
        {},
        {"origin": ""},
        {"origin": "community"},
        {"origin": 1},
    ),
)
def test_missing_or_invalid_build_origin_is_unknown_and_disables_updates(
    tmp_path, payload
) -> None:
    path = tmp_path / "app-build.json"
    if payload is not None:
        path.write_text(json.dumps(payload), encoding="utf-8")

    identity = load_build_identity(path)

    assert identity.origin is None
    assert identity.allows_official_updates is False
    assert identity.update_state == "unavailable"


@pytest.mark.parametrize(
    ("identity", "expected_state"),
    (
        (BuildIdentity(origin="custom"), "custom"),
        (BuildIdentity(origin=None), "unavailable"),
    ),
)
def test_non_official_build_never_constructs_platform_updater(
    monkeypatch, identity, expected_state
) -> None:
    def forbidden_backend(*_args, **_kwargs):
        pytest.fail("a non-official build must not construct the official updater backend")

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setitem(
        sys.modules,
        "controller_config.desktop_update_macos",
        SimpleNamespace(MacDesktopUpdater=forbidden_backend),
    )

    updater = create_desktop_updater(
        lambda: True,
        config=OFFICIAL_UPDATE_CONFIG,
        build_identity=identity,
    )

    assert isinstance(updater, UnavailableUpdater)
    assert updater.status.state == expected_state
    for operation in (updater.start, updater.check, updater.download, updater.install):
        operation()
        assert updater.status.state == expected_state


def test_official_build_keeps_existing_platform_factory(monkeypatch) -> None:
    calls = []
    backend = object()

    def create_backend(config, prepare_restart, *, parent=None):
        calls.append((config, prepare_restart, parent))
        return backend

    prepare_restart = object()
    parent = object()
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setitem(
        sys.modules,
        "controller_config.desktop_update_macos",
        SimpleNamespace(MacDesktopUpdater=create_backend),
    )

    result = create_desktop_updater(
        prepare_restart,
        parent=parent,
        config=OFFICIAL_UPDATE_CONFIG,
        build_identity=BuildIdentity(origin="official"),
    )

    assert result is backend
    assert calls == [(OFFICIAL_UPDATE_CONFIG["macos"], prepare_restart, parent)]
