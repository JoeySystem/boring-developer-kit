import json
from pathlib import Path
import struct
import tempfile
import unittest

import sys

TOOLS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS_ROOT))

import firmware_manifest


def test_image(payload: bytes = b"firmware") -> bytes:
    image = bytearray(firmware_manifest.CUSTOM_DESC_OFFSET + firmware_manifest.WMP_DESCRIPTOR.size)
    image[0] = firmware_manifest.ESP_IMAGE_MAGIC
    struct.pack_into(
        "<I", image, firmware_manifest.APP_DESC_OFFSET,
        firmware_manifest.ESP_APP_DESC_MAGIC,
    )
    image[firmware_manifest.APP_DESC_OFFSET + 16 : firmware_manifest.APP_DESC_OFFSET + 48] = (
        b"0.3.0-alpha.1\0".ljust(32, b"\0")
    )
    image[firmware_manifest.APP_DESC_OFFSET + 48 : firmware_manifest.APP_DESC_OFFSET + 80] = (
        b"wired_macro_pad\0".ljust(32, b"\0")
    )
    firmware_manifest.WMP_DESCRIPTOR.pack_into(
        image,
        firmware_manifest.CUSTOM_DESC_OFFSET,
        firmware_manifest.WMP_DESCRIPTOR_MAGIC,
        firmware_manifest.WMP_DESCRIPTOR_VERSION,
        firmware_manifest.WMP_DESCRIPTOR.size,
        b"wired-macro-pad-v1\0",
        b"WMP-S3-MATRIX12-V1\0",
    )
    return bytes(image) + payload


class FirmwareManifestTest(unittest.TestCase):
    def test_build_and_load_manifest_verify_image(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "wired_macro_pad.bin"
            image.write_bytes(test_image())
            manifest = firmware_manifest.build_manifest(image)
            manifest_path = root / "firmware-manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            loaded, loaded_image = firmware_manifest.load_manifest(manifest_path)
            self.assertEqual(manifest, loaded)
            self.assertEqual(image, loaded_image)
            self.assertEqual(64, len(loaded["sha256"]))

    def test_build_metadata_round_trips_with_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "wmp-matrix12-power-v2-codex-usb-v0.3.0-alpha.1-g12345678.bin"
            image.write_bytes(test_image())
            manifest = firmware_manifest.build_manifest(
                image,
                build_target="matrix12-power-v2-codex-usb",
                build_id="20260818.01-g12345678-dirty",
                git_commit="1234567890abcdef",
                git_dirty=True,
                validation_state="built",
                built_at="2026-08-18T12:00:00Z",
            )
            manifest_path = root / "firmware-manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            loaded, _image = firmware_manifest.load_manifest(manifest_path)
            self.assertEqual("matrix12-power-v2-codex-usb", loaded["build_target"])
            self.assertEqual("20260818.01-g12345678-dirty", loaded["build_id"])
            self.assertTrue(loaded["git_dirty"])
            self.assertEqual("built", loaded["validation_state"])

    def test_changed_image_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "wired_macro_pad.bin"
            image.write_bytes(test_image(b"before"))
            manifest = firmware_manifest.build_manifest(image)
            manifest_path = root / "firmware-manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            image.write_bytes(test_image(b"damage"))

            with self.assertRaisesRegex(
                firmware_manifest.ManifestError, "SHA-256"
            ):
                firmware_manifest.load_manifest(manifest_path)

    def test_image_path_cannot_escape_package_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "wired_macro_pad.bin"
            image.write_bytes(test_image())
            manifest = firmware_manifest.build_manifest(image)
            manifest["image"] = "../wired_macro_pad.bin"
            with self.assertRaisesRegex(firmware_manifest.ManifestError, "filename"):
                firmware_manifest.validate_manifest(
                    manifest, root / "firmware-manifest.json"
                )

    def test_unsafe_version_is_rejected(self):
        with self.assertRaisesRegex(firmware_manifest.ManifestError, "ASCII"):
            firmware_manifest._validate_version('bad"version')


if __name__ == "__main__":
    unittest.main()
