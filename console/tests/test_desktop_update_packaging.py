"""Verify updater build configuration and signed release artifacts without publishing."""
from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path
import plistlib
import shutil

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

from controller_config import desktop_update_windows


def _tool(name):
    path = Path(__file__).resolve().parents[1] / 'tools' / f'{name}.py'
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


stage = _tool('stage_macos_updater').stage
stage_build = _tool('stage_app_build').stage
build_feed = _tool('build_windows_update_feed').build_feed


@pytest.fixture
def mac_package(tmp_path, monkeypatch):
    for name in ('BORING_MACOS_UPDATE_FEED_URL', 'BORING_MACOS_UPDATE_PUBLIC_KEY',
                 'BORING_SPARKLE_FRAMEWORK', 'BORING_MACOS_SIGN_IDENTITY'):
        monkeypatch.delenv(name, raising=False)
    assets = tmp_path / 'assets'
    assets.mkdir()
    config = {'macos': {'feed_url': '', 'public_key': ''},
              'windows': {'feed_url': '', 'public_key': ''}}
    (assets / 'app-update-source.json').write_text(json.dumps(config))
    (assets / 'app-build.json').write_text(json.dumps({'origin': 'official'}))
    bundle = tmp_path / 'BORING Console.app'
    (bundle / 'Contents').mkdir(parents=True)
    (bundle / 'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleVersion': '0.1.14'}))
    # A framework-shaped fixture tests copying/plist work, not Apple authenticity.
    framework = tmp_path / 'Sparkle.framework'
    (framework / 'Versions/B').mkdir(parents=True)
    (framework / 'Versions/B/Sparkle').write_bytes(b'framework fixture')
    (framework / 'Versions/Current').symlink_to('B')
    (framework / 'Sparkle').symlink_to('Versions/Current/Sparkle')
    key = base64.b64encode(Ed25519PrivateKey.generate().public_key().public_bytes_raw()).decode()
    return assets, bundle, framework, key


def test_development_package_requires_no_update_secrets(mac_package):
    assets, bundle, _, _ = mac_package
    stage(assets, bundle)
    info = plistlib.loads((bundle / 'Contents/Info.plist').read_bytes())
    assert 'SUFeedURL' not in info and 'SUPublicEDKey' not in info
    assert not (bundle / 'Contents/Frameworks/Sparkle.framework').exists()


def test_default_custom_stage_removes_official_update_material_without_touching_source(
    tmp_path,
):
    source_assets = tmp_path / 'source-assets'
    staged_assets = tmp_path / 'staged-assets'
    source_assets.mkdir()
    configured = {
        'macos': {
            'channel': 'trial',
            'feed_url': 'https://updates.example.test/macos/appcast.xml',
            'public_key': 'mac-key',
        },
        'windows': {
            'channel': 'trial',
            'feed_url': 'https://updates.example.test/windows/update.json',
            'public_key': 'windows-key',
        },
    }
    (source_assets / 'app-build.json').write_text(
        json.dumps({'origin': 'custom'}), encoding='utf-8'
    )
    (source_assets / 'app-update-source.json').write_text(
        json.dumps(configured), encoding='utf-8'
    )
    shutil.copytree(source_assets, staged_assets)
    source_before = {
        path.name: path.read_bytes() for path in source_assets.iterdir()
    }

    identity = stage_build(staged_assets, 'custom')

    assert identity.origin == 'custom'
    assert json.loads((staged_assets / 'app-build.json').read_text()) == {
        'origin': 'custom'
    }
    staged_update = json.loads(
        (staged_assets / 'app-update-source.json').read_text()
    )
    for platform in ('macos', 'windows'):
        assert staged_update[platform]['feed_url'] == ''
        assert staged_update[platform]['public_key'] == ''
        assert staged_update[platform]['channel'] == 'trial'
    assert {
        path.name: path.read_bytes() for path in source_assets.iterdir()
    } == source_before


