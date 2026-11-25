from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest import mock

from provisioner import single_instance


@unittest.skipIf(sys.platform == "win32", "POSIX lock file path")
class PosixSingleInstanceTest(unittest.TestCase):
    def test_second_instance_is_refused_until_first_releases(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(
            os.environ, {"CODEX_MICRO_PROVISIONER_DATA": directory}
        ):
            first = single_instance.acquire_single_instance()
            self.assertIsNotNone(first)
            self.assertIsNone(single_instance.acquire_single_instance())
            first.release()
            again = single_instance.acquire_single_instance()
            self.assertIsNotNone(again)
            again.release()


@unittest.skipUnless(sys.platform == "win32", "Windows named mutex")
class WindowsSingleInstanceTest(unittest.TestCase):
    def test_second_instance_is_refused_until_first_releases(self):
        first = single_instance.acquire_single_instance()
        self.assertIsNotNone(first)
        self.assertIsNone(single_instance.acquire_single_instance())
        first.release()
        again = single_instance.acquire_single_instance()
        self.assertIsNotNone(again)
        again.release()


class AppEntryTest(unittest.TestCase):
    def test_second_launch_notifies_and_exits_without_starting_server(self):
        import authority_update_app as app

        with mock.patch.object(app, "acquire_single_instance", return_value=None), mock.patch.object(
            app, "notify_already_running"
        ) as notify, mock.patch.object(app, "create_authority_update_server") as create:
            self.assertEqual(app.main(["--no-browser"]), 1)

        notify.assert_called_once_with()
        create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
