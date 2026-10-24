# KiCad → CNC PCB Manufacturing on the Makera Z1

**Conversation summary — 22 October 2026**

## 1. Goals and context

The objective is to machine simple PCBs with a **Makera Z1 CNC**, starting from completed designs in **KiCad**, while evolving toward a reproducible, largely automated manufacturing workflow.

Desired end-to-end experience:

1. Finish a KiCad PCB project.
2. Push the project to a Git repository, preferably hosted on **Forgejo**.
3. Trigger a CI/CD job that exports manufacturing files, selects predefined cutters and parameters, and generates G-code.
4. Review/visualize toolpaths and, ideally, simulate material removal similarly to the **Fusion 360 CAM simulation**.
5. Run jobs on the Z1, using a **grid of surface probing points** to compensate for copper-clad unevenness and imperfect fixturing.
6. Manufacture **double-sided PCBs** with repeatable alignment using **registration pins** and an explicitly controlled flip/mirror operation.

The user wants preset-driven, repeatable production rather than manually rebuilding CAM operations for every KiCad revision.

## 2. Candidate software and trade-offs

| Software | Purpose | Strengths | Limitations / follow-up |
|---|---|---|---|
| **pcb2gcode** | Gerber/Excellon → PCB milling G-code | CLI-oriented; reusable `millproject` configuration; good CI/CD fit; front/back copper, drilling and board outline operations | Not a full graphical tool-library CAM system; G-code compatibility and custom formatting must be verified for the Z1 |
| **FlatCAM**, including Beta/Evo builds | Graphical PCB CAM | Visual Gerber inspection, toolpath generation, drills, isolation and copper clearing; project settings and tool database functionality in some builds | GUI-centered workflows are less convenient for fully headless CI; feature support varies by build |
| **KiCNC** | GUI/front-end around pcb2gcode | Saved jobs/settings; approachable interactive workflow | Verify current feature coverage and development state |
| **pcb2gcodeGUI** | GUI/front-end for pcb2gcode | Provides graphical access to pcb2gcode settings | Less compelling for intended automation workflow |
| **Makera CAM / Makera Studio** | Makera-native CAM/controller workflow | Official Makera-oriented toolpaths, machine setup and probing; useful baseline | Must test how imported third-party G-code interacts with probing/autoleveling and machine conventions |
| **CAMotics** | Offline G-code stock-removal simulation | Open-source, 3-axis, cutters and rendered machining preview | Does not reproduce Makera's live probing corrections or provide comprehensive machine/fixture collision checking |
| **makera-pcb** community project | Makera/Carvera Air-oriented PCB CAM | Interesting reference implementation with CLI, tool library and preview | Reported single-sided focus and Carvera Air target; not established as Z1-compatible |

**Recommendation:** Use **KiCad CLI + pcb2gcode** for deterministic, headless generation, initially compare outputs with **FlatCAM** and/or **Makera CAM**, and use **SVG overlays and CAMotics** for preview. Retain the option to change CAM engines later.

Links mentioned:

- pcb2gcode: https://github.com/pcb2gcode/pcb2gcode
- pcb2gcodeGUI: https://github.com/pcb2gcode/pcb2gcodeGUI
- KiCNC: https://microfire.co/articles/kicnc
- FlatCAM: https://www.flatcam.org/
- FlatCAM manual: https://flatcam.org/manual/basics.html
- CAMotics: https://camotics.org/
- Makera CAM guide: https://wiki.makera.com/en/software/MakeraCAM_userguide
- Community makera-pcb: https://github.com/sthamann/makera-pcb

## 3. Proposed CI/CD architecture

```text
KiCad source files (.kicad_pcb, .kicad_sch, .kicad_pro)
                 │
                 ▼
        Forgejo / Git push
                 │
                 ▼
          CI/CD runner
                 │
           KiCad CLI
                 │
                 ▼
      Gerbers + Excellon drills
                 │
                 ▼
  Preset / tool / fixture resolution
                 │
                 ▼
 pcb2gcode + registration generation
                 │
                 ├─────────────► SVG toolpath overlays
                 │                  + optional CAMotics simulation
                 ▼
       G-code validation
                 │
                 ▼
 CI artifacts: G-code, previews,
 Gerbers, manifest, fabrication plan
                 │
                 ▼
  Makera controller / Z1 local job
       ├─ Fixture and set origin
       ├─ Probe actual copper surface
       ├─ Run front-side operations
       ├─ Flip/register board
       ├─ Reprobe second side
       └─ Run back-side + cutout
```

