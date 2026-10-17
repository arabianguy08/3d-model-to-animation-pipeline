#!/bin/sh
set -eu
client=auto
project=.
blender=
skill_dir=
managed=
no_skill=
source_url=https://github.com/arabianguy08/companion-animation-pipeline/archive/refs/tags/v0.1.1.zip
while [ "$#" -gt 0 ]; do
    case "$1" in
        --client) client=$2; shift 2 ;;
        --project) project=$2; shift 2 ;;
        --blender) blender=$2; shift 2 ;;
        --skill-dir) skill_dir=$2; shift 2 ;;
        --source) source_url=$2; shift 2 ;;
        --managed-blender) managed=1; shift ;;
        --no-skill) no_skill=1; shift ;;
        *) echo "Unknown installer argument: $1" >&2; exit 1 ;;
    esac
done
if command -v uv >/dev/null 2>&1; then
    uv_path=$(command -v uv)
else
    bootstrap_home=${COMPANION_ANIMATION_HOME:-"$HOME/.local/share/companion-animation"}
    export UV_INSTALL_DIR="$bootstrap_home/tools"
    export UV_NO_MODIFY_PATH=1
    installer=$(mktemp)
    trap 'rm -f "$installer"' EXIT HUP INT TERM
    curl -LsSf https://astral.sh/uv/0.9.7/install.sh -o "$installer"
    sh "$installer"
    uv_path="$UV_INSTALL_DIR/uv"
fi
"$uv_path" python install 3.12 --no-bin
"$uv_path" tool install --python 3.12 --force "$source_url"
tool_bin=$("$uv_path" tool dir --bin)
set -- setup --client "$client" --project "$project"
[ -z "$blender" ] || set -- "$@" --blender "$blender"
[ -z "$skill_dir" ] || set -- "$@" --skill-dir "$skill_dir"
[ -z "$managed" ] || set -- "$@" --managed-blender
[ -z "$no_skill" ] || set -- "$@" --no-skill
"$tool_bin/companion-animation" "$@"
printf '\nInstalled. Runner: %s\nRestart your AI client to load the MCP server and skill.\n' "$tool_bin/companion-animation"
