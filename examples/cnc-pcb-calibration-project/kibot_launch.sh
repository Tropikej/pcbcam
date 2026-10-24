#!/usr/bin/env bash
# Runs KiBot for this project. Meant to be executed inside the KiBot docker
# image (KiCad 10), either:
#   - locally, through ./run_kibot.sh (Linux/macOS/WSL) or .\run_kibot.ps1 (Windows)
#   - in GitHub Actions (.github/workflows/ci.yaml)
# It can also be used on a host where KiCad 10 and KiBot are installed.

set -o pipefail

# ANSI color codes
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

cd "$(dirname "$0")" || exit 1

settings_file="kibot_settings.yaml"
kibot_config="kibot_yaml/kibot_main.yaml"
pid_file="/tmp/kibot_server.pid"

# Read a `key: value` entry from kibot_settings.yaml
get_setting() {
    sed -n "s/^$1:[[:space:]]*\([^#]*\).*/\1/p" "$settings_file" 2>/dev/null | head -n1 | sed 's/[[:space:]]*$//; s/^["'\'']//; s/["'\'']$//'
}

# Default options
variant="$(get_setting variant)"
variant="${variant:-CHECKED}"
revision=""
render_engine=""
costs_flag=false
check_flag=true
server_flag=false
server_port=8000
log_dir=""
variants_flag=true
board=""
stackup=""
force_flag=false
no_fill_flag=false
diagrams_flag=false
fit_flag=false
extra_args=()

