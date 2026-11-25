"""Guard: hard-coded visual values in Console UI code may only go down.

Every colour, font size, corner radius and inline stylesheet written directly
in UI code is one more place to edit when the design changes (审查报告：
Console 全界面视觉统一与模块化审查，第 1 节). The style foundation plan moves
these values into one token module step by step. This test keeps the count
from creeping back up in the meantime:

- a file may not exceed its ceiling below;
- when a file drops below its ceiling, lower the ceiling to the new count in
  the same change, so the table always records the current state;
- a UI file that is not listed may not contain any of these literals.

Never raise a ceiling to make this pass. If a new value is really needed, it
belongs in the token module (style/tokens.py, once it exists) and the design
owner (the designer) agrees on it first.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "src" / "controller_config"

PATTERNS = {
    "hex": r"#[0-9A-Fa-f]{6}\b|#[0-9A-Fa-f]{3}\b(?![0-9A-Fa-f])",
    "rgba": r"rgba?\(\s*\d+\s*,\s*\d+\s*,\s*\d+",
    "qcolor_rgb": r"QColor\(\s*\d+\s*,\s*\d+\s*,\s*\d+",
    "font_size": r"font-size:\s*[\d.]+\s*(?:px|pt)",
    "radius": r"border-(?:top-|bottom-)?(?:left-|right-)?radius:\s*\d+px",
    "inline_style": r"\.setStyleSheet\(",
}

# Device renderings carry physical material colours, not UI values; the device
# view is being redrawn as a flat top view, so they are left alone for now.
EXEMPT = {
    "views/device_silhouette.py",
    "views/boring_mist_3d.py",
}

# Counts at the start of the style foundation work (整合版 84e70797, 2026-11-18),
# lowered by step 2b (colour tokens in style/tokens.py, settings wall included)
# step 3a (overridden declarations removed from the old APP_STYLE layers)
# step 3b (selectors no widget can match removed) and step 3c (old layers moved
# to style/sheets.py with LEGACY_* colour tokens), then by the progress ring
# (进度条点阵样式 定稿 v1: the old QProgressBar rules and the menu bar quota bar went),
# then by the shared dialog shell (B v0.4) replacing the voice, AI setup and onboarding shells.
CEILINGS = {
    "appearance.py": {"radius": 24},
    "background_helper.py": {"hex": 1, "inline_style": 3},
    "onboarding/dialog.py": {"inline_style": 1},
    "views/action_editor.py": {"font_size": 3, "inline_style": 3},
    "views/actions.py": {"hex": 3, "radius": 1, "inline_style": 1},
    "views/ai_setup.py": {"radius": 1, "inline_style": 1},
    "views/automation.py": {"inline_style": 1},
    "views/connection_terminal.py": {"hex": 6, "font_size": 1, "inline_style": 2},
    "views/extensions.py": {"inline_style": 1},
    "views/main_window.py": {
        "hex": 6, "font_size": 3, "inline_style": 8,
    },
    # The Mode page scales with the window (Mode页 定稿 v1): its sizes follow the scale,
    # so one helper sets its stylesheets; every colour in them is a token.
    "views/mode_page.py": {"inline_style": 1},
    # Moved out of main_window.py unchanged with the pages (规划第 11 节).
    "views/pages/common.py": {"hex": 1},
    "views/pages/firmware.py": {"hex": 1, "font_size": 1, "inline_style": 3},
    "views/pages/state.py": {"inline_style": 1},
    "views/preferences_editor.py": {"hex": 3, "inline_style": 2},
    "views/prompt_library_editor.py": {"inline_style": 2},
    "views/screen_glyph_editor.py": {"hex": 1, "qcolor_rgb": 1},
    "views/settings_wall.py": {"font_size": 13, "radius": 2, "inline_style": 5},
    "views/v4_widgets.py": {"qcolor_rgb": 1},
    # The dialog shell styles itself only when it has no parent window to inherit from.
    "views/ui/dialog.py": {"inline_style": 1},
    "views/voice_practice.py": {"hex": 5, "radius": 2, "inline_style": 3},
    "views/workflows.py": {"hex": 1, "rgba": 3, "radius": 1, "inline_style": 2},
}


def _ui_files() -> list[str]:
    files = [*ROOT.glob("views/*.py"), *ROOT.glob("views/ui/*.py"), *ROOT.glob("views/pages/*.py"),
             *ROOT.glob("onboarding/*.py"),
             ROOT / "appearance.py", ROOT / "background_helper.py"]
    return sorted(path.relative_to(ROOT).as_posix() for path in files)


def _counts(relative: str) -> dict[str, int]:
    text = (ROOT / relative).read_text(encoding="utf-8")
    counts = {name: len(re.findall(pattern, text)) for name, pattern in PATTERNS.items()}
    return {name: count for name, count in counts.items() if count}


@pytest.mark.parametrize("relative", [name for name in _ui_files() if name not in EXEMPT])
def test_hard_coded_style_values_only_go_down(relative: str) -> None:
    actual = _counts(relative)
    ceiling = CEILINGS.get(relative, {})
    grown = {name: (count, ceiling.get(name, 0)) for name, count in actual.items()
             if count > ceiling.get(name, 0)}
    assert not grown, (
        f"{relative} 新增了写死的样式值（实际, 上限）：{grown}。"
        "请改用变量或已有组件，不要调高上限。"
    )
    shrunk = {name: (actual.get(name, 0), limit) for name, limit in ceiling.items()
              if actual.get(name, 0) < limit}
    assert not shrunk, (
        f"{relative} 写死的样式值变少了（实际, 上限）：{shrunk}。"
        "好事：请把 tests/test_style_literals.py 里这个文件的上限下调到实际值。"
    )


def test_ceiling_table_only_lists_existing_ui_files() -> None:
    stale = sorted(set(CEILINGS) - set(_ui_files()))
    assert not stale, f"这些文件已不存在，请从上限表里删掉：{stale}"
    overlap = sorted(set(CEILINGS) & EXEMPT)
    assert not overlap, f"豁免文件不该出现在上限表里：{overlap}"
