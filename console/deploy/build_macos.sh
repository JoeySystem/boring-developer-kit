#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
SOFTWARE_DIR=$(CDPATH= cd -- "$PROJECT_DIR/.." && pwd)
PYTHON="$PROJECT_DIR/.venv/bin/python"
BUILD_DIR="$PROJECT_DIR/build/macos"
DIST_DIR="$PROJECT_DIR/dist/macos"
STAGE_ROOT=""

cleanup_stage() {
  if [ -n "$STAGE_ROOT" ]; then
    case "$STAGE_ROOT" in
      "${TMPDIR:-/tmp}"/boring-console-build.*) rm -rf -- "${STAGE_ROOT:?}" ;;
      *) echo "Refusing to remove unexpected staging directory: $STAGE_ROOT" >&2 ;;
    esac
  fi
}
trap cleanup_stage EXIT HUP INT TERM

if [ "$(uname -s)" != "Darwin" ]; then
  echo "macOS package must be built on macOS" >&2
  exit 2
fi
if [ ! -x "$PYTHON" ]; then
  echo "Missing project Python: $PYTHON" >&2
  exit 2
fi
if ! "$PYTHON" -c 'import nuitka' >/dev/null 2>&1; then
  echo "Nuitka is not installed in .venv; install the packaging dependency first" >&2
  exit 2
fi
case "$BUILD_DIR" in
  "$PROJECT_DIR"/build/macos) ;;
  *) echo "Unexpected build directory: $BUILD_DIR" >&2; exit 2 ;;
esac
case "$DIST_DIR" in
  "$PROJECT_DIR"/dist/macos) ;;
  *) echo "Unexpected dist directory: $DIST_DIR" >&2; exit 2 ;;
esac
rm -rf -- "$BUILD_DIR" "$DIST_DIR"
mkdir -p "$BUILD_DIR/console" "$BUILD_DIR/runner" "$DIST_DIR"

# Files stored below macOS Documents may carry Finder/provenance metadata that
# codesign rejects inside an app bundle. Build from an isolated APFS clone and
# remove metadata only from that disposable copy; never mutate the workspace.
STAGE_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/boring-console-build.XXXXXX")
STAGE_PROJECT="$STAGE_ROOT/project"
STAGE_BUILD="$STAGE_ROOT/build"
STAGE_DIST="$STAGE_ROOT/dist"
mkdir -p "$STAGE_PROJECT" "$STAGE_BUILD/console" "$STAGE_BUILD/runner" "$STAGE_DIST"
cp -cR "$PROJECT_DIR/.venv" "$STAGE_PROJECT/.venv"
cp -cR "$PROJECT_DIR/src" "$STAGE_PROJECT/src"
cp -cR "$SOFTWARE_DIR/sdk" "$STAGE_PROJECT/sdk"
cp -cR "$PROJECT_DIR/deploy" "$STAGE_PROJECT/deploy"
cp -cR "$PROJECT_DIR/tools" "$STAGE_PROJECT/tools"
xattr -cr "$STAGE_PROJECT"
STAGE_PYTHON="$STAGE_PROJECT/.venv/bin/python"
"$STAGE_PYTHON" "$STAGE_PROJECT/tools/stage_macos_updater.py"   --assets "$STAGE_PROJECT/src/controller_config/assets"
APP_VERSION=$(PYTHONPATH="$STAGE_PROJECT/src" "$STAGE_PYTHON" -c \
  'from controller_config import __version__; print(__version__)')

PYTHONPATH="$STAGE_PROJECT/src" \
"$STAGE_PYTHON" "$STAGE_PROJECT/tools/stage_device_trust_policy.py" \
  "$STAGE_PROJECT/src/controller_config/assets/device-trust-roots.json" \
  "$STAGE_PROJECT/src/controller_config/assets/device-trust-policy.json"

# UI copy is loaded directly from the packaged JSON catalog.
PYTHONPATH="$STAGE_PROJECT/src" "$STAGE_PYTHON" -c \
  'from controller_config.text_catalog import get_text_catalog; get_text_catalog()'

PYTHONPATH="$STAGE_PROJECT/src:$STAGE_PROJECT/sdk/python" \
"$STAGE_PYTHON" -m nuitka \
  --standalone \
  --enable-plugin=pyside6 \
  --macos-create-app-bundle \
  --macos-app-name="BORING Console Community" \
  --macos-signed-app-name="com.boring.console.community" \
  --macos-app-version="$APP_VERSION" \
  --include-package=controller_config \
  --include-package=CoreBluetooth \
  --include-data-dir="$STAGE_PROJECT/src/controller_config/assets=controller_config/assets" \
  --include-data-dir="$STAGE_PROJECT/src/controller_config/translations=controller_config/translations" \
  --output-dir="$STAGE_BUILD/console" \
  "$STAGE_PROJECT/deploy/console_entry.py"

APP_SOURCE=$(find "$STAGE_BUILD/console" -maxdepth 1 -type d -name '*.app' -print)
if [ -z "$APP_SOURCE" ] || [ "$(printf '%s\n' "$APP_SOURCE" | wc -l | tr -d ' ')" -ne 1 ]; then
  echo "Expected exactly one macOS app bundle" >&2
  exit 2
