#!/bin/zsh
set -euo pipefail
cd "${0:A:h}"
app_name='BORING MIST 恢复工具'
if [[ -z "${BUILD_PYTHON:-}" ]]; then
  python3 -m venv .venv-build
  BUILD_PYTHON="$PWD/.venv-build/bin/python"
  "$BUILD_PYTHON" -m pip install -r requirements.txt -r requirements-build.txt
fi
mkdir -p build dist
"$BUILD_PYTHON" packaging/make_icon.py build/AppIcon.iconset
iconutil -c icns build/AppIcon.iconset -o build/AppIcon.icns
"$BUILD_PYTHON" -m PyInstaller --noconfirm --clean --windowed \
 --name "$app_name" --osx-bundle-identifier com.boring.mist-recovery \
 --icon "$PWD/build/AppIcon.icns" --distpath dist --workpath build/pyinstaller --specpath build \
 --hidden-import serial.tools.list_ports --collect-all cryptography \
 --add-data "$PWD/static:static" --add-data "$PWD/releases:releases" \
 --add-data "$PWD/licenses:licenses" --add-data "$PWD/LICENSE:." --add-data "$PWD/NOTICE:." \
 --add-data "$PWD/THIRD-PARTY-NOTICES.md:." --add-data "$PWD/firmware-signing.json:." authority_update_app.py
codesign --force --deep --sign - "dist/$app_name.app"
codesign --verify --deep --strict "dist/$app_name.app"
mkdir -p build/dmg
# The staging directory is owned solely by this build script.
ditto "dist/$app_name.app" "build/dmg/$app_name.app"
[[ -L build/dmg/Applications ]] || ln -s /Applications build/dmg/Applications
cp README.md build/dmg/使用说明.md
cp AI-RECOVERY-PROMPT.md TROUBLESHOOTING.md LICENSE NOTICE THIRD-PARTY-NOTICES.md build/dmg/
ditto licenses build/dmg/licenses
hdiutil create -volname "$app_name" -srcfolder build/dmg -ov -format UDZO \
 "dist/BORING-MIST-Recovery-20261007.01-$(uname -m).dmg"
