from pathlib import Path
import unittest

STATIC = Path(__file__).resolve().parents[1] / "static"


class FirmwareGuidanceTest(unittest.TestCase):
    def test_page_tells_the_operator_when_to_keep_or_replug_the_cable(self):
        script = (STATIC / "authority-update.js").read_text(encoding="utf-8")
        page = (STATIC / "authority-update.html").read_text(encoding="utf-8")
        self.assertIn('job.stage === "replug"', script)
        self.assertIn("请拔下 USB 线，等 2 秒再插回", script)
        self.assertIn("不需要重新烧录", script)
        for stage in ("transfer", "readback", "replug", "slots"):
            self.assertIn(f"{stage}: {{", script)
        self.assertIn('id="firmwareCalloutText"', page)
        self.assertIn('id="failedLog"', page)


if __name__ == "__main__":
    unittest.main()