def test_custom_mac_stage_ignores_inherited_official_environment(mac_package, monkeypatch):
    assets, bundle, framework, key = mac_package
    config = json.loads((assets / 'app-update-source.json').read_text())
    config['macos'].update(
        channel='trial',
        feed_url='https://updates.example.test/macos/appcast.xml',
        public_key=key,
    )
    (assets / 'app-update-source.json').write_text(json.dumps(config))
    stage_build(assets, 'custom')
    monkeypatch.setenv('BORING_MACOS_UPDATE_FEED_URL', 'https://official.example.test/appcast.xml')
    monkeypatch.setenv('BORING_MACOS_UPDATE_PUBLIC_KEY', key)
    monkeypatch.setenv('BORING_SPARKLE_FRAMEWORK', str(framework))

    stage(assets, bundle)

    info = plistlib.loads((bundle / 'Contents/Info.plist').read_bytes())
    staged = json.loads((assets / 'app-update-source.json').read_text())
    assert info.get('SUFeedURL') is None
    assert info.get('SUPublicEDKey') is None
    assert not (bundle / 'Contents/Frameworks/Sparkle.framework').exists()
    assert staged['macos']['feed_url'] == ''
    assert staged['macos']['public_key'] == ''


def test_custom_mac_stage_requires_shared_build_sanitizer(mac_package):
    assets, bundle, _, key = mac_package
    config = json.loads((assets / 'app-update-source.json').read_text())
    config['macos'].update(
        channel='trial',
        feed_url='https://updates.example.test/macos/appcast.xml',
        public_key=key,
    )
    (assets / 'app-update-source.json').write_text(json.dumps(config))
    (assets / 'app-build.json').write_text(json.dumps({'origin': 'custom'}))

    with pytest.raises(ValueError, match='stage_app_build.py'):
        stage(assets, bundle)


@pytest.mark.parametrize('origin', ['', 'community', 'OFFICIAL'])
def test_build_stage_rejects_invalid_origin(tmp_path, origin):
    assets = tmp_path / 'assets'
    assets.mkdir()
    (assets / 'app-build.json').write_text(json.dumps({'origin': 'custom'}))
    (assets / 'app-update-source.json').write_text(
        json.dumps({'macos': {}, 'windows': {}})
    )

    with pytest.raises(ValueError, match='official or custom'):
        stage_build(assets, origin)


def test_native_packaging_defaults_to_distinct_custom_install_identity():
    project = Path(__file__).resolve().parents[1]
    macos = (project / 'deploy/build_macos.sh').read_text()
    windows = (project / 'deploy/build_windows.ps1').read_text()
    installer = (project / 'deploy/boring-console.iss').read_text()
    assert 'BORING Console Community' in macos
    assert 'com.boring.console.community' in macos
    assert '--origin custom' in macos
    assert 'stage_app_build.py' in windows and "'custom'" in windows
    assert 'BORING Console Community' in installer
    assert 'A6A26BC2-8D59-43BB-84C6-7B62C89EAD90' in installer
    assert '7D499724-C0AE-438B-93C1-59E2D01785D7' not in installer


def test_mac_dmg_is_a_drag_to_applications_installer():
    project = Path(__file__).resolve().parents[1]
    macos = (project / 'deploy/build_macos.sh').read_text(encoding='utf-8')

    assert 'ln -s /Applications "$DMG_ROOT/Applications"' in macos
    assert 'set position of item appName to {175, 190}' in macos
    assert 'set position of item "Applications" to {465, 190}' in macos
    assert '[ ! -s "$VERIFY_MOUNT/.DS_Store" ]' in macos
    assert 'readlink "$VERIFY_MOUNT/Applications"' in macos


@pytest.mark.parametrize('setting,value,reason', [
    ('BORING_MACOS_UPDATE_FEED_URL', 'http://example.com/feed.xml', 'HTTPS'),
    ('BORING_MACOS_UPDATE_FEED_URL', 'https://user:password@example.com/feed.xml', 'HTTPS'),
    ('BORING_MACOS_UPDATE_PUBLIC_KEY', 'invalid key', None),
    ('BORING_MACOS_UPDATE_PUBLIC_KEY', base64.b64encode(b'short').decode(), 'Ed25519'),
    ('BORING_SPARKLE_FRAMEWORK', '', 'Sparkle.framework'),
    ('BORING_MACOS_SIGN_IDENTITY', '', 'Developer ID'),
])
def test_enabled_mac_build_rejects_incomplete_settings_before_staging(mac_package, monkeypatch, setting, value, reason):
    assets, bundle, framework, key = mac_package
    monkeypatch.setenv('BORING_MACOS_UPDATE_FEED_URL', 'https://example.com/feed.xml')
    monkeypatch.setenv('BORING_MACOS_UPDATE_PUBLIC_KEY', key)
    monkeypatch.setenv('BORING_SPARKLE_FRAMEWORK', str(framework))
    monkeypatch.setenv('BORING_MACOS_SIGN_IDENTITY', 'Developer ID Application: Test')
    monkeypatch.setenv(setting, value)
    before = (assets / 'app-update-source.json').read_bytes()
    with pytest.raises(ValueError, match=reason):
        stage(assets, bundle)
    assert (assets / 'app-update-source.json').read_bytes() == before
    assert not (bundle / 'Contents/Frameworks').exists()


