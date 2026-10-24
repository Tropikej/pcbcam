"""Just enough Excellon parsing to inventory hole sizes before CAM runs.

Handles the decimal-format files ``kicad-cli pcb export drill`` writes. Slots
(``G85`` in KiCad's "alternate" oval format, or routed ``M15``/``M16`` blocks)
are counted separately because v1 does not mill them.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

_TOOL_DEF = re.compile(r"^T(\d+)C([\d.]+)")
_TOOL_SELECT = re.compile(r"^T(\d+)$")
_HIT = re.compile(r"^X(-?[\d.]+)Y(-?[\d.]+)(G85)?")


@dataclass
class DrillInventory:
    holes: Counter[float] = field(default_factory=Counter)  # diameter mm -> count
    slots: Counter[float] = field(default_factory=Counter)

    @property
    def total(self) -> int:
        return sum(self.holes.values()) + sum(self.slots.values())


def parse(path: Path) -> DrillInventory:
    inv = DrillInventory()
    scale = 1.0
    sizes: dict[int, float] = {}
    current: int | None = None
    routing = False
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith(";"):
            continue
        if line.startswith("INCH"):
            scale = 25.4
        elif line.startswith("METRIC"):
            scale = 1.0
        elif m := _TOOL_DEF.match(line):
            sizes[int(m[1])] = round(float(m[2]) * scale, 4)
        elif m := _TOOL_SELECT.match(line):
            current = int(m[1]) or None
        elif line.startswith("M15"):
            routing = True
        elif line.startswith(("M16", "M17")):
            routing = False
        elif m := _HIT.match(line):
            if current is None or current not in sizes:
                raise ValueError(f"{path}: hole before any tool selection: {line}")
            dia = sizes[current]
            if m[3] or routing:
                inv.slots[dia] += 1
            else:
                inv.holes[dia] += 1
        elif line.startswith("G00") and current in sizes:
            # Routed slot ("route" oval format): G00X..Y.. M15 G01X..Y.. M16
            inv.slots[sizes[current]] += 1
    return inv
