from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from provisioner import paths


class ReleaseDiscoveryTest(unittest.TestCase):
    def test_current_device_pointer_adds_power_v2_package(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            firmware = root / "software" / "firmware"
            package = firmware / "artifacts" / "power-v2-current"
            package.mkdir(parents=True)
            (package / "firmware-manifest.json").write_text("{}", encoding="utf-8")
            (firmware / "current-device.json").write_text(
                json.dumps({"package": "artifacts/power-v2-current"}),
                encoding="utf-8",
            )

            with (
                mock.patch.object(paths, "project_root", return_value=root),
                mock.patch.object(paths, "data_root", return_value=root / "data"),
            ):
                discovered = paths.discover_release_paths()
                current = paths.discover_current_device_paths()

            self.assertIn(package.resolve(), discovered)
            self.assertEqual({package.resolve()}, current)

    def test_current_device_pointer_accepts_sample_verified_release(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            firmware = root / "software" / "firmware"
            package = firmware / "releases" / "power-v2-sample-verified"
            package.mkdir(parents=True)
            (package / "firmware-manifest.json").write_text("{}", encoding="utf-8")
            (firmware / "current-device.json").write_text(
                json.dumps({"package": "releases/power-v2-sample-verified"}),
                encoding="utf-8",
            )

            with (
                mock.patch.object(paths, "project_root", return_value=root),
                mock.patch.object(paths, "data_root", return_value=root / "data"),
            ):
                discovered = paths.discover_release_paths()

            self.assertIn(package.resolve(), discovered)

    def test_current_device_pointer_outside_artifacts_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            firmware = root / "software" / "firmware"
            outside = root / "not-an-artifact"
            firmware.mkdir(parents=True)
            outside.mkdir()
            (outside / "firmware-manifest.json").write_text("{}", encoding="utf-8")
            (firmware / "current-device.json").write_text(
                json.dumps({"package": "../../../not-an-artifact"}),
                encoding="utf-8",
            )

            with (
                mock.patch.object(paths, "project_root", return_value=root),
                mock.patch.object(paths, "data_root", return_value=root / "data"),
            ):
                discovered = paths.discover_release_paths()

            self.assertNotIn(outside.resolve(), discovered)

    def test_root_release_uploads_are_not_production_auto_discovered(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "release_uploads" / "usb-codex-stable-2026-07-30.zip"
            archive.parent.mkdir()
            archive.write_bytes(b"historical Rev A archive")

            with (
                mock.patch.object(paths, "project_root", return_value=root),
                mock.patch.object(paths, "data_root", return_value=root / "data"),
            ):
                discovered = paths.discover_release_paths()

            self.assertNotIn(archive.resolve(), discovered)

    def test_managed_authority_overrides_source_current_device(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data"
            managed = data / "releases" / "new-authority.zip"
            managed.parent.mkdir(parents=True)
            managed.write_bytes(b"managed package")
            (data / "authority.json").write_text(
                json.dumps({"managed_file": managed.name}),
                encoding="utf-8",
            )

            with (
                mock.patch.object(paths, "project_root", return_value=root),
                mock.patch.object(paths, "data_root", return_value=data),
            ):
                current = paths.discover_current_device_paths()

            self.assertEqual({managed.resolve()}, current)

    def test_production_authority_keeps_source_current_device_despite_managed_flash_choice(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            firmware = root / "software" / "firmware"
            source = firmware / "releases" / "production-current"
            source.mkdir(parents=True)
            (source / "firmware-manifest.json").write_text("{}", encoding="utf-8")
            (firmware / "current-device.json").write_text(
                json.dumps({"package": "releases/production-current"}),
                encoding="utf-8",
            )
            data = root / "data"
            managed = data / "releases" / "operator-flash-choice.zip"
            managed.parent.mkdir(parents=True)
            managed.write_bytes(b"managed package")
            (data / "authority.json").write_text(
                json.dumps({"managed_file": managed.name}),
                encoding="utf-8",
            )

            with (
                mock.patch.object(paths, "project_root", return_value=root),
                mock.patch.object(paths, "data_root", return_value=data),
            ):
                production = paths.discover_production_authority_path()
                discovered = paths.discover_release_paths()

            self.assertEqual(source.resolve(), production)
            self.assertIn(source.resolve(), discovered)

    def test_worktree_authority_wins_over_main_workspace_pointer(self):
        with tempfile.TemporaryDirectory() as directory:
            container = Path(directory)
            main = container / "project"
            worktree = main / ".worktrees" / "factory-tool"
            worktree_package = (
                worktree / "software" / "firmware" / "releases" / "worktree-current"
            )
            main_package = main / "software" / "firmware" / "releases" / "main-current"
            for package in (worktree_package, main_package):
                package.mkdir(parents=True)
                (package / "firmware-manifest.json").write_text("{}", encoding="utf-8")
                (package.parents[1] / "current-device.json").write_text(
                    json.dumps({"package": f"releases/{package.name}"}),
                    encoding="utf-8",
                )

            with (
                mock.patch.object(paths, "project_root", return_value=worktree),
                mock.patch.object(paths, "data_root", return_value=container / "data"),
            ):
                current = paths.discover_current_device_paths()

            self.assertEqual({worktree_package.resolve()}, current)

    def test_bundled_authority_is_used_without_source_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            resources = root / "resources"
            bundled = resources / "releases" / "factory-current"
            bundled.mkdir(parents=True)
            (resources / "bundled-authority.json").write_text(
                json.dumps({"relative_path": "releases/factory-current"}),
                encoding="utf-8",
            )

            with (
                mock.patch.object(paths, "project_root", return_value=root / "empty"),
                mock.patch.object(paths, "resource_root", return_value=resources),
                mock.patch.object(paths, "data_root", return_value=root / "data"),
            ):
                current = paths.discover_current_device_paths()

            self.assertEqual({bundled.resolve()}, current)

    def test_bundled_authority_allows_pyinstaller_resource_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            resources = root / "Contents" / "Resources"
            frameworks = root / "Contents" / "Frameworks"
            bundled = resources / "releases" / "factory-current"
            bundled.mkdir(parents=True)
            frameworks.mkdir(parents=True)
            (resources / "bundled-authority.json").write_text(
                json.dumps({"relative_path": "releases/factory-current"}),
                encoding="utf-8",
            )
            (frameworks / "bundled-authority.json").symlink_to(
                Path("../Resources/bundled-authority.json")
            )
            (frameworks / "releases").symlink_to(Path("../Resources/releases"))

            with mock.patch.object(paths, "resource_root", return_value=frameworks):
                selected = paths._bundled_authority_path()

            self.assertEqual(bundled.resolve(), selected)


if __name__ == "__main__":
    unittest.main()
