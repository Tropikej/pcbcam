"""kicad-cli wrappers: Gerber/Excellon export and design-rule checks."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .tooling import Resolved
from .config import Recipe

RULE_PREFIX = "pcbcam"
CALIBRATION_AREA = "pcbcam_calibration"  # rule area name exempting test patterns


class KiCadError(Exception):
    pass


def find_cli() -> str:
    """Locate kicad-cli: $KICAD_CLI, PATH, then the default Windows/macOS installs."""
    if env := os.environ.get("KICAD_CLI"):
        return env
    if found := shutil.which("kicad-cli"):
        return found
    def _ver(p: Path) -> tuple[int, ...]:
        return tuple(int(x) for x in re.findall(r"\d+", p.parent.parent.name))

    # Newest first, by version number ("10.0" sorts before "9.0" as text).
    candidates = sorted(Path("C:/Program Files/KiCad").glob("*/bin/kicad-cli.exe"), key=_ver, reverse=True)
    candidates.append(Path("/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"))
    for c in candidates:
        if c.is_file():
            return str(c)
    raise KiCadError("kicad-cli not found; install KiCad 9+ or set KICAD_CLI")


def _run(args: list[str], ok_codes: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(args, capture_output=True, text=True)
    if proc.returncode not in ok_codes:
        raise KiCadError(
            f"{' '.join(args[:3])} failed ({proc.returncode}):\n{proc.stdout}{proc.stderr}"
        )
    return proc


def version(cli: str) -> str:
    return _run([cli, "version"]).stdout.strip()


@dataclass(frozen=True)
class Exports:
    front: Path
    back: Path
    outline: Path
    drill: Path | None


def export(cli: str, board: Path, out: Path) -> Exports:
    """Export copper, outline and a single merged Excellon file in mm.

    Absolute coordinates are fine here: pcb2gcode re-zeroes everything on the
    outline's bounding box, so all jobs share one origin.
    """
    # One layer per directory: file names follow the board's user layer names
    # ("top_cu", "Dessus", ...), so they cannot be predicted.
    layers: dict[str, Path] = {}
    for key, layer in (("front", "F.Cu"), ("back", "B.Cu"), ("outline", "Edge.Cuts")):
        d = out / key
        d.mkdir(parents=True, exist_ok=True)
        _run([
            cli, "pcb", "export", "gerbers", "--output", str(d), "--layers", layer,
            "--no-protel-ext", "--no-x2", "--no-netlist", str(board),
        ])
        found = sorted(d.glob("*.gbr"))
        if len(found) != 1:
            raise KiCadError(f"exporting {layer}: expected one .gbr in {d}, found {len(found)}")
        layers[key] = found[0]
    _run([
        cli, "pcb", "export", "drill", "--output", str(out) + os.sep,
        "--format", "excellon", "--excellon-units", "mm", "--excellon-zeros-format", "decimal",
        "--excellon-oval-format", "alternate", "--drill-origin", "absolute",
        str(board),
    ])
    drill = out / f"{board.stem}.drl"
    return Exports(drill=drill if drill.is_file() else None, **layers)


def machine_rules(recipe: Recipe, resolved: Resolved) -> str:
    """Custom DRC rules describing what the CNC can physically make.

    Applied to a scratch copy of the board, replacing the project's own rules:
    the project's DRC answers "is the design right", this one answers "can my
    tools cut it".
    """
    c = recipe.checks
    gap = resolved.isolation_width_mm + c.clearance_margin_mm
    rules = [
        "(version 1)",
        f'(rule "{RULE_PREFIX}_isolation_gap"\n  (layer outer)\n'
        f"  (constraint clearance (min {gap:.4f}mm)))",
        f'(rule "{RULE_PREFIX}_track_width"\n  (layer outer)\n'
        f"  (constraint track_width (min {c.min_track_width_mm:.4f}mm)))",
        f'(rule "{RULE_PREFIX}_annular_ring"\n'
        f"  (constraint annular_width (min {c.min_annular_ring_mm:.4f}mm)))",
        f'(rule "{RULE_PREFIX}_hole_to_hole"\n'
        f"  (constraint hole_to_hole (min {c.min_hole_to_hole_mm:.4f}mm)))",
        f'(rule "{RULE_PREFIX}_edge_gap"\n'
        f"  (constraint edge_clearance (min {gap:.4f}mm)))",
        # Last = highest priority. Test patterns that probe the limits on
        # purpose live in rule areas with this name; only real layout errors
        # (overlaps, sub-0.09 mm features) are still reported there.
        f'(rule "{RULE_PREFIX}_calibration_area"\n'
        f"  (condition \"A.intersectsArea('{CALIBRATION_AREA}') || B.intersectsArea('{CALIBRATION_AREA}')\")\n"
        "  (constraint clearance (min 0.09mm))\n"
        "  (constraint track_width (min 0.09mm)))",
    ]
    return "\n\n".join(rules) + "\n"


@dataclass(frozen=True)
class Violation:
    rule: str
    description: str
    items: list[str]


def _violations(report: Path) -> list[dict]:
    data = json.loads(report.read_text(encoding="utf-8"))
    return [*data.get("violations", []), *data.get("unconnected_items", [])]


def _parse(v: dict) -> Violation:
    items = []
    for i in v.get("items", []):
        pos = i.get("pos", {})
        where = f" @ ({pos['x']:.2f}, {pos['y']:.2f})" if "x" in pos else ""
        items.append(i.get("description", "") + where)
    return Violation(v.get("type", "?"), v.get("description", ""), items)


def drc_project(cli: str, board: Path, out: Path) -> list[Violation]:
    """The project's own DRC, errors only."""
    report = out / "drc_project.json"
    _run([cli, "pcb", "drc", "--format", "json", "--severity-error", "--units", "mm",
          "-o", str(report), str(board)], ok_codes=(0, 5))
    return [_parse(v) for v in _violations(report)]


def drc_machine(cli: str, board: Path, rules: str, out: Path) -> list[Violation]:
    """DRC against the machining rules only; unrelated findings are filtered out."""
    scratch = out / "drc_machine"
    scratch.mkdir(parents=True, exist_ok=True)
    shutil.copy2(board, scratch / board.name)
    pro = board.with_suffix(".kicad_pro")
    if pro.is_file():
        shutil.copy2(pro, scratch / pro.name)
    (scratch / board.with_suffix(".kicad_dru").name).write_text(rules, encoding="utf-8")
    report = out / "drc_machine.json"
    _run([cli, "pcb", "drc", "--format", "json", "--severity-all", "--units", "mm",
          "-o", str(report), str(scratch / board.name)], ok_codes=(0, 5))
    found = [_parse(v) for v in _violations(report)]
    return [v for v in found if f"{RULE_PREFIX}_" in v.description]
