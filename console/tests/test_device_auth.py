from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from controller_config.protocol.device_auth import (
    DeviceAuthenticationError,
    DeviceAuthenticator,
    DeviceTrustState,
    TrustBundle,
    TrustPolicy,
    load_authenticator,
    load_default_authenticator,
)


def _bundle(fixture: dict, *, policy: TrustPolicy = TrustPolicy.DEVELOPMENT) -> TrustBundle:
    root = dict(fixture["test_root"])
    root["purpose"] = "test"
    return TrustBundle.from_document(
        {"version": 1, "roots": [root]},
        policy=policy,
    )


def _identity(fixture: dict) -> dict:
    certificate = fixture["certificate_response"]["result"]["certificate"]
    return {
        "product_id": certificate["product_id"],
        "hardware_id": certificate["hardware_id"],
        "serial": certificate["serial"],
        "usb_vid": 0x303A,
        "usb_pid": 0x8360,
    }


def test_shared_fixture_authenticates_certificate_and_challenge(load_fixture) -> None:
    fixture = load_fixture("device-auth-v1.json")
    authenticator = DeviceAuthenticator(
        _bundle(fixture),
        nonce_factory=lambda: bytes(range(32)),
    )

    nonce = authenticator.new_nonce()
    certificate = authenticator.verify_certificate_response(
        fixture["certificate_response"],
        hello_identity=_identity(fixture),
        usb_serial=fixture["certificate_response"]["result"]["certificate"]["serial"],
    )
    trust = authenticator.verify_challenge_response(
        fixture["challenge_response"],
        certificate=certificate,
        nonce=nonce,
    )

    assert trust.state is DeviceTrustState.AUTHENTICATED
    assert trust.is_authenticated
    assert trust.issuer_key_id == fixture["test_root"]["issuer_key_id"]
    assert trust.serial == _identity(fixture)["serial"]


@pytest.mark.parametrize(
    ("target", "message"),
    [
        ("issuer_signature", "证书签名"),
        ("public_key_spki", "证书签名"),
    ],
)
def test_certificate_tampering_is_rejected(load_fixture, target: str, message: str) -> None:
    fixture = load_fixture("device-auth-v1.json")
    response = copy.deepcopy(fixture["certificate_response"])
    if target == "issuer_signature":
        response["result"][target] = "AA"
    else:
        response["result"]["certificate"][target] = "AA"

    with pytest.raises(DeviceAuthenticationError, match=message) as caught:
        DeviceAuthenticator(_bundle(fixture)).verify_certificate_response(
            response,
            hello_identity=_identity(fixture),
            usb_serial=_identity(fixture)["serial"],
        )

    assert caught.value.reason == "certificate_signature"


@pytest.mark.parametrize("field", ["product_id", "hardware_id", "serial"])
def test_certificate_must_match_hello_identity(load_fixture, field: str) -> None:
    fixture = load_fixture("device-auth-v1.json")
    identity = _identity(fixture)
    identity[field] = f"wrong-{field}"

    with pytest.raises(DeviceAuthenticationError, match=field) as caught:
        DeviceAuthenticator(_bundle(fixture)).verify_certificate_response(
            fixture["certificate_response"],
            hello_identity=identity,
            usb_serial="",
        )

    assert caught.value.reason == "identity_mismatch"


def test_certificate_must_match_usb_serial_when_descriptor_reports_one(load_fixture) -> None:
    fixture = load_fixture("device-auth-v1.json")

    with pytest.raises(DeviceAuthenticationError, match="USB serial") as caught:
        DeviceAuthenticator(_bundle(fixture)).verify_certificate_response(
            fixture["certificate_response"],
            hello_identity=_identity(fixture),
            usb_serial="BORING-TEST-CLONED",
        )

    assert caught.value.reason == "identity_mismatch"


