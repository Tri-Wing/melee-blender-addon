#!/usr/bin/env bash
# Launch Blender with this checkout's add-on enabled. All arguments go to Blender.
# Examples:
#   ./scripts/launch_blender.sh
#   ./scripts/launch_blender.sh /path/to/stage.blend
#   BLENDER_BIN=/path/to/blender ./scripts/launch_blender.sh
set -euo pipefail

melee_launcher_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
melee_blender_bin="${BLENDER_BIN:-blender}"
if ! command -v "$melee_blender_bin" >/dev/null 2>&1; then
    printf 'Blender not found: %s. Set BLENDER_BIN to its executable path.\n' "$melee_blender_bin" >&2
    exit 1
fi

export MELEEMAP_REPO="$(dirname -- "$melee_launcher_dir")"
# Load any requested .blend before enabling the development add-on.
exec "$melee_blender_bin" "$@" --python "$melee_launcher_dir/load_blender_addon.py"
