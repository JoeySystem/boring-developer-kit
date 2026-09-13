"""Stage Sparkle and its public configuration in a disposable macOS build."""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import plistlib
import shutil
from urllib.parse import urlparse


def stage(assets: Path, bundle: Path | None = None) -> None:
    source = assets / 'app-update-source.json'
    config = json.loads(source.read_text())
    mac = config['macos']
    configured_key = mac['public_key']
    mac['feed_url'] = os.environ.get('BORING_MACOS_UPDATE_FEED_URL', mac['feed_url']).strip()
    mac['public_key'] = os.environ.get('BORING_MACOS_UPDATE_PUBLIC_KEY', mac['public_key']).strip()
    if configured_key and mac['public_key'] != configured_key:
        raise ValueError('Update public key override differs from the configured key; preserve existing users\' update key')
    enabled = bool(mac['feed_url'] or mac['public_key'])
    channel = mac.get('channel', 'stable')
    if channel not in {'trial', 'stable'}:
        raise ValueError('macOS update channel must be trial or stable')
    if channel == 'trial' and not enabled:
        raise ValueError('Trial macOS updates require a feed URL and public key')
    framework = os.environ.get('BORING_SPARKLE_FRAMEWORK', '')
    if enabled:
        url = urlparse(mac['feed_url'])
        if url.scheme != 'https' or not url.hostname or url.username or url.password:
            raise ValueError('macOS update feed must be HTTPS')
        if len(base64.b64decode(mac['public_key'], validate=True)) != 32:
            raise ValueError('macOS update public key must be an Ed25519 public key')
        if not framework or not (Path(framework) / 'Sparkle').exists():
            raise ValueError('BORING_SPARKLE_FRAMEWORK must point to the official Sparkle.framework')
        if channel != 'trial' and not os.environ.get('BORING_MACOS_SIGN_IDENTITY'):
            raise ValueError('Enabled macOS updates require BORING_MACOS_SIGN_IDENTITY (Developer ID)')
    source.write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n')
    if bundle is None:
        return
    if framework:
        destination = bundle / 'Contents/Frameworks/Sparkle.framework'
        shutil.copytree(framework, destination, symlinks=True)
    plist = bundle / 'Contents/Info.plist'
    data = plistlib.loads(plist.read_bytes())
    data.update(SUEnableAutomaticChecks=True, SUAutomaticallyUpdate=False,
                SUAllowsAutomaticUpdates=False, SUSendProfileInfo=False,
                SUVerifyUpdateBeforeExtraction=True)
    if enabled:
        data.update(SUFeedURL=mac['feed_url'], SUPublicEDKey=mac['public_key'],
                    BORINGUpdateChannel=channel)
    plist.write_bytes(plistlib.dumps(data))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--assets', type=Path, required=True)
    parser.add_argument('--bundle', type=Path)
    args = parser.parse_args()
    stage(args.assets, args.bundle)
