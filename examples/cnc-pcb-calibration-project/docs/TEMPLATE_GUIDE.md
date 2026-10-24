# KiCad 10 KiBot Template: Guide

A **KiCad 10** project template for **automated**, professional documentation
generation with [KiBot](https://kibot.readthedocs.io/en/master/), running
**locally with Docker** or **remotely on GitHub Actions**, with exactly the
same docker image and the same script.

Heavily inspired by (and compatible with the project conventions of)
[KDT_Hierarchical_KiBot](https://github.com/nguyen-v/KDT_Hierarchical_KiBot)
by Vincent Nguyen: same outputs, same `DRAFT` / `PRELIMINARY` / `CHECKED` /
`RELEASED` mechanism, same PCB layers and `kibot_*` groups, same YAML
definitions.

## TABLE OF CONTENTS

- [Features](#features)
- [Outputs per project status](#outputs-per-project-status)
- [Getting started](#getting-started)
- [Configuration](#configuration)
  - [Pipeline settings: `kibot_settings.yaml`](#pipeline-settings-kibot_settingsyaml)
  - [KiBot parameters: `kibot_yaml/kibot_main.yaml`](#kibot-parameters-kibot_yamlkibot_mainyaml)
  - [Stackup and design rules (JLCPCB)](#stackup-and-design-rules-jlcpcb)
  - [CNC milling (Makera Z1)](#cnc-milling-makera-z1)
  - [Manufacturing checks](#manufacturing-checks)
  - [Ordering at JLCPCB](#ordering-at-jlcpcb)
  - [Panel (KiKit)](#panel-kikit)
  - [3D renders: KiCad or Blender](#3d-renders-kicad-or-blender)
  - [Shared libraries](#shared-libraries-symbols-footprints-3d-models)
  - [Assembly variants (KiCad 10)](#assembly-variants-kicad-10)
  - [Diagrams (draw.io)](#diagrams-drawio)
  - [Multi-board repositories](#multi-board-repositories)
- [Running locally (Docker)](#running-locally-docker)
- [CI/CD on GitHub Actions](#cicd-on-github-actions)
  - [Dependency updates (Renovate)](#dependency-updates-renovate)
- [Project conversion guide](#project-conversion-guide)
- [Directory structure](#directory-structure)
- [Troubleshooting](#troubleshooting)
- [Differences with KDT_Hierarchical_KiBot](#differences-with-kdt_hierarchical_kibot)
- [Credits](#credits)

## FEATURES

- **KiCad 10** project (hierarchical schematic, custom drawing sheets) and
  KiBot 1.9 configuration.
- **JLCPCB ready**: design rules, 4-layer stackup (JLC04161H-7628) with its
  impedance table and impedance net classes, switch to 2 layers (JLC0216A) or
  6 layers (JLC06161H-3313) with one command, and
  the JLCPCB order files: Gerbers ZIP, BoM with LCSC part numbers and pick and
  place with the JLCPCB rotation corrections.
- **Starter board**: 40 × 30 mm outline, 4 mounting holes and a test point, so
  every project status works from the first run.
- **Automated fabrication document**: stackup table, fabrication notes, drill
  drawings/tables, testpoint tables/highlighting, one page per copper layer.
- **Automated assembly document**: 3D images, component count table, assembly
  notes, DNP crosses.
- **Gerbers as PDF**: one page per fabrication layer, in color, to review what
  is sent to the manufacturer without a Gerber viewer.
- **PCB routing PDF**: all copper layers, then one page per copper layer and
  the silkscreens.
- **Schematic PDF** with automatic table of contents and revision history
  synchronised with `CHANGELOG.md`.
- **3D renders** (top, bottom, angled) with the **KiCad 3D viewer (fast) or
  Blender (photo-realistic)**, selected with one parameter.
- Gerbers, drill files, ODB++, STEP, BoM (CSV, HTML, interactive HTML,
  XLSX with costs), pick and place, testpoint lists, ERC/DRC reports.
- **Automated README.md**, **KiRI** visual diff between commits and a
  **web page** to browse all the outputs.
- **Same pipeline locally and in CI**: `kibot_launch.sh` runs inside the
  `ghcr.io/inti-cmnb/kicad10_auto_full` image in both cases.
- **Releases**: pushing a semantic version tag generates the `RELEASED`
  documents, updates `CHANGELOG.md` and creates a GitHub release with assets.
- **Pull request checks**: ERC/DRC and all the documents generated for each
  pull request, with a summary posted on the pull request.
- **Clean history**: the outputs are published on a separate
  `kibot-outputs/<branch>` branch (configurable), your branches only get them
  on releases.
- **One-command setup**: `./run_kibot.sh --init` sets the project metadata and
  renames the project files; the repository URL is detected automatically.

## OUTPUTS PER PROJECT STATUS

The project status (KiBot *variant*) selects the generated outputs:

| Output | DRAFT | PRELIMINARY | CHECKED | RELEASED |
| --- | :---: | :---: | :---: | :---: |
| Schematic PDF (`Schematic/`) | ✅ | ✅ | ✅ | ✅ |
| Netlist, BoM CSV + HTML | ✅ | ✅ | ✅ | ✅ |
| README.md | ✅ | ✅ | ✅ | ✅ |
| PCB routing PDF (`PCB/`) | | ✅ | ✅ | ✅ |
| Gerbers PDF (`Manufacturing/Fabrication/`) | | ✅ | ✅ | ✅ |
| Fabrication PDF + notes, Gerbers, drill, ODB++, ZIP | | ✅ | ✅ | ✅ |
| Assembly PDF + notes, pick and place, interactive BoM | | ✅ | ✅ | ✅ |
| JLCPCB files: Gerbers ZIP, BoM, pick and place (`Manufacturing/JLCPCB/`) | | ✅ | ✅ | ✅ |
| 3D renders PNG (`Images/`), STEP (`3D/`) | | ✅ | ✅ | ✅ |
| Testpoint lists (`Testing/`) | | ✅ | ✅ | ✅ |
| KiRI diff viewer, HTML navigation page | | ✅ | ✅ | ✅ |
| ERC / DRC reports (`Reports/`) | | | ✅ | ✅ |
| GitHub release with assets | | | | ✅ (tag) |

- **DRAFT**: schematic in progress.
- **PRELIMINARY**: schematic and PCB documents, no ERC/DRC.
- **CHECKED**: schematic and PCB documents, with ERC/DRC.
- **RELEASED**: like CHECKED. Selected automatically in CI when a tag is pushed.
- Any other name is an **assembly variant**: run like RELEASED, outputs in
  `Variants/` (define it in the `variants:` section of `kibot_main.yaml`).

## GETTING STARTED

1. Install this template in your KiCad user templates folder
   (`KICAD_USER_TEMPLATE_DIR`, see *Preferences → Configure Paths*):

   - **Windows**: `%USERPROFILE%\Documents\KiCad\10.0\template`
   - **Linux**: `~/.local/share/kicad/10.0/template`
   - **macOS**: `~/Documents/KiCad/10.0/template`

   Export the committed files only (no `.git`, no generated outputs, which
   KiCad would copy into every new project):

   ```
   mkdir -p ~/Documents/KiCad/10.0/template/KiCad10_KiBot_Template
   git archive HEAD | tar -x -C ~/Documents/KiCad/10.0/template/KiCad10_KiBot_Template
   ```

2. Copy the color theme
   [`kibot_resources/colors/Altium_Theme.json`](../kibot_resources/colors/Altium_Theme.json)
   in your KiCad `colors` folder (`%APPDATA%\kicad\10.0\colors` on Windows,
   `~/.config/kicad/10.0/colors` on Linux). The sheets use Arial and Times New
   Roman, present on Windows and macOS (Linux: install `fonts-liberation`). The
   container uses the metric-compatible free Liberation fonts of
   [`kibot_resources/fonts`](../kibot_resources/fonts) instead (same text
   sizes and positions).

3. In KiCad: **File → New Project From Template** and select this template.

   > [!CAUTION]
   > KiCad may not copy the hidden `.github` folder (and `.gitignore`,
   > `.gitattributes`) when creating a project from a template, mostly on
   > Linux. Copy them manually if they are missing.

4. Create the git repository and the working branch:

   ```
   git init -b main
   git add -A && git commit -m "Initial commit"
   git checkout -b dev
   ```

5. Set the project metadata (and rename the project files if you cloned the
   template instead of using *New Project From Template*):

   ```
   ./run_kibot.sh --init                 # asks the values
   ./run_kibot.sh --init --project "Motor Controller" --board "MC-01 Mainboard" \
       --company KejLabs --designer "J. Doe" --name mc01_main -y
   ```

   It writes `PROJECT_NAME`, `BOARD_NAME`, `COMPANY` and `DESIGNER` in the
   `definitions:` section of
   [`kibot_yaml/kibot_main.yaml`](../kibot_yaml/kibot_main.yaml) (you can also
   edit them there, with `LOGO`...). `GIT_URL: auto` is replaced by the URL of
   the git remote `origin`.

   The starter parts of the *Section A* sheet (H1-H4 mounting holes, TP1 test
   point) and the 40 × 30 mm outline can be kept, moved or deleted.

6. Edit the report templates in
   [`kibot_resources/templates`](../kibot_resources/templates): fabrication and
   assembly notes, impedance table and README template.

7. Choose the stackup (see [Stackup and design rules](#stackup-and-design-rules-jlcpcb),
   JLCPCB 4 layers by default) and adapt the BoM columns
   ([`kibot_out_csv_bom.yaml`](../kibot_yaml/kibot_out_csv_bom.yaml),
   [`kibot_out_html_bom.yaml`](../kibot_yaml/kibot_out_html_bom.yaml),
   [`kibot_out_xlsx_bom.yaml`](../kibot_yaml/kibot_out_xlsx_bom.yaml)) to the
   symbol fields you use.

8. Set the project status in [`kibot_settings.yaml`](../kibot_settings.yaml)
   and generate the outputs locally (`./run_kibot.sh`, `.\run_kibot.ps1`) or
   push to GitHub.

## CONFIGURATION

All the parameters are in YAML files, the scripts and the workflow don't need
to be modified.

### Pipeline settings: `kibot_settings.yaml`

Read by the local runners and by the GitHub workflow:

```yaml
variant: DRAFT          # DRAFT | PRELIMINARY | CHECKED | RELEASED | <assembly variant>
docker_image: ghcr.io/inti-cmnb/kicad10_auto_full:1.9.1-1_k10.0.5_d13.2_b4.2.4LTS
ci_outputs: branch      # CI: where the outputs go (branch | commit | none)
ci_render_engine: kicad # CI: 3D render engine (kicad | blender)
pr_variant: CHECKED     # CI: status used to check the pull requests
lib_KEJLABS_LIB_url: https://github.com/kejlabs/kicad-libs.git   # shared libraries (optional)
```

Keep the simple `key: value` format, the file is also parsed by shell scripts.
Pin the docker image to a precise tag for reproducible documents
([available tags](https://github.com/INTI-CMNB/kicad_auto/pkgs/container/kicad10_auto_full)).

### KiBot parameters: `kibot_yaml/kibot_main.yaml`

The `definitions:` section at the end of `kibot_main.yaml` holds the KiBot
parameters. They are passed to the imported `kibot_out_*.yaml`,
`kibot_pre_*.yaml` and `kibot_filt_*.yaml` files, which don't need to be
edited for normal use. The main groups:

| Definitions | Purpose |
| --- | --- |
| `PROJECT_NAME`, `BOARD_NAME`, `COMPANY`, `DESIGNER`, `LOGO`, `GIT_URL` | Metadata, used as text variables in the documents |
| `CHECK_ZONE_FILLS`, `STACKUP_TABLE_NOTE` | Preflights (DRC, stackup drawing) |
| `MPN_FIELD`, `MAN_FIELD` | Symbol fields for the manufacturer part number and manufacturer |
| `GROUP_ROUND_SLOTS`, `GROUP_PTH_NPTH`, `GROUP_PTH_NPTH_DRL` | Drill tables and files |
| `PLOT_REFS` | Reference designators in the Gerbers |
| `COLOR_THEME`, `SHEET_WKS`, `*_SCALING` | PDF documents (0 = fit the page) |
| `GERBERS_PDF_MONOCHROME` | Gerbers PDF in gray scale instead of colors (layer colors: `COLOR_*` in `kibot_out_pdf_gerbers.yaml`) |
| `RENDER_ENGINE`, `3D_RESOLUTION`, `KICAD_3D_*`, `BLENDER_*` | 3D renders |
| `KIRI_MAX_COMMITS` | Number of commits in the KiRI diff viewer |
| `*_DIR` | Output directories |
| `LAYER_*` | Names of the user layers of the PCB |
| `*_OUTPUT` | Output names, also used in the `kibot_image_*` / `kibot_table_*` PCB groups |

Any definition can be overridden from the command line of KiBot with
`-E NAME=value`, i.e. `./run_kibot.sh -- -E BOARD_NAME=Test`.

### Stackup and design rules (JLCPCB)

The template targets **JLCPCB** without extra cost options, with one profile
per JLCPCB standard stackup (1.6 mm) in
[`kibot_resources/stackups`](../kibot_resources/stackups), and a profile for
boards milled on a desktop CNC ([below](#cnc-milling-makera-z1)):

| Profile | JLCPCB stackup | Copper | Design rules |
| --- | --- | --- | --- |
| `jlcpcb_2l` | JLC0216A, 2 layers | 1 oz | 1-2 layers: 0.10 mm track/space, vias 0.3/0.5 mm min., PTH annular ring 0.18 mm |
| `jlcpcb_4l` (default) | JLC04161H-7628, 4 layers | 1 oz outer / 0.5 oz inner | multilayer: 0.09 mm track/space, vias 0.2/0.45 mm min. |
| `jlcpcb_6l` | JLC06161H-3313, 6 layers | 1 oz outer / 0.5 oz inner | multilayer (same as 4 layers) |
| `makera_z1_2l` | CNC milled FR4 1.5 mm, 2 layers | 1 oz | isolation 0.2 mm min. (0.3 mm net classes), vias 0.6/1.2 mm min., no plating, approximate impedance |

Switch at any time, even in the middle of a project (close it in KiCad first,
then commit the changes):

```
./run_kibot.sh --stackup list        # available profiles
./run_kibot.sh --stackup jlcpcb_2l   # JLC0216A, 2 layers
./run_kibot.sh --stackup jlcpcb_6l   # JLC06161H-3313, 6 layers
./run_kibot.sh --stackup jlcpcb_4l   # back to JLC04161H-7628, 4 layers
./run_kibot.sh --stackup makera_z1_2l  # CNC milling, 2 layers
```

A profile sets:

- **PCB**: copper layer count, layer names and types (`L1 (Sig)`,
  `L2 (GND)`...), physical stackup (thicknesses, materials, dielectric
  constants), finish (HASL lead-free), colors.
- **Design rules**: *Board Setup → Constraints* minimums, the via sizes of the
  *Track & Via* tool, and the `.kicad_dru` custom rules (from
  `jlcpcb_2layer.kicad_dru` / `jlcpcb_multilayer.kicad_dru` /
  `makera_z1_2layer.kicad_dru`). A `.kicad_dru` you modified is kept (a
  warning tells you to compare it).
- **Fabrication notes** of the fabrication document
  (`kibot_resources/templates/fabrication_notes.txt`, from
  `fabrication_notes_jlcpcb.txt` / `fabrication_notes_makera_z1.txt`), kept
  too if you modified them.
- **Impedance net classes** `50R`, `USB_90R` and `DIFF_100R`, created or
  updated with the widths/gaps of the stackup (your other net classes are not
  touched). Assign them to your nets (*Board Setup → Net Classes*, or net class
  labels in the schematic).
- **Impedance table** of the fabrication document
  (`kibot_resources/templates/impedance_table.txt`).
- **Zones** refilled with the new clearances. KiBot doesn't refill them
  (`CHECK_ZONE_FILLS: false`, the Gerbers use the fills saved in the PCB), so
  stale fills would fail the DRC (i.e. `copper_edge_clearance` when the edge
  clearance goes from 0.25 to 0.3 mm). `./run_kibot.sh --stackup NAME --no-fill` to skip it (then refill in
  KiCad, *Edit → Fill All Zones*, `B`).

The fabrication document, stackup table, Gerbers and drill files follow at the
next run. Removing layers that still hold copper (i.e. 4 → 2 layers with inner
planes) is refused, `--force` to override. Add a profile for another stackup or
manufacturer by copying one of the YAML files. Profile entries:

| Entry | Use |
| --- | --- |
| `name`, `description` | Shown by `--stackup list` |
| `layers` | Top to bottom: `{copper: NAME, type, thickness}` / `{dielectric: core\|prepreg, material, thickness, epsilon_r, loss_tangent}` |
| `copper_finish`, `mask_color`, `silk_color`, `mask_thickness`, `mask_epsilon_r` | Physical stackup |
| `solder_mask`, `silkscreen` | `false`: no mask / silkscreen layers in the stackup (default `true`) |
| `design_rules`, `fabrication_notes` | Files of `kibot_resources/stackups` copied to the project |
| `board_rules`, `via_sizes` | *Board Setup → Constraints* (`.kicad_pro` keys) and *Pre-defined Sizes* vias |
| `impedance` | Rows of the impedance table, `[]` for *No controlled impedance* |
| `netclasses` | Net classes created or updated (`Default` included) |
| `netclass_minimums` | The other net classes are raised to these values (never lowered) |

The impedance values come from the JLCPCB
[impedance calculator](https://jlcpcb.com/pcb-impedance-calculator), pairs with
a 0.2032 mm gap:

| Net class | 2 layers (coplanar) | 4 layers (microstrip) | 6 layers (microstrip) |
| --- | --- | --- | --- |
| `50R` single-ended | 0.9182 mm | 0.3586 mm | 0.1509 mm |
| `USB_90R` differential | 0.4625 mm | 0.2906 mm | 0.1537 mm |
| `DIFF_100R` differential | 0.3058 mm | 0.2258 mm | 0.1191 mm |
| Net class clearance | 0.2032 mm | 0.65 mm | 0.3 mm |

- **4 and 6 layers**: microstrips on the outer layers, referenced to the
  adjacent plane. The clearance (~3x the prepreg height) keeps the neighbouring
  copper far enough not to change the impedance.
- **2 layers**: with a 1.43 mm core a microstrip would be ~3 mm wide, so these
  are **coplanar** lines: a ground pour at 0.2032 mm on the same layer, and the
  bottom layer as ground reference. The net class clearance *is* that gap: set
  the clearance of your ground zones at or below 0.2032 mm (*zone properties*)
  so that the net class clearance applies.

Keep only the lines of the impedance table you really use: they are
requirements for the manufacturer. The table is printed when the board setup
has *Impedance controlled* enabled (*Board Setup → Physical Stackup*).

### CNC milling (Makera Z1)

`makera_z1_2l` targets boards isolation-milled on a
[Makera Z1](https://www.makera.com/products/makera-z1-desktop-cnc) (also valid
for the Carvera / Carvera Air), from the Makera double-sided FR4 blanks
(1.5 mm, 35 µm copper). Makera doesn't publish PCB design rules: the values come
from the Makera community (Discord `#all-things-pcb` and `#z1-general`) and the
[Carvera PCB guide](https://github.com/brunostjohn/CarveraPCBGuide).

| Rule | Board Setup / `.kicad_dru` minimum | Net classes | Why |
| --- | --- | --- | --- |
| Isolation (clearance, all copper) | 0.2 mm | `Default` 0.3 mm, `POWER` 0.4 mm | Groove = tip + 2 × depth × tan(angle/2): 0.1 mm 30° V-bit ≈ 0.18 mm at 0.15 mm depth, 60° V-bit ≈ 0.27 mm. 0.2 mm needs the 30° bit and mesh auto-leveling |
| Track width | 0.25 mm | `Default` 0.4 mm, `POWER` 1.0 mm, `CNC_FINE` 0.25 mm | The V-bit eats the tracks (Z1 users report 0.25 mm tracks coming out too thin) |
| Holes | 0.6 mm | | Smallest usual drill bit, holes are **not plated** |
| Vias | 0.6 mm hole / 1.2 mm, ring 0.3 mm | 0.8 / 1.6 mm (sizes 0.6/1.2, 0.8/1.6, 1.0/2.0) | Rivets or wires, set by hand: flange and flip misalignment |
| Pad annular ring | 0.25 mm | | Copper left after drilling and flipping |
| Slots, castellations | 0.8 mm | | Milled with a 0.8 mm corn bit |
| Hole to hole / to copper | 0.5 / 0.3 mm | | Drill wander, FR4 web |
| Copper to edge | 0.5 mm | | Outline cut after the flip |
| Zone spokes | 0.3 mm | | Thin necks are cut or lifted |

The profile also:

- **Raises every other net class** to 0.4 mm tracks / 0.3 mm clearance / 1.6/0.8
  mm vias (`netclass_minimums`), so the router places machinable copper. Values
  are only raised: switching back to a JLCPCB profile keeps them (valid,
  just larger), and it restores the impedance classes.
- Removes the solder mask and silkscreen from the stackup, sets CNC
  fabrication notes (tools, order of operations, copper protection).
- Sets **approximate** impedance classes (below).

Approximate impedance (not controlled): grounded coplanar lines on bare copper,
0.3 mm milled gaps to the ground pour and between the pair, L2 as ground plane.

| Net class | Track width | Gap (pair / ground) | Milling tolerance (±0.025 mm cut per side) |
| --- | --- | --- | --- |
| `50R` single-ended | 1.48 mm | - / 0.3 mm | 47 – 52 Ω |
| `USB_90R` differential | 0.97 mm | 0.3 / 0.3 mm | 84 – 96 Ω |
| `DIFF_100R` differential | 0.70 mm | 0.3 / 0.3 mm | 92 – 108 Ω |

Nobody publishes the dielectric of the copper-clad blanks, so they are computed
with [`coplanar_impedance.py`](../kibot_resources/scripts/coplanar_impedance.py),
a 2D field solver of the cross-section (checked against the closed form and the
JLCPCB calculator), for Dk 4.5 and a 0.05 mm groove cut into the FR4 by the
V-bit. Dk 4.2 – 4.8 moves them by ±3 % and the groove depth (0 – 0.1 mm) by
±4 %: count on about ±10 %, fine for USB 2.0 or short RF runs on a prototype.
Measure the milled gap, keep the ground pour clearance at or below 0.3 mm,
stitch the top pour to L2 with vias along the lines. For another blank, gap or
bit (needs `pip install numpy scipy`, on the host):

```
python3 kibot_resources/scripts/coplanar_impedance.py --er 4.5 --h 1.43 --gap 0.3 --groove 0.05
```

Design for milling:

- **No plated holes**: a through-hole pin only connects on the layer where it
  can be soldered. Route through-hole parts on the bottom (the side opposite the
  part body), or solder the pin on both sides when it is accessible. Keep vias
  few: each one is a rivet or a wire.
- **Fine pitch**: the DRC isolation rule applies to the pads of the footprints,
  so parts below ~0.5 mm pitch report errors. 0402 passives are the practical
  limit. Use `CNC_FINE` only for escapes near fine pitch parts.
- **Pour ground** on both layers: less copper to remove, and MakeraCAM uses the
  zone outlines for the isolation pocket.
- Generate with the usual variants: MakeraCAM / FlatCAM take the Gerbers and
  Excellon drill files of `Manufacturing/Fabrication/Gerbers` (the JLCPCB
  ZIP is still generated, ignore it).

### Manufacturing checks

After each generation, `kibot_launch.sh` checks the files that leave for the
factory with its own parser
([`check_manufacturing.py`](../kibot_resources/scripts/check_manufacturing.py)),
independent of KiCad. Errors fail the run (locally and in CI), and the report
is in the run summary and the pull request comments:

| Files | Checks |
| --- | --- |
| Gerbers and drill (`Fabrication/Gerbers` and the manufacturer ZIPs) | Files complete (M02), board outline closed and its size, copper layers count = drill files layers, every hole inside the board, every plated hole on copper on both outer layers, mask/silkscreen present |
| Assembly | BoM / JLCPCB BoM / pick and place consistency (no DNP part ordered), LCSC code format, JLCPCB pick and place on the pads of the JLCPCB Gerbers (same origin), rotation of the 2-pad parts |
| PDF documents | No unexpanded `${VARIABLE}`, template placeholders (*Board Name*...), revision printed |

Skip them with `./run_kibot.sh --skip-checks`. The generator also empties the
`Gerbers`, `Drill Tables` and `JLCPCB` folders before a PCB run, so no stale
file (i.e. Gerbers of layers renamed by a stackup change) is left behind.

> [!NOTE]
> Two runs on the same commit give the same Gerbers, drill, ODB++, STEP, BoMs
> and reports apart from their timestamps; the 3D renders (and the PDFs
> embedding them) have a tiny rendering noise. The outputs are not byte for
> byte reproducible: with `ci_outputs: commit`, each run makes a small commit.

### Ordering at JLCPCB

PRELIMINARY and higher generate the JLCPCB order files in
`Manufacturing/JLCPCB/` (also attached to the releases), using the JLCPCB
template of KiBot:

| File | Upload it in |
| --- | --- |
| `<project>-_JLCPCB_compress.zip` (Gerbers + drill, JLCPCB naming) | PCB order: *Add Gerber file* |
| `<project>_bom_jlc.csv` (BoM with `LCSC Part #`) | PCB Assembly: *BOM* |
| `<project>_cpl_jlc.csv` (pick and place, JLCPCB rotations) | PCB Assembly: *CPL* |

- Add an **`LCSC`** field (i.e. `C307331`) to the symbols to assemble: only
  the parts with an LCSC code are in the JLCPCB BoM. `LCSC#`, `JLCPCB` and
  similar field names are accepted too. The regular BoMs show it in the
  `LCSC` column.
- The rotations are corrected with the KiBot JLCPCB database; check the
  preview of the JLCPCB assembly page anyway.
- `JLCPCB_TEMPLATE` in `kibot_main.yaml` selects the assembly type: `JLCPCB`
  (SMD only, default) or `JLCPCB_with_THT` (SMD and through hole), and
  `JLCPCB_stencil` / `JLCPCB_stencil_with_THT` to add the paste layers.

### Panel (KiKit)

Small boards are cheaper and assembled faster in a panel. With `panel: true`
in `kibot_settings.yaml`, the PRELIMINARY / CHECKED / RELEASED runs make one
with [KiKit](https://yaqwsx.github.io/KiKit/) in `Manufacturing/Panel`:

| File | Use |
| --- | --- |
| `<name>-panel.png` | Preview |
| `<name>-panel-GERBERS.zip` | Gerbers + drill of the panel: upload it as the PCB ("panel by customer") |
| `<name>-panel.kicad_pcb` | The panel, to open in KiCad |
| `Gerbers/` | The panel Gerbers, also checked by the manufacturing checks |

The layout is set by the `PANEL_*` definitions of `kibot_main.yaml`:

| Definition | Default | |
| --- | --- | --- |
| `PANEL_CUTS` | `mousebites` | `mousebites`: tabs with 0.5 mm holes, any board shape. `vcuts`: boards touching, V-cut lines on Edge.Cuts; rectangular boards **with sharp corners** only (rounded corners leave open outline pieces, reported by the checks) |
| `PANEL_ROWS`, `PANEL_COLS` | 2, 2 | Boards in the panel |
| `PANEL_SPACE`, `PANEL_TAB_WIDTH`, `PANEL_TAB_COUNT` | 2 mm, 3 mm, 2 | Mouse bites: routed gap, tab width, tabs per board edge |
| `PANEL_RAIL_WIDTH` | 5 mm | Top/bottom rails, with 3 tooling holes (1.152 mm, JLCPCB) and 3 fiducials |
| `PANEL_MIN_SIZE` | 70 mm | The rails grow to reach it (JLCPCB Standard PCBA minimum), 0 for none |

The top rail shows `${BOARD_NAME} rev ${REVISION}`. For more options (other
frames, texts, copper fill of the rails...), edit
`kibot_yaml/kibot_out_panelize_<cuts>.yaml`
([KiKit options](https://yaqwsx.github.io/KiKit/latest/panelization/cli/)).
The assembly files (BoM, pick and place) stay the ones of the single board:
JLCPCB asks for unique designators on a panel, so order the assembly of a
panel from the single-board files with "Panel by JLCPCB", or adapt the files.

### 3D renders: KiCad or Blender

The PNG renders (`Images/*-top.png`, `-bottom.png`, `-angled_top.png`,
`-angled_bottom.png`) are embedded in the assembly document, the schematic and
the README. Two engines are available, with the same output names:

| `RENDER_ENGINE` | Tool | Speed | Result |
| --- | --- | --- | --- |
| `kicad` (default) | KiCad 3D viewer (`kicad-cli pcb render`) | ~10 s per view | Like the KiCad 3D viewer |
| `blender` | Blender + [pcb2blender](https://github.com/30350n/pcb2blender) | minutes per view (CPU) | Photo-realistic |

Locally, select it in `kibot_main.yaml`, or for one run:

```
./run_kibot.sh -v CHECKED -r blender
```

Blender runs inside the docker image, on the CPU (KiBot doesn't use the GPU),
with all the CPUs given to Docker. Measured for one view (2000 px, 50 samples):

| Where | Per view | 4 views |
| --- | --- | --- |
| Local PC, 16 CPUs | ~2 min 15 s | ~9 min |
| GitHub runner, public repository (4 CPUs) | ~7 min | ~27 min |
| GitHub runner, private repository (2 CPUs) | ~14 min | ~55 min |

That's why **the CI always uses the engine of `ci_render_engine`** in
`kibot_settings.yaml` (`kicad` by default), whatever `RENDER_ENGINE` says.
Render with Blender locally when you need photo-realistic images (a manual run
of the workflow can still choose `blender`). The CI publishes KiCad renders:
keep your Blender renders for local use, or copy them elsewhere if you commit
them (`ci_outputs: commit` would replace them at the next run).

Parameters:

- `3D_RESOLUTION`: image size in pixels.
- `KICAD_3D_ROT_X/Y`: angled views for the KiCad engine, in degrees.
  Avoid rotations around Z: the KiCad renderer can't zoom out, a rotated
  board would be clipped. `KICAD_3D_RAYTRACING`: ray tracing (adds a floor
  shadow around the board).
- `BLENDER_3D_ROT_X/Y/Z`, `BLENDER_3D_ROT_Z_BOTTOM`: angled views for Blender.
  `BLENDER_SAMPLES`: 10 for a draft, 100+ for the final render. The board is
  exported once to `3D/<project>.pcb3d`, which can also be opened in Blender.

The 3D models referenced by the footprints are downloaded by KiBot when
missing, and cached (docker volume `kibot_3d_models_cache` locally, GitHub
cache in CI).

### Shared libraries (symbols, footprints, 3D models)

- **Symbols and footprints** need nothing: KiCad stores a copy of them in the
  schematic and the PCB, KiBot uses these copies. (DRC/ERC may only report
  non blocking "library mismatch" warnings if the libraries are missing.)
- **KiCad stock 3D models** (`${KICAD10_3DMODEL_DIR}`...) are downloaded by
  KiBot and cached.
- **3D models of your own libraries** must be reachable in the container.
  Use one of these methods:

**1. Library repository as a git submodule (recommended).** Declare each
library in `kibot_settings.yaml`, `<VAR>` being the KiCad path variable used
by its footprints (3D models referenced as `${VAR}/...`):

```yaml
lib_KEJLABS_LIB_url: https://github.com/kejlabs/kicad-libs.git   # git URL (https)
lib_KEJLABS_LIB_path: lib/kejlabs-kicad-libs                      # optional, default lib/<repository name>
lib_KEJLABS_LIB_branch: main                                      # optional, default branch of the repository
```

Several libraries: one block per variable (`lib_OTHER_LIB_url`...). Then:

```
./run_kibot.sh --setup-libs      # adds/updates the git submodules from the settings
git add .gitmodules lib && git commit -m "Add libraries"
```

| Command | Effect |
| --- | --- |
| `--setup-libs` | Adds the missing libraries as submodules, applies URL/branch changes, checks out the recorded versions |
| `--update-libs` | Moves the libraries to the latest commit of their branch (commit the change to use it in CI) |
| `--check-libs` | Fails if a declared library is missing (used by the CI) |

(Same options for `.
un_kibot.ps1`, except `--check-libs`.)

- The project records the exact commit of each library: an old release is
  always regenerated with the library version it was designed with.
- The runners and the CI define `${VAR}` in the container, pointing to the
  library folder. On your PC, define the same variable in KiCad
  (**Preferences → Configure Paths**), pointing to the submodule folder or to
  your usual copy of the library.
- Clone projects with `git clone --recurse-submodules`, or run
  `git submodule update --init --recursive` after a pull (the runners warn
  when a library folder is empty).
- **Private library repository**: create a fine-grained token with *Contents:
  read-only* access to the library repository and store it as the
  `KICAD_LIBS_TOKEN` secret of the project repository (**Settings → Secrets
  and variables → Actions**). The workflow uses it only to fetch the
  submodules; the outputs are still pushed with the default `GITHUB_TOKEN`.
  Public libraries don't need it.
- The CI fails early with an explicit message if a declared library is not a
  submodule of the project (`--setup-libs` not run or not committed).
- KiRI (diff between commits) also works with submodules: the launcher makes
  git fetch them from the local copy, no network needed.
- A library already copied in the project (not a submodule) only needs
  `lib_<VAR>_path` to define `${VAR}`.

**2. Models relative to the project.** Libraries copied inside the project
(i.e. `lib/`) and models referenced as `${KIPRJMOD}/lib/3d/part.step` work
without configuration.

**3. Embedded 3D models.** KiCad 10 can embed the 3D models in the footprints
of the board (footprint properties → *3D Models* → embed). The board becomes
self-contained, at the cost of a bigger `.kicad_pcb`.

When a model can't be found, the render is generated without it and KiBot
reports `Missing 3D model for <ref>` (W047) and an error.
### Assembly variants (KiCad 10)

One schematic, several builds (i.e. a `LITE` version without some parts, or
with other values). The template uses the **native KiCad 10 variants**:

1. *File → Schematic Setup → Variants*: add the variant (name and
   description).
2. *Tools → Edit Symbol Fields*: per variant, mark the parts not fitted (DNP)
   or change their fields. **When you change a value, also change `LCSC`,
   `Manufacturer Part Number`...**, or the factory fits the original part: the
   manufacturing checks warn when one LCSC code is used for different values.
3. *Tools → Update PCB from Schematic*.
4. List the variants in `kibot_settings.yaml`:

   ```yaml
   assembly_variants: LITE
   ```

After each CHECKED / RELEASED run (local or CI), each variant is generated in
`Variants/<name>`, with the `_<name>` suffix: BoMs, pick and place, iBoM,
assembly PDF, schematic PDF (DNP parts marked), 3D renders and STEP, JLCPCB
order files. The Gerbers, fabrication documents and KiRI are the ones of the
main run (same PCB). The releases attach the schematic, assembly files and
JLCPCB ZIP of each variant; the pull request comment shows the manufacturing
checks of each variant.

Run a single variant with `./run_kibot.sh -v LITE`, skip them with
`--no-variants`. KiBot needs the variant selected on the command line to be
declared in its configuration: `kibot_main.yaml` declares one generic `kicad`
variant named by the `ASSEMBLY_VARIANT` definition, set by the launcher.

### Diagrams (draw.io)

KiCad is not made for block diagrams: draw them in [draw.io](https://www.drawio.com/)
(desktop app, web app or VS Code extension) and import them in the sheets as
pictures. The template's *Block Diagram* sheet shows an example.

1. Save the diagram as `Diagrams/<Sheet name>.drawio`, named after its sheet:
   `Diagrams/Block Diagram.drawio` goes in `Block Diagram.kicad_sch`. A
   `.svg` or `.png` (from another tool) works the same way.
2. Generate as usual (`./run_kibot.sh`, `.\run_kibot.ps1`, or push): before
   each generation, the diagrams changed since their last import are
   imported automatically (`auto_diagrams: true` in `kibot_settings.yaml`),
   locally and in CI. Nothing is done when they are unchanged. To import
   without generating:

   ```
   ./run_kibot.sh --diagrams          # or .\run_kibot.ps1 --diagrams
   ```

   The `.drawio` files are exported to PNG (transparent, 3x) by the draw.io
   exporter image (`drawio_image` in `kibot_settings.yaml`, no draw.io
   installation needed), then embedded in the sheets (256 colors palette,
   ~100 KB per diagram). The first import centers the picture below the page
   title and fits it to the page; the next ones only replace it, **keeping
   the position and size** you gave it in KiCad. `--diagrams --fit` places it
   again.
3. Commit the updated sheet and `Diagrams/imported.json` (hashes of the
   imported sources), like any change of the schematic. In CI, a diagram
   changed without its sheet is imported for the run (the documents are
   right) with a warning to commit the sheet; with `ci_outputs: commit` the
   sheet is committed with the outputs.

Close the sheet in KiCad before importing. The picture prints sharp in the
schematic PDF (~300 dpi on A3); multi-page `.drawio` files: one diagram per
file (the first page is used). Multi-board repositories: `<board>/Diagrams`.

### Multi-board repositories

Several boards of one product (i.e. a main board and an I/O board) can share
the repository, the pipeline and the version. Each KiCad project goes in its
own folder, with its outputs:

```
CHANGELOG.md, kibot_settings.yaml, kibot_yaml/, kibot_resources/  (shared)
hw/main/   KiCad project (+ Templates/, Logos/, Images/), kibot_board.yaml
hw/io/     KiCad project (+ Templates/, Logos/, Images/), kibot_board.yaml
```

1. Move the KiCad project of the template (the `*.kicad_*` files, `Templates/`,
   `Logos/`, `Images/`) into `hw/main`, copy it to `hw/io`.
2. Name them (files, board name in `hw/io/kibot_board.yaml`):

   ```
   ./run_kibot.sh --init --dir hw/io --name io_board --board "IO board"
   ./run_kibot.sh --init --dir hw/main --name main_board --board "Main board"
   ```

3. In `kibot_settings.yaml`: `boards: hw/main hw/io`.

Each run then generates all the boards (`./run_kibot.sh --board hw/io` for
one), each in its folder; the CI summary and pull request comment show the
checks per board, and the release attaches the files of all the boards.
`kibot_board.yaml` overrides `kibot_main.yaml` definitions for one board
(`KEY: value`: `BOARD_NAME`, `PANEL_ROWS`, `RENDER_ENGINE`...). The stackup
profiles need the board: `./run_kibot.sh --stackup jlcpcb_2l --board hw/io`.

Shared by all the boards: `CHANGELOG.md` (one version for the product, one
revision history), the settings (`panel`, `assembly_variants`: a variant is
generated for the boards defining it), the fabrication notes and impedance
table of `kibot_resources/templates` (boards with different stackups: keep
them generic).

## RUNNING LOCALLY (DOCKER)

Install and start [Docker Desktop](https://docs.docker.com/desktop/) (or the
docker engine on Linux). The runners pull the image from
`kibot_settings.yaml` (~5 GB the first time), mount the project in the
container and run [`kibot_launch.sh`](../kibot_launch.sh), exactly like the
CI.

| Linux / macOS / WSL / Git Bash | Windows PowerShell |
| --- | --- |
| `./run_kibot.sh` | `.\run_kibot.ps1` |

```
./run_kibot.sh                        Variant from kibot_settings.yaml
./run_kibot.sh -v DRAFT               Schematic PDF, netlist and BoM
./run_kibot.sh -v CHECKED -r blender  Everything, ERC/DRC, Blender renders
./run_kibot.sh -v LITE                Assembly variant, outputs in Variants/LITE
./run_kibot.sh --version 1.2.0        Force the revision printed in the documents
./run_kibot.sh --log-dir kibot_logs   Keep the KiBot debug logs (and the checks report)
./run_kibot.sh --skip-checks          Don't run the manufacturing checks
./run_kibot.sh --costs                XLSX BoM with costs (KiCost)
./run_kibot.sh --help                 All the options
./run_kibot.sh --serve [PORT]         Browse the outputs on http://localhost:8000
./run_kibot.sh --shell                Interactive shell in the container
```

(same arguments for `.\run_kibot.ps1`). Everything after `--` is passed to
KiBot.

### Browsing the outputs in a web browser

| Page | Shows | How to open it |
| --- | --- | --- |
| `index.html` | Every output, with previews | `./run_kibot.sh --serve`, then http://localhost:8000 |
| `Manufacturing/Assembly/<name>-ibom.html` | Interactive BoM: PCB top/bottom, click a BoM line to highlight the parts (pin 1 marked), checkboxes for hand assembly | Double-click it (self-contained, works offline) |
| `KiRI/index.html` | Schematic and PCB of the last commits, with a visual diff | `./run_kibot.sh --serve` |

The KiCanvas viewer (interactive schematic and PCB) is configured but
disabled: KiCanvas can't read KiCad 10 PCBs yet. To try it, uncomment
`@HTML_KICANVAS_OUTPUT@` in `all_group` of `kibot_main.yaml`. On GitHub,
download the run artifacts or the `kibot-outputs` branch and serve them
locally (GitHub doesn't serve HTML pages from a branch). If KiCad 10 and KiBot are installed natively, `./kibot_launch.sh` can
be used directly with the same options.

Environment variables of the runners:

| Variable | Default | Purpose |
| --- | --- | --- |
| `KIBOT_IMAGE` | `docker_image` of `kibot_settings.yaml` | Use another image |
| `KIBOT_3D_CACHE_DIR` | docker volume `kibot_3d_models_cache` | Host directory for the 3D models cache (used by the CI) |
| `KIBOT_PROJECT_HOST_DIR` | project directory | Project path seen by the docker daemon, for nested docker setups ([act](https://github.com/nektos/act), dev containers) |

The runners never modify the `.kicad_pcb` / `.kicad_sch` files: KiBot saves
them while working, `kibot_launch.sh` restores them at the end (uncommitted
changes included). The text variables of the `.kicad_pro` are updated on
purpose, so the KiCad GUI shows the same values as the documents.

> [!WARNING]
> Don't commit the outputs generated locally: the CI publishes them
> (`ci_outputs`) and commits them on releases. With `ci_outputs: commit`,
> local and CI outputs would conflict.

### Calculating board costs (KiCost)

Copy [`kibot_yaml/kicost_config_local_template.yaml`](../kibot_yaml/kicost_config_local_template.yaml)
to `kibot_yaml/kicost_config_local.yaml` (ignored by git), fill in the API
keys of the distributors and run `./run_kibot.sh --costs`. The spreadsheet is
created in `Manufacturing/Assembly`. KiCost expects particular field names: the
fields set in `MPN_FIELD` and `MAN_FIELD` are renamed during the run.

## CI/CD ON GITHUB ACTIONS

The workflow [`.github/workflows/ci.yaml`](../.github/workflows/ci.yaml) runs
`./run_kibot.sh` on an `ubuntu-latest` runner: exactly the same command, docker
image and script as a local run. The 3D models are cached between runs.

> [!IMPORTANT]
> Allow the workflow to push: **Settings → Actions → General → Workflow
> permissions → Read and write permissions**.

| Event | Variant | Result |
| --- | --- | --- |
| Push on `main` or `dev` | `variant` of `kibot_settings.yaml` | Outputs published according to `ci_outputs` (see below) |
| Pull request to `main` or `dev` | `pr_variant` (`CHECKED`) | ERC/DRC and documents, summary comment on the pull request, nothing committed |
| Push of a tag `x.y.z` | `RELEASED` | `CHANGELOG.md` updated, outputs committed on `main`, GitHub release with assets |
| Manual run (Actions tab) | Choice (or settings) | Same as a push, the render engine can be chosen too |

**Where the outputs go** (`ci_outputs` in `kibot_settings.yaml`):

| `ci_outputs` | Push on a branch | Release (tag) |
| --- | --- | --- |
| `branch` (default) | Snapshot of the project + outputs force-pushed to `kibot-outputs/<branch>` (one commit, replaced at each run): browse it on GitHub, your branch history stays small | Committed on `main` |
| `commit` | Committed on the branch itself at each run (KDT behaviour) | Committed on `main` |
| `none` | Workflow artifacts only | Committed on `main` |

With `branch`, the generated `README.md` (renders, links) is visible on the
`kibot-outputs/<branch>` branch, and on `main` after each release.

**Run summary**: each run writes a summary on its page (*Actions* tab): status,
revision, ERC/DRC errors and warnings by type, and links to the documents. For
pull requests the same summary is posted as a comment, updated at each push.
It includes the [manufacturing checks](#manufacturing-checks). The check fails
if KiBot fails (i.e. ERC errors) or if the manufacturing checks find errors.

The 3D renders use `ci_render_engine` (`kicad` by default, see
[3D renders](#3d-renders-kicad-or-blender)). Library submodules are fetched
automatically (`KICAD_LIBS_TOKEN` secret for private ones, see
[Shared libraries](#shared-libraries-symbols-footprints-3d-models)).

The logs and the outputs are always available as workflow artifacts
(`kibot_logs`, `kibot_outputs_<VARIANT>`).
Pushes that only modify Markdown files (or empty commits) don't trigger the
workflow: use a manual run to regenerate. Merging a pull request runs the
workflow on the target branch, so its outputs are up to date.

### Testing the workflow locally

The workflow can be run locally with [act](https://github.com/nektos/act)
(`gh extension install nektos/gh-act`), in Git Bash / Linux:

```
gh act workflow_dispatch --input variant=CHECKED \
  -P ubuntu-latest=catthehacker/ubuntu:act-latest --bind \
  --env KIBOT_PROJECT_HOST_DIR="$(pwd)" -s GITHUB_TOKEN="$(gh auth token)"
```

act's artifact server doesn't support `actions/upload-artifact@v7`: switch
to v4 for local tests. The commit steps really push to `origin`.

### Workflow and semantic versioning

- Work on the `dev` branch, `main` receives pull requests and releases.
- Record the changes under `## [Unreleased]` in [`CHANGELOG.md`](../CHANGELOG.md),
  following [semantic versioning for hardware](https://www.maskset.net/blog/2023/02/26/semantic-versioning-for-hardware/).
- Push: the outputs are generated and committed. Pull them back
  (`git pull`) before working again. Avoid modifying the `.kicad_pro` file
  before pulling: KiBot updates its text variables.
- To synchronise the revision history sheet of the schematic with
  `CHANGELOG.md`, add the text variables of each version in
  [`kibot_pre_set_text_variables.yaml`](../kibot_yaml/kibot_pre_set_text_variables.yaml)
  and use them in the text boxes of the *Revision History* sheet:

  ```yaml
  - variable: '@RELEASE_TITLE_VAR@1.0.0'
    command: '@GET_TITLE_CMD@ 1.0.0'
  - variable: '@RELEASE_BODY_VAR@1.0.0'
    command: '@GET_BODY_CMD@ 1.0.0'
  ```

- Release: merge `dev` into `main` (pull request), then tag `main`:

  ```
  git checkout main && git pull
  git tag 1.0.0
  git push origin 1.0.0
  ```

  The `[Unreleased]` section becomes `[1.0.0] - <date>`, the `RELEASED`
  documents are generated with revision `1.0.0` and committed on `main`, and
  a GitHub release is created with the PDFs, fabrication ZIP, BoM, pick and
  place, STEP and renders.

- After a release: `git pull` on `main`, then `git checkout dev && git rebase main`.

### Dependency updates (Renovate)

The KiBot image is pinned in `kibot_settings.yaml` (reproducible outputs) and
the actions in the workflow. [`.github/renovate.json`](../.github/renovate.json)
lets [Renovate](https://docs.renovatebot.com/) open a pull request on Mondays
when a new version is out:

- **KiBot image**: new KiBot versions and KiCad 10.0.x patches (tags ordered
  by KiBot version, KiCad patch, image build). KiCad 10.1+ changes the file
  formats and is never proposed: migrate by hand. `dev_` tags are ignored.
- **GitHub Actions**: grouped in one pull request.

The pull request check generates the outputs with the new version: read its
comment and compare the artifacts before merging. To enable it, install the
free [Renovate GitHub app](https://github.com/apps/renovate) on the
repository; it opens an onboarding pull request first. To disable it, delete
`.github/renovate.json`.

## PROJECT CONVERSION GUIDE

To use this pipeline with an existing KiCad 10 project, copy `kibot_yaml/`,
`kibot_resources/`, `Templates/`, `.github/`, `kibot_launch.sh`,
`run_kibot.*`, `kibot_settings.yaml`, `CHANGELOG.md`, `.gitignore` and
`.gitattributes`, then set up the schematic and the PCB as described below.
Older KiCad files can be converted with `kicad-cli sch upgrade` /
`kicad-cli pcb upgrade` (available in the container: `./run_kibot.sh --shell`).

### Schematic

- **File → Page Settings → Drawing Sheet**: `Templates/Template_GIT.kicad_wks`.
  Set `Revision` to `${REVISION}` and `Company` to `${COMPANY}`, export to all
  sheets.
- Table of contents: the `${SHEET_NAME_<N>}` text variables are replaced by
  the name of sheet N (up to 40, see `kibot_pre_set_text_variables.yaml`).
- `${VARIANT}` is replaced by the project status, `${RELEASE_DATE}` and
  `${RELEASE_DATE_NUM}` by the date of the last commit (`17-Dec-2024`,
  `2024-12-17`), `${GIT_HASH_SCH}` / `${GIT_HASH_PCB}` by the last commit of
  the files.
- Images: a text box named `kibot_image_<output>` is replaced by the image of
  that output, i.e. `kibot_image_png_3d_viewer_angled_top`.

### PCB

The user layer names must match the `LAYER_*` definitions
(**File → Board Setup → Board Stackup → Board Editor Layers**). Named groups
(**Right-Click → Grouping → Group Items**, then **E** to rename) mark where
KiBot draws tables and images; their size and position set the size and
position of the element.

| Layer | Description | Items |
| --- | --- | --- |
| **TitlePage** | First page of the assembly document | Groups `kibot_image_png_3d_viewer_angled_top` and `kibot_image_png_3d_viewer_angled_bottom` |
| **User.Comments** | Free for the project | |
| **F.DNP / B.DNP** | Red crosses on *Do Not Populate* parts | Keep empty |
| **DrillMap** | Drill drawings and tables of the fabrication document | Group `kibot_table_csv_drill_table` |
| **F.TestPoint / B.TestPoint** | Testpoint highlighting | Keep empty |
| **F.AssemblyText** | Component count, assembly notes, top 3D render | Groups `kibot_table_csv_comp_count`, `kibot_image_png_3d_viewer_top`, text `${ASSEMBLY_NOTES}` |
| **B.AssemblyText** | Bottom 3D render | Group `kibot_image_png_3d_viewer_bottom` |
| **F.Dimensions** | Stackup, impedance table, fabrication notes, dimensions | Groups `kibot_fancy_stackup`, `kibot_table_csv_impedance_table`, text `${FABRICATION_NOTES}` |
| **B.Dimensions** | Dimensions seen from the bottom | KiCad dimension tool |
| **F.TestPointList** | Top testpoint table | Group `kibot_table_csv_testpoints_top` (slicing allowed: `[:32]`, `[32:]`) |
| **B.TestPointList** | Bottom testpoint table (printed mirrored) | Group `kibot_table_csv_testpoints_bottom` |

Testpoint coordinates are relative to the drill/place origin
(**Place → Drill/Place File Origin**), usually the bottom left corner of the
board. Testpoints are the symbols with a `TP` reference.

## DIRECTORY STRUCTURE

```
├─ .github           # GitHub Actions workflow, Renovate configuration
├─ AGENTS.md          # Instructions for coding agents (CLAUDE.md points to it)
├─ 3D                 # STEP / PCB3D models (generated)
├─ Computations       # Misc calculations (optional)
├─ Diagrams           # draw.io diagrams imported in the sheets (--diagrams)
├─ docs               # This guide, CONFIG_REFERENCE.md (generated reference)
├─ HTML               # Web page to browse the outputs (generated)
├─ Images             # Pictures and 3D renders
├─ kibot_resources
│  ├─ colors          # Color theme, installed by KiBot
│  ├─ fonts           # Liberation fonts (Arial / Times New Roman metrics)
│  ├─ scripts         # Scripts used by the text variables
│  └─ templates       # Templates of the generated reports (notes, README...)
├─ kibot_yaml         # KiBot configuration
├─ KiRI               # KiRI diff viewer (generated)
├─ Logos              # Logos (optional)
├─ Manufacturing
│  ├─ Assembly        # Assembly PDF, BoM, pick and place, notes
│  ├─ Fabrication     # Fabrication PDF, Gerbers PDF, ZIP, ODB++, notes
│  │  ├─ Drill Tables
│  │  └─ Gerbers
│  └─ JLCPCB          # JLCPCB order files (Gerbers ZIP, BoM, pick and place)
├─ PCB                # PCB routing PDF (generated)
├─ Reports            # ERC/DRC reports (generated)
├─ Schematic          # Schematic PDF (generated)
├─ Templates          # Drawing sheets
├─ Testing/Testpoints # Testpoint lists (generated)
├─ Variants           # Outputs of the assembly variants (generated)
├─ kibot_launch.sh    # Runs KiBot (in the container, locally and in CI)
├─ kibot_settings.yaml# Pipeline settings
├─ run_kibot.sh       # Local runner (Linux/macOS/WSL/Git Bash)
└─ run_kibot.ps1      # Local runner (Windows PowerShell)
```

## TROUBLESHOOTING

- **PRELIMINARY/CHECKED fails on an empty board**: some outputs need a board
  outline, components and holes (interactive BoM, drill tables and the
  fabrication document, STEP). The starter board of the template has them;
  if you delete it, use `DRAFT` until your board has an outline and parts.
- **A failing output**: KiBot continues with the other outputs
  (`--dont-stop`) but the run returns an error. Look for `ERROR` in the
  console or in the logs (`--log-dir`, `kibot_logs` artifact in CI).
- **ERC/DRC errors** in CHECKED/RELEASED: see `Reports/`. The DRC doesn't stop
  the generation, the ERC does.
- **Clipped KiCad 3D render**: reduce `KICAD_3D_ROT_*`, don't rotate around Z.
- **Blender is slow**: reduce `BLENDER_SAMPLES` / `3D_RESOLUTION`, or use it
  only for releases (manual run with the *blender* render engine).
- **Scripts fail with `\r` errors**: the shell scripts must have LF line
  endings, enforced by `.gitattributes`. Re-checkout them if needed.
- **Files owned by root on Linux**: `run_kibot.sh` gives them back to your
  user at the end of the run.
- **`fatal: not a git repository` in a git worktree**: the `.git` file of a
  worktree points outside the folder mounted in the container. Run the
  generation from a normal clone.

## DIFFERENCES WITH KDT_HIERARCHICAL_KIBOT

- KiCad 10 files and image (`kicad10_auto_full`), single `all_group` (ODB++
  always generated).
- JLCPCB design rules and stackup profiles (4/6 layers) instead of PCBWay 6 layers.
- New outputs: **Gerbers PDF** (`pdf_gerbers`) and **PCB routing PDF**
  (`pdf_pcb_routing`).
- **Render engine parameter** (`RENDER_ENGINE: kicad | blender`), with a
  single PCB3D export shared by the Blender renders.
- **Same command locally and in CI**: the workflow runs `./run_kibot.sh`
  (a non-interactive `docker run` of `kibot_launch.sh`) instead of the KiBot
  GitHub action.
- Pipeline settings (variant, image) in `kibot_settings.yaml` instead of the
  workflow file; manual runs can override the variant and the render engine.
- Release: CHANGELOG update, outputs and merge on `main` in a single push.
- KiBot runs with `--dont-stop --fail-on-ignored`.

## CREDITS

- © 2026 [KejLabs](../LICENSE), MIT license.
- [Vincent Nguyen](https://github.com/nguyen-v) for
  [KDT_Hierarchical_KiBot](https://github.com/nguyen-v/KDT_Hierarchical_KiBot)
  (MIT), the base of this template: project, drawing sheets, KiBot
  configuration, scripts and documentation.
- [Salvador E. Tropea (@set-soft)](https://github.com/set-soft) for
  [KiBot](https://github.com/INTI-CMNB/KiBot) and the
  [KiCad automation images](https://github.com/INTI-CMNB/kicad_auto).
- [KiCost](https://github.com/hildogjr/KiCost), [KiRI](https://github.com/leoheck/kiri),
  [pcb2blender](https://github.com/30350n/pcb2blender),
  [InteractiveHtmlBom](https://github.com/openscopeproject/InteractiveHtmlBom).
