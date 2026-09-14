#!/bin/sh
set -eu

board_target=devkit
validation_state=built
custom_name=local
official_build=false
custom_selected=false
while [ "$#" -gt 0 ]; do
    case "$1" in
        --target|--custom-name)
            [ "$#" -ge 2 ] || { echo "missing value for $1" >&2; exit 2; }
            if [ "$1" = "--target" ]; then board_target=$2; else custom_name=$2; custom_selected=true; fi
            shift 2
            ;;
        --official-build) official_build=true; shift ;;
        *) echo "usage: $0 [--target TARGET] [--custom-name NAME | --official-build]" >&2; exit 2 ;;
    esac
done
if [ "$official_build" = true ]; then
    [ "$custom_selected" = false ] || { echo "--official-build cannot be combined with --custom-name" >&2; exit 2; }
    custom_name=
elif [ -z "$custom_name" ]; then
    echo "custom name cannot be empty" >&2; exit 2
fi
if [ -n "$custom_name" ]; then
    case "$custom_name" in *[!a-zA-Z0-9_-]*|?????????????????*)
        echo "custom name must use 1-16 ASCII letters, digits, underscore or dash" >&2; exit 2 ;;
    esac
fi

case "$board_target" in
    devkit)
        sdkconfig_defaults='sdkconfig.defaults;sdkconfig.flash-16mb.defaults'
        ;;
    rev-a)
        sdkconfig_defaults='sdkconfig.defaults;sdkconfig.flash-16mb.defaults;sdkconfig.rev-a.defaults'
        ;;
    rev-a-codex-ble)
        sdkconfig_defaults='sdkconfig.defaults;sdkconfig.flash-16mb.defaults;sdkconfig.rev-a.defaults;sdkconfig.codex-ble.defaults'
        ;;
    rev-a-codex-usb)
        sdkconfig_defaults='sdkconfig.defaults;sdkconfig.flash-16mb.defaults;sdkconfig.rev-a.defaults;sdkconfig.codex-ble.defaults;sdkconfig.codex-usb.defaults'
        ;;
    matrix12-v1)
        sdkconfig_defaults='sdkconfig.defaults;sdkconfig.matrix12-v1.defaults'
        ;;
    matrix12-v1-codex-ble)
        sdkconfig_defaults='sdkconfig.defaults;sdkconfig.matrix12-v1.defaults;sdkconfig.codex-ble.defaults'
        ;;
    matrix12-v1-codex-usb)
        sdkconfig_defaults='sdkconfig.defaults;sdkconfig.matrix12-v1.defaults;sdkconfig.codex-ble.defaults;sdkconfig.codex-usb.defaults'
        ;;
    matrix12-power-v2)
        sdkconfig_defaults='sdkconfig.defaults;sdkconfig.matrix12-power-v2.defaults'
        ;;
    matrix12-power-v2-codex-ble)
        sdkconfig_defaults='sdkconfig.defaults;sdkconfig.matrix12-power-v2.defaults;sdkconfig.codex-ble.defaults'
        ;;
    matrix12-power-v2-codex-usb)
        sdkconfig_defaults='sdkconfig.defaults;sdkconfig.matrix12-power-v2.defaults;sdkconfig.codex-ble.defaults;sdkconfig.codex-usb.defaults'
        ;;
    *)
        echo "unknown board target: $board_target" >&2
        exit 2
        ;;
esac

command -v idf.py >/dev/null 2>&1 || {
    echo "idf.py is not available; activate ESP-IDF 6.0.2 first" >&2
    exit 1
}
command -v rsync >/dev/null 2>&1 || {
    echo "rsync is required for the ASCII staging build" >&2
    exit 1
}

firmware_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
software_root=$(dirname -- "$firmware_root")
firmware_version=$(tr -d '[:space:]' < "$firmware_root/version.txt")
if git_commit=$(git -C "$firmware_root" rev-parse HEAD 2>/dev/null); then
    git_short_commit=$(git -C "$firmware_root" rev-parse --short=8 HEAD)
    if [ -n "$(git -C "$firmware_root" status --porcelain --untracked-files=normal)" ]; then
        git_dirty=true
        dirty_suffix=-dirty
    else
        git_dirty=false
        dirty_suffix=
    fi
    build_revision="g$git_short_commit$dirty_suffix"
