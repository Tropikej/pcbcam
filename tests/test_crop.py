from pcbcam.crop import crop

# Two contours like pcb2gcode writes them (after pcbcam's approach dip): one
# inside the box, one crossing its right edge. Note the zero-length move after
# each plunge, which must not be mistaken for a plunge.
SRC = """(job)
T1M6
S12000 M3
G00 Z2.00000
G00 X1 Y1
G01 F30.00000
G0 Z0.5
G01 Z-0.08000
G01 F120.00000
G01 X1 Y1
G01 X3 Y1
G01 X3 Y3
G00 Z2.00000
G00 X8 Y1
G01 F30.00000
G0 Z0.5
G01 Z-0.08000
G01 F120.00000
G01 X8 Y1
G01 X12 Y1
G00 Z2.00000
G00 X50 Y50
G01 F30.00000
G0 Z0.5
G01 Z-0.08000
G01 F120.00000
G01 X60 Y50
G00 Z2.00000
M5
M30
"""


def test_crop_keeps_inside_and_replunges_at_plunge_feed():
    res = crop(SRC, [(0, 0, 10, 5)], safe_z=2.0, eol="\n", approach_z=0.5)
    lines = res.text.splitlines()
    assert res.cut_mm == 6.0  # 2 + 2 inside the first contour, X8 -> X10 of the second
    assert "G1 X10 Y1" in lines and "G01 X12 Y1" not in lines  # clipped at the box edge
    assert "G01 X60 Y50" not in lines  # contour outside the box is not cut
    assert not any("F120" in line and "Z-" in line for line in lines)  # never plunge at cut feed
    assert lines[1].startswith("(CROPPED") and lines[-2:] == ["M5", "M30"]
