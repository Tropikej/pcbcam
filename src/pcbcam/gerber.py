"""Bounding box of a board outline Gerber (Edge.Cuts), on the line centres.

pcb2gcode's ``zero-start`` zeroes on the outline *buffered by the cutter
diameter* (plus half the line width), so work zero would move with the tool.
Instead pcbcam reads the outline extent here and passes explicit offsets.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

_FS = re.compile(r"%FSLAX(\d)(\d)Y(\d)(\d)\*%")
_MO = re.compile(r"%MO(MM|IN)\*%")
_OP = re.compile(r"^(?:G0?([123]))?(?:X(-?\d+))?(?:Y(-?\d+))?(?:I(-?\d+))?(?:J(-?\d+))?D0?([123])\*$")


def outline_bbox(path: Path) -> tuple[float, float, float, float]:
    """``(xmin, ymin, xmax, ymax)`` in mm, Gerber orientation (Y up)."""
    text = path.read_text(encoding="utf-8", errors="replace")
    fs = _FS.search(text)
    if not fs:
        raise ValueError(f"{path}: no %FSLA format statement")
    scale = 10.0 ** -int(fs[2])
    mo = _MO.search(text)
    if mo and mo[1] == "IN":
        scale *= 25.4

    xs: list[float] = []
    ys: list[float] = []
    x = y = 0.0
    mode = "1"
    for raw in text.splitlines():
        line = raw.strip()
        if line in ("G01*", "G02*", "G03*"):
            mode = line[2]
            continue
        m = _OP.match(line)
        if not m:
            continue
        g, sx, sy, si, sj, d = m.groups()
        mode = g or mode
        nx = int(sx) * scale if sx is not None else x
        ny = int(sy) * scale if sy is not None else y
        if d == "1":
            xs += [x, nx]
            ys += [y, ny]
            if mode in "23" and (si is not None or sj is not None):
                cx = x + (int(si or 0) * scale)
                cy = y + (int(sj or 0) * scale)
                for px, py in _arc_extremes(x, y, nx, ny, cx, cy, clockwise=mode == "2"):
                    xs.append(px)
                    ys.append(py)
        x, y = nx, ny
    if not xs:
        raise ValueError(f"{path}: no outline drawn")
    return min(xs), min(ys), max(xs), max(ys)


def _arc_extremes(x0, y0, x1, y1, cx, cy, clockwise):
    """Axis-extreme points (0/90/180/270 deg) that an arc sweeps through."""
    r = math.hypot(x0 - cx, y0 - cy)
    a0 = math.atan2(y0 - cy, x0 - cx)
    a1 = math.atan2(y1 - cy, x1 - cx)
    if clockwise:
        a0, a1 = a1, a0
    sweep = (a1 - a0) % (2 * math.pi) or 2 * math.pi  # full circle when start == end
    for k in range(4):
        a = k * math.pi / 2
        if (a - a0) % (2 * math.pi) <= sweep + 1e-12:
            yield cx + r * math.cos(a), cy + r * math.sin(a)
