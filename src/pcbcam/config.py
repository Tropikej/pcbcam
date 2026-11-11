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
    # Rapid down to this height above the copper before each plunge; pcb2gcode
    # would feed the whole safe height at plunge speed (4 s per plunge at 30 mm/min).
    approach_mm: NonNegative = 0.5

    @model_validator(mode="after")
    def _order(self) -> Heights:
        if not self.approach_mm < self.safe_mm < self.tool_change_mm:
            raise ValueError("heights must satisfy approach_mm < safe_mm < tool_change_mm")
        return self


class Isolation(_Strict):
    enabled: bool = True
    # "front": top copper only. "back": bottom copper only, mirrored about the
    # fixture flip axis. "both": front then back. back/both need a fixture (its
    # flip axis sets the mirror); the same V-bit(s) cut both sides.
    side: Literal["front", "back", "both"] = "front"
    # pcb2gcode semantics: earlier tools are used only where they fit, the last
    # one isolates everything. List the widest first.
    tools: list[str] = Field(min_length=1)
    depth_mm: Positive
    isolation_width_mm: Positive | None = None
    overlap_percent: float = Field(default=50, ge=0, lt=100)
    # Calibration boards only: let pcb2gcode cut into copper where the last tool
    # doesn't fit (its "contentions" warning) instead of failing the build.
    allow_contentions: bool = False


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


class Engrave(_Strict):
    """Trace stroke-font text on a KiCad layer with a V-bit (labels, logos).

    ``front_layer`` is cut as drawn; ``back_layer`` is mirrored about the
    fixture flip axis, so draw it to read through the board from the front.
    Needs a fixture (for the flip axis) only when ``back_layer`` is set.
    """

    enabled: bool = True
    tool: str
    depth_mm: Positive
    front_layer: str | None = None
    back_layer: str | None = None

    @model_validator(mode="after")
    def _layers(self) -> Engrave:
        if not self.front_layer and not self.back_layer:
            raise ValueError("set front_layer and/or back_layer")
        return self


class Checks(_Strict):
    clearance_margin_mm: NonNegative = 0.02
    min_track_width_mm: Positive = 0.2
    min_annular_ring_mm: Positive = 0.25
    min_hole_to_hole_mm: Positive = 0.3
    # Copper to board outline. None = the isolation gap (+ margin); 0 = copper may
    # reach the outline (edge pads/connectors by design: the outline cut trims it).
    edge_gap_mm: NonNegative | None = None
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
    # Fixture file (path relative to the recipe). With it, the board is placed
    # by the fixture footprint drawn in KiCad instead of `placement`.
    fixture: str | None = None
    stock: Stock
    placement: Placement = Placement()
    heights: Heights = Heights()
    isolation: Isolation
    drilling: Drilling | None = None
    outline: Outline | None = None
    engrave: Engrave | None = None
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
        if self.engrave and self.engrave.enabled and self.engrave.depth_mm >= self.stock.thickness_mm:
            raise ValueError(
                f"engrave.depth_mm ({self.engrave.depth_mm}) must be shallower than the "
                f"{self.stock.thickness_mm} mm stock"
            )
        return self


# --- fixture ---------------------------------------------------------------

Name = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.-]+$")]


class FixtureStock(_Strict):
    """A blank size the fixture takes, with its registration pins.

    Coordinates are in the stock frame: work zero at the stock lower-left
    corner, X right, Y up. The board flips about the stock's centre line, so
    the flipped blank lands in the same place; each pin then goes into the
    hole of its mirror image (or its own hole, for a pin on the axis).
    """

    size_x_mm: Positive
    size_y_mm: Positive
    flip: Literal["left_right", "top_bottom"] = "left_right"
    pins_mm: list[tuple[float, float]] = Field(min_length=2)

    def flip_axis(self) -> tuple[Literal["x", "y"], float]:
        """``("x", c)``: the board flips left/right about the line X = c."""
        if self.flip == "left_right":
            return "x", self.size_x_mm / 2
        return "y", self.size_y_mm / 2

    def mirror(self, x: float, y: float) -> tuple[float, float]:
        axis, c = self.flip_axis()
        return (2 * c - x, y) if axis == "x" else (x, 2 * c - y)

    def lower_left_pin(self) -> tuple[float, float]:
        """The pin nearest the stock origin: the natural XY touch-off point on a
        permanent fixture (probe it once; it never moves)."""
        return min(self.pins_mm, key=lambda p: (p[0], p[1]))


