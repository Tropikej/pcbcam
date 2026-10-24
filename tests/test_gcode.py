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
    assert "M6 T1" in lines
    assert "M3 S12000" in lines
    assert "G4 P3" in lines and "G4 P2" in lines  # ms -> s
    for gone in ("M0", "M9", "T1", "G00 S12000", "G04 P0"):
        assert gone not in lines
    assert lines.index("M6 T1") < lines.index("M3 S12000") < lines.index("G01 Z-0.08000")
    assert lines[-2:] == ["M5", "M2"]
    assert validate.check(out, z1, recipe, xy_slack_mm=0.5) == []


def test_postprocess_probe_grid(recipe_data, tools, z1):
    from pcbcam.config import Recipe

    recipe_data["probing"] = {"enabled": True, "points_x": 4, "points_y": 3, "inset_mm": 1}
    recipe = Recipe.model_validate(recipe_data)
    out = postprocess.process(PCB2GCODE_FRONT, _front_job(recipe, tools), z1, recipe.probing)
    lines = out.splitlines()
    # toolpath spans X10..30, Y10..20
    assert "G32 X11 Y11 A18 B8 I4 J3 H2" in lines
    assert lines.index("M6 T0") < lines.index("G32 X11 Y11 A18 B8 I4 J3 H2") < lines.index("M6 T1")


def test_validate_catches_problems(recipe, z1):
    bad = "G21\nG90\nG20\nM3 S12000\nG1 X200 Y10 F99999\nM5\nG1 Z-0.1\nG1 Z-5\nG64\n"
    msgs = [i.message for i in validate.check(bad, z1, recipe, xy_slack_mm=0.5)]
    assert any("G20" in m for m in msgs)
    assert any("X200" in m for m in msgs)
    assert any("F99999" in m or "99999" in m for m in msgs)
    assert any("spindle off" in m for m in msgs)
    assert any("deeper than the stock" in m for m in msgs)
    assert any("G64 is not in" in m for m in msgs)


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
    text = millproject.render(recipe, res, holes, {"front": "f.gbr", "outline": "o.gbr", "drill": "d.drl"})
    opts = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
    assert opts["mill-diameters"] == "0.1429mm"
    assert opts["zwork"] == "-0.08"
    assert opts["drills-available"] == "0.8mm:0.05mm,1mm:0.05mm"
    assert opts["milldrill-diameter"] == "1mm"
    assert opts["zbridges"] == "-1.1"  # 1.6 stock - 0.5 tab
    assert opts["x-offset"] == "-5"
