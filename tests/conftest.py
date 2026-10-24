from pathlib import Path

import pytest

from pcbcam.config import Recipe, ToolLibrary, load_machine

EXAMPLES = Path(__file__).parent.parent / "examples" / "manufacturing"


@pytest.fixture
def tools() -> ToolLibrary:
    return ToolLibrary.model_validate({
        "tools": {
            "vbit": {"type": "vbit", "number": 1, "angle_deg": 30, "tip_diameter_mm": 0.1,
                     "rpm": 12000, "feed_mm_min": 150, "plunge_mm_min": 60},
            "d08": {"type": "drill", "number": 2, "diameter_mm": 0.8, "rpm": 10000, "plunge_mm_min": 60},
            "d10": {"type": "drill", "number": 3, "diameter_mm": 1.0, "rpm": 11000, "plunge_mm_min": 50},
            "em10": {"type": "endmill", "number": 4, "diameter_mm": 1.0, "rpm": 12000,
                     "feed_mm_min": 200, "plunge_mm_min": 60},
        }
    })


@pytest.fixture
def recipe_data() -> dict:
    return {
        "stock": {"size_x_mm": 100, "size_y_mm": 70},
        "isolation": {"tools": ["vbit"], "depth_mm": 0.08},
        "drilling": {"tools": ["d08", "d10"], "depth_mm": 1.9, "milldrill_tool": "em10"},
        "outline": {"tool": "em10", "depth_mm": 1.7, "stepdown_mm": 0.4},
    }


@pytest.fixture
def recipe(recipe_data) -> Recipe:
    return Recipe.model_validate(recipe_data)


@pytest.fixture
def z1():
    return load_machine("makera_z1")
