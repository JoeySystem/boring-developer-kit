#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
SOFTWARE_DIR=$(CDPATH= cd -- "$PROJECT_DIR/.." && pwd)
PYTHON="$PROJECT_DIR/.venv/bin/python"
# The public kit only builds the community app; it cannot produce an
# official-looking build.
BUILD_ORIGIN=custom
APP_NAME="BORING Console Community"
BUNDLE_ID="com.boring.console.community"
ARTIFACT_BASENAME="BORING-Console-Community-macOS-unsigned"
# Internal development package (not for release): BORING_CONSOLE_DEV_BUILD
# names what is under test. It gets its own app name and bundle
# id, an unmistakable version label and no in-app updates. Unset: unchanged.
# Parallel C compile jobs for Nuitka. Its default is every core, which pushed
# the Mac to a load of 26 on 11-19; 3 keeps room for tests and the desktop.
BUILD_JOBS=${BORING_CONSOLE_BUILD_JOBS:-3}
case "$BUILD_JOBS" in
  ''|*[!0-9]*|0) echo "BORING_CONSOLE_BUILD_JOBS must be a positive whole number" >&2; exit 2 ;;
esac
DEV_LABEL=${BORING_CONSOLE_DEV_BUILD:-}
DEV_DATE=""
BUILD_DIR="$PROJECT_DIR/build/macos/$BUILD_ORIGIN"
DIST_DIR="$PROJECT_DIR/dist/macos/$BUILD_ORIGIN"
if [ -n "$DEV_LABEL" ]; then
  DEV_DATE=${BORING_CONSOLE_DEV_DATE:-$(TZ=Asia/Taipei date +%Y%m%d)}
  APP_NAME="$APP_NAME Dev"
  BUNDLE_ID="$BUNDLE_ID.dev"
  ARTIFACT_BASENAME=$(printf '%s' "$ARTIFACT_BASENAME" | sed 's/-macOS-unsigned$//')"-Dev-macOS-$DEV_DATE"
  BUILD_DIR="$BUILD_DIR-dev"
  DIST_DIR="$DIST_DIR-dev"
fi
STAGE_ROOT=""
STAGE_BASE=""

cleanup_stage() {
  if [ -n "$STAGE_ROOT" ]; then
    case "$STAGE_ROOT" in
      "$STAGE_BASE"/boring-console-build.*) rm -rf -- "${STAGE_ROOT:?}" ;;
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
if ! "$PYTHON" -c 'from importlib.metadata import version; assert version("Nuitka") == "4.2.1"' >/dev/null 2>&1; then
  echo "Install Nuitka 4.2.1 in .venv before building" >&2
  exit 2
fi
case "$BUILD_DIR" in
  "$PROJECT_DIR"/build/macos/custom|"$PROJECT_DIR"/build/macos/custom-dev) ;;
  *) echo "Unexpected build directory: $BUILD_DIR" >&2; exit 2 ;;
esac
case "$DIST_DIR" in
  "$PROJECT_DIR"/dist/macos/custom|"$PROJECT_DIR"/dist/macos/custom-dev) ;;
  *) echo "Unexpected dist directory: $DIST_DIR" >&2; exit 2 ;;
esac
rm -rf -- "$BUILD_DIR" "$DIST_DIR"
mkdir -p "$BUILD_DIR/console" "$BUILD_DIR/runner" "$DIST_DIR"