def test_mac_feed_and_key_match_staged_bundle_and_preserve_framework_links(mac_package, monkeypatch):
    assets, bundle, framework, key = mac_package
    monkeypatch.setenv('BORING_MACOS_UPDATE_FEED_URL', 'https://example.com/feed.xml')
    monkeypatch.setenv('BORING_MACOS_UPDATE_PUBLIC_KEY', key)
    monkeypatch.setenv('BORING_SPARKLE_FRAMEWORK', str(framework))
    monkeypatch.setenv('BORING_MACOS_SIGN_IDENTITY', 'Developer ID Application: Test')
    stage(assets)
    stage(assets, bundle)
    config = json.loads((assets / 'app-update-source.json').read_text())
    info = plistlib.loads((bundle / 'Contents/Info.plist').read_bytes())
    assert info['SUFeedURL'] == config['macos']['feed_url'] == 'https://example.com/feed.xml'
    assert info['SUPublicEDKey'] == config['macos']['public_key'] == key
    assert info['CFBundleVersion'] == '0.1.14'
    assert info['SUVerifyUpdateBeforeExtraction'] is True
    assert info['SUAutomaticallyUpdate'] is False
    assert info['SUAllowsAutomaticUpdates'] is False
    assert info['SUScheduledCheckInterval'] == 60 * 60
    assert info['SUSendProfileInfo'] is False
    assert config['windows'] == {'feed_url': '', 'public_key': ''}
    staged = bundle / 'Contents/Frameworks/Sparkle.framework'
    assert (staged / 'Sparkle').is_symlink()
    assert (staged / 'Sparkle').read_bytes() == b'framework fixture'


@pytest.fixture
def windows_release(tmp_path):
    key = Ed25519PrivateKey.generate()
    private_key = tmp_path / 'release-key.pem'
    private_key.write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    installer = tmp_path / 'BORING-Console-Setup.exe'
    installer.write_bytes(b'MZsigned installer fixture')
    return installer, private_key, key


def test_windows_feed_signature_roundtrip_and_tamper(windows_release, monkeypatch):
    installer, private_key, key = windows_release
    release = build_feed(installer, private_key, '99.0.0', 'https://example.com/setup.exe',
                         public_key=base64.b64encode(key.public_key().public_bytes_raw()).decode(),
                         build_origin='official')
    monkeypatch.setattr(desktop_update_windows.platform, 'machine', lambda: 'AMD64')
    parsed = desktop_update_windows._release(json.dumps(release).encode())
    public_key = base64.b64encode(key.public_key().public_bytes_raw()).decode()
    desktop_update_windows._verify(installer, parsed, public_key)
    assert 'PRIVATE KEY' not in json.dumps(release)
    assert release['size'] == installer.stat().st_size
    installer.write_bytes(b'MZtampered installer!!!!!')
    # Keep length to exercise provenance instead of just download completeness.
    installer.write_bytes(installer.read_bytes().ljust(release['size'], b' ')[:release['size']])
    with pytest.raises(InvalidSignature):
        desktop_update_windows._verify(installer, parsed, public_key)


@pytest.mark.parametrize('version,url', [
    ('1.2', 'https://example.com/setup.exe'),
    ('not-a-version', 'https://example.com/setup.exe'),
    ('1.2.3', 'http://example.com/setup.exe'),
    ('1.2.3', 'https:///setup.exe'),
    ('1.2.3', 'https://user:password@example.com/setup.exe'),
])
def test_windows_feed_rejects_invalid_version_or_url(windows_release, version, url):
    installer, private_key, _ = windows_release
    with pytest.raises(ValueError):
        build_feed(installer, private_key, version, url, public_key='',
                   build_origin='official')