fi
APP_TARGET="$STAGE_DIST/BORING Console Community.app"
mv -- "$APP_SOURCE" "$APP_TARGET"

PYTHONPATH="$STAGE_PROJECT/src:$STAGE_PROJECT/sdk/python" \
"$STAGE_PYTHON" -m nuitka \
  --onefile \
  --enable-plugin=pyside6 \
  --include-module=controller_config.extension_runner \
  --include-package=boring_console_sdk \
  --output-filename=boring-extension-runner \
  --output-dir="$STAGE_BUILD/runner" \
  "$STAGE_PROJECT/deploy/runner_entry.py"

RUNNER_SOURCE="$STAGE_BUILD/runner/boring-extension-runner"
if [ ! -x "$RUNNER_SOURCE" ]; then
  echo "Private extension Runner was not built" >&2
  exit 2
fi
cp -- "$RUNNER_SOURCE" "$APP_TARGET/Contents/MacOS/boring-extension-runner"
PYTHONPATH="$STAGE_PROJECT/src" \
"$STAGE_PYTHON" "$STAGE_PROJECT/tools/stage_protocol_resources.py" \
  "$SOFTWARE_DIR/protocol" "$APP_TARGET/Contents/Resources/protocol"
/usr/libexec/PlistBuddy -c \
  "Add :NSBluetoothAlwaysUsageDescription string BORING Console Community uses Bluetooth to authenticate and configure your paired BORING device." \
  "$APP_TARGET/Contents/Info.plist"
"$STAGE_PYTHON" "$STAGE_PROJECT/tools/stage_macos_updater.py"   --assets "$STAGE_PROJECT/src/controller_config/assets" --bundle "$APP_TARGET"
# Qt's standard buttons/dialogs use Qt catalogs, separate from our JSON copy.
QT_TRANSLATIONS=$("$STAGE_PYTHON" -c 'from PySide6.QtCore import QLibraryInfo; print(QLibraryInfo.path(QLibraryInfo.TranslationsPath))')
mkdir -p "$APP_TARGET/Contents/MacOS/PySide6/Qt/translations"
for language in zh_CN ja; do
  cp "$QT_TRANSLATIONS/qtbase_$language.qm" "$APP_TARGET/Contents/MacOS/PySide6/Qt/translations/"
done
xattr -cr "$APP_TARGET"
for language in zh_CN ja; do
  /usr/bin/codesign --force -s "${BORING_MACOS_SIGN_IDENTITY:--}" \
    "$APP_TARGET/Contents/MacOS/PySide6/Qt/translations/qtbase_$language.qm"
done
if [ -n "${BORING_MACOS_SIGN_IDENTITY:-}" ]; then
  # Sign nested Sparkle helpers first, retaining their declared entitlements.
  if [ -d "$APP_TARGET/Contents/Frameworks/Sparkle.framework" ]; then
    find "$APP_TARGET/Contents/Frameworks/Sparkle.framework/Versions/B"       -depth \( -name '*.xpc' -o -name '*.app' \) -exec       /usr/bin/codesign --force --options runtime --timestamp       --preserve-metadata=entitlements -s "$BORING_MACOS_SIGN_IDENTITY" {} \;
    /usr/bin/codesign --force --options runtime --timestamp       -s "$BORING_MACOS_SIGN_IDENTITY" "$APP_TARGET/Contents/Frameworks/Sparkle.framework"
  fi
  /usr/bin/codesign --force --deep --options runtime --timestamp \
    --preserve-metadata=entitlements -s "$BORING_MACOS_SIGN_IDENTITY" "$APP_TARGET"
  # Apply PyObjC JIT permission only to the host executable, not Sparkle helpers.
  /usr/bin/codesign --force --options runtime --timestamp \
    --entitlements "$STAGE_PROJECT/deploy/macos-entitlements.plist" \
    -s "$BORING_MACOS_SIGN_IDENTITY" "$APP_TARGET"
else
  /usr/bin/codesign -s - --force --deep "$APP_TARGET"
fi
/usr/bin/codesign --verify --deep --strict "$APP_TARGET"

hdiutil create \
  -volname "BORING Console Community" \
  -srcfolder "$APP_TARGET" \
  -ov -format UDZO \
  "$STAGE_DIST/BORING-Console-Community-macOS-unsigned.dmg"

if [ -n "${BORING_NOTARY_PROFILE:-}" ]; then
  xcrun notarytool submit "$STAGE_DIST/BORING-Console-Community-macOS-unsigned.dmg"     --keychain-profile "$BORING_NOTARY_PROFILE" --wait
  xcrun stapler staple "$STAGE_DIST/BORING-Console-Community-macOS-unsigned.dmg"
fi

cp -- "$STAGE_DIST/BORING-Console-Community-macOS-unsigned.dmg" \
  "$DIST_DIR/BORING-Console-Community-macOS-unsigned.dmg"

echo "Built macOS artifacts in $DIST_DIR"
