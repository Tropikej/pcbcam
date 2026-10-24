"""The full pipeline: KiCad project + recipe -> checked, machine-ready .nc files."""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, excellon, gerber, kicad, millproject, postprocess, validate
from .config import ConfigError, MachineProfile, Recipe, load_machine, load_recipe, load_tools
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


@dataclass
class Result:
    out: Path
    jobs: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    board_size: tuple[float, float] | None = None


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
    return Plan(find_board(project), recipe_path, recipe, machine, resolve(recipe, tools))


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
    if problems:
        raise BuildFailed(problems)
    if check_only:
        return result

    # 3. pcb2gcode.
    rel = lambda path: path.relative_to(work).as_posix()  # noqa: E731
    files = {"front": rel(exports.front), "outline": rel(exports.outline)}
    if exports.drill:
        files["drill"] = rel(exports.drill)
    xmin, ymin, xmax, ymax = gerber.outline_bbox(exports.outline)
    board_w, board_h = xmax - xmin, ymax - ymin
    pl = recipe.placement
    if pl.margin_x_mm + board_w > recipe.stock.size_x_mm or pl.margin_y_mm + board_h > recipe.stock.size_y_mm:
        raise BuildFailed([
            f"board {board_w:.1f} x {board_h:.1f} mm at ({pl.margin_x_mm}, {pl.margin_y_mm}) does not fit "
            f"the {recipe.stock.size_x_mm} x {recipe.stock.size_y_mm} mm stock"])
    result.board_size = (board_w, board_h)
    (work / "millproject").write_text(
        millproject.render(recipe, res, assignments, files, (xmin, ymin)), encoding="utf-8")
    try:
        log = runner.run(work)
    except Pcb2GcodeError as e:
        (work / "pcb2gcode.log").write_text(e.log, encoding="utf-8")
        hint = " -- see the *.svg files in work/ for contended areas" if "contention" in e.log.lower() else ""
        raise BuildFailed([f"{e}{hint}", *e.log.strip().splitlines()[-15:]]) from None
    (work / "pcb2gcode.log").write_text(log, encoding="utf-8")

    # 4. Post-process and validate each job.
    jobs = [
        ("board_front.ngc", "01_isolation_front.nc", "isolation (front)", res.isolation, True),
        ("board_drill.ngc", "02_drill.nc", "drilling", res.drills, False),
        ("board_milldrill.ngc", "03_milldrill.nc", "milled holes", [res.milldrill] if res.milldrill else [], False),
        ("board_outline.ngc", "04_outline.nc", "board outline", [res.outline] if res.outline else [], False),
    ]
    slack = max(t.width_mm for t in res.all()) / 2 + 0.01
    gcode_dir = out / "gcode"
    gcode_dir.mkdir(parents=True)
    issues: list[str] = []
    for src_name, dst_name, label, tools, probe in jobs:
        src = work / src_name
        if not src.is_file() or not tools:
            continue
        job = postprocess.Job(label, tools, probe=probe, drilling=src_name == "board_drill.ngc")
        text = postprocess.process(src.read_text(encoding="utf-8"), job, p.machine, recipe.probing)
        for issue in validate.check(text, p.machine, recipe, slack):
            issues.append(f"{dst_name}: {issue}")
        dst = gcode_dir / dst_name
        dst.write_text(text, encoding="utf-8", newline="\n")
        result.jobs.append(dst)
    if issues:
        raise BuildFailed(issues)

    for svg in work.glob("*.svg"):
        (out / "preview").mkdir(exist_ok=True)
        shutil.copy2(svg, out / "preview" / svg.name)

    _manifest(p, result, assignments, cli, runner, log)
    return result


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
        "origin": "work zero = stock lower-left corner, top of the copper; board outline "
                  f"lower-left corner at ({p.recipe.placement.margin_x_mm}, {p.recipe.placement.margin_y_mm}) mm",
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