**Key division:** CI can generate geometry and nominal G-code, but **physical height mapping is a machine-side operation** because it depends on how the particular blank is mounted.

### Example project layout

```text
my-pcb/
├── my-pcb.kicad_pro
├── my-pcb.kicad_sch
├── my-pcb.kicad_pcb
├── manufacturing/
│   ├── recipe.yaml
│   ├── tools.yaml
│   ├── fixtures/
│   │   └── two_pin_fixture.yaml
│   └── postprocessors/
│       └── makera_z1.py
├── scripts/
│   ├── generate_cam.py
│   ├── generate_registration.py
│   ├── validate_gcode.py
│   └── generate_preview.py
└── .forgejo/
    └── workflows/
        └── pcb-cam.yaml
```

Proposed build outputs:

```text
manufacturing-output/
├── gcode/
│   ├── 00_registration.nc
│   ├── 01_front_isolation.nc
│   ├── 02_front_drilling.nc
│   ├── 03_back_isolation.nc
│   └── 04_outline.nc
├── preview/
│   ├── front.svg
│   ├── back.svg
│   └── toolpaths.svg
├── gerbers/
└── fabrication-plan.md
```

The precise order of drilling, finishing, and cutout needs adjustment for the specific fixture, board and tools. **Leave the outline cut until last** to preserve registration and clamping.

## 4. Tool libraries and reusable machining recipes

A high-level configuration independent of the CAM engine was proposed. A script would convert this to the actual pcb2gcode configuration and machine-specific output.

Illustrative design of **a custom schema** (not built-in pcb2gcode syntax):

```yaml
machine: makera_z1
material: fr4_1_6mm

tools:
  isolation:
    id: T1
    type: vbit
    tip_diameter: 0.1
    angle: 30
    rpm: 12000
    feed_xy: 150
    feed_z: 60

  outline:
    id: T2
    type: endmill
    diameter: 1.0
    rpm: 12000
    feed_xy: 200
    stepdown: 0.3

operations:
  isolation:
    tool: isolation
    depth: 0.08
    clearance: 0.2
  outline:
    tool: outline
    tabs: 4

registration:
  enabled: true
  flip_axis: x
  pin_diameter: 3.0
  pin_spacing: 80.0
  hole_clearance: 0.05

probing:
  mode: makera_native
  grid_x: 7
  grid_y: 7
```

**All these numerical feeds, speeds, depths, sizes and clearances are illustrative and require validation** for the actual cutters, spindle, FR4 thickness, runout and Z1 capabilities. The `probing` fields are intended as manufacturing instructions/config metadata; they should not be assumed to control Makera's native probing API.

pcb2gcode also has its own `millproject` configuration mechanism (settings like cutting depth, isolation offset, feeds/speeds and safe Z). Such native options should be checked against the installed version's documentation rather than blindly copying an example.

Benefits of separating the manufacturing recipe from the CAM backend:

- Stable, reviewable machining intent under Git version control.
- One set of tools/operations reusable across different PCB projects.
- Consistent regeneration from old commits.
- Freedom to swap pcb2gcode for another CAM implementation in the future.
- Scope for automated preflight checks (tool too wide for a gap, missing drill, stock/fixture dimensions, out-of-bounds moves, inconsistent mirror axis, etc.).
- Version-pin KiCad, pcb2gcode, scripts and machining presets for repeatable CI artifacts.

## 5. Visual inspection and simulation

Two complementary types of previews were recommended:

### A. 2D Gerber/toolpath overlays

Show board copper, traces, isolation contours, drills, cutout, mirrored bottom copper and locating holes together. This is important for checking **electrical isolation gaps and registration geometry**, which may be hard to assess from a generic 3D milling view.

pcb2gcode offers an SVG-related export option in some versions; confirm flags in the installed build. Alternatively, write a small plotting stage from generated toolpaths and Gerber geometry.

### B. 3D material-removal preview

Use **CAMotics** to load G-code and define the cutters and stock. The goal is a Fusion 360-like review of nominal machining paths and resulting removal.

Limitations:

