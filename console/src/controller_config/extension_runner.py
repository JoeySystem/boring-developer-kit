from __future__ import annotations

import argparse
import runpy
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path


RUNNER_BASENAME = "boring-extension-runner"


@dataclass(frozen=True)
class ExtensionRunnerCommand:
    """Program and fixed arguments used to enter the private runner."""

    program: str
    arguments: tuple[str, ...] = ()

    def for_script(self, script: Path) -> list[str]:
        return [*self.arguments, "--script", str(script)]


def private_extension_runner_command() -> ExtensionRunnerCommand:
    """Resolve the development runner or the runner shipped beside a frozen app."""

    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        suffix = ".exe" if sys.platform == "win32" else ""
        runner = Path(sys.executable).with_name(f"{RUNNER_BASENAME}{suffix}")
        return ExtensionRunnerCommand(str(runner))
    return ExtensionRunnerCommand(
        sys.executable,
        ("-m", "controller_config.extension_runner"),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="BORING private extension runner")
    parser.add_argument("--script", required=True, help="本地纯 Python 扩展入口")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    script = Path(args.script).expanduser().resolve()
    if not script.is_file():
        print(f"BORING extension entry does not exist: {script}", file=sys.stderr)
        return 2

    original_argv = sys.argv
    original_path = list(sys.path)
    sys.argv = [str(script)]
    sys.path.insert(0, str(script.parent))
    development_sdk = Path(__file__).resolve().parents[2] / "sdk" / "python"
    if development_sdk.is_dir():
        sys.path.insert(0, str(development_sdk))
    try:
        import boring_console_sdk  # noqa: F401 - bundled public SDK for extensions

        runpy.run_path(str(script), run_name="__main__")
    except SystemExit as exc:
        if exc.code is None:
            return 0
        if isinstance(exc.code, int):
            return exc.code
        print(str(exc.code), file=sys.stderr)
        return 1
    except Exception:  # noqa: BLE001 - runner must report extension failures
        traceback.print_exc()
        return 1
    finally:
        sys.argv = original_argv
        sys.path[:] = original_path
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
