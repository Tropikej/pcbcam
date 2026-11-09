"""Mirror front-style pcb2gcode output for the flipped back side.

The back copper is isolated by running pcb2gcode on the as-drawn B.Cu Gerber as
if it were a front layer (raw Gerber coordinates, no offset). Those toolpaths
then have to be reflected about the fixture's physical flip axis and brought to
the work origin -- exactly the transform the engraver already applies to back
text. For a left/right flip that is ``X -> 2c - X`` with Y unchanged, and for a
top/bottom flip ``Y -> 2c - Y`` with X unchanged, so each axis maps independently
and the rewrite is stateless: ``X -> sx*X + bx``, ``Y -> sy*Y + by`` with
``sx, sy in {+1, -1}``.

pcb2gcode isolation output is straight-line (G00/G01) only. Arcs would need the
direction flipped (G02<->G03) and I/J negated under a mirror, so if any appear
the rewrite refuses rather than emit a silently wrong path.
"""

from __future__ import annotations

import re

from .postprocess import split_comment

_COORD = re.compile(r"([XY])(-?\d+(?:\.\d+)?|-?\.\d+)")
_ARC = re.compile(r"\bG0?[23]\b")


def _fmt(v: float) -> str:
    return f"{v:.4f}".rstrip("0").rstrip(".")


def mirror_isolation(text: str, sx: float, bx: float, sy: float, by: float) -> str:
    """Rewrite X -> sx*X+bx and Y -> sy*Y+by in every code line (comments untouched)."""
    out: list[str] = []
    for raw in text.splitlines():
        code, comment = split_comment(raw)
        if code and _ARC.search(code.upper()):
            raise ValueError(
                "back isolation g-code contains arcs (G02/G03); mirroring them is not supported")

        def repl(m: re.Match[str]) -> str:
            v = float(m.group(2))
            return f"{m.group(1)}{_fmt(sx * v + bx if m.group(1) == 'X' else sy * v + by)}"

        new_code = _COORD.sub(repl, code) if code else code
        out.append(f"{new_code} {comment}".strip() if (new_code and comment) else (new_code or comment))
    return "\n".join(out) + "\n"
