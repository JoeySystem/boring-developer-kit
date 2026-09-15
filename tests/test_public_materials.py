from __future__ import annotations

import base64
import copy
import importlib.util
import json
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace
import zipfile

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "protocol/fixtures"


def read(name):
    return json.loads((FIXTURES / name).read_text())


def canonical(value):
    # These existing vectors only use the schema's integer/string JSON subset.
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def decode(value):
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def verify(spki, value, signature):
    key = serialization.load_der_public_key(decode(spki))
    key.verify(decode(signature), canonical(value),
               padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32), hashes.SHA256())


def test_fixture_index_and_config_examples():
    schema = json.loads((ROOT / "protocol/config-schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    for item in read("manifest.json")["fixtures"]:
        value = read(item["path"])
        if item["kind"] == "config":
            validator.validate(value)


def test_sanitized_public_authentication_vector_still_verifies():
    fixture = read("device-auth-v1.json")
    assert "test_private_keys" not in fixture
    cert = fixture["certificate_response"]["result"]
    verify(fixture["test_root"]["public_key_spki"], cert["certificate"], cert["issuer_signature"])
    verify(cert["certificate"]["public_key_spki"], fixture["challenge_signing_object"],
           fixture["challenge_response"]["result"]["signature"])


def test_official_public_trust_roots_are_separate_from_test_identity():
    roots = json.loads((ROOT / "protocol/device-trust-roots.json").read_text())["roots"]
    test_root = read("device-auth-v1.json")["test_root"]["public_key_spki"]
    assert roots
    for root in roots:
        assert set(root) == {"issuer_key_id", "purpose", "public_key_algorithm", "public_key_spki"}
        assert root["purpose"] == "production"
        assert root["public_key_spki"] != test_root
        assert serialization.load_der_public_key(decode(root["public_key_spki"])).key_size == 3072


@pytest.mark.parametrize("path", sorted(FIXTURES.glob("device-auth-invalid-*.json")), ids=lambda p: p.stem)
def test_negative_authentication_vectors(path):
    fixture = read("device-auth-v1.json")
    mutation = json.loads(path.read_text())
    changed = copy.deepcopy(fixture)
    owner = changed
    fields = mutation["mutation"]["target"].split(".")
    for field in fields[:-1]:
        owner = owner[field]
    owner[fields[-1]] = mutation["mutation"]["value"]
    with pytest.raises(InvalidSignature):
        if mutation["expected_failure"] == "certificate_signature":
            result = changed["certificate_response"]["result"]
            verify(fixture["test_root"]["public_key_spki"], result["certificate"], result["issuer_signature"])
        else:
            result = fixture["certificate_response"]["result"]
            verify(result["certificate"]["public_key_spki"], changed["challenge_signing_object"],
                   changed["challenge_response"]["result"]["signature"])


def test_release_archives_have_importable_roots_and_licenses(tmp_path):
    spec = importlib.util.spec_from_file_location("build_release", ROOT / "tools/build_release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    results = module.build(tmp_path)
    for name in module.EXAMPLES:
        with zipfile.ZipFile(tmp_path / f"{name}.zip") as archive:
            manifest = json.loads(archive.read("boring-extension.json"))
            assert archive.read(manifest["entrypoint"])
            assert archive.read("LICENSE") == (ROOT / "LICENSE").read_bytes()
            assert archive.read("NOTICE") == (ROOT / "NOTICE").read_bytes()
    with zipfile.ZipFile(results[-1]) as archive:
        names = archive.namelist()
        assert any(name.endswith("sdk/python/boring_console_sdk/client.py") for name in names)
        assert any(name.endswith("console/src/controller_config/app.py") for name in names)
        assert any(name.endswith("console/README.md") for name in names)
        assert any(name.endswith("firmware/main/app_main.c") for name in names)
        assert any(name.endswith("firmware/DIY-GUIDE.md") for name in names)
        assert any(name.endswith("firmware/tools/pack_custom_firmware.py") for name in names)
        assert not any("/artifacts/" in name or "/managed_components/" in name or "/output/" in name for name in names)
        assert not any(name.endswith(("provision_device.py", "prepare_online_firmware.py", ".pem", ".bin", ".elf")) for name in names)
        assert any(name.endswith("console/LICENSE") for name in names)
        assert not any("font-pack.json" in name or "boring-console-icon" in name for name in names)
        assert not any(".venv/" in name or "device-backups/" in name for name in names)
        assert any(name.endswith("examples/packages/claim_prompt.zip") for name in names)
        assert not any("test_private_keys" in archive.read(name).decode() for name in names if name.endswith(".json"))
        assert not any("__pycache__" in name or ".egg-info/" in name for name in names)


def test_proposal_example_stays_connected_for_user_review(monkeypatch):
    class Client:
        extension_id = "com.boring.example.propose-mapping"
        is_connected = True
        waited = False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            assert self.waited, "Exiting immediately makes the Console block proposal approval"

        def get_context(self):
            return {"device_serial": "CP01-AABBCCDDEEFF", "config_summary":
                    {"generation": 1, "digest": "a" * 64}, "active_profile": {"id": 0}}

        def propose_mapping(self, proposal):
            assert proposal["control_id"] == "key.12"
            assert proposal["action"]["usage"] == 40
            return {"status": "pending"}

        def next_event(self, *, timeout_ms):
            assert timeout_ms > 0
            self.waited = True
            self.is_connected = False  # Simulate the host ending this session.

    client = Client()
    monkeypatch.setitem(sys.modules, "boring_console_sdk", SimpleNamespace(
        BoringConsoleClient=SimpleNamespace(from_environment=lambda: client)))
    runpy.run_path(str(ROOT / "examples/extensions/propose_mapping/main.py"), run_name="__main__")
    assert client.waited


def test_public_build_does_not_subscribe_to_official_updates():
    assets = ROOT / 'console/src/controller_config/assets'
    application = json.loads((assets / 'app-update-source.json').read_text())
    assert set(application) == {'macos', 'windows'}
    for settings in application.values():
        assert settings['feed_url'] == settings['public_key'] == ''
    assert json.loads((assets / 'firmware-source.json').read_text())['manifest_url'] == ''
    assert json.loads((assets / 'official-firmware-releases.json').read_text()) == []
    assert not (assets / 'onboarding').exists()
    assert not (assets / 'boring_mist_3d').exists()
    assert not any(assets.glob('boring-console-icon.*'))
    assert not (assets / 'boring-mist-wireframe.png').exists()
    assert not (assets / 'mist-screen-source-preview.gif').exists()
