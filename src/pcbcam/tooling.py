"""Turn tool definitions plus a recipe into concrete cutting widths and drill assignments."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .config import ConfigError, Drill, EndMill, Recipe, Tool, ToolLibrary, VBit


def vbit_width(tool: VBit, depth_mm: float) -> float:
    """Effective groove width of a V-bit at ``depth_mm``.

    Uses the tool's calibration points when they bracket the depth (linear
    interpolation), otherwise the ideal cone: ``tip + 2 * depth * tan(angle / 2)``.
    A depth outside the calibrated range is an error rather than a silent
    extrapolation, because the whole point of calibrating is to stop guessing.
    """
    points = sorted(tool.calibration, key=lambda p: p.depth_mm)
    if not points:
        return tool.tip_diameter_mm + 2 * depth_mm * math.tan(math.radians(tool.angle_deg / 2))
    for p in points:
        if math.isclose(p.depth_mm, depth_mm, abs_tol=1e-6):
            return p.width_mm
    for lo, hi in zip(points, points[1:]):
        if lo.depth_mm < depth_mm < hi.depth_mm:
            t = (depth_mm - lo.depth_mm) / (hi.depth_mm - lo.depth_mm)
            return lo.width_mm + t * (hi.width_mm - lo.width_mm)
    raise ConfigError(
        f"depth {depth_mm} mm is outside the calibrated range "
        f"{points[0].depth_mm}-{points[-1].depth_mm} mm for this V-bit"
    )


def cutting_width(tool: Tool, depth_mm: float) -> float:
    if isinstance(tool, VBit):
        return vbit_width(tool, depth_mm)
    return tool.diameter_mm


@dataclass(frozen=True)
class ResolvedTool:
    name: str
    tool: Tool
    width_mm: float  # what pcb2gcode is told the cutter diameter is

    @property
    def number(self) -> int:
        return self.tool.number


@dataclass(frozen=True)
class Resolved:
    isolation: list[ResolvedTool]
    drills: list[ResolvedTool]
    milldrill: ResolvedTool | None
    outline: ResolvedTool | None

    @property
    def isolation_width_mm(self) -> float:
        """Width of the cutter that must fit every gap (the last one listed)."""
        return self.isolation[-1].width_mm

    def all(self) -> list[ResolvedTool]:
        items = [*self.isolation, *self.drills, self.milldrill, self.outline]
        return [t for t in items if t is not None]


def _get(lib: ToolLibrary, name: str, kinds: tuple[type, ...], role: str) -> Tool:
    try:
        tool = lib.tools[name]
    except KeyError:
        raise ConfigError(f"{role}: unknown tool '{name}' (known: {', '.join(lib.tools)})") from None
    if not isinstance(tool, kinds):
        allowed = " or ".join(k.__name__ for k in kinds)
        raise ConfigError(f"{role}: tool '{name}' is a {tool.type}, expected {allowed}")
    return tool


def resolve(recipe: Recipe, lib: ToolLibrary) -> Resolved:
    iso = [
        ResolvedTool(n, t, cutting_width(t, recipe.isolation.depth_mm))
        for n in recipe.isolation.tools
        for t in [_get(lib, n, (VBit, EndMill), "isolation.tools")]
    ]
    widths = [t.width_mm for t in iso]
    if widths != sorted(widths, reverse=True):
        raise ConfigError(
            "isolation.tools must go from widest to narrowest (pcb2gcode uses the last "
            f"one everywhere); effective widths are {[round(w, 3) for w in widths]}"
        )

    drills: list[ResolvedTool] = []
    milldrill = None
    d = recipe.drilling
    if d and d.enabled:
        for n in d.tools:
            t = _get(lib, n, (Drill,), "drilling.tools")
            drills.append(ResolvedTool(n, t, t.diameter_mm))
        drills.sort(key=lambda r: r.width_mm)
        if d.milldrill_tool:
            t = _get(lib, d.milldrill_tool, (EndMill,), "drilling.milldrill_tool")
            milldrill = ResolvedTool(d.milldrill_tool, t, t.diameter_mm)

    outline = None
    o = recipe.outline
    if o and o.enabled:
        t = _get(lib, o.tool, (EndMill,), "outline.tool")
        outline = ResolvedTool(o.tool, t, t.diameter_mm)

    return Resolved(iso, drills, milldrill, outline)


@dataclass(frozen=True)
class HoleAssignment:
    diameter_mm: float
    count: int
    tool: ResolvedTool | None  # None: no tool can make this hole
    method: str  # "drill", "milldrill" or "none"


def assign_holes(
    holes: dict[float, int], resolved: Resolved, tolerance_mm: float
) -> list[HoleAssignment]:
    """Match each finished hole size to a drill, falling back to the milldrill end mill."""
    out = []
    for dia, count in sorted(holes.items()):
        best = min(resolved.drills, key=lambda r: abs(r.width_mm - dia), default=None)
        if best and abs(best.width_mm - dia) <= tolerance_mm + 1e-9:
            out.append(HoleAssignment(dia, count, best, "drill"))
        elif resolved.milldrill and dia >= resolved.milldrill.width_mm + 0.1:
            out.append(HoleAssignment(dia, count, resolved.milldrill, "milldrill"))
        else:
            out.append(HoleAssignment(dia, count, None, "none"))
    return out
