import pytest

from pcbcam import excellon, millproject, postprocess, validate
from pcbcam.tooling import assign_holes, resolve

# Shaped exactly like pcb2gcode 3.0.4's ngc_exporter.cpp output.
PCB2GCODE_FRONT = """\
( pcb2gcode 3.0.4 )
( Software-independent Gcode )

G94 ( Millimeters per minute feed rate. )
G21 ( Units == Millimeters. )

G90 ( Absolute coordinates. )
G00 S12000 ( RPM spindle speed. )
G01 F150.00000 ( Feedrate. )


G00 Z15.00000 (Retract to tool change height)
T1
M5      (Spindle stop.)
G04 P2000 (Wait for spindle to stop)
(MSG, Change tool bit to mill diameter 0.14287mm)
M6      (Tool change.)
M0      (Temporary machine stop.)
M3 ( Spindle on clockwise. )
G04 P3000 (Wait for spindle to get up to speed)
G04 P0 ( dwell for no time -- G64 should not smooth over this point )
G00 Z2.00000 ( retract )

G00 X10.00000 Y10.00000 ( rapid move to begin. )
G01 Z-0.08000
G01 X30.00000 Y10.00000
G01 X30.00000 Y20.00000

G04 P0 ( dwell for no time -- G64 should not smooth over this point )
G00 Z15.000000 ( retract )

M5 ( Spindle off. )
G04 P2000
M9 ( Coolant off. )
M2 ( Program end. )
"""


def _front_job(recipe, tools):
    res = resolve(recipe, tools)
    return postprocess.Job("isolation", res.isolation, probe=True)


def test_postprocess_z1(recipe, tools, z1):
    out = postprocess.process(PCB2GCODE_FRONT, _front_job(recipe, tools), z1)
    lines = out.splitlines()
    assert "T1M6" in lines
    assert "S12000 M3" in lines and "M851 S100" in lines and "G54" in lines
    assert "G4 P3" in lines and "G4 P2" in lines  # ms -> s
    for gone in ("M0", "M9", "M2", "T1", "G00 S12000", "G04 P0"):
        assert gone not in lines
    assert lines.index("T1M6") < lines.index("S12000 M3") < lines.index("G01 Z-0.08000")
    assert lines[-5:] == ["M5", "M400", "M852", "G28", "M30"]
    assert out.endswith("M30\r\n") and "\n" not in out.replace("\r\n", "")  # CRLF only
    assert lines[:4] == ["(pcbcam)", "(Machine)", "(  vendor: Makera)", "(  model: Z1 3 axis)"]
    assert validate.check(out, z1, recipe, xy_slack_mm=0.5) == []


def test_postprocess_probe_grid(recipe_data, tools, z1):
    from pcbcam.config import Recipe

    recipe_data["probing"] = {"enabled": True, "points_x": 4, "points_y": 3, "inset_mm": 1}
    recipe = Recipe.model_validate(recipe_data)
    out = postprocess.process(PCB2GCODE_FRONT, _front_job(recipe, tools), z1, recipe.probing)
    lines = out.splitlines()
    # toolpath spans X10..30, Y10..20
    assert "G32 X11 Y11 A18 B8 I4 J3 H2" in lines
    assert lines.index("T0M6") < lines.index("G32 X11 Y11 A18 B8 I4 J3 H2") < lines.index("T1M6")


def test_validate_catches_problems(recipe, z1):
    bad = "G21\nG90\nG20\nM3 S12000\nG1 X200 Y10 F99999\nM5\nG1 Z-0.1\nG1 Z-5\nG64\n"
    msgs = [i.message for i in validate.check(bad, z1, recipe, xy_slack_mm=0.5)]
    assert any("G20" in m for m in msgs)
    assert any("X200" in m for m in msgs)
    assert any("F99999" in m or "99999" in m for m in msgs)
    assert any("spindle off" in m for m in msgs)
    assert any("deeper than the stock" in m for m in msgs)
    assert any("G64 is not in" in m for m in msgs)


def test_validate_bounds_follow_the_work_origin(recipe, z1):
    # Board feature 10 mm left of the pin: X-10 is off the stock from the corner,
    # but on the stock when work zero is the lower-left pin at (20, 8).
    gc = "G21\nG90\nM3 S12000\nG1 X-10 Y-3 F100\nM5\n"
    corner = [i.message for i in validate.check(gc, z1, recipe, 0.5)]
    assert any("X-10" in m for m in corner) and any("Y-3" in m for m in corner)
    assert validate.check(gc, z1, recipe, 0.5, origin_mm=(20.0, 8.0)) == []


def test_excellon_parse(tmp_path):
    drl = tmp_path / "b.drl"
    drl.write_text(
        "M48\n; DRILL file {KiCad 10.0.6}\nFMAT,2\nMETRIC\nT1C0.800\nT2C1.020\nT3C1.200\n%\n"
        "G90\nG05\nT1\nX1.0Y2.0\nX3.0Y2.0\nT2\nX5.0Y5.0\nT3\nX1.0Y1.0G85X3.0Y1.0\nT0\nM30\n"
    )
    inv = excellon.parse(drl)
    assert dict(inv.holes) == {0.8: 2, 1.02: 1}
    assert dict(inv.slots) == {1.2: 1}


