"""Estimate how long a finished job runs on the machine.

Walks post-processed G-code -- the exact text written to the ``.cnc`` file -- and
sums the time of every move: feed moves (G1/G2/G3) at their feed rate, rapids
(G0) at the profile's ``rapid_mm_min``, ``G4`` dwells (the spindle spin-up is
emitted as a dwell, so it is counted), and a fixed machine overhead per tool
change (the automatic tool-length probe). Arc moves use their true arc length.

The estimate is *machine motion only*. On a manual-tool-change machine like the
Z1 the time to physically swap the bit is not counted -- it is unbounded and
depends on the operator -- so each tool change adds only the profile's probe
overhead, and the tool-change count is reported so a caller can add a per-swap
allowance. Height-map probing (G32) is experimental and not estimated.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .config import MachineProfile
from .postprocess import command_name, split_comment, words


@dataclass
class Estimate:
    """A time breakdown for one job, in seconds (plus a tool-change count)."""

    cutting_s: float = 0.0  # lateral feed moves (X/Y)
    plunge_s: float = 0.0  # feed moves straight down/up (Z only)
    rapid_s: float = 0.0  # G0 traverses
    dwell_s: float = 0.0  # G4 (includes spindle spin-up)
    tool_change_s: float = 0.0  # automatic tool-length probe overhead
    tool_changes: int = 0  # number of T..M6 (manual swap time not counted)

    def __add__(self, other: Estimate) -> Estimate:
        return Estimate(
            self.cutting_s + other.cutting_s,
            self.plunge_s + other.plunge_s,
            self.rapid_s + other.rapid_s,
            self.dwell_s + other.dwell_s,
            self.tool_change_s + other.tool_change_s,
            self.tool_changes + other.tool_changes,
        )

    @property
    def total_s(self) -> float:
        return self.cutting_s + self.plunge_s + self.rapid_s + self.dwell_s + self.tool_change_s

    @property
    def minutes(self) -> float:
        return self.total_s / 60.0


def _arc_length(x0: float, y0: float, x1: float, y1: float, i: float, j: float, mode: str) -> float:
    """Length of a G2/G3 arc from (x0, y0) to (x1, y1) with centre offset (i, j)."""
    r = math.hypot(i, j)
    if r == 0:
        return math.hypot(x1 - x0, y1 - y0)
    cx, cy = x0 + i, y0 + j
    a0 = math.atan2(y0 - cy, x0 - cx)
    a1 = math.atan2(y1 - cy, x1 - cx)
    sweep = (a0 - a1) if mode == "G2" else (a1 - a0)  # G2 is clockwise
    sweep %= 2 * math.pi
    if sweep <= 1e-9:  # start == end: a full circle, not a zero move
        sweep = 2 * math.pi
    return r * sweep


def estimate(gcode: str, profile: MachineProfile) -> Estimate:
    """Estimate the run time of post-processed G-code for ``profile``."""
    est = Estimate()
    x = y = z = 0.0
    feed: float | None = None  # modal feed, mm/min
    mode: str | None = None  # modal motion (G0/G1/G2/G3)
    rapid = profile.rapid_mm_min

    for raw in gcode.splitlines():
        code, _ = split_comment(raw)
        if not code:
            continue
        ws = words(code)
        if not ws:
            continue
        table = dict(ws)  # last value wins; motion lines never repeat a letter
        names = [command_name(letter, value) for letter, value in ws if letter in "GM"]

        if "M6" in names:  # tool change: probe overhead only, swap is manual
            est.tool_changes += 1
            est.tool_change_s += profile.tool_change_s
            continue
        if "G4" in names:  # dwell, already in the profile's unit
            p = table.get("P", 0.0)
            est.dwell_s += p if profile.dwell_unit == "s" else p / 1000.0
            continue
        if "F" in table:
            feed = table["F"]
        if motions := [n for n in names if n in ("G0", "G1", "G2", "G3")]:
            mode = motions[0]

        coords = {k: table[k] for k in "XYZ" if k in table}
        if not coords or mode is None:
            continue  # G28/G54/... and the modal F line carry no motion
        nx, ny, nz = coords.get("X", x), coords.get("Y", y), coords.get("Z", z)
        if mode in ("G2", "G3"):
            dist = _arc_length(x, y, nx, ny, table.get("I", 0.0), table.get("J", 0.0), mode)
        else:
            dist = math.dist((x, y, z), (nx, ny, nz))
        if dist > 0:
            if mode == "G0":
                est.rapid_s += dist / rapid * 60.0
            else:
                seconds = dist / (feed or rapid) * 60.0
                if math.hypot(nx - x, ny - y) > 1e-9:
                    est.cutting_s += seconds
                else:
                    est.plunge_s += seconds
        x, y, z = nx, ny, nz
    return est


def human_time(seconds: float) -> str:
    """``754`` -> ``"12m 34s"``; hours fold in above 60 minutes."""
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def _swaps(n: int) -> str:
    return "" if n == 0 else f"  ({n} tool change{'s' if n != 1 else ''})"


def format_report(rows: list[tuple[str, Estimate]]) -> str:
    """A plain-text table of per-job times plus the total, for the CLI."""
    total = sum((e for _, e in rows), Estimate())
    width = max((len(name) for name, _ in rows), default=0)
    lines = ["estimated run time (machine motion; manual tool swaps not counted):"]
    for name, e in rows:
        lines.append(f"  {name:<{width}}  {human_time(e.total_s):>8}{_swaps(e.tool_changes)}")
    lines.append(f"  {'total':<{width}}  {human_time(total.total_s):>8}{_swaps(total.tool_changes)}")
    return "\n".join(lines)


def github_summary(title: str, rows: list[tuple[str, Estimate]], profile: MachineProfile) -> str:
    """A Markdown section for a GitHub Actions job summary ($GITHUB_STEP_SUMMARY)."""
    total = sum((e for _, e in rows), Estimate())
    out = [
        "## pcbcam run-time estimate",
        "",
        f"Machine-motion estimate for **{title}** on {profile.name} "
        f"(rapid {profile.rapid_mm_min:g} mm/min; manual tool swaps not counted).",
        "",
        "| Job | Est. time | Tool changes |",
        "| --- | ---: | ---: |",
    ]
    for name, e in rows:
        out.append(f"| `{name}` | {human_time(e.total_s)} | {e.tool_changes or ''} |")
    out.append(f"| **Total** | **{human_time(total.total_s)}** | **{total.tool_changes}** |")
    return "\n".join(out) + "\n"
