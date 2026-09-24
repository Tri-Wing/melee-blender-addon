#!/usr/bin/env bash
# Launch Blender with this checkout's add-on enabled. An optional first DAT
# argument is imported after loading the add-on; other arguments go to Blender.
# Examples:
#   ./scripts/launch_blender.sh
#   ./scripts/launch_blender.sh /path/to/stage.blend
#   ./scripts/launch_blender.sh /path/to/GrNLa.dat
#   BLENDER_BIN=/path/to/blender ./scripts/launch_blender.sh
set -euo pipefail

melee_launcher_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
melee_blender_bin="${BLENDER_BIN:-blender}"
if ! command -v "$melee_blender_bin" >/dev/null 2>&1; then
    printf 'Blender not found: %s. Set BLENDER_BIN to its executable path.\n' "$melee_blender_bin" >&2
    exit 1
fi

export MELEEMAP_REPO="$(dirname -- "$melee_launcher_dir")"
export PYTHONPATH="$MELEEMAP_REPO/blender_addon${PYTHONPATH:+:$PYTHONPATH}"
export MELEEMAP_IMPORT_DAT=""
if [[ ${1:-} == *.[dD][aA][tT] ]]; then
    if [[ ! -f "$1" ]]; then
        printf 'Stage DAT not found: %s\n' "$1" >&2
        exit 1
    fi
    MELEEMAP_IMPORT_DAT="$(cd -- "$(dirname -- "$1")" && pwd)/$(basename -- "$1")"
    shift
fi
# Load any requested .blend before enabling the development add-on.
exec "$melee_blender_bin" --python-use-system-env "$@" --python "$melee_launcher_dir/load_blender_addon.py"
