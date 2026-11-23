#!/usr/bin/env bash
set -euo pipefail

tool_root="$(cd "$(dirname "$0")/.." && pwd)"
build_root="$tool_root/build/macos-authority-updater"
dist_root="$tool_root/dist"
resources="$build_root/resources"
venv="$build_root/venv"
authority_path="${1:-}"
build_python="${BORING_BUILD_PYTHON:-$venv/bin/python}"

mkdir -p "$build_root" "$dist_root"
if [[ -z "${BORING_BUILD_PYTHON:-}" && ! -x "$build_python" ]]; then
  python3 -m venv "$venv"
fi
if ! "$build_python" -c 'import serial, webview, PyInstaller' 2>/dev/null; then
  "$build_python" -m pip install --disable-pip-version-check \
    -r "$tool_root/requirements-authority-update.txt" \
    -r "$tool_root/requirements-build.txt"
fi

if [[ -z "$authority_path" ]]; then
  echo "usage: $0 /path/to/extracted-firmware-package" >&2
  exit 2
fi
authority_path="$(cd "$authority_path" && pwd)"
rm -rf "$resources"
"$build_python" "$tool_root/packaging/macos/prepare_bundle.py" \
  --tool-root "$tool_root" --output "$resources" --authority-only \
  --authority "$authority_path"

build_id="$($build_python -c 'import json,sys; print(json.load(open(sys.argv[1]))["build_id"])' "$resources/bundle-metadata.json")"
arch="$(uname -m)"
package_name="BORING-MIST-${build_id}-macOS-${arch}"
delivery="$build_root/$package_name"
rm -rf "$delivery"
mkdir -p "$delivery"

"$build_python" "$tool_root/packaging/make_icon.py"
"$build_python" -m PyInstaller --noconfirm --clean --onedir --windowed \
  --name "BORING-MIST-BLE-Recovery" --distpath "$delivery" \
  --workpath "$build_root/pyinstaller" --specpath "$build_root" \
  --hidden-import serial.tools.list_ports \
  --hidden-import webview.platforms.cocoa \
  --icon "$tool_root/packaging/icons/recovery-icon.icns" \
  --add-data "$tool_root/static/authority-update.html:static" \
  --add-data "$tool_root/static/authority-update.css:static" \
  --add-data "$tool_root/static/authority-update.js:static" \
  --add-data "$resources/releases:releases" \
  --add-data "$resources/bundled-authority.json:." \
  "$tool_root/authority_update_app.py"

app="$delivery/BORING-MIST-BLE-Recovery.app"
codesign --force --deep --sign - "$app"
sed "s/@BUILD_ID@/$build_id/g" "$tool_root/packaging/macos/README-template.txt" \
  > "$delivery/READ-ME-FIRST.txt"
official_zip=("$authority_path"/online/Firmware-BORING-*.zip)
[[ ${#official_zip[@]} -eq 1 && -f "${official_zip[0]}" ]]
cp "${official_zip[0]}" "$delivery/"

archive="$dist_root/$package_name.zip"
rm -f "$archive"
ditto -c -k --sequesterRsrc --keepParent "$delivery" "$archive"
printf 'Created %s\n' "$archive"