else
    [ "$official_build" = false ] || { echo "Official candidates require a Git checkout" >&2; exit 2; }
    git_commit=unavailable
    git_dirty=true
    build_revision=nogit
fi
build_date=$(date -u +%Y%m%d)
artifact_parent="$firmware_root/artifacts/v$firmware_version/$board_target"
if [ -n "$custom_name" ]; then artifact_parent="$artifact_parent/custom-$custom_name"; fi
highest_sequence=0
for existing_package in "$artifact_parent/"*"$build_date."*-*; do
    [ -e "$existing_package" ] || continue
    existing_name=${existing_package##*/}
    existing_sequence=${existing_name##*"$build_date."}
    existing_sequence=${existing_sequence%%-*}
    case "$existing_sequence" in
        ''|*[!0-9]*) continue ;;
    esac
    existing_sequence=$(expr "$existing_sequence" + 0)
    if [ "$existing_sequence" -gt "$highest_sequence" ]; then
        highest_sequence=$existing_sequence
    fi
done
build_sequence=$((highest_sequence + 1))
sequence=$(printf '%02d' "$build_sequence")
build_id="$build_date.$sequence-$build_revision"
if [ -n "$custom_name" ]; then build_id="custom-$custom_name-$build_id"; fi
artifact_dir="$artifact_parent/$build_id-$validation_state"
built_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
release_image="wmp-$board_target-v$firmware_version-$build_id.bin"
stage_root=$(mktemp -d /tmp/wmp-firmware-build.XXXXXX)
trap 'rm -rf -- "$stage_root"' EXIT HUP INT TERM

mkdir -p "$stage_root/software"
rsync -a \
    --exclude artifacts \
    --exclude build \
    --exclude docs \
    --exclude managed_components \
    --exclude sdkconfig \
    --exclude sdkconfig.old \
    "$firmware_root/" "$stage_root/software/firmware/"
rsync -a "$software_root/protocol/" "$stage_root/software/protocol/"

(
    cd "$stage_root/software/firmware"
    idf.py -D "SDKCONFIG_DEFAULTS=$sdkconfig_defaults" -D "WMP_BUILD_ID=$build_id" set-target esp32s3
    idf.py -D "SDKCONFIG_DEFAULTS=$sdkconfig_defaults" -D "WMP_BUILD_ID=$build_id" build
)

mkdir -p "$artifact_dir/bootloader" "$artifact_dir/partition_table"
cp "$stage_root/software/firmware/build/wired_macro_pad.bin" "$artifact_dir/$release_image"
cp "$stage_root/software/firmware/build/wired_macro_pad.bin" "$artifact_dir/wired_macro_pad.bin"
cp "$stage_root/software/firmware/build/wired_macro_pad.elf" "$artifact_dir/wired_macro_pad.elf"
cp "$stage_root/software/firmware/build/bootloader/bootloader.bin" "$artifact_dir/bootloader/"
cp "$stage_root/software/firmware/build/partition_table/partition-table.bin" "$artifact_dir/partition_table/"
cp "$stage_root/software/firmware/build/ota_data_initial.bin" "$artifact_dir/"
cp "$stage_root/software/firmware/build/flash_args" "$artifact_dir/"
python3 "$firmware_root/tools/firmware_manifest.py" \
    --image "$artifact_dir/$release_image" \
    --output "$artifact_dir/firmware-manifest.json" \
    --build-target "$board_target" \
    --build-id "$build_id" \
    --git-commit "$git_commit" \
    --git-dirty "$git_dirty" \
    --validation-state "$validation_state" \
    --built-at "$built_at"

echo "Board target: $board_target"
echo "Firmware version: $firmware_version"
echo "Build ID: $build_id"
echo "Validation state: $validation_state"
echo "Build artifacts: $artifact_dir"

if [ -n "$custom_name" ]; then
    python3 "$firmware_root/tools/pack_custom_firmware.py" \
        --manifest "$artifact_dir/firmware-manifest.json" \
        --output "$artifact_dir/$build_id.zip"
fi