function display_help() {
    echo -e "USAGE"
    echo -e "  ./kibot_launch.sh [OPTIONS] [-- EXTRA_KIBOT_ARGS]"
    echo
    echo -e "OPTIONS"
    echo -e "  -v, --variant VARIANT       Project status / variant: DRAFT, PRELIMINARY, CHECKED,"
    echo -e "                              RELEASED, or an assembly variant name."
    echo -e "                              Default: 'variant' in $settings_file (currently: $variant)."
    echo -e "  --version VERSION           Revision to print in the documents. Default: computed"
    echo -e "                              from CHANGELOG.md (e.g. '1.0.0+ (Unreleased)')."
    echo -e "  -r, --render ENGINE         3D render engine: kicad (fast) or blender (photo-realistic)."
    echo -e "                              Default: RENDER_ENGINE in $kibot_config."
    echo -e "  --costs                     Only generate the XLSX costs BoM (KiCost, needs API keys in"
    echo -e "                              kibot_yaml/kicost_config_local.yaml)."
    echo -e "  --log-dir DIR               Store the KiBot logs in DIR."
    echo -e "  --skip-checks               Don't run the manufacturing checks after the generation."
    echo -e "  --no-variants               Don't generate the assembly_variants of $settings_file after"
    echo -e "                              a CHECKED / RELEASED run."
    echo -e "  --board DIR                 Multi-board repository: only the KiCad project of DIR (outputs"
    echo -e "                              in DIR). Default: all the 'boards' of $settings_file."
    echo -e "  --stackup NAME|list         Apply a stackup profile of kibot_resources/stackups to the PCB"
    echo -e "                              (copper layers, stackup, design rules, impedance net classes,"
    echo -e "                              zones refilled)."
    echo -e "                              Close the board in KiCad first. 'list' shows the profiles."
    echo -e "  --force                     With --stackup: remove copper layers even if they are used."
    echo -e "  --no-fill                   With --stackup: don't refill the zones."
    echo -e "  --diagrams                  Import Diagrams/<Sheet name>.drawio|svg|png in the sheets (run"
    echo -e "                              it with run_kibot.sh / run_kibot.ps1: they export the .drawio)."
    echo -e "  --fit                       With --diagrams: center and fit the pictures again."
    echo -e "  --server [PORT]             Start an HTTP server to browse the outputs (default: 8000)."
    echo -e "  --stop-server               Stop the running HTTP server."
    echo -e "  --init [OPTIONS]            Set the project metadata and rename the project files, asks"
    echo -e "                              when not given: --project, --board, --company, --designer,"
    echo -e "                              --name (KiCad files name), --dir DIR (board of a multi-board"
    echo -e "                              repository), -y (don't ask)."
    echo -e "  -h, --help                  Display this help message."
    echo
    echo -e "RUNNER OPTIONS (run_kibot.sh / run_kibot.ps1 only)"
    echo -e "  --shell                     Interactive shell in the KiBot container."
    echo -e "  --serve [PORT]              Browse the outputs on http://localhost:PORT (default: 8000)."
    echo -e "  --setup-libs                Add/update the shared libraries (lib_<VAR>_url in"
    echo -e "                              $settings_file) as git submodules."
    echo -e "  --update-libs               Move the libraries to the latest commit of their branch."
    echo -e "  --check-libs                Fail if a declared library is missing (run_kibot.sh)."
    echo
    echo -e "EXAMPLES"
    echo -e "  ./kibot_launch.sh                        Run with the variant from $settings_file."
    echo -e "  ./kibot_launch.sh -v DRAFT               Schematic PDF, netlist and BoM only."
    echo -e "  ./kibot_launch.sh -v CHECKED -r blender  All outputs, ERC/DRC, Blender renders."
    echo -e "  ./kibot_launch.sh --costs                XLSX costs spreadsheet in Manufacturing/Assembly."
    echo -e "  ./kibot_launch.sh -v LITE                Assembly variant (KiCad 10 variant), outputs in Variants/LITE."
    echo -e "  ./kibot_launch.sh --board hw/io          Multi-board repository: only the board of hw/io."
    echo -e "  ./kibot_launch.sh --server 8080          Browse the outputs on http://localhost:8080."
    echo -e "  ./kibot_launch.sh --stackup jlcpcb_2l    Switch to the JLCPCB 2 layers stackup, rules and net classes."
    echo -e "  ./kibot_launch.sh --stackup makera_z1_2l CNC milled board (Makera Z1): unplated 2 layers, milling rules."
    echo -e "  ./run_kibot.sh --diagrams                Import Diagrams/Block Diagram.drawio in the Block Diagram sheet."
    echo
    echo -e "VARIANT DESCRIPTIONS"
    echo -e "  DRAFT:       only schematic in progress, generates schematic PDF, netlist and BoM"
    echo -e "  PRELIMINARY: generates both schematic and PCB documents, but no ERC/DRC"
    echo -e "  CHECKED:     generates both schematic and PCB documents, with ERC/DRC"
    echo -e "  RELEASED:    similar to CHECKED, used for releases (automatic on tags in CI)"
    echo -e "  Other:       assembly variants, run like RELEASED, outputs saved in Variants/<name>"
    exit 0
}

# Project initialization: metadata and project files name
if [[ "$1" == --init ]]; then
    shift
    exec python3 kibot_resources/scripts/init_project.py "$@"
fi

