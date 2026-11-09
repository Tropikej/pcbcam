import pytest
from pydantic import ValidationError

from pcbcam import build, fixture
from pcbcam.config import Fixture, Recipe
from pcbcam.tooling import Resolved


def _fixture(**over) -> Fixture:
    data = {
        "name": "test_pins",
        "pin_diameter_mm": 4,
        "stocks": {"100x160": {"size_x_mm": 100, "size_y_mm": 160, "pins_mm": [[50, 5], [50, 155]]}},
    }
    data.update(over)
    return Fixture.model_validate(data)


def _board_with(fix: Fixture, at: str = "100 200", extra: str = "") -> str:
    """A minimal .kicad_pcb holding the fixture footprint as KiCad would save it."""
    fp = fixture.render_footprint(fix, "100x160")
    lib_id = f'{fixture.LIBRARY}:{fixture.footprint_name(fix, "100x160")}'
    fp = fp.replace(f'(footprint "{fixture.footprint_name(fix, "100x160")}"',
                    f'(footprint "{lib_id}"\n\t(at {at})', 1)
    return f'(kicad_pcb\n\t(version 20260206)\n{fp}{extra})\n'


def _stock(pins, flip="left_right"):
    return {"s": {"size_x_mm": 100, "size_y_mm": 160, "flip": flip, "pins_mm": pins}}


def test_register_origin_defaults_to_the_stock_corner():
    four = [[20, 8], [80, 8], [20, 152], [80, 152]]
    fix = _fixture(stocks=_stock(four))
    stock = fix.stocks["s"]
    # Default: fresh blank, zero its lower-left corner (no shift).
    assert fix.register_origin == "stock_corner"
    assert fix.register_shift(stock) == (0.0, 0.0)
    # Opt in: re-bore, zero the probed lower-left hole.
    pin = _fixture(stocks=_stock(four), register_origin="lower_left_pin")
    assert pin.register_shift(stock) == stock.lower_left_pin() == (20, 8)


def test_pins_must_mirror_about_the_flip_axis():
    assert _fixture().stocks["100x160"].flip_axis() == ("x", 50)
    four = [[20, 5], [80, 5], [20, 155], [80, 155]]
    assert len(_fixture(stocks=_stock(four)).stocks["s"].pins_mm) == 4
    assert _fixture(stocks=_stock(four, "top_bottom")).stocks["s"].flip_axis() == ("y", 80)
    with pytest.raises(ValidationError, match="no mirror pin"):
        _fixture(stocks=_stock([[40, 5], [40, 155]]))  # off the X = 50 centre line
    with pytest.raises(ValidationError, match="no mirror pin"):
        _fixture(stocks=_stock([[20, 5], [80, 5], [20, 155], [70, 155]]))
    # A top/bottom flip pairs Y = 5 with Y = 155, not 150.
    assert _fixture(stocks=_stock([[50, 5], [50, 155]], "top_bottom"))
    with pytest.raises(ValidationError, match="no mirror pin"):
        _fixture(stocks=_stock([[50, 5], [50, 150]], "top_bottom"))
    with pytest.raises(ValidationError, match="not inside"):
        _fixture(stocks=_stock([[50, 1], [50, 155]]))
    with pytest.raises(ValidationError, match="two pins"):
        _fixture(stocks=_stock([[50, 5], [50, 5]]))


def test_footprint_round_trip(tmp_path):
    fix = _fixture()
    board = tmp_path / "b.kicad_pcb"
    board.write_text(_board_with(fix), encoding="utf-8")
    [placed] = fixture.find(board)
    assert (placed.x, placed.y, placed.rotation, placed.layer) == (100, 200, 0, "F.Cu")
    assert placed.properties[fixture.PROP_GEOMETRY] == fixture.geometry(fix, "100x160")
    # Stock corner at KiCad (100, 200) = Gerber (100, -200): offsets bring it to (0, 0).
    assert fixture.work_offset(placed) == (-100, 200)
    assert fixture.check(fix, placed, (100, 160), (10, 20, 90, 140), 2.0) == []
    # Every drawing is on the fixture layer: nothing reaches copper or Edge.Cuts.
    text = fixture.render_footprint(fix, "100x160")
    assert "(pad " not in text and "Edge.Cuts" not in text
    assert "board_only" in text and "allow_missing_courtyard" in text


