"""The full pipeline: KiCad project + recipe -> checked, machine-ready .nc files."""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import (__version__, backside, engrave, excellon, fixture, gerber, kicad, millproject, postprocess,
               register, validate)
from .config import (ConfigError, Fixture, FixtureStock, MachineProfile, Recipe, load_fixture, load_machine,
                     load_recipe, load_tools)
from .pcb2gcode import Pcb2GcodeError, Runner
from .tooling import HoleAssignment, Resolved, assign_holes, resolve


class BuildFailed(Exception):
    """Preflight or a pipeline stage found something that must be fixed first."""

    def __init__(self, problems: list[str]):
        super().__init__("\n".join(problems))
        self.problems = problems


@dataclass
class Plan:
    """Everything known before running any external tool."""

    board: Path
    recipe_path: Path
    recipe: Recipe
    machine: MachineProfile
    resolved: Resolved
    fixture: Fixture | None = None


@dataclass
class Result:
    out: Path
    jobs: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    board_size: tuple[float, float] | None = None
    origin: str = ""  # how the board was placed on the stock, for the manifest


def find_board(project: Path) -> Path:
    if project.is_file() and project.suffix == ".kicad_pcb":
        return project
    boards = sorted(project.glob("*.kicad_pcb"))
    if len(boards) != 1:
        raise ConfigError(f"{project}: expected exactly one .kicad_pcb, found {len(boards)}")
    return boards[0]


def plan(project: Path, recipe_path: Path) -> Plan:
    recipe = load_recipe(recipe_path)
    tools = load_tools(recipe_path.parent / recipe.tools)
    machine = load_machine(recipe.machine, base=recipe_path.parent)
    fix = load_fixture(recipe_path.parent / recipe.fixture) if recipe.fixture else None
    return Plan(find_board(project), recipe_path, recipe, machine, resolve(recipe, tools), fix)


@dataclass
class Placement:
    """How the board sits on the stock, in two frames.

    ``corner`` is the Gerber->stock-corner offset (work zero at the stock corner).
    ``shift`` is the stock-frame point used as the real work origin (the lower-left
    pin, or (0, 0) for the corner); subtract it from a corner-frame coordinate to
    reach work coordinates. pcb2gcode and the engraver use ``work`` = corner - shift.
    """

    corner: tuple[float, float]
    shift: tuple[float, float]
    origin: str

    @property
    def work(self) -> tuple[float, float]:
        return (self.corner[0] - self.shift[0], self.corner[1] - self.shift[1])


def _place(p: Plan, bbox: tuple[float, float, float, float]) -> Placement:
    """pcb2gcode offset from Gerber to work coordinates, and how it was chosen.

    With a fixture, the fixture footprint drawn in KiCad is the stock, so its
    origin is the stock corner; work zero is then the lower-left pin (or the
    corner), per the fixture's ``work_origin``. Without a fixture, the outline
    corner goes at the margins.
    """
    recipe = p.recipe
    xmin, ymin, xmax, ymax = bbox
    placed = fixture.find(p.board)
    if p.fixture is None:
        if placed:
            raise BuildFailed([f"the board has a fixture footprint ({placed[0].reference}) but the recipe "
                               "has no 'fixture:' file"])
        pl = recipe.placement
        if (pl.margin_x_mm + xmax - xmin > recipe.stock.size_x_mm
                or pl.margin_y_mm + ymax - ymin > recipe.stock.size_y_mm):
            raise BuildFailed([
                f"board {xmax - xmin:.1f} x {ymax - ymin:.1f} mm at ({pl.margin_x_mm}, {pl.margin_y_mm}) "
                f"does not fit the {recipe.stock.size_x_mm} x {recipe.stock.size_y_mm} mm stock"])
        return Placement((pl.margin_x_mm - xmin, pl.margin_y_mm - ymin), (0.0, 0.0),
                         f"work zero = stock lower-left corner; board outline lower-left corner "
                         f"at ({pl.margin_x_mm}, {pl.margin_y_mm}) mm")
    if len(placed) != 1:
        lib = fixture.LIBRARY
        raise BuildFailed([f"recipe uses fixture '{p.fixture.name}': place exactly one {lib} footprint on "
                           f"the board (found {len(placed)})"])
    dx, dy = fixture.work_offset(placed[0])
    box = (xmin + dx, ymin + dy, xmax + dx, ymax + dy)
    res = p.resolved
    cutter = res.outline.width_mm if recipe.outline and recipe.outline.enabled and res.outline else None
    problems = fixture.check(p.fixture, placed[0], (recipe.stock.size_x_mm, recipe.stock.size_y_mm),
                             box, cutter)
    if problems:
        raise BuildFailed(problems)
    stock = p.fixture.stocks[placed[0].properties[fixture.PROP_STOCK]]
    ox, oy = p.fixture.origin_shift(stock)
    where = (f"work zero = lower-left pin at stock ({ox:g}, {oy:g}) mm"
             if (ox, oy) != (0.0, 0.0) else "work zero = stock lower-left corner")
    return Placement((dx, dy), (ox, oy),
                     f"{where}; fixture footprint {placed[0].reference} ({placed[0].footprint}) is the stock; "
                     f"board outline lower-left corner at stock ({box[0]:.3f}, {box[1]:.3f}) mm")


