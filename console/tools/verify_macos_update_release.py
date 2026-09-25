"""Verify a DMG with a shipped app's key, then inspect its embedded update key.

Read-only local release check. Does not create keys, alter packages, or publish.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import json
from pathlib import Path
import plistlib
import re
import subprocess
import tempfile
from urllib.parse import unquote, urlparse
import xml.etree.ElementTree as ET

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

SPARKLE = '{http://www.andymatuschak.org/xml-namespaces/sparkle}'


def _verify_minimum_system_version(item, candidate_app: Path, candidate: dict) -> str:
    declared = candidate.get('LSMinimumSystemVersion')
    offered = item.findtext(SPARKLE + 'minimumSystemVersion')
    if not declared or not offered:
        raise ValueError('Package and appcast must declare minimum macOS version')
    # Inspect the pinned Qt runtime already present in the mounted package.
    build = subprocess.run(
        ['xcrun', 'vtool', '-show-build', str(candidate_app / 'Contents/MacOS/QtCore')],
        check=True, capture_output=True, text=True,
    ).stdout
    requirements = re.findall(r'^\s*minos\s+([\d.]+)', build, re.MULTILINE)
    if not requirements:
        raise ValueError('Cannot read packaged Qt minimum macOS version')
    def version(value):
        parts = tuple(map(int, value.split('.')))
        return parts + (0,) * (3 - len(parts))
    if version(offered) != version(declared):
        raise ValueError('Appcast minimum macOS version differs from the package')
    if version(declared) < max(map(version, requirements)):
        raise ValueError('Package minimum macOS version is lower than packaged Qt requires')
    return declared


@contextmanager
def _mounted_app(archive: Path):
    with tempfile.TemporaryDirectory(prefix='boring-update-release-') as directory:
        mount = Path(directory) / 'volume'
        mount.mkdir()
        subprocess.run(['hdiutil', 'attach', '-readonly', '-nobrowse', '-mountpoint', str(mount), str(archive.resolve())],
                       check=True, capture_output=True)
        try:
            apps = list(mount.glob('*.app'))
            if len(apps) != 1:
                raise ValueError('Expected exactly one Console app in the DMG')
            yield apps[0]
        finally:
            subprocess.run(['hdiutil', 'detach', str(mount)], check=True, capture_output=True)


def verify(previous_app: Path, archives: Path, version: str, source_config: Path) -> dict:
    previous = plistlib.loads((previous_app / 'Contents/Info.plist').read_bytes())
    configured = json.loads(source_config.read_text())['macos']
    public_key = previous['SUPublicEDKey']
    if not public_key or configured['public_key'] != public_key:
        raise ValueError('Configured update public key differs from the shipped app')
    if tuple(map(int, version.split('.'))) <= tuple(map(int, previous['CFBundleVersion'].split('.'))):
        raise ValueError('Candidate version must be newer than the shipped app')
    items = ET.parse(archives / 'appcast.xml').findall('./channel/item')
    item = next((item for item in items if item.findtext(SPARKLE + 'version') == version), None)
    if item is None:
        raise ValueError('Candidate version is absent from the appcast')
    enclosure = item.find('enclosure')
    if enclosure is None:
        raise ValueError('Candidate download is absent from the appcast')
    archive = archives / Path(unquote(urlparse(enclosure.attrib['url']).path)).name
    payload = archive.read_bytes()
    if len(payload) != int(enclosure.attrib['length']):
        raise ValueError('Archive size differs from the appcast')
    Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key, validate=True)).verify(
        base64.b64decode(enclosure.attrib[SPARKLE + 'edSignature'], validate=True), payload)
    # Inspect the very archive just verified, not an unrelated local .app.
    with _mounted_app(archive) as candidate_app:
        candidate = plistlib.loads((candidate_app / 'Contents/Info.plist').read_bytes())
        packaged_assets = candidate_app / 'Contents/MacOS/controller_config/assets'
        bundled = json.loads((packaged_assets / 'app-update-source.json').read_text())['macos']
        try:
            candidate_origin = json.loads(
                (packaged_assets / 'app-build.json').read_text(encoding='utf-8')
            ).get('origin')
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            raise ValueError('Candidate package must declare official build origin') from exc
        if candidate_origin != 'official':
            raise ValueError('Candidate package must declare official build origin')
        if candidate['CFBundleVersion'] != version:
            raise ValueError('Packaged version differs from the appcast')
        if any(key != public_key for key in (candidate.get('SUPublicEDKey'), bundled.get('public_key'))):
            raise ValueError('Packaged update public key differs from the shipped app; future updates would break')
        if candidate['CFBundleIdentifier'] != previous['CFBundleIdentifier']:
            raise ValueError('Application identity differs from the shipped app')
        if any(url != previous['SUFeedURL'] for url in (
            candidate.get('SUFeedURL'), bundled.get('feed_url'), configured.get('feed_url'),
        )):
            raise ValueError('Update feed changed; channel migration needs a separate old-client upgrade plan')
        minimum_system_version = _verify_minimum_system_version(item, candidate_app, candidate)
    return {'previous_version': previous['CFBundleVersion'], 'candidate_version': version,
            'build_origin': 'official', 'public_key_unchanged': True, 'feed_unchanged': True,
            'minimum_system_version': minimum_system_version,
            'archive_signature_verified_by_previous_key': True, 'archive': str(archive)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--previous-app', type=Path, required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--archives', type=Path, required=True)
    parser.add_argument('--source-config', type=Path,
                        default=Path(__file__).resolve().parents[1] / 'src/controller_config/assets/app-update-source.json')
    args = parser.parse_args()
    print(json.dumps(verify(args.previous_app, args.archives, args.version, args.source_config), indent=2))