def test_check_catches_misplaced_boards(tmp_path):
    fix = _fixture()
    board = tmp_path / "b.kicad_pcb"
    board.write_text(_board_with(fix), encoding="utf-8")
    [placed] = fixture.find(board)
    # Outline 2 mm from the stock edge (margin is 3).
    assert "edge margin" in fixture.check(fix, placed, (100, 160), (2, 20, 90, 140), 2.0)[0]
    # Outline up to y = 151: 4 mm from the pin centre at (50, 155), needs 2 + 3.
    [msg] = fixture.check(fix, placed, (100, 160), (10, 20, 90, 151), 2.0)
    assert "pin at (50, 155)" in msg
    # Recipe stock differs from the footprint's.
    assert "recipe says" in fixture.check(fix, placed, (100, 70), (10, 20, 90, 60), 2.0)[0]
    # Outline cutter wider than the pin clearance.
    assert "outline cutter" in fixture.check(fix, placed, (100, 160), (10, 20, 90, 140), 3.175)[0]


def test_work_origin_is_the_lower_left_pin_by_default():
    four = [[20, 8], [80, 8], [20, 152], [80, 152]]
    fix = _fixture(stocks=_stock(four))
    s = fix.stocks["s"]
    assert s.lower_left_pin() == (20, 8)
    assert fix.work_origin == "lower_left_pin"
    assert fix.origin_shift(s) == (20, 8)
    corner = _fixture(stocks=_stock(four), work_origin="stock_corner")
    assert corner.origin_shift(corner.stocks["s"]) == (0.0, 0.0)
    # The work-zero marker and the geometry hash both move with the origin.
    assert "origin=lower_left_pin" in fixture.geometry(fix, "s")
    assert fixture.geometry(fix, "s") != fixture.geometry(corner, "s")


def _plan(board, fix, recipe):
    return build.Plan(board=board, recipe_path=board, recipe=recipe, machine=None,
                      resolved=Resolved([], [], None, None), fixture=fix)


def _recipe_100x160():
    return Recipe.model_validate(
        {"stock": {"size_x_mm": 100, "size_y_mm": 160}, "isolation": {"tools": ["v"], "depth_mm": 0.08}})


def test_place_shifts_the_work_origin_to_the_lower_left_pin(tmp_path):
    fix = _fixture()  # pins (50, 5) and (50, 155); lower-left is (50, 5)
    board = tmp_path / "b.kicad_pcb"
    board.write_text(_board_with(fix, at="100 200"), encoding="utf-8")
    # Gerber bbox chosen so the outline lands at stock (10, 10)-(90, 80).
    bbox = (110.0, -190.0, 190.0, -120.0)
    pl = build._place(_plan(board, fix, _recipe_100x160()), bbox)
    assert pl.corner == (-100.0, 200.0)       # stock corner -> work zero
    assert pl.shift == (50.0, 5.0)            # the lower-left pin
    assert pl.work == (-150.0, 195.0)         # pcb2gcode/engrave offset
    assert "lower-left pin at stock (50, 5)" in pl.origin


def test_place_keeps_the_corner_origin_when_asked(tmp_path):
    fix = _fixture(work_origin="stock_corner")
    board = tmp_path / "b.kicad_pcb"
    board.write_text(_board_with(fix, at="100 200"), encoding="utf-8")
    pl = build._place(_plan(board, fix, _recipe_100x160()), (110.0, -190.0, 190.0, -120.0))
    assert pl.shift == (0.0, 0.0)
    assert pl.work == pl.corner == (-100.0, 200.0)
    assert "stock lower-left corner" in pl.origin


def test_check_catches_stale_or_rotated_footprints(tmp_path):
    fix = _fixture()
    board = tmp_path / "b.kicad_pcb"
    board.write_text(_board_with(fix, at="100 200 90"), encoding="utf-8")
    [placed] = fixture.find(board)
    assert "rotated" in fixture.check(fix, placed, (100, 160), (10, 20, 90, 140), 2.0)[0]
    # The fixture file changed after the footprint was placed.
    moved = _fixture(stocks={"100x160": {"size_x_mm": 100, "size_y_mm": 160, "pins_mm": [[50, 6], [50, 154]]}})
    board.write_text(_board_with(fix), encoding="utf-8")
    [placed] = fixture.find(board)
    assert "out of date" in fixture.check(moved, placed, (100, 160), (10, 20, 90, 140), 2.0)[0]
