"""Safe-mode texts fit the round MIST screen.

Found on 68DE8 (2026-11-11): "SAFE MODE / RESTART=EXIT / 1+12=DOWNLOAD"
was cut at the circle edge and "=" (not in the MIST font) showed as "?".
This replays the firmware's per-row width limit (add_overlay_at in
mist_glyph_pages.c) with the font table from mist_glyph_assets.c and checks
every safe-mode text row.
"""

import re
import unittest
from pathlib import Path

from test_lighting_preview_protocol import c_function


ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "components/board/mist_glyph_assets.c"
PAGES = ROOT / "components/board/mist_glyph_pages.c"
ACTION = ROOT / "components/action_engine/action_engine.c"
APP_MAIN = ROOT / "main/app_main.c"


def font():
    source = ASSETS.read_text()
    table = source[source.index("mist_glyph_font["):]
    table = table[:table.index("};")]
    return {c: int(w) for c, w in re.findall(r"\{\s*'(.)'\s*,\s*(\d+)", table)}


def columns(text, widths):
    total = 0
    for ch in text:
        total += widths[ch] + (1 if total > 0 else 0)
    return total


def row_limit(y):
    """add_overlay_at(): usable columns of the text row starting at y."""
    distance = max(abs(2 * y - 127), abs(2 * (y + 13) - 127))
    half = 0
    while half < 62 and 4 * (half + 1) ** 2 + distance ** 2 <= 125 * 125:
        half += 1
    available = min(64 - (64 - half + 3), (64 + half - 3) - 64)
    return available * 2 // 3 if available > 0 else 1


def local_text_calls(source):
    return re.findall(r'board_display_show_local\(\s*"([^"]*)",\s*"([^"]*)",\s*"([^"]*)",\s*"([^"]*)"',
                      source)


class SafeModeScreenTextTests(unittest.TestCase):
    def setUp(self):
        self.widths = font()
        build = c_function(PAGES.read_text(), "static void build_local_text(")
        self.rows = [int(y) for y in re.findall(r",\s*(\d+),\s*gray_level", build)]
        self.assertEqual(4, len(self.rows))
        overlay = c_function(PAGES.read_text(), "static void add_overlay_at(")
        self.assertIn("4 * (half_width + 1) * (half_width + 1) +", overlay)
        self.assertIn("available * 2 / 3", overlay)

    def check(self, texts):
        for text, y in zip(texts, self.rows):
            with self.subTest(text=text):
                self.assertTrue(text, "an empty row renders as '?'")
                for ch in text:
                    self.assertIn(ch, self.widths, f"{ch!r} is not in the MIST font")
                self.assertLessEqual(columns(text, self.widths), row_limit(y))

    def test_no_safe_mode_wording_for_users(self):
        # users never see "safe mode"; DOWNLOAD stays (after-sales).
        show = c_function(ACTION.read_text(), "static void show_status(const char *hint)")
        boot = c_function(APP_MAIN.read_text(), "static void boot_combo_confirm(boot_combo_t *combo)")
        for texts in local_text_calls(show) + local_text_calls(boot):
            self.assertFalse(any("SAFE" in text for text in texts), texts)

    def test_safe_mode_home(self):
        show = c_function(ACTION.read_text(), "static void show_status(const char *hint)")
        calls = local_text_calls(show)
        self.assertEqual(1, len(calls))
        self.check(calls[0])

    def test_boot_and_download_prompts(self):
        source = APP_MAIN.read_text()
        for name in ("static void boot_combo_confirm(boot_combo_t *combo)",
                     "static bool poll_safe_mode_download(boot_combo_t *combo)"):
            calls = local_text_calls(c_function(source, name))
            self.assertEqual(1, len(calls), name)
            self.check(calls[0])


if __name__ == "__main__":
    unittest.main()
