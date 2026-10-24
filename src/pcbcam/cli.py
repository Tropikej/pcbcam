"""Command line: ``pcbcam build|check|tools``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, build, crop, kicad, validate
from .config import ConfigError
from .pcb2gcode import Pcb2GcodeError
from .postprocess import PostProcessError

DEFAULT_RECIPE = Path("manufacturing") / "recipe.yaml"
DEFAULT_OUT = Path("manufacturing-output")


def _paths(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    project = Path(args.project)
    base = project if project.is_dir() else project.parent
    recipe = Path(args.recipe) if args.recipe else base / DEFAULT_RECIPE
    out = Path(args.out) if getattr(args, "out", None) else base / DEFAULT_OUT
    return project, recipe, out


def _print_tools(p: build.Plan) -> None:
    res = p.resolved
    print(f"machine: {p.machine.name}{'' if p.machine.verified else '  (UNVERIFIED profile)'}")
    print(f"{'T':>3}  {'name':<20} {'type':<8} {'cut width':>10}  role")
    roles = [("isolation", t) for t in res.isolation] + [("drill", t) for t in res.drills]
    if res.milldrill:
        roles.append(("milled holes", res.milldrill))
    if res.outline:
        roles.append(("outline", res.outline))
    for role, t in roles:
        print(f"{t.number:>3}  {t.name:<20} {t.tool.type:<8} {t.width_mm:>8.3f}mm  {role}")
    gap = res.isolation_width_mm + p.recipe.checks.clearance_margin_mm
    print(f"\nisolation depth {p.recipe.isolation.depth_mm} mm -> minimum copper gap {gap:.3f} mm")
    print(f"set KiCad clearance to at least {gap:.3f} mm (the machine DRC enforces this)")


def _crop(p: build.Plan, out: Path, job: str, box_args: list[str]) -> int:
    ext = p.machine.file_extension
    src = out / "gcode" / f"{job}.{ext}"
    if not src.is_file():
        raise ConfigError(f"{src} not found: run 'pcbcam build' first")
    boxes = []
    for b in box_args:
        try:
            x0, y0, x1, y1 = (float(v) for v in b.split(","))
        except ValueError:
            raise ConfigError(f"--box {b!r}: expected X0,Y0,X1,Y1") from None
        boxes.append((min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)))
    eol = "\r\n" if p.machine.line_ending == "crlf" else "\n"
    res = crop.crop(src.read_text(encoding="ascii"), boxes, p.recipe.heights.safe_mm, eol,
                    approach_z=p.recipe.heights.approach_mm)
    slack = max(t.width_mm for t in p.resolved.all()) / 2 + 0.01
    issues = validate.check(res.text, p.machine, p.recipe, slack)
    if issues:
        print(f"FAILED: {len(issues)} problem(s) in the cropped job", file=sys.stderr)
        for i in issues[:20]:
            print(f"  - {i}", file=sys.stderr)
        return 1
    dst = src.with_name(f"{job}_crop.{ext}")
    dst.write_bytes(res.text.encode("ascii"))
    print(f"wrote {dst}")
    print(f"kept {res.cut_mm / 1000:.2f} m of {res.cut_total_mm / 1000:.2f} m cutting, "
          f"about {res.minutes:.0f} min plus rapids and the tool change")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pcbcam", description=__doc__)
    ap.add_argument("--version", action="version", version=f"pcbcam {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, help_ in [
        ("build", "export, check, run pcb2gcode and write machine-ready .nc files"),
        ("check", "export and run the preflight checks only"),
        ("tools", "show the resolved tools and the clearance they need"),
    ]:
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("project", help="KiCad project directory or .kicad_pcb file")
        sp.add_argument("--recipe", help=f"recipe file (default: <project>/{DEFAULT_RECIPE.as_posix()})")
        if name != "tools":
            sp.add_argument("--out", help=f"output directory (default: <project>/{DEFAULT_OUT})")
        if name in ("build", "check"):
            sp.add_argument("--skip-project-drc", action="store_true",
                            help="skip the board's own DRC (the machine DRC still runs)")
            sp.add_argument("--skip-drc", action="store_true", help="skip both DRC passes")
    cp = sub.add_parser("crop", help="cut only rectangles of a built isolation job (short test cuts)")
    cp.add_argument("project", help="KiCad project directory or .kicad_pcb file")
    cp.add_argument("--recipe", help=f"recipe file (default: <project>/{DEFAULT_RECIPE.as_posix()})")
    cp.add_argument("--out", help=f"output directory of the build (default: <project>/{DEFAULT_OUT})")
    cp.add_argument("--job", default="01_isolation_front", help="job to crop (default: %(default)s)")
    cp.add_argument("--box", action="append", required=True, metavar="X0,Y0,X1,Y1",
                    help="rectangle to keep, work coordinates in mm; repeat for several")
    args = ap.parse_args(argv)

    try:
        project, recipe, out = _paths(args)
        p = build.plan(project, recipe)
        if args.cmd == "crop":
            return _crop(p, out, args.job, args.box)
        if args.cmd == "tools":
            _print_tools(p)
            return 0
        result = build.run(
            p, out,
            skip_project_drc=args.skip_drc or args.skip_project_drc,
            skip_machine_drc=args.skip_drc,
            check_only=args.cmd == "check",
        )
    except build.BuildFailed as e:
        print(f"FAILED: {len(e.problems)} problem(s)", file=sys.stderr)
        for line in e.problems:
            print(f"  - {line}", file=sys.stderr)
        return 1
    except (ConfigError, kicad.KiCadError, Pcb2GcodeError, PostProcessError, crop.CropError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    for w in result.warnings:
        print(f"warning: {w}")
    if args.cmd == "check":
        print("preflight OK")
    else:
        for job in result.jobs:
            print(f"wrote {job}")
        print(f"manifest: {result.out / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
