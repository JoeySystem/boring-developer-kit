"""Sign the final Windows installer for the desktop updater.

The PEM Ed25519 private key belongs outside the repository. This command writes
public release metadata only and never uploads files or prints private material.
"""
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import re
from urllib.parse import urlparse

from cryptography.hazmat.primitives.serialization import load_pem_private_key
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def build_feed(installer: Path, private_key_file: Path, version: str, url: str, *, public_key: str) -> dict:
    if re.fullmatch(r'\d+\.\d+\.\d+', version) is None:
        raise ValueError('Version must be major.minor.patch')
    parsed = urlparse(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Installer download URL must use HTTPS')
    payload = installer.read_bytes()
    if not payload.startswith(b'MZ'):
        raise ValueError('Expected the final Windows installer executable')
    key = load_pem_private_key(private_key_file.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError('Expected an Ed25519 PEM private key')
    if key.public_key().public_bytes_raw() != base64.b64decode(public_key, validate=True):
        raise ValueError('Signing key does not match the configured Windows update public key; no feed generated')
    return {'version': version, 'url': url, 'arch': 'x86_64', 'size': len(payload),
            'signature': base64.b64encode(key.sign(payload)).decode('ascii')}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--installer', type=Path, required=True)
    parser.add_argument('--private-key-file', type=Path, required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--url', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source-config', type=Path,
                        default=Path(__file__).resolve().parents[1] / 'src/controller_config/assets/app-update-source.json')
    args = parser.parse_args()
    config = json.loads(args.source_config.read_text(encoding='utf-8'))
    result = build_feed(args.installer, args.private_key_file, args.version, args.url,
                        public_key=config['windows']['public_key'])
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