- CAMotics is not a full Z1 digital twin.
- It cannot know the exact, measured PCB warp before probing.
- Generic CNC simulation does not guarantee hardware compatibility or prevent all collisions.
- Initially, simply publish toolpath SVGs in CI and inspect a CAMotics project locally. Automating screenshots or HTML reports can follow later.

## 6. Makera Z1 surface probing / PCB flatness

**Problem:** PCB isolation uses tiny Z depths, so PCB warp, uneven tape/clamps, the bed surface, and tool runout materially change trace isolation width and risk copper not being cut fully or traces being overcut.

The Makera Z1 documentation was reported to include native **grid-based auto-leveling** controls. Example values discussed were 3×3 probing points and a 2 mm probe height; denser 5×5 or 7×7 probing was proposed for experimentation, **subject to what the installed software actually supports**.

Recommended execution:

1. Mount the actual copper-clad workpiece using the selected fixture (with or without a vacuum table).
2. Set work coordinates and reference the tool as required by the Makera workflow.
3. Probe the surface at multiple XY points and obtain a measured Z height map.
4. Execute the relevant isolation job with Z compensation applied by the controller, *if its imported G-code workflow supports it*.
5. After flipping a double-sided blank, establish/recheck Z and reprobe its new exposed surface.

**Critical unresolved issue:** Verify whether **Makera native grid compensation applies to arbitrary imported G-code**, not only G-code generated through Makera CAM. Also check whether height compensation persists or needs to be rerun for each tool and machining job. A controlled shallow test is recommended before trusting the production workflow.

An alternative (if native autoleveling is inadequate) is a custom probing + G-code warping layer. This should not be implemented until Makera capabilities have been tested; interpolating and transforming G-code correctly, including arcs and safe moves, requires care.

### V-bit sensitivity

For an ideal conical V-bit with a finite flat tip:

\[
 w = d_{\text{tip}} + 2h\tan(\theta/2)
\]

where:

- `w` = effective cut width;
- `d_tip` = actual tool-tip diameter;
- `h` = penetration depth;
- `theta` = included cutter angle.

Illustration: 30° included angle, 0.10 mm tip, 0.08 mm depth gives an approximate cut width of **0.143 mm**. This ignores tool wear, runout and the actual tool shape. Small Z height differences change the isolation width.

## 7. Double-sided boards: flipping and registration pins

**Requirement:** Machine front and back copper on the same blank without losing alignment. An additional preference is to **generate registration-hole machining automatically**, rather than manually laying out pins in every KiCad project.

Recommended concept:

- Define a **fixture coordinate system** and persistent location of registration pins, independent of board layout.
- Use two fitting pin/hole locations in **sacrificial stock outside the final PCB outline**.
- Define the actual physical flip axis and mirror the backside toolpaths about exactly that axis.
- Drill fixture/stock holes in a coordinated operation and insert matching pins.
- Machine the first side, flip the blank onto the same pins, re-probe the new exposed surface, then machine the second side.
- Cut out the board last.

For a reflection around the vertical line `x = a`:

\[
 x' = 2a-x, \qquad y' = y.
\]

Pin geometry must itself be invariant under the selected reflection (for example, both pin holes along the flip axis; other symmetric arrangements may also work). Mechanical hole clearance and fits should be tested; clearance too loose reduces repeatability while clearance too tight can jam.

pcb2gcode exposes double-sided mirroring features (such as `--mirror-axis` and `--mirror-yaxis`; confirm exact behavior and syntax per version). **Do not confuse a logical copper-layer mirror with the actual physical rotation/flip of a pinned panel.** Validate with an asymmetric test pattern and fiducials before cutting a real PCB.

Additional checks for accurate two-sided work:

- Use at least one visibly asymmetric copper/test feature to catch flipped axes.
- Verify a common XY datum and machine-coordinate frame for both faces.
- Confirm that the registration holes are outside the routed outline and within the available sacrificial border.
- Verify drilling order and that machining does not remove clamping/locating material before the final stage.
- Reprobe after flipping because opposite surfaces can have different height maps.
- Consider adding registration pins to the physical CNC fixture or future PCB vacuum table.

## 8. G-code safety and Z1 compatibility

A nominal `*.nc` file from pcb2gcode is **not automatically machine-safe** just because it is valid G-code.

Check the Z1's supported language and external-import behavior:

