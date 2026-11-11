import math

from pcbcam import eta, postprocess
from pcbcam.tooling import resolve

from .test_gcode import PCB2GCODE_DRILL, PCB2GCODE_FRONT


def test_feed_move_time(z1):
    # 60 mm of cutting at 150 mm/min = 0.4 min = 24 s.
    gc = "G21\nG90\nM3 S12000\nG1 F150\nG1 X60 Y0\nM5\n"
    est = eta.estimate(gc, z1)
    assert est.cutting_s == 24.0
    assert est.plunge_s == 0 and est.rapid_s == 0


def test_rapid_uses_profile_rate(z1):
    # 120 mm of rapid at the Z1's 1200 mm/min = 6 s; feed does not apply to G0.
    gc = "G21\nG90\nG1 F150\nG0 X120 Y0\n"
    est = eta.estimate(gc, z1)
    assert est.rapid_s == 6.0
    assert est.cutting_s == 0


def test_plunge_classified_separately(z1):
    # Z-only feed move is a plunge: 2 mm down at 60 mm/min = 2 s.
    gc = "G21\nG90\nM3 S12000\nG1 F60\nG1 Z-2\nM5\n"
    est = eta.estimate(gc, z1)
    assert est.plunge_s == 2.0 and est.cutting_s == 0


def test_dwell_and_tool_change(z1):
    # Two dwells (3 s + 2 s) and one tool change (profile tool_change_s = 15).
    gc = "G21\nG90\nT1M6\nG4 P3\nG4 P2\n"
    est = eta.estimate(gc, z1)
    assert est.dwell_s == 5.0
    assert est.tool_changes == 1
    assert est.tool_change_s == z1.tool_change_s == 15


def test_arc_length_quarter_circle(z1):
    # Quarter circle of radius 10 (centre at origin), CCW from (10,0) to (0,10).
    # Arc length = 2*pi*10/4 at 300 mm/min.
    gc = "G21\nG90\nM3 S12000\nG1 F300\nG0 X10 Y0\nG3 X0 Y10 I-10 J0\nM5\n"
    est = eta.estimate(gc, z1)
    expected = (2 * math.pi * 10 / 4) / 300 * 60
    assert math.isclose(est.cutting_s, expected)


def test_total_and_minutes_add_up(z1):
    gc = "G21\nG90\nM3 S12000\nT1M6\nG1 F150\nG1 X60 Y0\nG0 X120 Y0\nG4 P3\nM5\n"
    est = eta.estimate(gc, z1)
    assert math.isclose(est.total_s, est.cutting_s + est.rapid_s + est.dwell_s + est.tool_change_s)
    assert math.isclose(est.minutes, est.total_s / 60)


def test_estimate_on_real_postprocessed_jobs(recipe, tools, z1):
    res = resolve(recipe, tools)
    front = postprocess.process(PCB2GCODE_FRONT, postprocess.Job("iso", res.isolation, probe=True), z1)
    drill = postprocess.process(PCB2GCODE_DRILL, postprocess.Job("drill", res.drills, drilling=True), z1)
    for text in (front, drill):
        est = eta.estimate(text, z1)
        assert est.total_s > 0
        assert est.tool_changes >= 1  # each job changes to its tool
        assert est.dwell_s > 0  # spindle spin-up dwell is counted


def test_estimate_add_and_report(z1):
    a = eta.estimate("G21\nG90\nG1 F150\nG1 X150 Y0\n", z1)  # 60 s cutting
    b = eta.estimate("G21\nG90\nT1M6\n", z1)  # one tool change
    total = a + b
    assert total.cutting_s == 60.0 and total.tool_changes == 1
    report = eta.format_report([("01_iso.cnc", a), ("02_drill.cnc", b)])
    assert "01_iso.cnc" in report and "total" in report
    assert "1 tool change" in report


def test_human_time():
    assert eta.human_time(5) == "5s"
    assert eta.human_time(65) == "1m 05s"
    assert eta.human_time(3700) == "1h 01m"


def test_github_summary_is_markdown(z1):
    e = eta.estimate("G21\nG90\nM3\nG1 F150\nG1 X150 Y0\nT1M6\n", z1)
    md = eta.github_summary("Blight", [("01_iso.cnc", e)], z1)
    assert md.startswith("## pcbcam run-time estimate")
    assert "| `01_iso.cnc` |" in md and "**Total**" in md
    assert "1200 mm/min" in md