def _hole_problems(assignments: list[HoleAssignment], inv: excellon.DrillInventory) -> list[str]:
    problems = [
        f"no tool for {a.count} hole(s) of {a.diameter_mm} mm "
        "(add a drill, widen drilling.match_tolerance_mm, or set drilling.milldrill_tool)"
        for a in assignments
        if a.method == "none"
    ]
    if inv.slots:
        sizes = ", ".join(f"{d} mm x{n}" for d, n in sorted(inv.slots.items()))
        problems.append(f"slots are not supported yet: {sizes}")
    return problems


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(
    p: Plan,
    out: Path,
    runner: Runner | None = None,
    skip_project_drc: bool = False,
    skip_machine_drc: bool = False,
    check_only: bool = False,
) -> Result:
    runner = runner or Runner.from_env()
    recipe, res = p.recipe, p.resolved
    # Empty rather than delete: on Windows the folder is often open in a shell
    # or Explorer, which makes removing the directory itself fail.
    out.mkdir(parents=True, exist_ok=True)
    for child in out.iterdir():
        shutil.rmtree(child) if child.is_dir() else child.unlink()
    work = out / "work"
    gerbers = work / "gerbers"
    result = Result(out)
    if not p.machine.verified:
        result.warnings.append(f"machine profile '{p.machine.name}' is not verified on hardware")
    if p.fixture and not p.fixture.verified:
        result.warnings.append(f"fixture '{p.fixture.name}': pin positions are not verified on the real fixture")
    if recipe.isolation.allow_contentions:
        result.warnings.append(
            "isolation.allow_contentions is on: where the V-bit doesn't fit it WILL cut into "
            "copper (calibration boards only); see preview/*.svg")

    # 1. Export from KiCad.
    cli = kicad.find_cli()
    exports = kicad.export(cli, p.board, gerbers)

    # 2. Preflight: holes vs. drills, design rules vs. tools.
    problems: list[str] = []
    assignments: list[HoleAssignment] = []
    if exports.drill and recipe.drilling and recipe.drilling.enabled:
        inv = excellon.parse(exports.drill)
        assignments = assign_holes(dict(inv.holes), res, recipe.drilling.match_tolerance_mm)
        problems += _hole_problems(assignments, inv)
    if recipe.checks.project_drc and not skip_project_drc:
        for v in kicad.drc_project(cli, p.board, work):
            problems.append(f"KiCad DRC: {v.description} {'; '.join(v.items)}")
    if recipe.checks.machine_drc and not skip_machine_drc:
        rules = kicad.machine_rules(recipe, res)
        (work / "machine.kicad_dru").write_text(rules, encoding="utf-8")
        for v in kicad.drc_machine(cli, p.board, rules, work):
            problems.append(f"not machinable: {v.description} {'; '.join(v.items)}")
    if recipe.engrave and recipe.engrave.enabled and recipe.engrave.back_layer and p.fixture is None:
        problems.append("engrave.back_layer needs a fixture (its flip axis sets the back mirror)")
    if recipe.isolation.enabled and recipe.isolation.side in ("back", "both"):
        if p.fixture is None:
            problems.append("isolation.side back/both needs a fixture (its flip axis sets the back mirror)")
        elif _fixture_stock(p) is None:
            problems.append(f"isolation.side {recipe.isolation.side}: no fixture stock matches the "
                            f"{recipe.stock.size_x_mm} x {recipe.stock.size_y_mm} mm recipe stock")
    if problems:
        raise BuildFailed(problems)
    bbox = gerber.outline_bbox(exports.outline)
    placement = _place(p, bbox)
    offset = placement.work  # Gerber -> work coordinates (pin origin, or stock corner)
    result.origin = placement.origin
    result.board_size = (bbox[2] - bbox[0], bbox[3] - bbox[1])
    if check_only:
        return result

    # 3. pcb2gcode.
    rel = lambda path: path.relative_to(work).as_posix()  # noqa: E731
    files = {"front": rel(exports.front), "outline": rel(exports.outline)}
    if exports.drill:
        files["drill"] = rel(exports.drill)
    (work / "millproject").write_text(
        millproject.render(recipe, res, assignments, files, offset), encoding="utf-8")
    try:
        log = runner.run(work)
    except Pcb2GcodeError as e:
        (work / "pcb2gcode.log").write_text(e.log, encoding="utf-8")
        hint = " -- see the *.svg files in work/ for contended areas" if "contention" in e.log.lower() else ""
        raise BuildFailed([f"{e}{hint}", *e.log.strip().splitlines()[-15:]]) from None
    (work / "pcb2gcode.log").write_text(log, encoding="utf-8")

    # 4. Post-process and validate each job.
    jobs = [
        ("board_front.ngc", "01_isolation_front", "isolation (front)", res.isolation, True),
        ("board_drill.ngc", "02_drill", "drilling", res.drills, False),
        ("board_milldrill.ngc", "03_milldrill", "milled holes", [res.milldrill] if res.milldrill else [], False),
        ("board_outline.ngc", "04_outline", "board outline", [res.outline] if res.outline else [], False),
    ]
    slack = max(t.width_mm for t in res.all()) / 2 + 0.01
    gcode_dir = out / "gcode"
    gcode_dir.mkdir(parents=True)
    issues: list[str] = []
    for src_name, stem, label, tools, probe in jobs:
        src = work / src_name
        if not src.is_file() or not tools:
            continue
        dst_name = f"{stem}.{p.machine.file_extension}"
        job = postprocess.Job(label, tools, program=stem, probe=probe, drilling=src_name == "board_drill.ngc",
                              approach_mm=recipe.heights.approach_mm)
        text = postprocess.process(src.read_text(encoding="utf-8"), job, p.machine, recipe.probing)
        for issue in validate.check(text, p.machine, recipe, slack, placement.shift):
            issues.append(f"{dst_name}: {issue}")
        dst = gcode_dir / dst_name
        dst.write_bytes(text.encode("ascii"))  # line endings come from the profile
        result.jobs.append(dst)

    # 4b. Back copper isolation: a second pcb2gcode run on B.Cu, mirrored about
    # the fixture flip axis (so it registers after the physical flip).
    if recipe.isolation.enabled and recipe.isolation.side in ("back", "both"):
        issues += _back_isolation(p, placement, exports.back, work, runner, gcode_dir, result, slack)

    # 4c. Engrave labels, traced from a KiCad layer (not through pcb2gcode).
    if recipe.engrave and recipe.engrave.enabled:
        issues += _engrave(p, cli, placement, work, gcode_dir, result, slack)

    # 4d. Registration bores: the blank's pin holes, a separate setup zeroed on
    # the blank's lower-left corner (no pins exist yet to register against).
    if p.fixture:
        issues += _register(p, gcode_dir, result, slack)

    if issues:
        raise BuildFailed(issues)

    for svg in work.glob("*.svg"):
        (out / "preview").mkdir(exist_ok=True)
        shutil.copy2(svg, out / "preview" / svg.name)

    _manifest(p, result, assignments, cli, runner, log)
    return result