- Coordinate units and modes (G20/G21, G90/G91).
- Work offsets, origin positions, homing and stock-zero conventions.
- Plane selection, line/arc modes, arc-center behavior and unsupported commands.
- Spindle start/stop, RPM settings and any machine-specific M-codes.
- Z-safe travel, retract height and tool-change behavior.
- Cutter compensation and handling of multiple tool jobs.
- Grid compensation applicability to external files.
- Travel envelope, clamp clearance and pin locations.
- Whether splitting jobs changes origins, leveling state or tool-offset state.

Use an **air cut/dry run** and a simple scrap-board test before committing to a fully automated workflow.

A Makera-specific postprocessor or G-code validator may be needed, but the initial priority is to determine whether the simpler native output already works correctly.

## 9. Implementation plan discussed

### Phase 1 — Single-sided proof of concept

- Export top copper, board outline and drills from KiCad CLI.
- Generate nominal isolation G-code via pcb2gcode.
- Export a 2D preview and inspect nominal cuts in CAMotics/Makera software.
- Verify G-code parser compatibility, Z reference and tool settings.
- Test Z1's native grid probing and one simple isolation test on mounted copper clad.

**Success criterion:** repeatable isolation cut, without manual CAM redesign, and reliable Z correction.

### Phase 2 — Double-sided alignment

- CAD/manufacture a simple two-pin registration fixture.
- Define fixture origin and physical flip axis.
- Generate locating holes and mirrored underside code automatically.
- Test both-face alignment with asymmetric marks, then a simple two-sided test board.
- Reprobe after the flip; finish with outline routing.

**Success criterion:** predictable front-to-back registration.

### Phase 3 — CI/CD integration

- Commit `manufacturing/` presets and scripts in the KiCad repository.
- Add Forgejo workflow with pinned CLI versions.
- Output named G-code jobs, Gerbers, 2D overlays, machine setup notes and checksums/manifest.
- Add static checks for unsupported G-code, bad dimensions and impossible cutter selections.

**Success criterion:** commit/tag → reproducible downloadable machine-job package.

### Phase 4 — Enhanced preview and usability

- Build a lightweight browser-accessible fabrication report with 2D overlays.
- Add optional CAMotics simulation artifacts or locally loadable project.
- Compare rendering with actual machined results and calibrate tool/settings presets.
- Consider a higher-level user interface only once the pipeline is proven.

## 10. Important unresolved decisions and experiments

1. **Makera Z1 imported G-code autoleveling:** does the measured grid modify third-party G-code automatically? Under what workflow and for which operations?
2. **Z1 postprocessor requirements:** what precise G/M codes, tool-change format, spindle control and coordinate conventions must be generated?
3. **PCB stock / fixture definition:** dimensions, mounting method (vacuum table, tape, clamps), allowable pin placement and origin location.
4. **Tool calibration:** actual V-bit tip diameter, included angle, spindle runout, Z-zero method, feeds, plunge, safe Z, and cutter life in FR4.
5. **Mirroring:** exact semantics of pcb2gcode's mirror options and their correspondence to the physical pinned flip.
6. **Simulation automation:** feasibility and maintainability of making CI output self-contained CAMotics/HTML previews rather than only SVGs.
7. **Drill and outline sequencing:** pin-hole drilling, functional drills and isolation scheduling around fixturing, tool changes and final cutout.
8. **Tool library integration:** maintain simple YAML plus translation scripts initially; avoid overbuilding a general CAM framework.
9. **FR4 safety:** effective dust extraction/containment and appropriate respiratory protection are necessary; fiberglass dust should not be dispersed around the machine or workshop.

## 11. Overall recommendation

Build the first version around:

**KiCad CLI → pcb2gcode + versioned machining recipes → SVG + CAMotics review → Makera controller/Z1 native probing → CNC execution.**

Use a **fixed, well-defined two-pin fixture** for double-sided jobs and keep all registration coordinates in the fixture's frame. Keep **nominal geometry generation** separate from **actual machine-side surface measurement**. Before writing custom probing compensation or a postprocessor, test how Makera handles native leveling and imported G-code on a small sample.

This approach preserves future flexibility while minimizing the amount of bespoke CAM software required.

---

*This file summarizes the discussion and proposed architecture; it is not a tested machining procedure. Illustrative numerical parameters must be confirmed against the actual machine, cutter and workpiece.*