def test_windows_feed_rejects_wrong_key_type(windows_release):
    installer, private_key, _ = windows_release
    private_key.write_bytes(ec.generate_private_key(ec.SECP256R1()).private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    with pytest.raises(ValueError, match='Ed25519'):
        build_feed(installer, private_key, '1.2.3', 'https://example.com/setup.exe',
                   public_key='', build_origin='official')


def test_windows_feed_rejects_non_installer_input(windows_release):
    installer, private_key, _ = windows_release
    installer.write_bytes(b'<html>Download error</html>')
    with pytest.raises(ValueError, match='installer executable'):
        build_feed(installer, private_key, '1.2.3', 'https://example.com/setup.exe',
                   public_key='', build_origin='official')


def test_trial_mac_build_keeps_signature_validation_without_developer_id(mac_package, monkeypatch):
    assets, bundle, framework, key = mac_package
    config = json.loads((assets / 'app-update-source.json').read_text())
    config['macos'].update(channel='trial', feed_url='https://updates.boringconcept.com/console/macos/arm64/trial/appcast.xml', public_key=key)
    (assets / 'app-update-source.json').write_text(json.dumps(config))
    monkeypatch.setenv('BORING_SPARKLE_FRAMEWORK', str(framework))
    stage(assets, bundle)
    info = plistlib.loads((bundle / 'Contents/Info.plist').read_bytes())
    assert info['BORINGUpdateChannel'] == 'trial'
    assert info['SUPublicEDKey'] == key
    assert info['SUVerifyUpdateBeforeExtraction'] is True
    assert info['SUAutomaticallyUpdate'] is False
    assert info['SUScheduledCheckInterval'] == 60 * 60
    assert (bundle / 'Contents/Frameworks/Sparkle.framework/Sparkle').exists()


def test_trial_mac_build_cannot_silently_disable_updates(mac_package):
    assets, bundle, _, _ = mac_package
    source = assets / 'app-update-source.json'
    config = json.loads(source.read_text())
    config['macos']['channel'] = 'trial'
    source.write_text(json.dumps(config))
    with pytest.raises(ValueError, match='feed URL and public key'):
        stage(assets, bundle)


@pytest.mark.parametrize('channel', ['stable', 'typo'])
def test_non_trial_mac_build_cannot_omit_developer_identity(mac_package, monkeypatch, channel):
    assets, bundle, framework, key = mac_package
    config = json.loads((assets / 'app-update-source.json').read_text())
    config['macos'].update(channel=channel, feed_url='https://example.com/appcast.xml', public_key=key)
    (assets / 'app-update-source.json').write_text(json.dumps(config))
    monkeypatch.setenv('BORING_SPARKLE_FRAMEWORK', str(framework))
    with pytest.raises(ValueError):
        stage(assets, bundle)


def test_windows_publisher_rejects_different_signing_key(windows_release):
    installer, private_key, _ = windows_release
    other_public_key = base64.b64encode(Ed25519PrivateKey.generate().public_key().public_bytes_raw()).decode()
    with pytest.raises(ValueError, match='does not match'):
        build_feed(installer, private_key, '1.2.3', 'https://example.com/setup.exe',
                   public_key=other_public_key, build_origin='official')


def test_windows_feed_rejects_custom_build_before_reading_release_key(
    windows_release,
):
    installer, private_key, key = windows_release
    private_key.unlink()

    with pytest.raises(ValueError, match='official build origin'):
        build_feed(
            installer,
            private_key,
            '1.2.3',
            'https://example.com/setup.exe',
            public_key=base64.b64encode(
                key.public_key().public_bytes_raw()
            ).decode(),
            build_origin='custom',
        )


def test_mac_build_rejects_accidental_public_key_override(mac_package, monkeypatch):
    assets, bundle, framework, key = mac_package
    source = assets / 'app-update-source.json'
    config = json.loads(source.read_text())
    config['macos'].update(channel='trial', public_key=key, feed_url='https://example.com/appcast.xml')
    source.write_text(json.dumps(config))
    before = source.read_bytes()
    monkeypatch.setenv('BORING_SPARKLE_FRAMEWORK', str(framework))
    monkeypatch.setenv('BORING_MACOS_UPDATE_PUBLIC_KEY', base64.b64encode(Ed25519PrivateKey.generate().public_key().public_bytes_raw()).decode())
    with pytest.raises(ValueError, match='override differs'):
        stage(assets, bundle)
    assert source.read_bytes() == before