def _fixture_stock(p: Plan) -> FixtureStock | None:
    """The fixture stock matching the recipe stock size (back mirror axis, pins)."""
    if not p.fixture:
        return None
    for s in p.fixture.stocks.values():
        if (abs(s.size_x_mm - p.recipe.stock.size_x_mm) < 1e-6
                and abs(s.size_y_mm - p.recipe.stock.size_y_mm) < 1e-6):
            return s
    return None


def _mirror_axes(stock: FixtureStock, placement: Placement) -> tuple[float, float, float, float]:
    """Per-axis maps (sx, bx, sy, by) taking raw back-Gerber coords to mirrored
    work coords: the same reflect-then-shift the engraver uses, written as two
    independent affine maps (see backside.mirror_isolation)."""
    axis, c = stock.flip_axis()
    dx, dy = placement.corner
    ox, oy = placement.shift
    if axis == "x":  # left/right flip: X -> 2c - (gx + dx) - ox; Y -> (gy + dy) - oy
        return -1.0, 2 * c - dx - ox, 1.0, dy - oy
    return 1.0, dx - ox, -1.0, 2 * c - dy - oy  # top/bottom flip


def _back_isolation(p: Plan, placement: Placement, back_gbr: Path, work: Path, runner: Runner,
                    gcode_dir: Path, result: Result, slack: float) -> list[str]:
    """Emit 01b_isolation_back: B.Cu isolated and mirrored about the flip axis."""
    stock = _fixture_stock(p)
    assert stock is not None  # guaranteed by preflight
    sub = work / "backiso"
    sub.mkdir(parents=True, exist_ok=True)
    shutil.copy2(back_gbr, sub / "copper.gbr")
    (sub / "millproject").write_text(
        millproject.render_isolation_only(p.recipe, p.resolved, "copper.gbr"), encoding="utf-8")
    try:
        (sub / "pcb2gcode.log").write_text(runner.run(sub), encoding="utf-8")
    except Pcb2GcodeError as e:
        (sub / "pcb2gcode.log").write_text(e.log, encoding="utf-8")
        raise BuildFailed([f"back isolation: {e}", *e.log.strip().splitlines()[-15:]]) from None
    raw = (sub / "board_front.ngc").read_text(encoding="utf-8")
    try:
        mirrored = backside.mirror_isolation(raw, *_mirror_axes(stock, placement))
    except ValueError as e:
        return [f"01b_isolation_back: {e}"]
    job = postprocess.Job("isolation (back)", p.resolved.isolation, program="01b_isolation_back", probe=True)
    text = postprocess.process(mirrored, job, p.machine, p.recipe.probing)
    dst_name = f"01b_isolation_back.{p.machine.file_extension}"
    issues = [f"{dst_name}: {i}"
              for i in validate.check(text, p.machine, p.recipe, slack, placement.shift)]
    dst = gcode_dir / dst_name
    dst.write_bytes(text.encode("ascii"))
    result.jobs.append(dst)
    for svg in sub.glob("*.svg"):
        (result.out / "preview").mkdir(exist_ok=True)
        shutil.copy2(svg, result.out / "preview" / f"back_{svg.name}")
    return issues


