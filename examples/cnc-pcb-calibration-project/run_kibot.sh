#!/usr/bin/env bash
# Runs the KiBot pipeline locally in the same docker image as the CI.
# Linux / macOS / WSL / Git Bash. On Windows PowerShell use run_kibot.ps1.
#
# USAGE
#   ./run_kibot.sh [kibot_launch.sh OPTIONS]   Generate the outputs (see ./run_kibot.sh --help)
#   ./run_kibot.sh --shell                     Open an interactive shell in the container
#   ./run_kibot.sh --serve [PORT]              Browse the outputs on http://localhost:PORT
#   ./run_kibot.sh --setup-libs                Add/update the shared libraries of
#                                              kibot_settings.yaml as git submodules
#   ./run_kibot.sh --update-libs               Update the libraries to the latest
#                                              commit of their branch
#   ./run_kibot.sh --check-libs                Fail if a library is missing (used by the CI)
#   ./run_kibot.sh --diagrams [--fit]          Export Diagrams/*.drawio and import them in
#                                              the schematic sheets (automatic before a
#                                              generation when they changed, auto_diagrams)
#
# The image is read from kibot_settings.yaml (docker_image), it can be
# overridden with the KIBOT_IMAGE environment variable.

set -e
cd "$(dirname "$0")"
project_dir="$(pwd)"
settings_file="kibot_settings.yaml"

# Read a `key: value` entry from kibot_settings.yaml
get_setting() {
    sed -n "s/^$1:[[:space:]]*\([^#[:space:]]*\).*/\1/p" "$settings_file" | head -n1 | sed "s/^[\"']//; s/[\"']\$//"
}

# Shared libraries: lib_<VAR>_url / lib_<VAR>_path / lib_<VAR>_branch
lib_vars() {
    sed -nE 's/^lib_([A-Za-z0-9_]*)_(url|path):.*/\1/p' "$settings_file" | awk '!seen[$0]++'
}
lib_path() {
    local path url
    path="$(get_setting "lib_$1_path")"
    if [[ -z "$path" ]]; then
        url="$(get_setting "lib_$1_url")"
        path="lib/$(basename "${url%.git}")"
    fi
    echo "${path#./}"
}
# Name of the git submodule using a path (empty if none)
submodule_name() {
    [[ -f .gitmodules ]] || return 0
    git config -f .gitmodules --get-regexp '^submodule\..*\.path$' | awk -v p="$1" '$2 == p { n=$1; sub(/^submodule\./, "", n); sub(/\.path$/, "", n); print n }'
}

setup_libs() {
    local update="$1" var url path branch name current found=false
    for var in $(lib_vars); do
        url="$(get_setting "lib_${var}_url")"
        path="$(lib_path "$var")"
        branch="$(get_setting "lib_${var}_branch")"
        [[ -z "$url" ]] && continue  # Library already in the project, nothing to fetch
        found=true
        name="$(submodule_name "$path")"
        if [[ -z "$name" ]]; then
            echo "Adding library \${$var}: $url -> $path"
            git submodule add ${branch:+-b "$branch"} -- "$url" "$path"
            continue
        fi
        current="$(git config -f .gitmodules --get "submodule.$name.url")"
        if [[ "$current" != "$url" ]]; then
            echo "Library \${$var}: URL changed to $url"
            git submodule set-url -- "$path" "$url"
        fi
        if [[ -n "$branch" ]]; then
            git submodule set-branch --branch "$branch" -- "$path"
        fi
        if [[ "$update" == true ]]; then
            echo "Updating library \${$var} ($path) to the latest commit${branch:+ of $branch}"
            git submodule update --init --remote --recursive -- "$path"
        fi
    done
    if [[ "$found" != true ]]; then
        echo "No library with a lib_<VAR>_url entry in $settings_file"
        return 0
    fi
    if [[ "$update" != true ]]; then
        # Check out the commits recorded in the project (--update-libs moved them)
        git submodule update --init --recursive
    fi
    echo
    echo "Done. Commit the changes to use them in CI, i.e.:"
    echo "  git add .gitmodules lib && git commit -m \"Update libraries\""
}

check_libs() {
    local var path missing=0
    for var in $(lib_vars); do
        path="$(lib_path "$var")"
        if [[ -z "$(ls -A "$path" 2>/dev/null)" ]]; then
            echo "Error: library \${$var} missing in '$path'." >&2
            if [[ -n "$(get_setting "lib_${var}_url")" && -z "$(submodule_name "$path")" ]]; then
                echo "       It is not a git submodule yet: run ./run_kibot.sh --setup-libs and commit." >&2
            fi
            missing=1
        else
            echo "Library \${$var}: $path"
        fi
    done
    return $missing
}

case "$1" in
    --setup-libs)  setup_libs false; exit ;;
    --update-libs) setup_libs true; exit ;;
    --check-libs)  check_libs; exit ;;
esac

# Git Bash / MSYS on Windows: give docker a Windows path and disable the
# automatic path conversion of the arguments
case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*)
        project_dir="$(pwd -W)"
        export MSYS_NO_PATHCONV=1
        ;;
esac

# Nested docker (act, dev containers...): the path of the project as seen by
# the docker daemon can be given in KIBOT_PROJECT_HOST_DIR
project_dir="${KIBOT_PROJECT_HOST_DIR:-$project_dir}"

# Diagrams (Diagrams/<Sheet name>.drawio|svg|png, see kibot_launch.sh --help):
# the .drawio are exported to PNG with the draw.io exporter image (headless
# draw.io), then kibot_launch.sh --diagrams imports them in the sheets.
boards_list() {
    local boards
    boards="$(sed -n 's/^boards:[[:space:]]*\([^#]*\).*/\1/p' "$settings_file" | head -n1 | tr ',' ' ')"
    echo "${boards:-.}"
}

