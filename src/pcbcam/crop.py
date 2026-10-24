"""Crop a finished isolation job to rectangles, for short test cuts.

Inside the rectangles the cut is exactly the original toolpath. Where the path
leaves them the tool lifts to the safe height, and it plunges again where the
path comes back in. Everything that is not a move (header, tool change,
spindle, footer) is kept as is, so the file stays a valid job for the machine.

Only straight cutting moves are clipped: pcb2gcode's isolation paths are G1
segments. Arcs at cutting depth are refused rather than approximated.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .postprocess import command_name, gcode_comment, split_comment, words

Box = tuple[float, float, float, float]  # x0, y0, x1, y1 in work coordinates


class CropError(Exception):
    pass


def _clip(ax: float, ay: float, bx: float, by: float, box: Box) -> tuple[float, float] | None:
    """Liang-Barsky: the [t0, t1] part of segment A->B inside ``box``, or None."""
    x0, y0, x1, y1 = box
    dx, dy = bx - ax, by - ay
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, ax - x0), (dx, x1 - ax), (-dy, ay - y0), (dy, y1 - ay)):
        if p == 0:
            if q < 0:
                return None
            continue
        t = q / p
        if p < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
        if t0 > t1:
            return None
    return t0, t1


def _inside(x: float, y: float, boxes: list[Box]) -> bool:
    return any(b[0] <= x <= b[2] and b[1] <= y <= b[3] for b in boxes)


def _f(v: float) -> str:
    return f"{v:.4f}".rstrip("0").rstrip(".")


def _drop_idle_rapids(lines: list[str], safe_z: float) -> list[str]:
    """Remove trips to contours that were cropped away.

    At safe height, an XY rapid followed only by feed changes and safe-height
    retracts before the next XY rapid leads nowhere: drop it and those lines.
    Each kept contour sets its own plunge feed again, so no feed is lost.
    """
    out: list[str] = []
    idle_from: int | None = None  # index in ``out`` of the last XY rapid at safe height
    for line in lines:
        code, _ = split_comment(line)
        ws = words(code) if code else []
        letters = [w[0] for w in ws]
        gs = [command_name(*w) for w in ws if w[0] == "G"]
        coords = {k: v for k, v in ws if k in "XYZ"}
        xy_rapid = gs[:1] == ["G0"] and coords and "Z" not in coords
        neutral = (letters in (["G", "F"], ["F"])) or (gs[:1] == ["G0"] and list(coords) == ["Z"]
                                                       and coords["Z"] >= safe_z - 1e-9)
        if xy_rapid:
            if idle_from is not None:
                del out[idle_from:]
            idle_from = len(out)
            out.append(line)
        elif neutral and idle_from is not None:
            out.append(line)
        else:
            idle_from = None
            out.append(line)
    return out


@dataclass
class CropResult:
    text: str
    cut_mm: float  # cutting length kept
    cut_total_mm: float  # cutting length in the original
    minutes: float  # rough time estimate: cutting at feed + plunges


def crop(text: str, boxes: list[Box], safe_z: float, eol: str = "\r\n",
         approach_z: float | None = None) -> CropResult:
    out: list[str] = []
    x = y = 0.0
    z = safe_z
    modal_f: float | None = None  # feed in effect, as in the source
    feed = plunge_feed = None  # last feed seen on an XY cut / on a plunge
    phys_down = False  # is the real tool in the material (vs the original path)?
    cut_kept = cut_total = 0.0
    minutes = 0.0

    def lift() -> None:
        nonlocal phys_down
        if phys_down:
            out.append(f"G0 Z{_f(safe_z)}")
            phys_down = False

    def plunge_at(px: float, py: float) -> None:
        nonlocal phys_down, minutes
        out.append(f"G0 X{_f(px)} Y{_f(py)}")
        start = safe_z
        if approach_z is not None and approach_z < safe_z:
            out.append(f"G0 Z{_f(approach_z)}")
            start = approach_z
        out.append(f"G1 Z{_f(z)} F{_f(plunge_feed or feed or 30)}")
        if feed:
            out.append(f"G1 F{_f(feed)}")
        minutes += (start - z) / (plunge_feed or feed or 30)
        phys_down = True

    for raw in text.splitlines():
        code, _ = split_comment(raw)
        ws = words(code) if code else []
        gs = [command_name(*w) for w in ws if w[0] == "G"]
        motion = next((g for g in gs if g in ("G0", "G1", "G2", "G3")), None)
        coords = {k: v for k, v in ws if k in "XYZ"}
        if "F" in dict(ws):
            modal_f = dict(ws)["F"]
        if not coords:
            out.append(raw)  # header, tool change, spindle, dwell, comments, footer
            continue
        if motion in ("G2", "G3") and min(z, coords.get("Z", z)) < 0:
            raise CropError("arc at cutting depth: only G1 isolation paths can be cropped")
        nx, ny, nz = coords.get("X", x), coords.get("Y", y), coords.get("Z", z)

        if nz < 0 and z < 0 and (nx, ny) != (x, y) and motion != "G0":
            # Cutting move: keep the parts inside the boxes.
            feed = modal_f
            length = math.hypot(nx - x, ny - y)
            cut_total += length
            pieces = sorted(r for b in boxes if (r := _clip(x, y, nx, ny, b)))
            merged: list[list[float]] = []
            for t0, t1 in pieces:
                if merged and t0 <= merged[-1][1] + 1e-9:
                    merged[-1][1] = max(merged[-1][1], t1)
                else:
                    merged.append([t0, t1])
            for t0, t1 in merged:
                sx, sy = x + t0 * (nx - x), y + t0 * (ny - y)
                ex, ey = x + t1 * (nx - x), y + t1 * (ny - y)
                if not phys_down or t0 > 1e-9:
                    lift()
                    plunge_at(sx, sy)
                out.append(f"G1 X{_f(ex)} Y{_f(ey)}")
                kept = (t1 - t0) * length
                cut_kept += kept
                minutes += kept / (feed or 100)
            if not merged or merged[-1][1] < 1 - 1e-9:
                lift()
        elif nz < 0 and (nx, ny) == (x, y) and nz == z:
            # Zero-length move at depth (pcb2gcode writes one after each plunge).
            if phys_down:
                out.append(code)
        elif nz < 0 and (nx, ny) == (x, y):
            # Plunge (or depth change) in place: only if we are inside a box.
            plunge_feed = modal_f
            if _inside(x, y, boxes):
                out.append(code)
                phys_down = True
                minutes += abs(z - nz) / (plunge_feed or feed or 30)
            else:
                phys_down = False
        else:
            # Rapids and retracts above the copper are always safe to keep,
            # except the approach dip before a plunge we are skipping.
            if nz >= 0:
                phys_down = False
                approach_only = "Z" in coords and len(coords) == 1 and nz < safe_z
                if not (approach_only and not _inside(x, y, boxes)):
                    out.append(code)
            elif phys_down:
                out.append(code)
        x, y, z = nx, ny, nz

    out = _drop_idle_rapids(out, safe_z)
    note = gcode_comment("CROPPED test job: only " + ", ".join(
        f"X{_f(b[0])}-{_f(b[2])} Y{_f(b[1])}-{_f(b[3])}" for b in boxes))
    # After the program name line so Makera's header stays first.
    out.insert(1 if out and out[0].startswith("(") else 0, note)
    return CropResult(eol.join(out) + eol, cut_kept, cut_total, minutes)