def work_origin_shift(p: Plan) -> tuple[float, float]:
    """Where work zero sits in the stock-corner frame for this plan's main jobs.

    (0, 0) without a fixture or with ``work_origin: stock_corner``; the lower-left
    pin otherwise. Used to bound-check coordinates against the stock (they go
    negative when work zero is the pin)."""
    stock = _fixture_stock(p)
    return p.fixture.origin_shift(stock) if (p.fixture and stock) else (0.0, 0.0)


def _engrave(p: Plan, cli: str, placement: Placement, work: Path, gcode_dir: Path,
             result: Result, slack: float) -> list[str]:
    """Trace the engrave layer(s): front as drawn, back mirrored about the flip axis."""
    e, res = p.recipe.engrave, p.resolved
    assert e is not None and res.engrave is not None
    stock = _fixture_stock(p)
    sides = []
    if e.front_layer:
        sides.append(("front", e.front_layer, "05_engrave_front", None))
    if e.back_layer:
        sides.append(("back", e.back_layer, "06_engrave_back", stock))
    (cx, cy), (ox, oy) = placement.corner, placement.shift
    issues: list[str] = []
    for side, layer, stem, mirror in sides:
        dxf = kicad.export_dxf(cli, p.board, layer, work / f"{stem}.dxf")
        segments = engrave.read_segments(dxf.read_text(encoding="utf-8", errors="replace"))
        if not segments:
            result.warnings.append(f"engrave: layer '{layer}' has no stroke geometry, {stem} skipped")
            continue
        paths = engrave.chain(segments)
        xf = engrave.Transform(cx, cy, ox, oy, mirror)
        source = engrave.build_source(paths, res.engrave, e.depth_mm, p.recipe.heights.safe_mm, xf)
        job = postprocess.Job(f"engrave ({side})", [res.engrave], program=stem,
                              approach_mm=p.recipe.heights.approach_mm)
        text = postprocess.process(source, job, p.machine, p.recipe.probing)
        dst_name = f"{stem}.{p.machine.file_extension}"
        for issue in validate.check(text, p.machine, p.recipe, slack, (ox, oy)):
            issues.append(f"{dst_name}: {issue}")
        dst = gcode_dir / dst_name
        dst.write_bytes(text.encode("ascii"))
        result.jobs.append(dst)
    return issues