# Parse arguments
all_args=("$@")
while [[ $# -gt 0 ]]; do
    case $1 in
        --variant|-v)
            if [[ -n $2 && $2 != -* ]]; then variant="$2"; shift
            else echo -e "${YELLOW}Warning: --variant|-v requires a value.${NC}"; exit 1; fi
            ;;
        --version)
            if [[ -n $2 && $2 != -* ]]; then revision="$2"; shift
            else echo -e "${YELLOW}Warning: --version requires a value.${NC}"; exit 1; fi
            ;;
        --render|-r)
            if [[ $2 == kicad || $2 == blender ]]; then render_engine="$2"; shift
            else echo -e "${YELLOW}Warning: --render|-r requires 'kicad' or 'blender'.${NC}"; exit 1; fi
            ;;
        --skip-checks)
            check_flag=false
            ;;
        --no-variants)
            variants_flag=false
            ;;
        --board)
            if [[ -n $2 && $2 != -* ]]; then board="${2%/}"; shift
            else echo -e "${YELLOW}Warning: --board requires a directory.${NC}"; exit 1; fi
            ;;
        --costs)
            costs_flag=true
            ;;
        --stackup)
            if [[ -n $2 && $2 != -* ]]; then stackup="$2"; shift
            else echo -e "${YELLOW}Warning: --stackup requires a profile name or 'list'.${NC}"; exit 1; fi
            ;;
        --force)
            force_flag=true
            ;;
        --no-fill)
            no_fill_flag=true
            ;;
        --diagrams)
            diagrams_flag=true
            ;;
        --fit)
            fit_flag=true
            ;;
        --log-dir)
            if [[ -n $2 && $2 != -* ]]; then log_dir="$2"; shift
            else echo -e "${YELLOW}Warning: --log-dir requires a value.${NC}"; exit 1; fi
            ;;
        --server)
            server_flag=true
            if [[ -n $2 && $2 != -* ]]; then server_port="$2"; shift; fi
            ;;
        --stop-server)
            if [[ -f $pid_file ]] && kill -0 "$(cat $pid_file)" 2>/dev/null; then
                echo -e "${GREEN}Stopping HTTP server with PID $(cat $pid_file)...${NC}"
                kill "$(cat $pid_file)"
                rm -f $pid_file
                exit 0
            fi
            echo -e "${YELLOW}No server is running.${NC}"
            rm -f $pid_file
            exit 1
            ;;
        -h|--help)
            display_help
            ;;
        --)
            shift
            extra_args=("$@")
            break
            ;;
        *)
            echo -e "${YELLOW}Warning: Unrecognized argument: $1${NC}"
            display_help
            ;;
    esac
    shift
done

# HTTP server to browse the outputs
if [[ "$server_flag" == true ]]; then
    if [[ -f $pid_file ]] && kill -0 "$(cat $pid_file)" 2>/dev/null; then
        echo -e "${YELLOW}A server is already running on PID $(cat $pid_file). Stop it first with --stop-server.${NC}"
        exit 1
    fi
    echo -e "${GREEN}Starting HTTP server on port $server_port...${NC}"
    python3 -m http.server "$server_port" &
    echo $! > $pid_file
    sleep 1
    echo -e "${GREEN}Server running. Navigate to: http://localhost:$server_port${NC}"
    exit 0
fi

# Multi-board repository (`boards` in kibot_settings.yaml): one run per board,
# each KiCad project in its own folder with its outputs. The pipeline files,
# CHANGELOG.md and the settings are shared (run from the repository root).
boards="$(get_setting boards)"
boards="${boards//,/ }"
if [[ -z "$board" && -n "${boards// /}" && "$stackup" != list ]]; then
    if [[ -n "$stackup" && "$stackup" != list ]]; then
        echo -e "${RED}Multi-board repository: choose the board, i.e. --stackup $stackup --board ${boards%% *}${NC}"
        exit 1
    fi
    failed=0
    for b in $boards; do
        echo -e "${GREEN}Board: $b${NC}"
        sub_args=("${all_args[@]}" --board "$b")
        [[ -n "$log_dir" ]] && sub_args+=(--log-dir "$log_dir/${b//\//_}")
        if ! bash "$0" "${sub_args[@]}"; then
            echo -e "${RED}Board $b failed${NC}"
            failed=1
        fi
    done
    exit $failed
fi

# KiCad project of this run: the one of the current directory, or of --board
project_dir="${board:-.}"
if [[ ! -d "$project_dir" ]]; then
    echo -e "${RED}Board directory not found: $project_dir${NC}"
    exit 1
