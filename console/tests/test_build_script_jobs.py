"""Nuitka must not take every core when the Console is packaged (11-19: load 26)."""
import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "build_macos.sh"


def test_both_nuitka_builds_use_the_job_limit_defaulting_to_three():
    script = SCRIPT.read_text(encoding="utf-8")
    assert "BUILD_JOBS=${BORING_CONSOLE_BUILD_JOBS:-3}" in script
    assert script.count('-m nuitka \\\n  --jobs="$BUILD_JOBS" \\\n') == 2
    assert script.count("-m nuitka") == 2


def test_a_bad_job_count_stops_before_anything_is_built():
    for value in ("0", "many", "-2"):
        result = subprocess.run(["sh", str(SCRIPT)], capture_output=True, text=True, timeout=30,
                                env={**os.environ, "BORING_CONSOLE_BUILD_JOBS": value})
        assert result.returncode == 2
        assert "BORING_CONSOLE_BUILD_JOBS" in result.stderr
