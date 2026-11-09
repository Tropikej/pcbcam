"""Registration bores: mill the fixture pin holes into a fresh blank.

Before a blank can ride the pin fixture, it needs the matching pin holes. This
is a *separate* machine setup with its own work zero -- the blank's lower-left
corner (a corner square), because there are no pins yet to register against. So
the bores are emitted in the stock-corner frame, independent of the main jobs'
pin origin.

The user has no 4 mm drill, so each hole is helically milled with an end mill:
ramp down a helix of radius (hole - cutter) / 2, then one flat finishing circle.
The helix is flattened to straight G01 segments (like the engraver) for the
widest controller support, rather than relying on G02/G03 arc-with-Z helical
interpolation, which is unverified on the Z1.
"""

from __future__ import annotations

import math

from .tooling import ResolvedTool

# Chord error when flattening the helix to segments. This is a clearance hole
# for a dowel, not a trace, so a coarse flat is fine and keeps the file small.
_ARC_TOLERANCE_MM = 0.02


def _g(v: float) -> str:
    return f"{v:.4f}".rstrip("0").rstrip(".")


def _segments_per_turn(helix_r: float) -> int:
    if helix_r <= _ARC_TOLERANCE_MM:
        return 8
    step = 2 * math.acos(max(0.0, 1 - _ARC_TOLERANCE_MM / helix_r))
    return max(8, math.ceil(2 * math.pi / step))


def build_source(
    pins: list[tuple[float, float]],
    bore_diameter_mm: float,
    tool: ResolvedTool,
    depth_mm: float,
    zsafe_mm: float,
    approach_mm: float,
    stepdown_mm: float,
) -> str:
    """pcb2gcode-flavoured G-code for the registration bores, for the post-processor.

    ``pins`` are in the stock-corner frame (= this job's work coordinates). Emits
    the tool-change message, spindle start and helical moves; the machine
    header/footer, tool-change block and spindle command are added downstream.
    """
    helix_r = (bore_diameter_mm - tool.width_mm) / 2
    if helix_r <= 0:
        raise ValueError(
            f"a {_g(bore_diameter_mm)} mm bore is not wider than the {_g(tool.width_mm)} mm end mill"
        )
    feed, plunge = _g(tool.tool.feed_mm_min), _g(tool.tool.plunge_mm_min)
    n = _segments_per_turn(helix_r)
    ramp = max(1, math.ceil(depth_mm / stepdown_mm)) * n
    out = [
        f"(MSG, Change tool bit to {_g(tool.width_mm)} mm)",
        "M6",
        "M3",
        f"G00 Z{_g(zsafe_mm)}",
    ]
    for cx, cy in pins:
        out.append(f"G00 X{_g(cx + helix_r)} Y{_g(cy)}")  # to the helix start, above the stock
        out.append(f"G00 Z{_g(approach_mm)}")
        out.append(f"G01 Z0 F{plunge}")                    # touch the surface at the start point
        for i in range(1, ramp + 1):                       # helical ramp to full depth
            a = 2 * math.pi * i / n
            z = -depth_mm * min(1.0, i / ramp)
            x, y = cx + helix_r * math.cos(a), cy + helix_r * math.sin(a)
            out.append(f"G01 X{_g(x)} Y{_g(y)} Z{_g(z)}" + (f" F{feed}" if i == 1 else ""))
        for i in range(1, n + 1):                          # flat finishing pass at full depth
            a = 2 * math.pi * i / n
            x, y = cx + helix_r * math.cos(a), cy + helix_r * math.sin(a)
            out.append(f"G01 X{_g(x)} Y{_g(y)}")
        out.append(f"G00 Z{_g(zsafe_mm)}")
    return "\n".join(out) + "\n"
