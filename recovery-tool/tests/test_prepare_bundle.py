from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from provisioner import paths
from provisioner.release import ReleasePackage
from tests.helpers import make_ota_release


class PrepareBundleTest(unittest.TestCase):
    def test_explicit_package_does_not_use_another_worktrees_pointer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = make_ota_release(root / "approved")
            tool_root = Path(__file__).resolve().parents[1]
            script = tool_root / "packaging" / "macos" / "prepare_bundle.py"
            spec = importlib.util.spec_from_file_location("explicit_bundle_test", script)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            with (
                mock.patch.object(paths, "discover_source_authority_path",
                                  side_effect=AssertionError("stale checkout used")),
                mock.patch.object(sys, "argv", [
                    str(script), "--tool-root", str(tool_root),
                    "--output", str(root / "bundle"), "--authority-only",
                    "--authority", str(source),
                ]),
            ):
                self.assertEqual(0, module.main())
            meta = json.loads((root / "bundle/bundle-metadata.json").read_text())
            self.assertEqual("20260824.02-g1fafffab-dirty", meta["build_id"])

    def run_bundle(self, root, source, *extra):
        tool_root = Path(__file__).resolve().parents[1]
        script = tool_root / "packaging" / "macos" / "prepare_bundle.py"
        spec = importlib.util.spec_from_file_location("acceptance_bundle_test", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with mock.patch.object(sys, "argv", [
            str(script), "--tool-root", str(tool_root), "--output", str(root / "bundle"),
            "--authority-only", "--authority", str(source), *extra,
        ]):
            return module.main()

    def test_unverified_firmware_needs_an_explicit_acceptance_build(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = make_ota_release(root / "candidate")
            from tests.helpers import mark_release_unverified

            mark_release_unverified(source)
            with self.assertRaisesRegex(RuntimeError, "--acceptance-test"):
                self.run_bundle(root, source)
            self.assertEqual(0, self.run_bundle(root, source, "--acceptance-test"))
            pointer = json.loads((root / "bundle/bundled-authority.json").read_text())
            self.assertIs(pointer["acceptance_test"], True)

    def test_stages_current_source_authority_and_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            firmware_root = root / "software" / "firmware"
            source = make_ota_release(firmware_root / "releases")
            tools = firmware_root / "tools"
            provisioning = tools / "provisioning"
            provisioning.mkdir(parents=True)
            (provisioning / "__init__.py").write_text("# bundled factory tools\n")
            (provisioning / "production.py").write_text("# production orchestration\n")
            for filename in ("firmware_update.py", "firmware_manifest.py", "flash_common.py"):
                (tools / filename).write_text(f"# {filename}\n")
            fixture = root / "software" / "protocol" / "fixtures" / "device-auth-v1.json"
            fixture.parent.mkdir(parents=True)
            fixture.write_text('{"version":1}\n', encoding="utf-8")
            (firmware_root / "current-device.json").write_text(
                json.dumps({"package": f"releases/{source.name}"}),
                encoding="utf-8",
            )
            output = root / "bundle"
            tool_root = Path(__file__).resolve().parents[1]
            script = tool_root / "packaging" / "macos" / "prepare_bundle.py"
            spec = importlib.util.spec_from_file_location("prepare_bundle_test", script)
            module = importlib.util.module_from_spec(spec)
            assert spec.loader is not None
            spec.loader.exec_module(module)

            with (
                mock.patch.object(paths, "project_root", return_value=root),
                mock.patch.object(
                    sys,
                    "argv",
                    [
                        str(script),
                        "--tool-root",
                        str(tool_root),
                        "--output",
                        str(output),
                    ],
                ),
            ):
                self.assertEqual(0, module.main())

            authority = json.loads(
                (output / "bundled-authority.json").read_text(encoding="utf-8")
            )
            metadata = json.loads(
                (output / "bundle-metadata.json").read_text(encoding="utf-8")
            )
            staged = output / authority["relative_path"]
            with ReleasePackage.open(staged) as package:
                self.assertEqual("matrix12-power-v2-test", package.release_id)
                self.assertEqual("20260824.02-g1fafffab-dirty", package.build_id)
                self.assertIsNotNone(package.ota_application)

            self.assertEqual(authority["release_id"], metadata["release_id"])
            self.assertEqual(authority["relative_path"], metadata["relative_path"])
            # The public kit never bundles factory tooling or identity fixtures.
            self.assertFalse((output / "factory-tools").exists())
            self.assertFalse((output / "protocol").exists())

    def test_authority_only_bundle_excludes_factory_identity_material(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            firmware_root = root / "software" / "firmware"
            source = make_ota_release(firmware_root / "releases")
            (firmware_root / "current-device.json").write_text(
                json.dumps({"package": f"releases/{source.name}"}),
                encoding="utf-8",
            )
            output = root / "bundle"
            tool_root = Path(__file__).resolve().parents[1]
            script = tool_root / "packaging" / "macos" / "prepare_bundle.py"
            spec = importlib.util.spec_from_file_location("authority_bundle_test", script)
            module = importlib.util.module_from_spec(spec)
            assert spec.loader is not None
            spec.loader.exec_module(module)

            with (
                mock.patch.object(paths, "project_root", return_value=root),
                mock.patch.object(
                    sys,
                    "argv",
                    [
                        str(script),
                        "--tool-root",
                        str(tool_root),
                        "--output",
                        str(output),
                        "--authority-only",
                    ],
                ),
            ):
                self.assertEqual(0, module.main())

            self.assertTrue((output / "bundled-authority.json").is_file())
            self.assertTrue((output / "releases").is_dir())
            self.assertFalse((output / "factory-tools").exists())
            self.assertFalse((output / "protocol").exists())


if __name__ == "__main__":
    unittest.main()
