import math

import pytest

from pcbcam import engrave, postprocess, validate
from pcbcam.config import FixtureStock, Recipe
from pcbcam.tooling import resolve


def dxf(*entities: str) -> str:
    """Wrap raw entity blocks in just enough DXF for read_segments to scan."""
    return "0\nSECTION\n2\nENTITIES\n" + "".join(entities) + "0\nENDSEC\n0\nEOF\n"


def line(x1, y1, x2, y2):
    return f"0\nLINE\n10\n{x1}\n20\n{y1}\n11\n{x2}\n21\n{y2}\n"


# --- DXF reading -----------------------------------------------------------


def test_reads_line_segments():
    segs = engrave.read_segments(dxf(line(1, 2, 3, 4), line(3, 4, 5, 6)))
    assert segs == [((1, 2), (3, 4)), ((3, 4), (5, 6))]


def test_circle_flattens_to_a_closed_loop():
    segs = engrave.read_segments(dxf("0\nCIRCLE\n10\n0\n20\n0\n40\n1\n"))
    pts = [segs[0][0]] + [s[1] for s in segs]
    assert len(segs) >= 8  # fine enough for a 1 mm circle at 0.01 mm tolerance
    assert all(abs(math.hypot(x, y) - 1.0) < 1e-6 for x, y in pts)
    assert pts[0] == pytest.approx(pts[-1])  # closed


def test_arc_sweeps_ccw_between_its_angles():
    segs = engrave.read_segments(dxf("0\nARC\n10\n0\n20\n0\n40\n2\n50\n0\n51\n90\n"))
    start, end = segs[0][0], segs[-1][1]
    assert start == pytest.approx((2.0, 0.0), abs=1e-6)
    assert end == pytest.approx((0.0, 2.0), abs=1e-6)


def test_lwpolyline_vertices_become_segments():
    body = "0\nLWPOLYLINE\n90\n3\n70\n0\n10\n0\n20\n0\n10\n1\n20\n0\n10\n1\n20\n1\n"
    segs = engrave.read_segments(dxf(body))
    assert segs == [((0, 0), (1, 0)), ((1, 0), (1, 1))]


# --- chaining --------------------------------------------------------------


def test_chain_joins_touching_segments_into_one_path():
    segs = [((0, 0), (1, 0)), ((1, 0), (1, 1)), ((1, 1), (0, 1))]
    paths = engrave.chain(segs)
    assert len(paths) == 1
    assert paths[0] == [(0, 0), (1, 0), (1, 1), (0, 1)]


def test_chain_keeps_disjoint_strokes_separate():
    segs = [((0, 0), (1, 0)), ((5, 5), (6, 5))]
    paths = engrave.chain(segs)
    assert len(paths) == 2


# --- transform -------------------------------------------------------------


def test_front_transform_is_the_work_offset():
    xf = engrave.Transform(-10.0, 20.0)
    assert xf((15.0, -5.0)) == (5.0, 15.0)


def test_back_transform_mirrors_about_the_flip_axis():
    stock = FixtureStock(size_x_mm=100, size_y_mm=160, flip="left_right",
                         pins_mm=[(20, 5), (80, 5)])
    xf = engrave.Transform(0.0, 0.0, mirror=stock)
    assert xf((10.0, 20.0)) == (90.0, 20.0)  # mirror about X = 50


# --- G-code emission -------------------------------------------------------


def _engrave_recipe(tools, **engrave_kw):
    data = {
        "stock": {"size_x_mm": 100, "size_y_mm": 160, "thickness_mm": 1.5},
        "isolation": {"tools": ["vbit"], "depth_mm": 0.08},
        "engrave": {"tool": "vbit", "depth_mm": 0.1, "front_layer": "User.7", **engrave_kw},
    }
    return Recipe.model_validate(data)


def test_resolve_picks_up_the_engrave_tool(tools):
    res = resolve(_engrave_recipe(tools), tools)
    assert res.engrave is not None
    assert res.engrave.name == "vbit"
    assert res.engrave in res.all()


def test_build_source_has_plunge_trace_and_retract(tools):
    res = resolve(_engrave_recipe(tools), tools)
    paths = [[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]]
    src = engrave.build_source(paths, res.engrave, 0.1, 2.0, engrave.Transform(0.0, 0.0))
    lines = src.splitlines()
    assert lines[0].startswith("(MSG, Change tool bit to")
    assert "M6" in lines and "M3" in lines
    assert "G01 Z-0.1 F60" in src           # plunge at the tool's plunge feed
    assert "G01 X1 Y0 F150" in src          # first trace move carries the cut feed
    assert src.count("G00 Z2") == 2         # initial safe + one retract after the path


def test_engrave_gcode_passes_machine_validation(tools, z1):
    res = resolve(_engrave_recipe(tools), tools)
    recipe = _engrave_recipe(tools)
    paths = [[(0.0, 0.0), (10.0, 0.0), (10.0, 5.0)]]
    src = engrave.build_source(paths, res.engrave, 0.1, 2.0, engrave.Transform(5.0, 5.0))
    job = postprocess.Job("engrave (front)", [res.engrave], program="05_engrave_front",
                          approach_mm=recipe.heights.approach_mm)
    text = postprocess.process(src, job, z1, recipe.probing)
    slack = res.engrave.width_mm / 2 + 0.01
    assert validate.check(text, z1, recipe, slack) == []
    assert "T1M6" in text  # the Z1 profile's one-line tool change for the V-bit