class Fixture(_Strict):
    name: Name
    verified: bool = False
    # KiCad layer the footprint draws on; add it to the board (Board Setup >
    # Board Editor Layers) and name it "Fixture".
    layer: str = "User.10"
    pin_diameter_mm: Positive
    pin_clearance_mm: NonNegative = 3.0  # pin edge to board outline
    edge_margin_mm: NonNegative = 3.0  # stock edge to board outline
    # Where g-code X0 Y0 sits. "lower_left_pin": a permanent pin fixture, probe
    # the pin once and reuse it for every board and both sides. "stock_corner":
    # probe the blank's corner each time (the old behaviour). The blank's own
    # registration bores are always emitted in the stock-corner frame (00_register),
    # because when they are cut there are no pins yet to register against.
    work_origin: Literal["stock_corner", "lower_left_pin"] = "lower_left_pin"
    # Added to the pin diameter for the holes bored into the blank: a touch of
    # slip-fit clearance over the dowels. An end mill deflects inward and leaves
    # the bore a hair under nominal, so some clearance is needed for the blank to
    # drop onto the pins (0.1 measured right for a 2 mm single flute boring 4 mm).
    blank_bore_clearance_mm: NonNegative = 0.0
    # Where g-code X0 Y0 sits for the 00_register blank-bore job.
    # "stock_corner" (default): a FRESH blank has no holes yet, so zero its
    # lower-left CORNER (a corner square / edge-probe). "lower_left_pin": the
    # blank already has holes (re-boring wider, or a second pass) -- zero the
    # probed centre of the lower-left hole, which is far more repeatable than an
    # edge probe and shares the datum of every later job.
    register_origin: Literal["stock_corner", "lower_left_pin"] = "stock_corner"
    stocks: dict[Name, FixtureStock] = Field(min_length=1)

    def origin_shift(self, stock: FixtureStock) -> tuple[float, float]:
        """Stock-frame coordinates of the g-code work origin (subtract to reach it)."""
        return stock.lower_left_pin() if self.work_origin == "lower_left_pin" else (0.0, 0.0)

    def register_shift(self, stock: FixtureStock) -> tuple[float, float]:
        """Stock-frame coordinates of the 00_register work origin (subtract to reach it)."""
        return stock.lower_left_pin() if self.register_origin == "lower_left_pin" else (0.0, 0.0)

    @model_validator(mode="after")
    def _pins(self) -> Fixture:
        r = self.pin_diameter_mm / 2
        same = lambda a, b: abs(a[0] - b[0]) < 1e-6 and abs(a[1] - b[1]) < 1e-6  # noqa: E731
        for key, s in self.stocks.items():
            for i, (x, y) in enumerate(s.pins_mm):
                if not (r <= x <= s.size_x_mm - r and r <= y <= s.size_y_mm - r):
                    raise ValueError(f"stocks.{key}: pin ({x}, {y}) is not inside the stock")
                if any(same((x, y), q) for q in s.pins_mm[:i]):
                    raise ValueError(f"stocks.{key}: two pins at ({x}, {y})")
                # After the flip every pin must find a hole.
                if not any(same(s.mirror(x, y), q) for q in s.pins_mm):
                    axis, c = s.flip_axis()
                    raise ValueError(f"stocks.{key}: pin ({x}, {y}) has no mirror pin about "
                                     f"{axis.upper()} = {c:g} ({s.flip} flip)")
        return self


# --- machine profile -------------------------------------------------------


class MachineProfile(_Strict):
    name: str
    verified: bool = False
    notes: str = ""
    dwell_unit: Literal["s", "ms"]
    file_extension: str = "nc"
    line_ending: Literal["lf", "crlf"] = "lf"
    # File header lines. "{program}" is the job name; a line "{info}" is
    # replaced by pcbcam's comments (job, warnings, tool list).
    header: str
    footer: str
    tool_change: str  # lines, fields: {number} {name} {diameter_mm}
    spindle_on: str  # lines, field: {rpm}
    probe_grid: str | None = None  # fields: {x} {y} {a} {b} {i} {j} {h}
    drop_words: list[str] = []  # whole lines starting with these words are removed
    allowed_words: list[str]  # anything else fails validation
    max_feed_mm_min: Positive
    max_rpm: Positive
    # Rapid traverse rate (G0) used only for the run-time estimate. The Makera
    # Z1's published max travel speed is 1200 mm/min (Z1 Pro 2000). It does not
    # gate anything; set it to your machine's rapid so the ETA is realistic.
    rapid_mm_min: Positive = 1000.0
    # Machine time the estimate adds per tool change (T..M6): the automatic
    # tool-length probe cycle. The manual bit swap is NOT included (it is
    # operator-dependent); the estimate reports the tool-change count separately.
    tool_change_s: NonNegative = 0.0


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


def load_fixture(path: Path) -> Fixture:
    return _load(path, Fixture)  # type: ignore[return-value]


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
