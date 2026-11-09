"""Engrave text labels: trace a KiCad layer's stroke geometry with a V-bit.

pcb2gcode mills copper; it cannot engrave arbitrary text. Instead pcbcam exports
a dedicated engrave layer to DXF -- stroke-font text plots as LINE/ARC/CIRCLE
*centrelines*, not filled outlines -- and traces those strokes at a shallow
depth. The DXF plot shares the Gerber coordinate frame (X = board X,
Y = -board Y, mm), so the same work offset pcb2gcode uses registers the
engraving with the copper. A back-side layer is mirrored about the fixture's
flip axis, the same transform the (future) back isolation will use, so the two
register after the physical flip.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from .config import FixtureStock

# Arcs and circles are flattened to straight segments no farther than this from
# the true curve; well below the V-bit groove width, so invisible on the board.
_ARC_TOLERANCE_MM = 0.01
# Endpoints closer than this are treated as the same point when chaining strokes.
_JOIN_TOLERANCE_MM = 0.01

Point = tuple[float, float]
Segment = tuple[Point, Point]


class EngraveError(Exception):
    pass


# --- DXF reading -----------------------------------------------------------


def _pairs(text: str) -> list[tuple[str, str]]:
    """DXF is (group code, value) on consecutive lines."""
    lines = text.splitlines()
    return [(lines[i].strip(), lines[i + 1].strip()) for i in range(0, len(lines) - 1, 2)]


def _flatten_arc(cx: float, cy: float, r: float, a0: float, a1: float) -> list[Point]:
    """Points along an arc, CCW from ``a0`` to ``a1`` (degrees), chord-bounded."""
    sweep = math.radians((a1 - a0) % 360.0 or 360.0)
    # Max angle per segment that keeps the chord within tolerance of the arc.
    step = 2 * math.acos(max(0.0, 1 - _ARC_TOLERANCE_MM / r)) if r > _ARC_TOLERANCE_MM else sweep
    n = max(1, math.ceil(sweep / step)) if step else 1
    start = math.radians(a0)
    return [(cx + r * math.cos(start + sweep * k / n), cy + r * math.sin(start + sweep * k / n))
            for k in range(n + 1)]


def read_segments(text: str) -> list[Segment]:
    """Every stroke in a DXF plot, as straight segments (arcs/circles flattened)."""
    segs: list[Segment] = []
    ent: str | None = None
    v: dict[str, float] = {}
    verts: list[Point] = []

    def flush() -> None:
        nonlocal verts
        if ent == "LINE" and {"10", "20", "11", "21"} <= v.keys():
            segs.append(((v["10"], v["20"]), (v["11"], v["21"])))
        elif ent == "CIRCLE" and {"10", "20", "40"} <= v.keys():
            pts = _flatten_arc(v["10"], v["20"], v["40"], 0.0, 360.0)
            segs.extend(zip(pts, pts[1:]))
        elif ent == "ARC" and {"10", "20", "40", "50", "51"} <= v.keys():
            pts = _flatten_arc(v["10"], v["20"], v["40"], v["50"], v["51"])
            segs.extend(zip(pts, pts[1:]))
        elif ent in ("LWPOLYLINE", "POLYLINE") and len(verts) >= 2:
            segs.extend(zip(verts, verts[1:]))
            if v.get("70", 0) and int(v["70"]) & 1:  # closed
                segs.append((verts[-1], verts[0]))
        verts = []

    px: float | None = None
    for code, val in _pairs(text):
        if code == "0":
            flush()
            ent = val
            v = {}
            px = None
            continue
        if ent in ("LWPOLYLINE", "POLYLINE") and code == "10":
            px = float(val)
        elif ent in ("LWPOLYLINE", "POLYLINE") and code == "20" and px is not None:
            verts.append((px, float(val)))
            px = None
        else:
            try:
                v[code] = float(val)
            except ValueError:
                pass
    flush()
    return segs


# --- chaining into strokes -------------------------------------------------


def _key(p: Point) -> tuple[int, int]:
    q = _JOIN_TOLERANCE_MM
    return round(p[0] / q), round(p[1] / q)


def chain(segments: list[Segment]) -> list[list[Point]]:
    """Greedily join segments sharing an endpoint into polylines, fewest pen lifts."""
    from collections import defaultdict

    ends: dict[tuple[int, int], list[int]] = defaultdict(list)
    for i, (a, b) in enumerate(segments):
        ends[_key(a)].append(i)
        ends[_key(b)].append(i)
    used = [False] * len(segments)
    paths: list[list[Point]] = []

    for start in range(len(segments)):
        if used[start]:
            continue
        a, b = segments[start]
        used[start] = True
        path = [a, b]
        # Extend forward from the tail, then the head, as far as unused segments reach.
        for grow_head in (False, True):
            while True:
                tip = path[0] if grow_head else path[-1]
                nxt = None
                for j in ends[_key(tip)]:
                    if not used[j]:
                        nxt = j
                        break
                if nxt is None:
                    break
                used[nxt] = True
                p, q = segments[nxt]
                far = q if _key(p) == _key(tip) else p
                path.insert(0, far) if grow_head else path.append(far)
        paths.append(path)
    return paths


# --- G-code ----------------------------------------------------------------


@dataclass(frozen=True)
class Transform:
    """Map DXF plot coordinates (mm) to work coordinates.

    ``dx``/``dy`` bring the plot into the stock-corner frame; the optional back
    mirror is applied there (its axis is defined in that frame); then ``ox``/``oy``
    shift to the chosen work origin (the lower-left pin). Front and back therefore
    share the same work origin after the physical flip.
    """

    dx: float
    dy: float
    ox: float = 0.0
    oy: float = 0.0
    mirror: FixtureStock | None = None  # back side: mirror about the flip axis

    def __call__(self, p: Point) -> Point:
        x, y = p[0] + self.dx, p[1] + self.dy
        if self.mirror:
            x, y = self.mirror.mirror(x, y)
        return x - self.ox, y - self.oy


def _g(v: float) -> str:
    return f"{v:.4f}".rstrip("0").rstrip(".")


def build_source(paths: list[list[Point]], tool: ResolvedTool, depth_mm: float,
                 zsafe_mm: float, xf: Transform) -> str:
    """pcb2gcode-flavoured G-code for one engrave side, for the post-processor.

    Emits the tool-change message, spindle start and stroke moves; the machine
    header/footer, tool-change block and spindle command are added downstream.
    """
    feed, plunge = _g(tool.tool.feed_mm_min), _g(tool.tool.plunge_mm_min)
    out = [
        f"(MSG, Change tool bit to {_g(tool.width_mm)} mm)",
        "M6",
        "M3",
        f"G00 Z{_g(zsafe_mm)}",
    ]
    for path in paths:
        if len(path) < 2:
            continue
        x0, y0 = xf(path[0])
        out.append(f"G00 X{_g(x0)} Y{_g(y0)}")
        out.append(f"G01 Z{_g(-depth_mm)} F{plunge}")
        for i, p in enumerate(path[1:]):
            x, y = xf(p)
            out.append(f"G01 X{_g(x)} Y{_g(y)}" + (f" F{feed}" if i == 0 else ""))
        out.append(f"G00 Z{_g(zsafe_mm)}")
    return "\n".join(out) + "\n"
