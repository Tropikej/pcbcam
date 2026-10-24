"""YAML configuration: tool library, machining recipe and machine profile.

Every value carries its unit in the field name (``_mm``, ``_mm_min``, ``_deg``) so a
recipe reads the same no matter which CAM backend consumes it. Unknown keys are
rejected, so a typo fails loudly instead of being silently ignored.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class ConfigError(Exception):
    """A configuration file is missing, malformed or inconsistent."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


Positive = Annotated[float, Field(gt=0)]
NonNegative = Annotated[float, Field(ge=0)]
ToolNumber = Annotated[int, Field(ge=1, le=99)]


# --- tool library ----------------------------------------------------------


class CalibrationPoint(_Strict):
    """A groove width measured on a real test cut at a given depth."""

    depth_mm: Positive
    width_mm: Positive


class VBit(_Strict):
    type: Literal["vbit"]
    number: ToolNumber
    angle_deg: float = Field(gt=0, lt=180)
    tip_diameter_mm: NonNegative
    calibration: list[CalibrationPoint] = []
    rpm: Positive
    feed_mm_min: Positive
    plunge_mm_min: Positive
    description: str = ""


class EndMill(_Strict):
    type: Literal["endmill"]
    number: ToolNumber
    diameter_mm: Positive
    rpm: Positive
    feed_mm_min: Positive
    plunge_mm_min: Positive
    description: str = ""


class Drill(_Strict):
    type: Literal["drill"]
    number: ToolNumber
    diameter_mm: Positive
    rpm: Positive
    plunge_mm_min: Positive
    description: str = ""


Tool = Annotated[VBit | EndMill | Drill, Field(discriminator="type")]


class ToolLibrary(_Strict):
    tools: dict[str, Tool]

    @model_validator(mode="after")
    def _unique_numbers(self) -> ToolLibrary:
        seen: dict[int, str] = {}
        for name, tool in self.tools.items():
            if tool.number in seen:
                raise ValueError(
                    f"tools '{seen[tool.number]}' and '{name}' share tool number {tool.number}"
                )
            seen[tool.number] = name
        return self


# --- recipe ----------------------------------------------------------------


class Stock(_Strict):
    size_x_mm: Positive
    size_y_mm: Positive
    thickness_mm: Positive = 1.6
    copper_thickness_mm: Positive = 0.035


class Placement(_Strict):
    """Where the board's lower-left corner sits relative to work zero (stock corner)."""

    margin_x_mm: NonNegative = 5.0
    margin_y_mm: NonNegative = 5.0


class Heights(_Strict):
    safe_mm: Positive = 2.0
    tool_change_mm: Positive = 15.0


class Isolation(_Strict):
    enabled: bool = True
    side: Literal["front"] = "front"  # back side arrives with double-sided support
    # pcb2gcode semantics: earlier tools are used only where they fit, the last
    # one isolates everything. List the widest first.
    tools: list[str] = Field(min_length=1)
    depth_mm: Positive
    isolation_width_mm: Positive | None = None
    overlap_percent: float = Field(default=50, ge=0, lt=100)


class Drilling(_Strict):
    enabled: bool = True
    tools: list[str] = Field(min_length=1)
    depth_mm: Positive
    match_tolerance_mm: NonNegative = 0.05
    # Holes larger than every drill (+ tolerance) are milled with this end mill.
    milldrill_tool: str | None = None


class Tabs(_Strict):
    count: int = Field(default=4, ge=0)
    width_mm: Positive = 1.5
    height_mm: Positive = 0.5


class Outline(_Strict):
    enabled: bool = True
    tool: str
    depth_mm: Positive
    stepdown_mm: Positive
    tabs: Tabs = Tabs()


class Checks(_Strict):
    clearance_margin_mm: NonNegative = 0.02
    min_track_width_mm: Positive = 0.2
    min_annular_ring_mm: Positive = 0.25
    min_hole_to_hole_mm: Positive = 0.3
    project_drc: bool = True  # the board's own rules, errors only
    machine_drc: bool = True  # rules derived from the tools


