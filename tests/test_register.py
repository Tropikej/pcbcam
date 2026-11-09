import math

from pcbcam import engrave, postprocess, register, validate
from pcbcam.config import FixtureStock, Recipe
from pcbcam.tooling import resolve


def test_back_transform_mirrors_then_shifts_to_the_work_origin():
    """The mirror axis lives in the stock-corner frame, so it must be applied
    before the pin shift -- front and back share the same work origin."""
    stock = FixtureStock(size_x_mm=100, size_y_mm=160, flip="left_right",
                         pins_mm=[(20, 8), (80, 8), (20, 152), (80, 152)])
    # Front: just shift to the lower-left pin (20, 8).
    front = engrave.Transform(0.0, 0.0, 20.0, 8.0)
    assert front((20.0, 8.0)) == (0.0, 0.0)
    # Back: mirror about X = 50 (corner frame), then subtract the same pin.
    back = engrave.Transform(0.0, 0.0, 20.0, 8.0, mirror=stock)
    assert back((20.0, 8.0)) == (80.0 - 20.0, 8.0 - 8.0) == (60.0, 0.0)


def _bore_tool(tools):
    recipe = Recipe.model_validate({
        "stock": {"size_x_mm": 100, "size_y_mm": 160},
        "isolation": {"tools": ["vbit"], "depth_mm": 0.08},
        "drilling": {"tools": ["d08"], "depth_mm": 1.9, "milldrill_tool": "em10"},
    })
    return resolve(recipe, tools).milldrill  # em10, a 1.0 mm end mill


def test_build_source_helically_bores_each_pin(tools):
    tool = _bore_tool(tools)
    pins = [(20.0, 8.0), (80.0, 152.0)]
    src = register.build_source(pins, 4.0, tool, depth_mm=1.8, zsafe_mm=2.0,
                                approach_mm=0.5, stepdown_mm=0.3)
    lines = src.splitlines()
    assert lines[0].startswith("(MSG, Change tool bit to 1 mm)")
    assert "M6" in lines and "M3" in lines
    # helix radius = (4 - 1) / 2 = 1.5; the start point is pin_x + r on X, pin_y on Y.
    assert "G00 X21.5 Y8" in lines and "G00 X81.5 Y152" in lines
    assert src.count("G00 Z2") == 1 + len(pins)        # initial safe + one retract per hole
    assert "Z-1.8" in src                              # reaches full depth
    # every cutting XY stays within radius of a pin centre
    for line in lines:
        ws = dict(postprocess.words(line))
        if "X" in ws and "Y" in ws and ("Z" in ws or line.startswith("G01")):
            near = min(math.hypot(ws["X"] - px, ws["Y"] - py) for px, py in pins)
            assert near <= 1.5 + 1e-3  # on the helix radius, bar 4-decimal rounding


def test_build_source_rejects_a_bore_narrower_than_the_cutter(tools):
    tool = _bore_tool(tools)  # 1.0 mm end mill
    try:
        register.build_source([(20.0, 8.0)], 0.8, tool, 1.8, 2.0, 0.5, 0.3)
        assert False, "expected a ValueError"
    except ValueError as exc:
        assert "not wider" in str(exc)


def test_register_gcode_passes_machine_validation(tools, z1):
    from pcbcam.config import Recipe

    recipe = Recipe.model_validate({
        "stock": {"size_x_mm": 100, "size_y_mm": 160, "thickness_mm": 1.5},
        "isolation": {"tools": ["vbit"], "depth_mm": 0.08},
        "drilling": {"tools": ["d08"], "depth_mm": 1.9, "milldrill_tool": "em10"},
    })
    tool = resolve(recipe, tools).milldrill
    src = register.build_source([(20.0, 8.0), (80.0, 152.0)], 4.0, tool,
                                depth_mm=recipe.stock.thickness_mm + 0.3, zsafe_mm=2.0,
                                approach_mm=0.5, stepdown_mm=0.3)
    job = postprocess.Job("registration bores", [tool], program="00_register", approach_mm=0.5)
    text = postprocess.process(src, job, z1, recipe.probing)
    slack = tool.width_mm / 2 + 0.01
    assert validate.check(text, z1, recipe, slack) == []
    assert "T4M6" in text  # em10 is tool number 4 in the test library
