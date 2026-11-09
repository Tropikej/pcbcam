"""Static checks on final G-code: allowed words, limits, and obviously unsafe motion."""

from __future__ import annotations

from dataclasses import dataclass

from .config import MachineProfile, Recipe
from .postprocess import command_name, split_comment, words


@dataclass(frozen=True)
class Issue:
    line: int
    message: str

    def __str__(self) -> str:
        return f"line {self.line}: {self.message}"


def _comment_problem(line: str) -> str | None:
    """Comment syntax that strict parsers reject silently (suspected in Makera Studio)."""
    if any(ord(c) > 126 or (ord(c) < 32 and c != "\t") for c in line):
        return "non-ASCII or control character"
    depth = 0
    for c in line:
        if c == "(":
            depth += 1
            if depth > 1:
                return "nested '(' inside a comment"
        elif c == ")":
            depth -= 1
            if depth < 0:
                return "')' without a matching '('"
        elif c == ";" and depth:
            return "';' inside a (comment)"
    return "unclosed '(' comment" if depth else None


def check(gcode: str, profile: MachineProfile, recipe: Recipe, xy_slack_mm: float,
          origin_mm: tuple[float, float] = (0.0, 0.0)) -> list[Issue]:
    """``xy_slack_mm`` allows tool paths outside the stock by this much (cutter radius).

    ``origin_mm`` is where work zero sits in the stock-corner frame (the lower-left
    pin, or (0, 0)): the stock then spans ``[-origin, size - origin]`` in work
    coordinates, so a pin origin legitimately puts board features at negative X/Y.
    """
    allowed = {w.upper() for w in profile.allowed_words}
    issues: list[Issue] = []
    metric = absolute = spindle = False
    z_floor = -(recipe.stock.thickness_mm + 0.5)  # at most 0.5 mm into the spoilboard
    ox, oy = origin_mm
    min_x, max_x = -ox - xy_slack_mm, recipe.stock.size_x_mm - ox + xy_slack_mm
    min_y, max_y = -oy - xy_slack_mm, recipe.stock.size_y_mm - oy + xy_slack_mm
    z = None

    for n, raw in enumerate(gcode.splitlines(), start=1):
        if problem := _comment_problem(raw):
            issues.append(Issue(n, problem))
        code, _ = split_comment(raw)
        if not code:
            continue
        ws = words(code)
        coords: dict[str, float] = {}
        motion = False
        for letter, value in ws:
            if letter in "GM":
                name = command_name(letter, value)
                if name not in allowed:
                    issues.append(Issue(n, f"{name} is not in the machine's allowed words"))
                if name == "G21":
                    metric = True
                elif name == "G20":
                    issues.append(Issue(n, "inch mode (G20) is not allowed"))
                elif name == "G90":
                    absolute = True
                elif name == "G91":
                    issues.append(Issue(n, "relative mode (G91) is not allowed"))
                elif name in ("M3", "M4"):
                    spindle = True
                elif name in ("M5", "M6"):
                    spindle = False
                elif name in ("G0", "G1", "G2", "G3"):
                    motion = True
            else:
                coords[letter] = value
        if "F" in coords and coords["F"] > profile.max_feed_mm_min:
            issues.append(Issue(n, f"feed {coords['F']} exceeds {profile.max_feed_mm_min} mm/min"))
        if "S" in coords and coords["S"] > profile.max_rpm:
            issues.append(Issue(n, f"spindle {coords['S']} exceeds {profile.max_rpm} rpm"))
        if not (motion or {"X", "Y", "Z"} & coords.keys()):
            continue
        if not (metric and absolute):
            issues.append(Issue(n, "motion before G21 and G90 are both set"))
            metric = absolute = True  # report once
        if "Z" in coords:
            z = coords["Z"]
            if z < z_floor:
                issues.append(Issue(n, f"Z{z} is deeper than the stock allows ({z_floor:.2f})"))
        if z is not None and z < 0 and not spindle:
            issues.append(Issue(n, "cutting below Z0 with the spindle off"))
        x, y = coords.get("X"), coords.get("Y")
        if x is not None and not min_x <= x <= max_x:
            issues.append(Issue(n, f"X{x} is outside the stock ({-ox:.1f}..{recipe.stock.size_x_mm - ox:.1f})"))
        if y is not None and not min_y <= y <= max_y:
            issues.append(Issue(n, f"Y{y} is outside the stock ({-oy:.1f}..{recipe.stock.size_y_mm - oy:.1f})"))
    return issues
