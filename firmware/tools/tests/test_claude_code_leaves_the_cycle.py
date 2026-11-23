"""boring-D46 step one: Claude Code leaves the double-click mode cycle.

Claude is shown through the shared desktop status source in Normal mode; the
Claude Code mode code stays until step two so Console 0.2.x still works.
"""
import re
import unittest
from pathlib import Path

FIRMWARE = Path(__file__).resolve().parents[2]
COMPONENTS = FIRMWARE / "components"


class ClaudeCodeLeavesTheCycleTests(unittest.TestCase):
    def test_capability_reports_claude_code_mode_off(self):
        source = (COMPONENTS / "config_protocol/config_protocol.c").read_text()
        self.assertIn('cJSON_AddBoolToObject(features, "claude_code_mode", false);', source)
        # Console 0.2.x greys the tab out on false; the status source keeps its flag.
        self.assertIn('"claude_code_status"', source)

    def test_glyph_catalog_keeps_the_claude_code_mode_row(self):
        """Glyphs are stored in NVS by index (g%02u): removing a row shifts the rest."""
        source = (COMPONENTS / "screen_icon_store/screen_glyph_store.c").read_text()
        body = source[source.index("screen_glyph_catalog[SCREEN_GLYPH_COUNT]"):]
        body = body[: body.index("};")]
        names = re.findall(r'\{"([a-z_0-9]+)",\d+,\d+,(?:true|false)\}', body)
        self.assertEqual(len(names), 28)
        self.assertEqual(names[24], "claude_code_mode")
        self.assertEqual(names[25:28], ["usb", "bluetooth", "battery"])

    def test_claude_code_mode_code_is_still_compiled_for_step_two(self):
        mode = (COMPONENTS / "codex_micro/include/codex_micro_mode.h").read_text()
        self.assertIn("CODEX_MICRO_MODE_CLAUDE_CODE", mode)
        self.assertIn("claude_code_supported", mode)
        self.assertTrue((COMPONENTS / "codex_micro/codex_micro_mode.c").exists())


if __name__ == "__main__":
    unittest.main()
