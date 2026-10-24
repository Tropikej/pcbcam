# pcbcam

KiCad project + versioned YAML recipe → checked, machine-ready G-code for PCB
isolation milling on a **Makera Z1**, using [pcb2gcode](https://github.com/pcb2gcode/pcb2gcode)
as the CAM engine.

> **Status: v0.1, not yet verified on hardware.** Every generated file says so in
> its header. Air-cut first, then cut scrap.

## What it does

```
KiCad project ──► kicad-cli export (F.Cu, Edge.Cuts, Excellon mm)
                    │
recipe.yaml ──┐     ▼
tools.yaml ───┴─► preflight ── project DRC (your rules, errors only)
                    │       ── machine DRC (rules derived from your tools)
                    │       ── every hole size has a drill / milling tool
                    ▼
                 millproject ──► pcb2gcode (pinned Docker image, warnings fatal)
                    ▼
                 Z1 post-processor ──► validator ──► gcode/*.nc + manifest.json
```

* **V-bits are modelled properly.** pcb2gcode only knows cylindrical cutters, so
  the V-bit's effective width at the cut depth (`tip + 2·depth·tan(angle/2)`, or
  your measured calibration points) is computed and passed as the diameter.
* **"Can my tools cut this board?" is a DRC.** pcbcam writes KiCad custom rules
  (minimum gap = V-bit width + margin, track width, annular ring, hole-to-hole,
  edge gap) and runs `kicad-cli pcb drc` on a scratch copy, so violations come
  back with positions, exactly like a normal DRC. Your project is never modified.
* **pcb2gcode warnings fail the build.** A "contentions" warning means the last
  cutter would eat into copper.
* **Z1 post-processing**, from pcb2gcode 3.0.4's source and the Z1 firmware docs:
  `G04 P` milliseconds → seconds, `T1`/`M6`/`M0` → `M6 T1`, per-tool `M3 S<rpm>`,
  drops `M0 M9 G64 G91.1 G80`; optional `G32` height-map probing before isolation.
* **Validator** rejects anything outside the profile's allowed words, inch or
  relative mode, feeds/rpm over limits, XY outside the stock, Z deeper than the
  stock, and cutting below Z0 with the spindle off.

## Outputs

```
manufacturing-output/
├── gcode/
│   ├── 01_isolation_front.nc
│   ├── 02_drill.nc
│   ├── 03_milldrill.nc        holes larger than any drill
│   └── 04_outline.nc          with tabs; always run last
├── preview/*.svg              pcb2gcode toolpath renders
├── manifest.json              versions, hashes, tools, hole assignments
└── work/                      exported Gerbers, millproject, logs, DRC reports
```

Work zero is the **lower-left corner of the stock**; the board sits at
`placement.margin_*` from it.

## Usage

```sh
uv sync
docker build -t pcbcam/pcb2gcode:3.0.4 docker/pcb2gcode

# in a KiCad project containing manufacturing/recipe.yaml + tools.yaml
uv run pcbcam tools  path/to/project   # resolved tools + the clearance they need
uv run pcbcam check  path/to/project   # export + DRC + hole checks only
uv run pcbcam build  path/to/project   # everything
```

Start from [`examples/manufacturing/`](examples/manufacturing). Recipes are strict:
unknown keys are errors and every value has its unit in its name.

Environment:

| Variable | Default | Purpose |
|---|---|---|
| `KICAD_CLI` | PATH, then `C:\Program Files\KiCad\*\bin` | kicad-cli to use |
| `PCBCAM_PCB2GCODE_IMAGE` | `pcbcam/pcb2gcode:3.0.4` | Docker image |
| `PCBCAM_PCB2GCODE_BIN` | unset | run a local pcb2gcode instead of Docker |

## Not done yet

* Double-sided boards (back isolation, mirroring, registration pins).
* Slots / oval holes (reported as errors).
* Verification on a real Z1: tool-change behaviour in Makera Studio, `G32`
  probing tool and whether compensation survives an imported file.

## Tests

```sh
uv run pytest            # unit tests
uv run pytest -m integration   # needs kicad-cli + the Docker image
```
