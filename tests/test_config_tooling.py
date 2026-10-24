import pytest
from pydantic import ValidationError

from pcbcam.config import ConfigError, Recipe, ToolLibrary, load_recipe, load_tools
from pcbcam.tooling import assign_holes, resolve, vbit_width

from .conftest import EXAMPLES


def test_vbit_width_ideal_cone(tools):
    # 0.1 + 2 * 0.08 * tan(15 deg) = 0.1429
    assert vbit_width(tools.tools["vbit"], 0.08) == pytest.approx(0.14287, abs=1e-5)


def test_vbit_calibration_interpolates_and_refuses_extrapolation():
    vbit = ToolLibrary.model_validate({"tools": {"v": {
        "type": "vbit", "number": 1, "angle_deg": 30, "tip_diameter_mm": 0.1,
        "calibration": [{"depth_mm": 0.05, "width_mm": 0.15}, {"depth_mm": 0.10, "width_mm": 0.19}],
        "rpm": 1, "feed_mm_min": 1, "plunge_mm_min": 1}}}).tools["v"]
    assert vbit_width(vbit, 0.075) == pytest.approx(0.17)
    assert vbit_width(vbit, 0.10) == pytest.approx(0.19)
    with pytest.raises(ConfigError, match="outside the calibrated range"):
        vbit_width(vbit, 0.2)


def test_unknown_key_is_rejected(recipe_data):
    recipe_data["isolation"]["dept_mm"] = 0.1
    with pytest.raises(ValidationError, match="dept_mm"):
        Recipe.model_validate(recipe_data)


def test_isolation_must_cut_through_copper(recipe_data):
    recipe_data["isolation"]["depth_mm"] = 0.02
    with pytest.raises(ValidationError, match="copper"):
        Recipe.model_validate(recipe_data)


def test_outline_must_cut_through(recipe_data):
    recipe_data["outline"]["depth_mm"] = 1.0
    with pytest.raises(ValidationError, match="does not cut through"):
        Recipe.model_validate(recipe_data)


def test_duplicate_tool_numbers(tools):
    data = tools.model_dump()
    data["tools"]["d10"]["number"] = 2
    with pytest.raises(ValidationError, match="share tool number 2"):
        ToolLibrary.model_validate(data)


def test_wrong_tool_type_for_role(recipe_data, tools):
    recipe_data["outline"]["tool"] = "d10"
    with pytest.raises(ConfigError, match="is a drill, expected EndMill"):
        resolve(Recipe.model_validate(recipe_data), tools)


def test_isolation_tools_must_go_widest_first(recipe_data, tools):
    recipe_data["isolation"]["tools"] = ["vbit", "em10"]
    with pytest.raises(ConfigError, match="widest to narrowest"):
        resolve(Recipe.model_validate(recipe_data), tools)


def test_hole_assignment(recipe, tools):
    res = resolve(recipe, tools)
    got = {a.diameter_mm: (a.method, a.tool.name if a.tool else None)
           for a in assign_holes({0.8: 4, 1.02: 2, 0.6: 1, 3.2: 2}, res, 0.05)}
    assert got == {
        0.6: ("none", None),          # smaller than every drill
        0.8: ("drill", "d08"),
        1.02: ("drill", "d10"),       # within tolerance
        3.2: ("milldrill", "em10"),
    }


def test_examples_load():
    recipe = load_recipe(EXAMPLES / "recipe.yaml")
    resolve(recipe, load_tools(EXAMPLES / recipe.tools))