def _register(p: Plan, gcode_dir: Path, result: Result, slack: float) -> list[str]:
    """Emit 00_register: the blank's pin holes, helically milled.

    Runs first (number 00). Zeroed on the blank's lower-left corner by default
    (a fresh blank has no holes yet); set fixture ``register_origin:
    lower_left_pin`` to zero on the probed lower-left hole instead, for re-boring
    wider or a second pass. Needs an end mill (no 4 mm drill): the drilling
    milldrill tool, else the outline cutter. Skipped with a warning if the
    fixture stock or a cutter is missing, so it never blocks a single-sided build.
    """
    assert p.fixture is not None
    stock = _fixture_stock(p)
    if stock is None:
        return []
    tool = p.resolved.milldrill or p.resolved.outline
    if tool is None:
        result.warnings.append(
            "registration bores (00_register) skipped: no end mill available "
            "(set drilling.milldrill_tool or outline.tool)")
        return []
    bore = p.fixture.pin_diameter_mm + p.fixture.blank_bore_clearance_mm
    # Through the blank with a little to spare; validate caps depth at 0.5 mm into
    # the spoilboard, which the blank sits on for this op.
    depth = p.recipe.stock.thickness_mm + 0.3
    # Zero on the blank corner (fresh blank) or the lower-left hole (re-bore).
    shift = p.fixture.register_shift(stock)
    pins = [(x - shift[0], y - shift[1]) for x, y in stock.pins_mm]
    try:
        source = register.build_source(
            pins, bore, tool, depth, p.recipe.heights.safe_mm,
            p.recipe.heights.approach_mm, stepdown_mm=0.3)
    except ValueError as exc:
        return [f"00_register: {exc}"]
    job = postprocess.Job("registration bores", [tool], program="00_register",
                          approach_mm=p.recipe.heights.approach_mm)
    text = postprocess.process(source, job, p.machine, p.recipe.probing)
    dst_name = f"00_register.{p.machine.file_extension}"
    issues = [f"{dst_name}: {i}" for i in validate.check(text, p.machine, p.recipe, slack, shift)]
    dst = gcode_dir / dst_name
    dst.write_bytes(text.encode("ascii"))
    result.jobs.insert(0, dst)  # it is machined before everything else
    return issues


def _manifest(p: Plan, result: Result, holes: list[HoleAssignment], cli: str, runner: Runner, log: str) -> None:
    res = p.resolved
    data = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pcbcam": __version__,
        "kicad": kicad.version(cli),
        "pcb2gcode": runner.version(),
        "board": {"file": p.board.name, "sha256": _sha256(p.board)},
        "recipe": {"file": p.recipe_path.name, "sha256": _sha256(p.recipe_path)},
        "machine": {"name": p.machine.name, "verified": p.machine.verified},
        "fixture": {"name": p.fixture.name, "verified": p.fixture.verified} if p.fixture else None,
        "origin": f"{result.origin}; Z0 on top of the copper (probe the blank each side)",
        "board_size_mm": [round(v, 3) for v in result.board_size] if result.board_size else None,
        "tools": [
            {"number": t.number, "name": t.name, "type": t.tool.type, "cut_width_mm": round(t.width_mm, 4)}
            for t in res.all()
        ],
        "holes": [
            {"diameter_mm": h.diameter_mm, "count": h.count, "method": h.method,
             "tool": h.tool.name if h.tool else None}
            for h in holes
        ],
        "jobs": [{"file": j.name, "sha256": _sha256(j)} for j in result.jobs],
        "warnings": result.warnings,
    }
    (result.out / "manifest.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
