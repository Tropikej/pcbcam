"""Rewrite pcb2gcode output for a specific controller, driven by a machine profile.

pcb2gcode writes LinuxCNC-flavoured G-code. For the Makera Z1 (Smoothieware
fork) the important differences, all verified against pcb2gcode 3.0.4 source:

* ``G04 P`` is written in **milliseconds**; the Z1 reads P as **seconds**.
* A tool change is ``T1`` / ``M5`` / ``G04`` / ``(MSG ...)`` / ``M6`` / ``M0`` /
  ``M3`` on separate lines. Carvera-family controllers want ``M6 T1`` on one
  line, and ``M0`` would pause the job for nothing.
* The spindle speed is set once with ``G00 S...`` before ``M3``; per-tool speeds
  are applied here instead.
* Drilling uses one feed for every bit (the slowest) and retracts at that feed
  with ``G1``; here each drill gets its own plunge feed and retracts are rapid.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .config import MachineProfile
from .tooling import ResolvedTool

_MSG_TOOL = re.compile(r"\(MSG, Change tool bit to .*?([\d.]+)\s*(mm|in(?:ch)?)\)")
_WORD = re.compile(r"([A-Z])\s*(-?\d+(?:\.\d*)?|-?\.\d+)")


def split_comment(line: str) -> tuple[str, str]:
    """``"G00 Z1 ( retract )"`` -> ``("G00 Z1", "( retract )")``."""
    code, comment = line, ""
    for opener in ("(", ";"):
        i = code.find(opener)
        if i >= 0:
            code, comment = code[:i], code[i:] + comment
    return code.strip(), comment.strip()


def words(code: str) -> list[tuple[str, float]]:
    return [(letter, float(num)) for letter, num in _WORD.findall(code.upper())]


def command_name(letter: str, value: float) -> str:
    """``("G", 1.0)`` -> ``"G1"``, ``("G", 38.2)`` -> ``"G38.2"``."""
    text = f"{value:.1f}".rstrip("0").rstrip(".")
    return f"{letter}{text}"


@dataclass
class Job:
    name: str
    tools: list[ResolvedTool]
    program: str = "pcbcam"  # short program name for the header (file stem)
    probe: bool = False
    drilling: bool = False
    approach_mm: float | None = None  # rapid to this Z before plunging into the stock
    extents: tuple[float, float, float, float] | None = None  # filled in for probing
    lines: list[str] = field(default_factory=list)


class PostProcessError(Exception):
    pass


def gcode_comment(text: str) -> str:
    """A G-code comment that any parser accepts.

    Comments cannot nest, ``;`` starts a line comment on Smoothieware-style
    controllers, and some parsers choke on non-ASCII: all of it is replaced.
    """
    clean = text.replace("(", "[").replace(")", "]").replace(";", ",")
    clean = "".join(c if 32 <= ord(c) < 127 else "?" for c in clean)
    return f"({clean})"


def _fmt(v: float) -> str:
    return f"{v:.3f}".rstrip("0").rstrip(".")


def _tool_for(diameter_mm: float, tools: list[ResolvedTool]) -> ResolvedTool:
    best = min(tools, key=lambda t: abs(t.width_mm - diameter_mm))
    if abs(best.width_mm - diameter_mm) > 1e-3:
        raise PostProcessError(
            f"pcb2gcode asked for a {diameter_mm} mm tool but none of "
            f"{[t.name for t in tools]} matches"
        )
    return best


def xy_extents(lines: list[str]) -> tuple[float, float, float, float]:
    xs, ys = [], []
    for line in lines:
        code, _ = split_comment(line)
        for letter, value in words(code):
            if letter == "X":
                xs.append(value)
            elif letter == "Y":
                ys.append(value)
    if not xs or not ys:
        raise PostProcessError("no XY motion found")
    return min(xs), min(ys), max(xs), max(ys)


def process(source: str, job: Job, profile: MachineProfile, probing=None) -> str:
    """Return controller-ready G-code for one pcb2gcode output file."""
    body: list[str] = []
    current: ResolvedTool | None = None
    used: list[ResolvedTool] = []
    drop = {w.upper() for w in profile.drop_words}
    cur_z: float | None = None

    for raw in source.splitlines():
        line = raw.strip()
        if not line:
            continue
        code, comment = split_comment(line)
        if not code:
            if m := _MSG_TOOL.search(comment):
                dia = float(m[1]) * (25.4 if m[2].startswith("in") else 1.0)
                current = _tool_for(dia, job.tools)
                if current not in used:
                    used.append(current)
                body.append(gcode_comment(f"{current.name}: {current.tool.type} {_fmt(current.width_mm)} mm"))
            continue  # pcb2gcode's own banner comments are replaced by ours
        ws = words(code)
        if not ws:
            continue
        first = command_name(*ws[0])
        prev_z, zv = cur_z, dict(ws).get("Z")
        if zv is not None:
            cur_z = zv

        if first in drop or (first in ("M2", "M30")):
            continue
        if first[0] == "T" and len(ws) == 1:
            continue  # tool selection is folded into the M6 line
        if ws[0][0] == "S" or (first == "G0" and [w[0] for w in ws] == ["G", "S"]):
            continue  # speed is emitted with M3
        if first == "G4":
            ms = dict(ws).get("P", 0.0)
            if ms <= 0:
                continue
            p = ms / 1000 if profile.dwell_unit == "s" else ms
            body.append(f"G4 P{_fmt(p)}")
            continue
        if first == "M6":
            if current is None:
                raise PostProcessError(f"{job.name}: M6 without a preceding tool message")
            body += profile.tool_change.format(
                number=current.number, name=current.name, diameter_mm=_fmt(current.width_mm)
            ).strip().splitlines()
            continue
        if first == "M3":
            if current is None:
                raise PostProcessError(f"{job.name}: spindle start before any tool change")
            body += profile.spindle_on.format(rpm=_fmt(current.tool.rpm)).strip().splitlines()
            continue
        if job.drilling and first == "G1":
            letters = [w[0] for w in ws]
            if letters == ["G", "F"] and current is not None:
                body.append(f"G1 F{_fmt(current.tool.plunge_mm_min)}")
                continue
            if letters == ["G", "Z"] and ws[1][1] > 0:
                body.append(f"G0 Z{_fmt(ws[1][1])}")  # retract out of the hole
                continue
        plunge = first == "G1" and {w[0] for w in ws} <= {"G", "Z", "F"} and zv is not None and zv < 0
        if plunge and job.approach_mm is not None and prev_z is not None and prev_z > job.approach_mm:
            body.append(f"G0 Z{_fmt(job.approach_mm)}")
        body.append(code)

    info = [gcode_comment(f"{job.name} -- generated by pcbcam for {profile.name}")]
    if not profile.verified:
        info.append(gcode_comment("WARNING: machine profile not verified on hardware -- air cut first"))
    info += [gcode_comment(f"T{t.number} {t.name}: {t.tool.type} {_fmt(t.width_mm)} mm") for t in used]
    out: list[str] = []
    header = profile.header.strip().splitlines()
    if not any(line.strip() == "{info}" for line in header):
        header.insert(0, "{info}")
    for line in header:
        if line.strip() == "{info}":
            out += info
        else:
            out.append(line.format(program=gcode_comment(job.program)[1:-1]))
    if job.probe and probing is not None and probing.enabled:
        if not profile.probe_grid:
            raise PostProcessError(f"machine '{profile.name}' has no probe_grid template")
        x0, y0, x1, y1 = job.extents or xy_extents(body)
        inset = probing.inset_mm
        out.append(gcode_comment("height map over the isolation area, compensation stays on until reset"))
        out += profile.probe_grid.format(
            x=_fmt(x0 + inset), y=_fmt(y0 + inset),
            a=_fmt(max(x1 - x0 - 2 * inset, 1)), b=_fmt(max(y1 - y0 - 2 * inset, 1)),
            i=probing.points_x, j=probing.points_y, h=_fmt(probing.probe_height_mm),
        ).strip().splitlines()
    out += body
    out += profile.footer.strip().splitlines()
    eol = "\r\n" if profile.line_ending == "crlf" else "\n"
    return eol.join(out) + eol