class Probing(_Strict):
    """Height-map probing injected at the start of the isolation job (experimental)."""

    enabled: bool = False
    points_x: int = Field(default=5, ge=2, le=15)
    points_y: int = Field(default=5, ge=2, le=15)
    probe_height_mm: Positive = 2.0
    inset_mm: NonNegative = 1.0


class Recipe(_Strict):
    machine: str = "makera_z1"
    tools: str = "tools.yaml"  # path relative to the recipe file
    stock: Stock
    placement: Placement = Placement()
    heights: Heights = Heights()
    isolation: Isolation
    drilling: Drilling | None = None
    outline: Outline | None = None
    checks: Checks = Checks()
    probing: Probing = Probing()

    @model_validator(mode="after")
    def _depths(self) -> Recipe:
        if self.isolation.depth_mm <= self.stock.copper_thickness_mm:
            raise ValueError(
                f"isolation.depth_mm ({self.isolation.depth_mm}) must exceed the copper "
                f"thickness ({self.stock.copper_thickness_mm})"
            )
        if self.outline and self.outline.enabled:
            if self.outline.depth_mm < self.stock.thickness_mm:
                raise ValueError(
                    f"outline.depth_mm ({self.outline.depth_mm}) does not cut through "
                    f"{self.stock.thickness_mm} mm stock"
                )
            if self.outline.tabs.count and self.outline.tabs.height_mm >= self.stock.thickness_mm:
                raise ValueError("outline.tabs.height_mm must be thinner than the stock")
        if self.drilling and self.drilling.enabled and self.drilling.depth_mm < self.stock.thickness_mm:
            raise ValueError(
                f"drilling.depth_mm ({self.drilling.depth_mm}) does not go through "
                f"{self.stock.thickness_mm} mm stock"
            )
        return self


# --- machine profile -------------------------------------------------------


class MachineProfile(_Strict):
    name: str
    verified: bool = False
    notes: str = ""
    dwell_unit: Literal["s", "ms"]
    header: str
    footer: str
    tool_change: str  # format string, fields: {number} {name} {diameter_mm}
    spindle_on: str  # format string, field: {rpm}
    probe_grid: str | None = None  # fields: {x} {y} {a} {b} {i} {j} {h}
    drop_words: list[str] = []  # whole lines starting with these words are removed
    allowed_words: list[str]  # anything else fails validation
    max_feed_mm_min: Positive
    max_rpm: Positive


# --- loading ---------------------------------------------------------------


def _load(path: Path, model: type[BaseModel]) -> BaseModel:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"{path}: file not found") from None
    except yaml.YAMLError as e:
        raise ConfigError(f"{path}: invalid YAML: {e}") from None
    try:
        return model.model_validate(data or {})
    except ValidationError as e:
        lines = [f"{path}: invalid {model.__name__}"]
        for err in e.errors():
            loc = ".".join(str(p) for p in err["loc"]) or "<root>"
            lines.append(f"  {loc}: {err['msg']}")
        raise ConfigError("\n".join(lines)) from None


def load_tools(path: Path) -> ToolLibrary:
    return _load(path, ToolLibrary)  # type: ignore[return-value]


def load_recipe(path: Path) -> Recipe:
    return _load(path, Recipe)  # type: ignore[return-value]


def load_machine(name_or_path: str, base: Path | None = None) -> MachineProfile:
    """Load a bundled profile by name (``makera_z1``) or a YAML file by path."""
    candidate = (base / name_or_path) if base else Path(name_or_path)
    if candidate.suffix in (".yaml", ".yml") and candidate.is_file():
        return _load(candidate, MachineProfile)  # type: ignore[return-value]
    bundled = resources.files("pcbcam") / "machines" / f"{name_or_path}.yaml"
    if not bundled.is_file():
        raise ConfigError(f"unknown machine '{name_or_path}' (no bundled profile, no such file)")
    with resources.as_file(bundled) as p:
        return _load(p, MachineProfile)  # type: ignore[return-value]