def test_challenge_rejects_old_nonce_and_invalid_signature(load_fixture) -> None:
    fixture = load_fixture("device-auth-v1.json")
    authenticator = DeviceAuthenticator(_bundle(fixture))
    certificate = authenticator.verify_certificate_response(
        fixture["certificate_response"],
        hello_identity=_identity(fixture),
        usb_serial="",
    )

    with pytest.raises(DeviceAuthenticationError, match="nonce") as caught:
        authenticator.verify_challenge_response(
            fixture["challenge_response"],
            certificate=certificate,
            nonce="AQIDBAUGBwgJCgsMDQ4PEBESExQVFhcYGRobHB0eHyA",
        )
    assert caught.value.reason == "nonce_mismatch"

    response = copy.deepcopy(fixture["challenge_response"])
    response["result"]["signature"] = "AA"
    with pytest.raises(DeviceAuthenticationError, match="挑战签名") as caught:
        authenticator.verify_challenge_response(
            response,
            certificate=certificate,
            nonce=fixture["challenge_request"]["nonce"],
        )
    assert caught.value.reason == "challenge_signature"


def test_each_authenticator_session_generates_a_fresh_32_byte_nonce(load_fixture) -> None:
    first = DeviceAuthenticator(_bundle(load_fixture("device-auth-v1.json"))).new_nonce()
    second = DeviceAuthenticator(_bundle(load_fixture("device-auth-v1.json"))).new_nonce()

    assert len(first) == 43
    assert len(second) == 43
    assert first != second
    assert "=" not in first + second


def test_development_policy_allows_missing_identity_but_production_rejects_it(
    load_fixture,
) -> None:
    fixture = load_fixture("device-auth-v1.json")
    development = DeviceAuthenticator(_bundle(fixture))
    production = DeviceAuthenticator(
        TrustBundle.from_document(
            {"version": 1, "roots": []},
            policy=TrustPolicy.PRODUCTION,
        )
    )

    trust = development.trust_without_device_identity("capability_missing")
    assert trust.state is DeviceTrustState.DEVELOPMENT_UNAUTHENTICATED
    assert not trust.is_authenticated

    with pytest.raises(DeviceAuthenticationError, match="设备认证能力") as caught:
        production.trust_without_device_identity("capability_missing")
    assert caught.value.reason == "authentication_required"


def test_production_policy_never_accepts_test_roots_or_private_material(load_fixture) -> None:
    fixture = load_fixture("device-auth-v1.json")
    production = _bundle(fixture, policy=TrustPolicy.PRODUCTION)

    with pytest.raises(DeviceAuthenticationError, match="签发方") as caught:
        DeviceAuthenticator(production).verify_certificate_response(
            fixture["certificate_response"],
            hello_identity=_identity(fixture),
            usb_serial="",
        )
    assert caught.value.reason == "unknown_issuer"

    root_with_private_material = dict(fixture["test_root"])
    root_with_private_material["purpose"] = "production"
    root_with_private_material["private_key"] = "must-not-be-here"
    with pytest.raises(ValueError, match="私钥"):
        TrustBundle.from_document(
            {"version": 1, "roots": [root_with_private_material]},
            policy=TrustPolicy.PRODUCTION,
        )


def test_source_runtime_uses_shared_test_root_in_development(
    load_fixture,
) -> None:
    authenticator = load_default_authenticator()
    fixture_root = load_fixture("device-auth-v1.json")["test_root"]

    assert authenticator.trust_bundle.policy is TrustPolicy.DEVELOPMENT
    root = authenticator.trust_bundle.roots["BORING-TEST-ROOT-2026-09"]
    assert root.public_key_spki == fixture_root["public_key_spki"]
    assert root.purpose == "test"


def test_production_marker_cannot_start_with_only_test_roots(
    load_fixture,
    tmp_path,
) -> None:
    test_root = dict(load_fixture("device-auth-v1.json")["test_root"])
    test_root["purpose"] = "test"
    roots = tmp_path / "device-trust-roots.json"
    roots.write_text(
        json.dumps(
            {
                "version": 1,
                "roots": [test_root],
            }
        ),
        encoding="utf-8",
    )
    marker = tmp_path / "device-trust-policy.json"
    marker.write_text(
        '{"version": 1, "policy": "production"}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="生产信任根"):
        load_authenticator(roots, policy_marker_path=marker)
