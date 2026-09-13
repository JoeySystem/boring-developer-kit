from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _validator():
    path = Path(__file__).resolve().parents[1] / "tools" / "validate_windows_update_config.py"
    spec = importlib.util.spec_from_file_location("validate_windows_update_config", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.validate


def _config(tmp_path: Path, **windows) -> Path:
    path = tmp_path / "app-update-source.json"
    path.write_text(json.dumps({"windows": windows}), encoding="utf-8")
    return path


@pytest.fixture
def public_key() -> str:
    key = Ed25519PrivateKey.generate().public_key().public_bytes_raw()
    return base64.b64encode(key).decode()


def test_trial_allows_unsigned_complete_installer(tmp_path, public_key):
    path = _config(
        tmp_path,
        channel="trial",
        feed_url="https://updates.example.com/windows/feed.json",
        public_key=public_key,
    )

    assert _validator()(path) == "trial"
    assert json.loads(path.read_text())["windows"]["public_key"] == public_key


def test_default_channel_keeps_unsigned_updates_disabled_development_build(tmp_path):
    path = _config(tmp_path, feed_url="", public_key="")

    assert _validator()(path) == "stable"


def test_trial_rejects_skip_installer(tmp_path, public_key):
    path = _config(
        tmp_path,
        channel="trial",
        feed_url="https://updates.example.com/windows/feed.json",
        public_key=public_key,
    )

    with pytest.raises(ValueError, match="installer build"):
        _validator()(path, skip_installer=True)


@pytest.mark.parametrize(
    "windows, reason",
    [
        ({"channel": "trial", "feed_url": "", "public_key": ""}, "feed URL"),
        (
            {"channel": "trial", "feed_url": "http://example.com/feed.json", "public_key": "bad"},
            "HTTPS",
        ),
        (
            {"channel": "trial", "feed_url": "https://example.com/feed.json", "public_key": "bad"},
            "Ed25519",
        ),
    ],
)
def test_trial_requires_https_feed_and_ed25519_key(tmp_path, windows, reason):
    path = _config(tmp_path, **windows)

    with pytest.raises(ValueError, match=reason):
        _validator()(path)


@pytest.mark.parametrize("channel", [None, "stable"])
def test_default_and_stable_updates_still_require_authenticode(
    tmp_path, public_key, channel
):
    windows = {
        "feed_url": "https://updates.example.com/windows/feed.json",
        "public_key": public_key,
    }
    if channel is not None:
        windows["channel"] = channel
    path = _config(tmp_path, **windows)

    with pytest.raises(ValueError, match="Authenticode"):
        _validator()(path)
    assert _validator()(path, has_signing_certificate=True) == "stable"


def test_unknown_channel_cannot_bypass_authenticode(tmp_path, public_key):
    path = _config(
        tmp_path,
        channel="preview",
        feed_url="https://updates.example.com/windows/feed.json",
        public_key=public_key,
    )

    with pytest.raises(ValueError, match="stable or trial"):
        _validator()(path)