# Files stored below macOS Documents may carry Finder/provenance metadata that
# codesign rejects inside an app bundle. Build from an isolated APFS clone and
# remove metadata only from that disposable copy; never mutate the workspace.
# Stage under the resolved temp path (/private/var/…, not /var/…). Nuitka
# compares DLL paths with sys.path textually; with a symlinked prefix the Qt
# frameworks that Qt plugins load were dropped as "system DLLs" and plugins
# kept @rpath references, so the app could not load Qt (2026-11-16).
STAGE_BASE=$(CDPATH= cd -- "${TMPDIR:-/tmp}" && pwd -P)
STAGE_ROOT=$(mktemp -d "$STAGE_BASE/boring-console-build.XXXXXX")
STAGE_PROJECT="$STAGE_ROOT/project"
STAGE_BUILD="$STAGE_ROOT/build"
STAGE_DIST="$STAGE_ROOT/dist"
mkdir -p "$STAGE_PROJECT" "$STAGE_BUILD/console" "$STAGE_BUILD/runner" "$STAGE_DIST"
cp -cR "$PROJECT_DIR/.venv" "$STAGE_PROJECT/.venv"
cp -cR "$PROJECT_DIR/src" "$STAGE_PROJECT/src"
cp -cR "$SOFTWARE_DIR/sdk" "$STAGE_PROJECT/sdk"
cp -cR "$PROJECT_DIR/deploy" "$STAGE_PROJECT/deploy"
# Finder metadata in Qt's QML tree is not a plugin. Nuitka otherwise probes it
# as a binary when collecting QML dependencies. Clean only the disposable copy.
find "$STAGE_PROJECT" -type f -name '.DS_Store' -delete
cp -cR "$PROJECT_DIR/tools" "$STAGE_PROJECT/tools"
xattr -cr "$STAGE_PROJECT"
STAGE_PYTHON="$STAGE_PROJECT/.venv/bin/python"
# Nuitka bundles the whole Qt QML tree and every library its plugins link,
# which brought in QtWebEngineCore (216 MB), QtPdf, Qt3D and friends that
# nothing loads. Keep only the QML modules the Console imports, in the
# disposable copy only (docs/macos-package-size-optimization.md).
STAGE_QML=$("$STAGE_PYTHON" -c 'import pathlib, PySide6; print(pathlib.Path(PySide6.__file__).resolve().parent / "Qt" / "qml")')
case "$STAGE_QML" in
  "$STAGE_PROJECT"/.venv/*) ;;
  *) echo "Refusing to prune QML outside the staging copy: $STAGE_QML" >&2; exit 2 ;;
esac
"$STAGE_PYTHON" "$STAGE_PROJECT/tools/prune_staged_qml.py" "$STAGE_QML"
if [ -n "$DEV_LABEL" ]; then
  PYTHONPATH="$STAGE_PROJECT/src" \
  "$STAGE_PYTHON" "$STAGE_PROJECT/tools/stage_app_build.py" \
    --assets "$STAGE_PROJECT/src/controller_config/assets" \
    --origin "$BUILD_ORIGIN" --dev-label "$DEV_LABEL" --dev-date "$DEV_DATE"
else
PYTHONPATH="$STAGE_PROJECT/src" \
"$STAGE_PYTHON" "$STAGE_PROJECT/tools/stage_app_build.py" \
  --assets "$STAGE_PROJECT/src/controller_config/assets" \
  --origin "$BUILD_ORIGIN"
fi
"$STAGE_PYTHON" "$STAGE_PROJECT/tools/stage_macos_updater.py"   --assets "$STAGE_PROJECT/src/controller_config/assets"
APP_VERSION=$(PYTHONPATH="$STAGE_PROJECT/src" "$STAGE_PYTHON" -c \
  'from controller_config import __version__; print(__version__)')

# The production marker is written here, never taken from the source tree (it
# is gitignored; a stray local copy would make the stager refuse). Drop any
# copy from the disposable staging tree, then validate the roots and write it.
rm -f -- "$STAGE_PROJECT/src/controller_config/assets/device-trust-policy.json"
PYTHONPATH="$STAGE_PROJECT/src" \
"$STAGE_PYTHON" "$STAGE_PROJECT/tools/stage_device_trust_policy.py" \
  "$STAGE_PROJECT/src/controller_config/assets/device-trust-roots.json" \
  "$STAGE_PROJECT/src/controller_config/assets/device-trust-policy.json"

# UI copy is loaded directly from the packaged JSON catalog.
PYTHONPATH="$STAGE_PROJECT/src" "$STAGE_PYTHON" -c \
  'from controller_config.text_catalog import get_text_catalog; get_text_catalog()'
PYTHONPATH="$STAGE_PROJECT/src" "$STAGE_PYTHON" -c \
  'from controller_config.app_release_summary import load_app_release_summary; load_app_release_summary("zh_CN")'

# Qt's PDF image-format plugin is the only user of QtPdf (7 MB); icon import
# accepts PNG/JPEG only (screen_icon.py). Excluding the plugin alone is not
# enough because Nuitka still follows its dependencies, so QtPdf is named too.

PYTHONPATH="$STAGE_PROJECT/src:$STAGE_PROJECT/sdk/python" \
"$STAGE_PYTHON" -m nuitka \
  --jobs="$BUILD_JOBS" \
  --standalone \
  --enable-plugin=pyside6 \
  --macos-create-app-bundle \
  --macos-app-name="BORING Console Community" \
  --macos-signed-app-name="com.boring.console.community" \
  --macos-app-version="$APP_VERSION" \
  --include-package=controller_config \
  --include-package=CoreBluetooth \
  --include-package=dispatch \
  --noinclude-dlls=PySide6/qt-plugins/imageformats/libqpdf.dylib \
  --noinclude-dlls=QtPdf \
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

# The Runner only talks to the Console over a blocking QLocalSocket, with no
# QApplication: none of the GUI, image or TLS Qt plugins are ever loaded.
PYTHONPATH="$STAGE_PROJECT/src:$STAGE_PROJECT/sdk/python" \
"$STAGE_PYTHON" -m nuitka \
  --jobs="$BUILD_JOBS" \
  --onefile \
  --enable-plugin=pyside6 \
  --noinclude-qt-plugins=iconengines,imageformats,platforms,styles,tls \
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
# The pinned Qt 6.11.1 macOS binaries require macOS 13 or later.
/usr/libexec/PlistBuddy -c "Add :LSMinimumSystemVersion string 13.0" \
  "$APP_TARGET/Contents/Info.plist"
if [ -n "$DEV_LABEL" ]; then
  /usr/libexec/PlistBuddy -c "Add :BORINGDevBuild string $DEV_LABEL · $DEV_DATE" \
    "$APP_TARGET/Contents/Info.plist"
fi
"$STAGE_PYTHON" "$STAGE_PROJECT/tools/stage_macos_updater.py"   --assets "$STAGE_PROJECT/src/controller_config/assets" --bundle "$APP_TARGET"
# Qt's standard buttons/dialogs use Qt catalogs, separate from our JSON copy.
QT_TRANSLATIONS=$("$STAGE_PYTHON" -c 'from PySide6.QtCore import QLibraryInfo; print(QLibraryInfo.path(QLibraryInfo.TranslationsPath))')
mkdir -p "$APP_TARGET/Contents/MacOS/PySide6/Qt/translations"
for language in zh_CN ja; do
  cp "$QT_TRANSLATIONS/qtbase_$language.qm" "$APP_TARGET/Contents/MacOS/PySide6/Qt/translations/"
done
xattr -cr "$APP_TARGET"
# A bundle whose Qt libraries cannot load must not be signed or shipped.
"$STAGE_PYTHON" "$STAGE_PROJECT/tools/check_macos_bundle.py" "$APP_TARGET"
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
  -volname "$APP_NAME" \
  -srcfolder "$APP_TARGET" \
  -ov -format UDZO \
  "$STAGE_DIST/$ARTIFACT_BASENAME.dmg"

if [ -n "${BORING_NOTARY_PROFILE:-}" ]; then
  xcrun notarytool submit "$STAGE_DIST/$ARTIFACT_BASENAME.dmg"     --keychain-profile "$BORING_NOTARY_PROFILE" --wait
  xcrun stapler staple "$STAGE_DIST/$ARTIFACT_BASENAME.dmg"
fi

mkdir -p "$DIST_DIR"
cp -- "$STAGE_DIST/$ARTIFACT_BASENAME.dmg" \
  "$DIST_DIR/$ARTIFACT_BASENAME.dmg"
if [ -n "$DEV_LABEL" ]; then
  # Internal testers also get the bare signed app next to the DMG.
  /usr/bin/ditto "$APP_TARGET" "$DIST_DIR/$APP_NAME.app"
  /usr/bin/codesign --verify --deep --strict "$DIST_DIR/$APP_NAME.app"
fi

echo "Built macOS artifacts in $DIST_DIR"
