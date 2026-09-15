"""Build offline developer and extension ZIPs; does not publish or touch devices."""

from __future__ import annotations

import argparse
import io
from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parents[1]
VERSION = "v0.1.0-preview.8"
EXAMPLES = ("observe_prompt", "claim_prompt", "propose_mapping")
ROOT_FILES = (
    "README.md", "START-HERE.md", "LICENSE", "NOTICE", "LICENSING.md",
    "THIRD-PARTY-NOTICES.md", "SUPPORT.md", "CONTRIBUTING.md", "CHANGELOG.md",
    "requirements-dev.txt",
)
DIRECTORIES = ("firmware", "console", "sdk", "examples", "protocol", "docs", "tests", "tools")


def source_files(directory: Path):
    for path in sorted(directory.rglob("*")):
        parts = path.relative_to(directory).parts
        if any(part in {"__pycache__", ".pytest_cache", ".venv", "build", "dist", "output", "packages", "artifacts", "managed_components"}
               or part.endswith(".egg-info") for part in parts):
            continue
        if path.is_file() and path.name not in {".DS_Store", "sdkconfig", "sdkconfig.old"} and path.suffix not in {".pyc", ".pyo"}:
            yield path


def build(output: Path) -> list[Path]:
    output.mkdir(parents=True, exist_ok=True)
    extension_bytes = {}
    results = []
    for name in EXAMPLES:
        directory = ROOT / "examples" / "extensions" / name
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in source_files(directory):
                archive.write(path, path.relative_to(directory).as_posix())
        extension_bytes[name] = data.getvalue()
        path = output / f"{name}.zip"
        path.write_bytes(extension_bytes[name])
        results.append(path)
    bundle = output / f"BORING-Developer-Kit-{VERSION}.zip"
    prefix = f"BORING-Developer-Kit-{VERSION}/"
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in ROOT_FILES:
            archive.write(ROOT / name, prefix + name)
        for name in DIRECTORIES:
            for path in source_files(ROOT / name):
                archive.write(path, prefix + path.relative_to(ROOT).as_posix())
        for name, data in extension_bytes.items():
            archive.writestr(prefix + f"examples/packages/{name}.zip", data)
    return [*results, bundle]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    for artifact in build(args.output.resolve()):
        print(artifact)
