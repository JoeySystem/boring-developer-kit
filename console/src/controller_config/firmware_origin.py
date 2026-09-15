"""Match device-reported identity to signed releases, not runtime attestation.

Keep signed manifests already encountered so replacing the online channel's latest
release does not turn an older official installation into an unknown build.
"""
from importlib.resources import files
import json
from PySide6.QtCore import QSettings
from controller_config.firmware_release import is_custom_firmware
from controller_config.firmware_signature import FirmwareSignatureError, verify_manifest_signature


def _identity(manifest):
    return tuple(manifest.get(k) for k in ('product_id', 'hardware_id', 'version', 'build_id'))


class FirmwareReleaseHistory:
    def __init__(self, settings=None, bundled=None):
        self.settings = settings if settings is not None else QSettings()
        self._releases = {}
        if bundled is None:
            bundled = json.loads(files('controller_config.assets').joinpath('official-firmware-releases.json').read_text(encoding='utf-8'))
        try:
            cached = json.loads(self.settings.value('firmware/official_releases', '[]', type=str))
        except (ValueError, TypeError):
            cached = []
        for manifest in [*bundled, *(cached if isinstance(cached, list) else [])]:
            try:
                self._accept(manifest)
            except FirmwareSignatureError:
                continue  # Unverifiable history confers no official status.

    def _accept(self, manifest):
        if not isinstance(manifest, dict):
            raise FirmwareSignatureError('发布记录格式无效')
        verify_manifest_signature(manifest)
        identity = _identity(manifest)
        if (not all(isinstance(v, str) and v for v in identity)
                or manifest.get('origin') == 'custom' or identity[-1].startswith('custom-')):
            raise FirmwareSignatureError('此记录不能用于识别官方固件')
        changed = self._releases.get(identity) != manifest
        self._releases[identity] = dict(manifest)
        return changed

    def remember(self, manifest):
        if not self._accept(manifest):
            return
        self.settings.setValue('firmware/official_releases', json.dumps(list(self._releases.values()), ensure_ascii=False))

    def classify(self, snapshot):
        if snapshot is None:
            return 'unknown'
        build = str(snapshot.versions.get('build_id', ''))
        if is_custom_firmware(snapshot):
            return 'custom'
        identity = (snapshot.identity.get('product_id'), snapshot.identity.get('hardware_id'),
                    snapshot.versions.get('firmware'), build)
        return 'official' if identity in self._releases else 'unknown'
