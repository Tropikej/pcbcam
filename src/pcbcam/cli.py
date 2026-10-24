"""Command line: ``pcbcam build|check|tools``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, build, kicad
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
            sp.add_argument("--skip-project-drc", action="store_true",
                            help="skip the board's own DRC (the machine DRC still runs)")
            sp.add_argument("--skip-drc", action="store_true", help="skip both DRC passes")
    args = ap.parse_args(argv)

    try:
        project, recipe, out = _paths(args)
        p = build.plan(project, recipe)
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
    except (ConfigError, kicad.KiCadError, Pcb2GcodeError, PostProcessError) as e:
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