# Hash of a diagram source, as import_diagrams.py computes it (LF line endings
# for the text sources)
diagram_hash() {
    local sum=(sha256sum)
    command -v sha256sum >/dev/null || sum=(shasum -a 256)
    case "$1" in
        *.drawio|*.DRAWIO|*.svg|*.SVG) tr -d '\r' < "$1" | "${sum[@]}" | cut -d' ' -f1 ;;
        *) "${sum[@]}" < "$1" | cut -d' ' -f1 ;;
    esac
}

# True if a diagram of the board changed since its last import (imported.json)
diagrams_stale() {
    local f name recorded lock="$1/Diagrams/imported.json"
    for f in "$1"/Diagrams/*.drawio "$1"/Diagrams/*.svg "$1"/Diagrams/*.png; do
        [[ -f "$f" ]] || continue
        name="$(basename "$f")"
        recorded="$(grep -A1 -F "\"$name\": {" "$lock" 2>/dev/null | sed -n 's/.*"sha256": "\([0-9a-f]*\)".*/\1/p')"
        [[ "$recorded" == "$(diagram_hash "$f")" ]] || return 0
    done
    return 1
}

# Export the .drawio of a board. The exporter sometimes hangs at start: time
# limit and retries.
export_diagrams() {
    local drawio_image try
    [[ -n "$(ls "$1"/Diagrams/*.drawio 2>/dev/null)" ]] || return 0
    drawio_image="$(get_setting drawio_image)"
    drawio_image="${drawio_image:-rlespinasse/drawio-export:v4.60.0}"
    rm -rf "$1/Diagrams/export"
    for try in 1 2 3; do
        if docker run --rm --volume "$project_dir:/data" --workdir /data \
                --env DRAWIO_DESKTOP_COMMAND_TIMEOUT=60s "$drawio_image" \
                -f png --scale 3 --border 20 --transparent --remove-page-suffix "$1/Diagrams"; then
            return 0
        fi
        echo "draw.io export failed or timed out (try $try/3)" >&2
    done
    echo "Error: draw.io export of $1/Diagrams failed" >&2
    return 1
}

# ./run_kibot.sh --diagrams: export all, then the import below (kibot_launch.sh)
if [[ "$1" == --diagrams ]]; then
    for b in $(boards_list); do
        export_diagrams "$b" || exit 1
    done
fi

# Before a generation (auto_diagrams: true): export and import the diagrams
# changed since their last import, so the documents are up to date. The
# updated sheets are kept (commit them).
auto_diagrams=()
if [[ "$(get_setting auto_diagrams)" != false ]]; then
    generation=true
    for a in "$@"; do
        case "$a" in
            --shell|--serve|--diagrams|--stackup|--init|--costs|-h|--help|--server|--stop-server) generation=false ;;
        esac
    done
    if [[ "$generation" == true ]]; then
        for b in $(boards_list); do
            if diagrams_stale "$b"; then
                echo "Diagrams of $b changed since their last import: importing them (auto_diagrams)"
                export_diagrams "$b" || exit 1
                auto_diagrams+=("$b")
            fi
        done
    fi
fi

image="${KIBOT_IMAGE:-$(get_setting docker_image)}"
if [[ -z "$image" ]]; then
    echo "Error: docker_image not found in $settings_file" >&2
    exit 1
fi

tty_flags=()
if [[ -t 0 && -t 1 ]]; then tty_flags=(-it); fi

# Cache for the 3D models downloaded by KiBot: a docker volume, or a host
# directory given in KIBOT_3D_CACHE_DIR (used by the CI to save the cache)
cache_3d="${KIBOT_3D_CACHE_DIR:-kibot_3d_models_cache}"
if [[ -n "$KIBOT_3D_CACHE_DIR" ]]; then mkdir -p "$KIBOT_3D_CACHE_DIR"; fi

docker_args=(
    run --rm "${tty_flags[@]}"
    --volume "$project_dir:/project"
    --workdir /project
    --volume "$cache_3d:/root/.cache/kibot_3d_models"
    # Give the generated files back to the current user
    --env HOST_UID="$(id -u)"
    --env HOST_GID="$(id -g)"
    --env NO_AT_BRIDGE=1
)

# Define the KiCad path variables of the shared libraries in the container
for var in $(lib_vars); do
    path="$(lib_path "$var")"
    if [[ -z "$(ls -A "$path" 2>/dev/null)" ]]; then
        echo "Warning: library \${$var} missing in '$path'." >&2
        echo "         Run: ./run_kibot.sh --setup-libs (or git submodule update --init --recursive)" >&2
    fi
    docker_args+=(--env "$var=/project/$path")
done

case "$1" in
    --shell)
        exec docker "${docker_args[@]}" --entrypoint /bin/bash "$image"
        ;;
    --serve)
        port="${2:-8000}"
        echo "Serving the outputs on http://localhost:$port (Ctrl+C to stop)"
        exec docker "${docker_args[@]}" -p "$port:$port" --entrypoint python3 "$image" -m http.server "$port"
        ;;
    *)
        if [[ ${#auto_diagrams[@]} -gt 0 ]]; then
            docker "${docker_args[@]}" --entrypoint /bin/bash "$image" ./kibot_launch.sh --diagrams || exit 1
        fi
        exec docker "${docker_args[@]}" --entrypoint /bin/bash "$image" ./kibot_launch.sh "$@"
        ;;
esac