fi
project_pro=""
for f in "$project_dir"/*.kicad_pro; do
    [[ -f "$f" && ! "$(basename "$f")" =~ ^kibot_.{8}\.kicad_pro$ ]] && project_pro="$f" && break
done
board_args=()
if [[ -n "$board" ]]; then
    if [[ -z "$project_pro" ]]; then
        echo -e "${RED}No KiCad project in $board${NC}"
        exit 1
    fi
    board_args=(-e "${project_pro%.kicad_pro}.kicad_sch" -b "${project_pro%.kicad_pro}.kicad_pcb")
    # Definitions of kibot_main.yaml for this board (BOARD_NAME...): KEY: value
    if [[ -f "$board/kibot_board.yaml" ]]; then
        while IFS= read -r line; do
            [[ "$line" =~ ^([A-Za-z0-9_]+):[[:space:]]*(.*)$ ]] || continue
            key="${BASH_REMATCH[1]}"
            value="${BASH_REMATCH[2]%%[[:space:]]#*}"
            value="${value%"${value##*[![:space:]]}"}"
            if [[ "$value" =~ ^\'(.*)\'$ ]]; then
                value="${BASH_REMATCH[1]//\'\'/\'}"
            elif [[ "$value" =~ ^\"(.*)\"$ ]]; then
                value="${BASH_REMATCH[1]}"
            fi
            board_args+=(-E "$key=$value")
        done < "$board/kibot_board.yaml"
    fi
fi

# Fonts: the sheets use Arial / Times New Roman, not redistributable. Use the
# metric-compatible Liberation fonts of kibot_resources/fonts under these names
# (fontconfig rule for this process tree only, the font files are unchanged),
# so KiCad doesn't report a substitution.
font_dir="$(mktemp -d)"
trap 'rm -rf "$font_dir"' EXIT
if [[ -d kibot_resources/fonts ]] && command -v fc-cache >/dev/null; then
    cat > "$font_dir/fonts.conf" <<EOF
<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">
<fontconfig>
  <cachedir>$font_dir/cache</cachedir>
  <include ignore_missing="yes">/etc/fonts/fonts.conf</include>
  <dir>$PWD/kibot_resources/fonts</dir>
  <match target="scan">
    <test name="family"><string>Liberation Sans</string></test>
    <edit name="family" mode="prepend"><string>Arial</string></edit>
  </match>
  <match target="scan">
    <test name="family"><string>Liberation Serif</string></test>
    <edit name="family" mode="prepend"><string>Times New Roman</string></edit>
  </match>
</fontconfig>
EOF
    export FONTCONFIG_FILE="$font_dir/fonts.conf"
    fc-cache >/dev/null 2>&1 || true
fi

# Diagrams: import the exported pictures in the sheets and exit
if [[ "$diagrams_flag" == true ]]; then
    diagram_args=(--project "$project_dir")
    if [[ "$fit_flag" == true ]]; then diagram_args+=(--fit); fi
    python3 kibot_resources/scripts/import_diagrams.py "${diagram_args[@]}"
    ret=$?
    if [[ -n "$HOST_UID" && -n "$HOST_GID" && "$(id -u)" == "0" ]]; then
        chown -R "$HOST_UID:$HOST_GID" "$project_dir" 2>/dev/null
    fi
    exit $ret
fi

# Stackup profile: edit the PCB and exit
if [[ -n "$stackup" ]]; then
    if [[ "$stackup" == list ]]; then
        exec python3 kibot_resources/scripts/set_stackup.py --list
    fi
    stackup_args=("$stackup")
    if [[ "$force_flag" == true ]]; then stackup_args+=(--force); fi
    if [[ "$no_fill_flag" == true ]]; then stackup_args+=(--no-fill); fi
    if [[ -n "$board" ]]; then stackup_args+=(-b "${project_pro%.kicad_pro}.kicad_pcb"); fi
    python3 kibot_resources/scripts/set_stackup.py "${stackup_args[@]}"
    exit $?
fi

# The repository is often owned by another user than the one running the
# container: allow git to read it (non persistent, only for this process tree)
git_config=("safe.directory=*")
# Shared libraries as git submodules: KiRI checks out old commits and updates
# their submodules. Fetch them from the local copy (.git/modules/...), no
# network or credentials needed.
if [[ -f .gitmodules ]]; then
    git_config+=("protocol.file.allow=always")
    while read -r key _; do
        name="${key#submodule.}"
        name="${name%.url}"
        if [[ -d ".git/modules/$name" ]]; then
            git_config+=("submodule.$name.url=$PWD/.git/modules/$name")
        fi
    done < <(git config -f .gitmodules --get-regexp '^submodule\..*\.url$')
fi
export GIT_CONFIG_COUNT=${#git_config[@]}
for i in "${!git_config[@]}"; do
    export "GIT_CONFIG_KEY_$i=${git_config[$i]%%=*}"
    export "GIT_CONFIG_VALUE_$i=${git_config[$i]#*=}"
done

# Cache for the 3D models downloaded by KiBot
export KIBOT_3D_MODELS="${KIBOT_3D_MODELS:-$HOME/.cache/kibot_3d_models}"
mkdir -p "$KIBOT_3D_MODELS" 2>/dev/null

# Get revision if not specified
if [[ -z "$revision" ]]; then
    revision=$(python3 kibot_resources/scripts/get_changelog_version.py -f CHANGELOG.md)
    if [[ $? -ne 0 ]]; then
        echo -e "${YELLOW}Warning: Unable to determine version from CHANGELOG.md. Defaulting to empty revision.${NC}"
        revision=""
    fi
fi

# Output directory: each assembly variant in its own Variants/<name> folder
case "$variant" in
    DRAFT|PRELIMINARY|CHECKED|RELEASED) output_dir="$project_dir" ;;
    *) output_dir="$project_dir/Variants/$variant" ;;
esac
output_dir="${output_dir#./}"

# Common KiBot arguments. --dont-stop: a failing output doesn't prevent the
# generation of the others, --fail-on-ignored: but we still return an error.
common_args=(-c "$kibot_config" "${board_args[@]}" -d "$output_dir" -g "variant=$variant"
             -E "REVISION=$revision" -E "OUTPUT_ROOT=$output_dir" -E "PROJECT_DIR=$project_dir"
             --dont-stop --fail-on-ignored)
if [[ -n "$render_engine" ]]; then
    common_args+=(-E "RENDER_ENGINE=$render_engine")
fi
# Names and descriptions of the KiCad 10 variants of the project
function project_variants() {
    [[ -n "$project_pro" ]] || return 0
    python3 - "$project_pro" <<'PYEOF' 2>/dev/null
import json, sys
for v in json.load(open(sys.argv[1], encoding='utf-8')).get('schematic', {}).get('variants', []):
    print('{}\t{}'.format(v.get('name', ''), (v.get('description') or '').replace("'", ' ')))
PYEOF
}
# Assembly variant (KiCad 10 variant of the project): declared through
# definitions in kibot_main.yaml, with the description of the project
case "$variant" in
    DRAFT|PRELIMINARY|CHECKED|RELEASED) ;;
    *)
    variant_comment="$(project_variants | awk -F '\t' -v v="$variant" '$1 == v {print $2}')"
    common_args+=(-E "ASSEMBLY_VARIANT=$variant" -E "ASSEMBLY_VARIANT_COMMENT=${variant_comment:-$variant}")
    ;;
esac
# GIT_URL: auto -> URL of the git remote (https, without credentials)
if grep -qE "^  GIT_URL:[[:space:]]*['\"]?auto['\"]?[[:space:]]*(#.*)?$" "$kibot_config"; then
    git_url="$(git remote get-url origin 2>/dev/null)"
    git_url="$(echo "$git_url" | sed -E 's#^git@([^:]+):#https://\1/#; s#^ssh://git@([^/]+)/#https://\1/#; s#://[^@/]+@#://#; s#\.git$##')"
    common_args+=(-E "GIT_URL=$git_url")
fi

# KiBot saves the PCB/schematic while working (text variables, stackup
# drawing...). Keep the designer's files untouched, including uncommitted
# changes: snapshot them and restore them at exit. The text variables stored
# in the .kicad_pro are kept up to date on purpose.
snapshot_dir="$(mktemp -d)"
shopt -s nullglob
design_files=("$project_dir"/*.kicad_pcb "$project_dir"/*.kicad_sch)
shopt -u nullglob
if [[ ${#design_files[@]} -gt 0 ]]; then
    cp -p "${design_files[@]}" "$snapshot_dir/"
fi
function restore_design_files() {
    local f
    for f in "${design_files[@]}"; do
        cp -p "$snapshot_dir/$(basename "$f")" "$f"
    done
    rm -rf "$snapshot_dir" "$font_dir"
}
trap restore_design_files EXIT

# Netlist XML of the schematic: the cover page index (set_text_variables) reads
# it before the update_xml preflight regenerates it, so it must exist before
# KiBot starts (fresh checkout, CI)
for pro in "$project_dir"/*.kicad_pro; do
    [[ -f "$pro" && ! "$(basename "$pro")" =~ ^kibot_.{8}\.kicad_pro$ ]] || continue
    sch="${pro%.kicad_pro}.kicad_sch"
    if [[ -f "$sch" ]] && ! kicad-cli sch export python-bom -o "${sch%.kicad_sch}.xml" "$sch" >/dev/null 2>&1; then
        echo -e "${YELLOW}Warning: couldn't export the netlist XML of $sch, the cover page index will be empty.${NC}"
    fi
done

# Run KiBot: run_kibot <log name> [kibot options...] <outputs/groups...>
failed=0
function run_kibot() {
    local name="$1"; shift
    # KiBot needs the options before the output/group names (last arguments)
    local cmd=(kibot "${common_args[@]}" "${extra_args[@]}" "$@")
    if [[ -n "$log_dir" ]]; then
        mkdir -p "$log_dir"
        cmd=(kibot --log "$log_dir/kibot_$name.log" "${common_args[@]}" "${extra_args[@]}" "$@")
    fi
    echo -e "${GREEN}Running: ${cmd[*]}${NC}"
    "${cmd[@]}"
    local ret=$?
    if [[ $ret -ne 0 ]]; then
        echo -e "${RED}KiBot step '$name' failed (exit code $ret)${NC}"
        failed=$ret
    fi
    return $ret
}

# Variant specific flags. The fabrication/assembly notes are generated first,
# as the set_text_variables preflight includes them in the PCB documents.
# Folders fully regenerated by the PCB outputs: KiBot doesn't delete the files
# it no longer generates (i.e. Gerbers of renamed or removed layers after a
# stackup change), so start from empty folders
if [[ "$costs_flag" != true && "$variant" != DRAFT ]]; then
    for d in "Manufacturing/Fabrication/Gerbers" "Manufacturing/Fabrication/Drill Tables" "Manufacturing/JLCPCB"; do
        rm -rf "${output_dir:?}/$d"
    done
fi

# Panel (KiKit): the panel PCB, then its Gerbers / drill with kibot_panel.yaml
# (a separate configuration: the panel is another board, without variants).
# Before the other outputs, so the HTML navigation page shows its preview
if [[ "$costs_flag" != true && "$(get_setting panel)" == true ]]; then
    case "$variant" in
        PRELIMINARY|CHECKED|RELEASED)
            rm -rf "$output_dir/Manufacturing/Panel"
            if run_kibot panel --skip-pre all panel_group; then
                panel_pcb="$(find "$output_dir/Manufacturing/Panel" -name '*-panel.kicad_pcb' | head -n1)"
                if [[ -n "$panel_pcb" ]]; then
                    cmd=(kibot -c kibot_yaml/kibot_panel.yaml -b "$panel_pcb" -e "" -d "$output_dir" --dont-stop)
                    [[ -n "$log_dir" ]] && cmd=(kibot --log "$log_dir/kibot_panel_files.log" "${cmd[@]:1}")
                    echo -e "${GREEN}Running: ${cmd[*]}${NC}"
                    if ! "${cmd[@]}"; then
                        echo -e "${RED}KiBot step 'panel_files' failed${NC}"
                        failed=1
                    fi
                else
                    echo -e "${RED}Panel PCB not found${NC}"
                    failed=1
                fi
            fi
            ;;
    esac
fi

if [[ "$costs_flag" == true ]]; then
    run_kibot costs --skip-pre erc,drc,draw_fancy_stackup \
        -E "KICOST_CONFIG=kibot_yaml/kicost_config_local.yaml" xlsx_bom
else
    case "$variant" in
        DRAFT)
            # README after the outputs: set_text_variables updated the project
            run_kibot outputs --skip-pre draw_fancy_stackup,erc,drc draft_group
            run_kibot readme --skip-pre set_text_variables,draw_fancy_stackup,erc,drc md_readme
            ;;
        PRELIMINARY)
            run_kibot notes --skip-pre all notes
            run_kibot outputs --skip-pre erc,drc all_group
            ;;
        CHECKED|RELEASED)
            run_kibot notes --skip-pre all notes
            run_kibot outputs all_group
            ;;
        *)
            # Assembly variant
            run_kibot notes --skip-pre all notes
            run_kibot outputs variant_group
            ;;
    esac
fi

# Manufacturing checks: Gerbers/drill, assembly files, PDF documents
if [[ "$costs_flag" != true && "$check_flag" == true ]]; then
    check_args=(-d "$output_dir")
    if [[ -n "$log_dir" ]]; then
        check_args+=(--markdown "$(pwd)/$log_dir/manufacturing_checks.md")
    fi
    title="Manufacturing checks"
    [[ -n "$board" ]] && title+=": $board"
    [[ "$output_dir" == */Variants/* || "$output_dir" == Variants/* ]] && title+=" (variant $variant)"
    check_args+=(--title "$title")
    echo -e "${GREEN}Running: manufacturing checks${NC}"
    python3 kibot_resources/scripts/check_manufacturing.py "${check_args[@]}"
    ret=$?
    if [[ $ret -ne 0 ]]; then
        echo -e "${RED}Manufacturing checks failed${NC}"
        failed=$ret
    fi
fi

# Assembly variants of kibot_settings.yaml, after a CHECKED / RELEASED run:
# one run per variant (outputs in Variants/<name>, logs in <log dir>/<name>)
if [[ "$costs_flag" != true && "$variants_flag" == true && ( "$variant" == CHECKED || "$variant" == RELEASED ) ]]; then
    assembly_variants="$(get_setting assembly_variants)"
    declared=" $(project_variants | cut -f1 | tr '\n' ' ') "
    for av in ${assembly_variants//,/ }; do
        if [[ "$declared" != *" $av "* ]]; then
            # Multi-board: the variant belongs to another board
            echo -e "${YELLOW}Assembly variant $av: not defined in $project_pro, skipped${NC}"
            continue
        fi
        echo -e "${GREEN}Assembly variant: $av${NC}"
        sub_args=(-v "$av")
        [[ -n "$board" ]] && sub_args+=(--board "$board")
        [[ -n "$revision" ]] && sub_args+=(--version "$revision")
        [[ -n "$render_engine" ]] && sub_args+=(-r "$render_engine")
        [[ "$check_flag" != true ]] && sub_args+=(--skip-checks)
        [[ -n "$log_dir" ]] && sub_args+=(--log-dir "$log_dir/$av")
        if ! bash "$0" "${sub_args[@]}"; then
            echo -e "${RED}Assembly variant $av failed${NC}"
            failed=1
        fi
    done
fi

# Remove the temporary project copies KiBot may leave behind
rm -f kibot_????????.kicad_{pcb,pro,prl,dru,sch} ./*.kicad_pcb-bak ./*.kicad_pro-bak ./~*.lck
rm -f "$project_dir"/kibot_????????.kicad_{pcb,pro,prl,dru,sch} "$project_dir"/*.kicad_pcb-bak "$project_dir"/*.kicad_pro-bak

# Give the generated files back to the host user (Linux hosts, see run_kibot.sh)
if [[ -n "$HOST_UID" && -n "$HOST_GID" && "$(id -u)" == "0" ]]; then
    chown -R "$HOST_UID:$HOST_GID" . 2>/dev/null
fi

exit $failed
