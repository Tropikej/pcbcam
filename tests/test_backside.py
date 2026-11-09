import pytest

from pcbcam import backside, build, engrave, millproject
from pcbcam.config import FixtureStock, Recipe
from pcbcam.tooling import resolve

RAW = """\
(MSG, Change tool bit to mill diameter 0.2mm)
M6
M3
G01 F60.00000
G00 X10 Y20 ( rapid )
G01 Z-0.08000
G01 X30 Y20
G01 X30.5 Y25.25
G00 Z2
"""


def test_mirror_isolation_reflects_xy_leaves_the_rest():
    # left/right flip about X = 50 with no offsets: X -> 100 - X, Y unchanged.
    out = backside.mirror_isolation(RAW, -1.0, 100.0, 1.0, 0.0)
    lines = out.splitlines()
    assert "G00 X90 Y20 ( rapid )" in lines   # 10 -> 90, comment kept
    assert "G01 X70 Y20" in lines             # 30 -> 70
    assert "G01 X69.5 Y25.25" in lines        # 30.5 -> 69.5, Y untouched
    assert "G01 Z-0.08000" in lines           # Z/F/comments pass through verbatim
    assert "G01 F60.00000" in lines
    assert lines[0] == "(MSG, Change tool bit to mill diameter 0.2mm)"


def test_mirror_isolation_refuses_arcs():
    with pytest.raises(ValueError, match="arcs"):
        backside.mirror_isolation("G02 X1 Y1 I0 J1\n", -1.0, 100.0, 1.0, 0.0)
    # G21 / G28 must NOT be mistaken for an arc.
    assert backside.mirror_isolation("G21\nG28\nG01 X5 Y5\n", -1.0, 100.0, 1.0, 0.0)


@pytest.mark.parametrize("flip", ["left_right", "top_bottom"])
def test_mirror_axes_match_the_engraver(flip):
    stock = FixtureStock(size_x_mm=100, size_y_mm=160, flip=flip,
                         pins_mm=[(20, 8), (80, 8), (20, 152), (80, 152)])
    placement = build.Placement(corner=(-100.0, 200.0), shift=(20.0, 8.0), origin="x")
    sx, bx, sy, by = build._mirror_axes(stock, placement)
    xf = engrave.Transform(-100.0, 200.0, 20.0, 8.0, mirror=stock)  # the proven back transform
    for gx, gy in [(30.0, 40.0), (0.0, 0.0), (79.5, 133.25)]:
        assert (sx * gx + bx, sy * gy + by) == pytest.approx(xf((gx, gy)))


def _recipe(side):
    return Recipe.model_validate({
        "stock": {"size_x_mm": 100, "size_y_mm": 70},
        "isolation": {"tools": ["vbit"], "depth_mm": 0.08, "side": side},
        "drilling": {"tools": ["d08"], "depth_mm": 1.9},
        "outline": {"tool": "em10", "depth_mm": 1.7, "stepdown_mm": 0.4},
    })


def test_side_accepts_back_and_both(tools):
    for side in ("front", "back", "both"):
        assert _recipe(side).isolation.side == side


def test_render_gates_the_front_mill_on_side(tools):
    files = {"front": "f.gbr", "outline": "o.gbr", "drill": "d.drl"}
    from pcbcam.tooling import assign_holes

    def opts(side):
        res = resolve(_recipe(side), tools)
        holes = assign_holes({0.8: 2}, res, 0.05)
        text = millproject.render(_recipe(side), res, holes, files, (-1.0, 2.0))
        return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)

    assert opts("both")["front"] == "f.gbr" and "mill-diameters" in opts("both")
    assert "front" not in opts("back") and "mill-diameters" not in opts("back")  # back is a separate run
    assert opts("back")["outline"] == "o.gbr" and "drill" in opts("back")        # drill/outline still run


def test_render_isolation_only_is_offsetless_and_mill_only(tools):
    res = resolve(_recipe("both"), tools)
    text = millproject.render_isolation_only(_recipe("both"), res, "copper.gbr")
    opts = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
    assert opts["front"] == "copper.gbr"
    assert opts["x-offset"] == "0" and opts["y-offset"] == "0"
    assert opts["mill-diameters"] == "0.1429mm"
    for off in ("drill", "outline", "cutter-diameter", "milldrill-diameter"):
        assert off not in opts