def test_millproject(recipe, tools):
    res = resolve(recipe, tools)
    holes = assign_holes({0.8: 3, 3.2: 1}, res, 0.05)
    text = millproject.render(recipe, res, holes, {"front": "f.gbr", "outline": "o.gbr", "drill": "d.drl"},
                              (-125.0, 133.0))
    opts = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
    assert opts["mill-diameters"] == "0.1429mm"
    assert opts["zwork"] == "-0.08"
    assert opts["drills-available"] == "0.8mm:0.05mm,1mm:0.05mm"
    assert opts["milldrill-diameter"] == "1mm"
    assert opts["zbridges"] == "-1.1"  # 1.6 stock - 0.5 tab
    assert opts["zero-start"] == "false"
    assert opts["x-offset"] == "-125"
    assert opts["y-offset"] == "133"


def test_outline_bbox_with_rounded_corners(tmp_path):
    from pcbcam.gerber import outline_bbox

    # KiCad-style 10 x 5 mm outline with 1 mm corner arcs, X 130..140, Y -75..-70
    g = tmp_path / "edge.gbr"
    g.write_text(
        "%FSLAX46Y46*%\n%MOMM*%\nG75*\nG01*\n"
        "X130000000Y-71000000D02*\nG03*\nX131000000Y-70000000I1000000J0D01*\nG01*\n"
        "X139000000Y-70000000D01*\nG02*\nX140000000Y-71000000I0J-1000000D01*\nG01*\n"
        "X140000000Y-74000000D01*\nG02*\nX139000000Y-75000000I-1000000J0D01*\nG01*\n"
        "X131000000Y-75000000D01*\nG02*\nX130000000Y-74000000I0J1000000D01*\nG01*\n"
        "X130000000Y-71000000D01*\nM02*\n"
    )
    assert outline_bbox(g) == pytest.approx((130.0, -75.0, 140.0, -70.0))


def test_outline_bbox_circle(tmp_path):
    from pcbcam.gerber import outline_bbox

    g = tmp_path / "round.gbr"  # full circle r=5 around (10, 10), drawn as one arc
    g.write_text("%FSLAX46Y46*%\n%MOMM*%\nG75*\nX15000000Y10000000D02*\n"
                 "G02*\nX15000000Y10000000I-5000000J0D01*\nM02*\n")
    assert outline_bbox(g) == pytest.approx((5.0, 5.0, 15.0, 15.0))


PCB2GCODE_DRILL = """\
G94       (Millimeters per minute feed rate.)
G21       (Units == Millimeters.)
G90       (Absolute coordinates.)
G00 S12000     (RPM spindle speed.)

G00 Z15.00000 (Retract)
T1
M5      (Spindle stop.)
G04 P2000
(MSG, Change tool bit to drill size 1mm)
M6      (Tool change.)
M0      (Temporary machine stop.)
M3      (Spindle on clockwise.)
G0 Z2.00000
G04 P3000

G1 F20.00000
G0 X10.00000 Y10.00000
G1 Z-1.80000
G1 Z2.00000
G00 Z15.000 ( All done -- retract )

M5      (Spindle off.)
G04 P2000
M9      (Coolant off.)
M2      (Program end.)
"""


def test_drill_feeds_per_tool(recipe, tools, z1):
    res = resolve(recipe, tools)
    out = postprocess.process(PCB2GCODE_DRILL, postprocess.Job("drill", res.drills, drilling=True), z1)
    lines = out.splitlines()
    assert "T3M6" in lines and "S11000 M3" in lines  # d10: number 3, 11000 rpm
    assert "G1 F50" in lines                         # d10 plunge, not the global F20
    assert "G0 Z2" in lines and "G1 Z2.00000" not in lines
    assert "(T3 d10: drill 1 mm)" in lines and not any("d08" in line for line in lines)
    assert validate.check(out, z1, recipe, xy_slack_mm=0.5) == []


def test_comments_are_parser_safe(recipe, tools, z1):
    res = resolve(recipe, tools)
    out = postprocess.process(PCB2GCODE_FRONT, postprocess.Job("isolation (front)", res.isolation), z1)
    assert "(isolation [front] -- generated by pcbcam for Makera Z1)" in out.splitlines()
    assert validate.check(out, z1, recipe, xy_slack_mm=0.5) == []


def test_validate_rejects_bad_comments(recipe, z1):
    for bad in ["(a (b) c)", "(open", "close)", "(x; y)", "(café)"]:
        issues = validate.check(f"G21\nG90\n{bad}\n", z1, recipe, xy_slack_mm=0.5)
        assert [i.line for i in issues] == [3], bad
