from __future__ import annotations

import json
from pathlib import Path
import struct
import tempfile
import unittest
import zipfile

from provisioner.errors import PackageError
from provisioner.release import ReleasePackage
from provisioner.summaries import package_fingerprint
from tests.helpers import make_native_ota_release, make_ota_release, make_release, sha256


class ReleasePackageTest(unittest.TestCase):
    def test_validated_package_exposes_locked_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            package = ReleasePackage.open(make_release(Path(directory)))

            self.assertEqual("usb-codex-stable-test", package.release_id)
            self.assertEqual("rev-a-codex-usb", package.target)
            self.assertEqual("v6.0.2", package.esp_idf)
            self.assertEqual("303A", package.runtime_usb.vid)
            self.assertEqual("8360", package.runtime_usb.pid)
            self.assertEqual(
                [0x0, 0x8000, 0x10000],
                [image.offset for image in package.flash_images],
            )
            self.assertTrue(all(image.path.is_file() for image in package.flash_images))

    def test_tampered_image_is_rejected_before_flash(self):
        with tempfile.TemporaryDirectory() as directory:
            release = make_release(Path(directory))
            (release / "wired_macro_pad.bin").write_bytes(b"tampered")

            with self.assertRaisesRegex(PackageError, "SHA-256"):
                ReleasePackage.open(release)

    def test_unsafe_rollback_contract_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            release = make_release(Path(directory))
            manifest_path = release / "release-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["rollback"]["erase_flash"] = True
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(PackageError, "erase_flash"):
                ReleasePackage.open(release)

    def test_application_cannot_cross_partition_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            release = make_release(Path(directory))
            application = release / "wired_macro_pad.bin"
            application.write_bytes(b"x" * (0x200000 + 1))
            manifest_path = release / "release-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["files"]["wired_macro_pad.bin"]["sha256"] = sha256(
                application.read_bytes()
            )
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(PackageError, "覆盖 NVS"):
                ReleasePackage.open(release)

    def test_partition_table_cannot_declare_overlapping_app_and_nvs(self):
        with tempfile.TemporaryDirectory() as directory:
            release = make_release(Path(directory))
            partition_path = release / "partition_table/partition-table.bin"
            table = bytearray(partition_path.read_bytes())
            struct.pack_into("<L", table, 8, 0x220000)
            partition_path.write_bytes(table)
            manifest_path = release / "release-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["files"]["partition_table/partition-table.bin"]["sha256"] = sha256(
                partition_path.read_bytes()
            )
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(PackageError, "分区重叠"):
                ReleasePackage.open(release)

    def test_runtime_usb_ids_are_validated_while_opening_package(self):
        with tempfile.TemporaryDirectory() as directory:
            release = make_release(Path(directory))
            manifest_path = release / "release-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["runtime_usb"]["vid"] = "not-hex"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(PackageError, "十六进制"):
                ReleasePackage.open(release)

    def test_trusted_release_id_requires_built_in_archive_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            release = make_release(Path(directory))
            manifest_path = release / "release-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["release_id"] = "usb-codex-stable-2026-07-30"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(PackageError, "可信 SHA-256"):
                ReleasePackage.open(release)

    def test_fingerprint_covers_all_manifest_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            release = make_release(Path(directory))
            with ReleasePackage.open(release) as package:
                before = package_fingerprint(package)

            manifest_path = release / "release-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["target"] = "other-target"
            manifest["runtime_usb"]["pid"] = "4005"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with ReleasePackage.open(release) as package:
                after = package_fingerprint(package)

            self.assertNotEqual(before, after)

    def test_zip_package_is_supported_and_path_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release = make_release(root)
            safe_zip = root / "safe.zip"
            with zipfile.ZipFile(safe_zip, "w") as archive:
                for path in release.rglob("*"):
                    if path.is_file():
                        archive.write(path, Path(release.name) / path.relative_to(release))

            with ReleasePackage.open(safe_zip) as package:
                self.assertEqual("usb-codex-stable-test", package.release_id)
                self.assertFalse(package.archive_checksum_verified)

            sidecar = Path(str(safe_zip) + ".sha256")
            sidecar.write_text(
                sha256(safe_zip.read_bytes()) + "  safe.zip\n",
                encoding="utf-8",
            )
            with ReleasePackage.open(safe_zip) as package:
                self.assertTrue(package.archive_checksum_verified)

            unsafe_zip = root / "unsafe.zip"
            with zipfile.ZipFile(unsafe_zip, "w") as archive:
                archive.writestr("../outside.bin", b"unsafe")
                archive.writestr("release-manifest.json", "{}")
            with self.assertRaisesRegex(PackageError, "unsafe"):
                ReleasePackage.open(unsafe_zip)

    def test_directory_package_is_snapshotted_before_later_source_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            release = make_release(Path(directory))
            with ReleasePackage.open(release) as package:
                expected = package.application.path.read_bytes()
                (release / "wired_macro_pad.bin").write_bytes(b"replaced after validation")
                self.assertEqual(expected, package.application.path.read_bytes())

    def test_power_v2_package_exposes_ota_metadata_and_four_boot_images(self):
        with tempfile.TemporaryDirectory() as directory:
            with ReleasePackage.open(make_ota_release(Path(directory))) as package:
                self.assertEqual(
                    "WMP-S3-MATRIX12-POWER-V2", package.hardware_id
                )
                self.assertEqual(
                    "20260824.02-g1fafffab-dirty", package.build_id
                )
                self.assertEqual("sample-verified", package.validation_state)
                self.assertEqual(
                    [0x0, 0x8000, 0x10000, 0x240000],
                    [image.offset for image in package.flash_images],
                )
                self.assertEqual(0x240000, package.ota_data.offset)
                self.assertEqual(
                    "0.3.0-alpha.1", package.ota_application.version
                )
                self.assertEqual(package.application.sha256, package.ota_application.sha256)

    def test_current_native_power_v2_directory_needs_no_release_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            release = make_native_ota_release(Path(directory))
            self.assertFalse((release / "release-manifest.json").exists())

            with ReleasePackage.open(release) as package:
                self.assertEqual(release.name, package.release_id)
                self.assertEqual("matrix12-power-v2-codex-usb", package.target)
                self.assertIsNone(package.esp_idf)
                self.assertEqual("303A", package.runtime_usb.vid)
                self.assertEqual("8360", package.runtime_usb.pid)
                self.assertEqual(
                    [0x0, 0x8000, 0x10000, 0x240000],
                    [image.offset for image in package.flash_images],
                )
                self.assertEqual(
                    "20260824.02-g1fafffab-dirty", package.ota_application.build_id
                )

    def test_ota_package_requires_otadata_image(self):
        with tempfile.TemporaryDirectory() as directory:
            release = make_ota_release(Path(directory))
            manifest_path = release / "release-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            del manifest["files"]["ota_data_initial.bin"]
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(PackageError, "0x240000"):
                ReleasePackage.open(release)

    def test_boot_and_ota_application_must_be_the_same_image(self):
        with tempfile.TemporaryDirectory() as directory:
            release = make_ota_release(Path(directory))
            ota_image = release / "power-v2.bin"
            ota_image.write_bytes(b"different-ota-image")
            ota_manifest_path = release / "firmware-manifest.json"
            ota_manifest = json.loads(ota_manifest_path.read_text(encoding="utf-8"))
            ota_manifest["size"] = ota_image.stat().st_size
            ota_manifest["sha256"] = sha256(ota_image.read_bytes())
            ota_manifest_path.write_text(json.dumps(ota_manifest), encoding="utf-8")
            release_manifest_path = release / "release-manifest.json"
            release_manifest = json.loads(
                release_manifest_path.read_text(encoding="utf-8")
            )
            release_manifest["files"]["power-v2.bin"]["sha256"] = sha256(
                ota_image.read_bytes()
            )
            release_manifest["files"]["firmware-manifest.json"]["sha256"] = sha256(
                ota_manifest_path.read_bytes()
            )
            release_manifest_path.write_text(
                json.dumps(release_manifest), encoding="utf-8"
            )

            with self.assertRaisesRegex(PackageError, "BOOT.*OTA"):
                ReleasePackage.open(release)

    def test_ota_manifest_identity_must_match_release(self):
        with tempfile.TemporaryDirectory() as directory:
            release = make_ota_release(Path(directory))
            ota_manifest_path = release / "firmware-manifest.json"
            ota_manifest = json.loads(ota_manifest_path.read_text(encoding="utf-8"))
            ota_manifest["build_id"] = "other-build"
            ota_manifest_path.write_text(json.dumps(ota_manifest), encoding="utf-8")
            release_manifest_path = release / "release-manifest.json"
            release_manifest = json.loads(
                release_manifest_path.read_text(encoding="utf-8")
            )
            release_manifest["files"]["firmware-manifest.json"]["sha256"] = sha256(
                ota_manifest_path.read_bytes()
            )
            release_manifest_path.write_text(
                json.dumps(release_manifest), encoding="utf-8"
            )

            with self.assertRaisesRegex(PackageError, "build_id"):
                ReleasePackage.open(release)


if __name__ == "__main__":
    unittest.main()
