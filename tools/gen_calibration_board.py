"""Generate the CNC calibration patterns into a KiCad 10 board (F.Cu only).

Run with KiCad's own Python, with the board closed in KiCad:

    "C:/Program Files/KiCad/10.0/bin/python.exe" tools/gen_calibration_board.py \
        examples/cnc-pcb-calibration-project/cnc-pcb-calibration.kicad_pcb

Idempotent: everything this script owns (copper tracks and texts, CAL*
footprints, CAL_* nets, the pcbcam_calibration rule area, the outline) is
removed and redrawn. Template documentation layers are left alone; the
mounting holes and the test point are moved to their new places.

Patterns narrower than the process limits sit inside the rule area named
``pcbcam_calibration``: the project rules and pcbcam's machine DRC both exempt
it, so a normal board keeps the strict rules.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pcbnew as p

FP_LIBS = Path("C:/Program Files/KiCad/10.0/share/kicad/footprints")

# Board: top-left corner, size (mm). 85 x 58 fits a 100 x 70 blank with 4 mm margins.
X0, Y0, W, H, R = 130.0, 70.0, 85.0, 58.0, 1.5

TEXT_H, TEXT_T = 1.5, 0.25  # copper labels: readable after a 0.15 mm V-groove
CAL_AREA = "pcbcam_calibration"


def mm(v: float) -> int:
    return p.FromMM(v)


def pt(x: float, y: float) -> p.VECTOR2I:
    return p.VECTOR2I(mm(x), mm(y))


def layers(*ids: int) -> p.LSET:
    s = p.LSET()
    for i in ids:
        s.AddLayer(i)
    return s


class Builder:
    def __init__(self, board: p.BOARD):
        self.b = board
        self.nets: dict[str, p.NETINFO_ITEM] = {}
        self.n_fp = 0

    # --- primitives ------------------------------------------------------

    def net(self, name: str) -> p.NETINFO_ITEM:
        if name not in self.nets:
            n = p.NETINFO_ITEM(self.b, name)
            self.b.Add(n)
            self.nets[name] = n
        return self.nets[name]

    def track(self, pts: list[tuple[float, float]], width: float, net: str) -> None:
        for a, c in zip(pts, pts[1:]):
            t = p.PCB_TRACK(self.b)
            t.SetStart(pt(*a))
            t.SetEnd(pt(*c))
            t.SetWidth(mm(width))
            t.SetLayer(p.F_Cu)
            t.SetNet(self.net(net))
            self.b.Add(t)

    def text(self, s: str, x: float, y: float, size: float = TEXT_H, align: str = "left") -> None:
        t = p.PCB_TEXT(self.b)
        t.SetText(s)
        t.SetLayer(p.F_Cu)
        t.SetTextSize(p.VECTOR2I(mm(size), mm(size)))
        t.SetTextThickness(mm(TEXT_T if size <= 2 else 0.4))
        t.SetHorizJustify({"left": p.GR_TEXT_H_ALIGN_LEFT, "center": p.GR_TEXT_H_ALIGN_CENTER,
                           "right": p.GR_TEXT_H_ALIGN_RIGHT}[align])
        t.SetVertJustify(p.GR_TEXT_V_ALIGN_CENTER)
        t.SetPosition(pt(x, y))
        self.b.Add(t)

    def footprint(self, value: str) -> p.FOOTPRINT:
        """An empty board-only footprint to hold test pads."""
        self.n_fp += 1
        fp = p.FOOTPRINT(self.b)
        fp.SetReference(f"CAL{self.n_fp}")
        fp.SetValue(value)
        fp.Reference().SetVisible(False)
        fp.Value().SetVisible(False)
        fp.SetBoardOnly(True)
        fp.SetExcludedFromBOM(True)
        fp.SetExcludedFromPosFiles(True)
        self.b.Add(fp)
        return fp

    def smd(self, fp: p.FOOTPRINT, num: str, x: float, y: float, w: float, h: float, net: str) -> None:
        pad = p.PAD(fp)
        pad.SetAttribute(p.PAD_ATTRIB_SMD)
        pad.SetShape(p.PAD_SHAPE_RECT)
        pad.SetSize(p.VECTOR2I(mm(w), mm(h)))
        pad.SetLayerSet(layers(p.F_Cu))
        pad.SetNumber(num)
        fp.Add(pad)
        pad.SetPosition(pt(x, y))
        pad.SetNet(self.net(net))

    def tht(self, fp: p.FOOTPRINT, num: str, x: float, y: float, drill: float, dia: float, net: str) -> None:
        pad = p.PAD(fp)
        pad.SetAttribute(p.PAD_ATTRIB_PTH)
        pad.SetShape(p.PAD_SHAPE_CIRCLE)
        pad.SetSize(p.VECTOR2I(mm(dia), mm(dia)))
        pad.SetDrillSize(p.VECTOR2I(mm(drill), mm(drill)))
        pad.SetLayerSet(layers(p.F_Cu))  # single-sided: no copper on B.Cu
        pad.SetNumber(num)
        fp.Add(pad)
        pad.SetPosition(pt(x, y))
        pad.SetNet(self.net(net))

    def library(self, lib: str, name: str, ref: str, x: float, y: float, rot: float = 0) -> p.FOOTPRINT:
        fp = p.FootprintLoad(str(FP_LIBS / f"{lib}.pretty"), name)
        if fp is None:
            raise SystemExit(f"footprint {lib}:{name} not found")
        fp.SetReference(ref)
        fp.Reference().SetVisible(False)
        fp.SetBoardOnly(True)
        fp.SetExcludedFromBOM(True)
        fp.SetExcludedFromPosFiles(True)
        self.b.Add(fp)
        fp.SetPosition(pt(x, y))
        fp.SetOrientationDegrees(rot)
        for pad in fp.Pads():
            pad.SetNet(self.net(f"CAL_{ref}_{pad.GetNumber() or 'EP'}"))
        return fp

    # --- patterns --------------------------------------------------------

    def pad_row(self, x: float, y: float, pitch: float, pad_w: float, n: int, pad_h: float,
                label: str, tag: str) -> float:
        """n pads at ``pitch``, one net each so every gap is a real clearance."""
        self.text(label, x, y)
        fp = self.footprint(f"pitch {pitch} pad {pad_w}")
        x0 = x + 11.5
        for i in range(n):
            self.smd(fp, str(i + 1), x0 + i * pitch, y, pad_w, pad_h, f"CAL_{tag}_{i + 1}")
        return x0 + (n - 1) * pitch

    def clearance_ladder(self, x: float, y: float, gaps: list[float], pitch: float) -> None:
        w, length, tp = 0.5, 14.0, 1.4
        for k, gap in enumerate(gaps):
            yy = y + k * pitch
            self.text(f"{gap:.3f}".rstrip("0").rstrip(".").ljust(4, "0"), x, yy + (w + gap) / 2)
            tx = x + 8.5
            fp = self.footprint(f"gap {gap}")
            a, b = f"CAL_GAP{k}_A", f"CAL_GAP{k}_B"
            # A: pad on the left, B: pad on the right; the gap runs between them.
            self.smd(fp, "1", tx, yy, tp, tp, a)
            self.track([(tx, yy), (tx + length, yy)], w, a)
            yb = yy + w + gap
            self.smd(fp, "2", tx + length + 2.2, yb, tp, tp, b)
            self.track([(tx + 1.5, yb), (tx + length + 2.2, yb)], w, b)
            # Free ends land on track-sized pads: same copper, no dangling track.
            self.smd(fp, "3", tx + length, yy, w, w, a)
            self.smd(fp, "4", tx + 1.5, yb, w, w, b)

    def width_comb(self, x: float, y: float, widths: list[float], pitch: float) -> None:
        length, tp = 16.0, 1.4
        for k, w in enumerate(widths):
            yy = y + k * pitch
            self.text(f"{w:.2f}", x, yy)
            fp = self.footprint(f"track {w}")
            net = f"CAL_W{k}"
            tx = x + 7.5
            self.smd(fp, "1", tx, yy, tp, tp, net)
            self.smd(fp, "2", tx + length, yy, tp, tp, net)
            self.track([(tx, yy), (tx + length, yy)], w, net)

    def qfn_fanout(self, x: float, y: float) -> None:
        """QFN-32 0.5 mm pitch, right-hand pins fanned out to 1.27 mm test pads."""
        qfn = self.library("Package_DFN_QFN", "QFN-32-1EP_5x5mm_P0.5mm_EP3.45x3.45mm", "CAL_U1", x, y)
        right = sorted((pd for pd in qfn.Pads() if pd.GetPosition().x > mm(x + 2)),
                       key=lambda pd: pd.GetPosition().y)
        w, out_pitch = 0.25, 1.27
        n = len(right)
        bend0 = p.ToMM(right[0].GetPosition().x) + 0.9
        fp = self.footprint("QFN fanout test pads")
        reach = max((n - 1) / 2 * (out_pitch - 0.5), 0)
        x_pads = bend0 + 0.3 * (n // 2) + reach + 1.6
        for k, pad in enumerate(right):
            px, py = p.ToMM(pad.GetPosition().x), p.ToMM(pad.GetPosition().y)
            ty = y + (k - (n - 1) / 2) * out_pitch
            # Inner pins bend later so the 45 deg segments keep >= 0.3 mm gaps.
            stagger = 0.3 * (k if k < n / 2 else n - 1 - k)
            bx = bend0 + stagger
            net = pad.GetNetname()
            self.track([(px, py), (bx, py), (bx + abs(ty - py), ty), (x_pads, ty)], w, net)
            self.smd(fp, str(k + 1), x_pads, ty, 1.6, 0.9, net)

    def hole_grid(self, x: float, y: float) -> None:
        drills = [0.6, 0.7, 0.8, 1.0, 1.2]
        fp = self.footprint("hole sizes")
        for i, d in enumerate(drills):
            xx = x + i * 3.6
            self.text(f"{d:.1f}", xx, y, size=1.2, align="center")
            for j, ring in enumerate((0.25, 0.4)):
                self.tht(fp, f"{i}{j}", xx, y + 2.6 + j * 3.2, d, d + 2 * ring, f"CAL_H{i}_{j}")
        hdr = self.footprint("2.54 header")
        for i in range(4):
            self.tht(hdr, str(i + 1), x + 19.5 + i * 2.54, y + 4.2, 1.0, 1.7, f"CAL_HDR{i}")
        self.text("2.54", x + 23.3, y, size=1.2, align="center")

    def witness(self, x: float, y: float, tag: str) -> None:
        """Same 0.5 mm pitch row in every corner: compare them to judge leveling."""
        fp = self.footprint(f"witness {tag}")
        for i in range(6):
            self.smd(fp, str(i + 1), x + i * 0.5, y, 0.25, 1.0, f"CAL_WIT_{tag}_{i + 1}")
        self.rule_area(x - 0.6, y - 0.9, x + 3.1, y + 0.9)  # a test pattern too

    def f_marker(self, x: float, y: float) -> None:
        """An 'F' plus an arrow: reads wrong if X or Y is mirrored or rotated."""
        strokes = [((x, y + 4), (x, y)), ((x, y), (x + 3, y)), ((x, y + 1.8), (x + 2, y + 1.8)),
                   ((x + 4.5, y + 4), (x + 7.5, y + 4)), ((x + 7.5, y + 4), (x + 6.5, y + 3))]
        for a, c in strokes:
            s = p.PCB_SHAPE(self.b, p.SHAPE_T_SEGMENT)
            s.SetStart(pt(*a))
            s.SetEnd(pt(*c))
            s.SetLayer(p.F_Cu)
            s.SetWidth(mm(0.8))
            self.b.Add(s)

    def rule_area(self, x0: float, y0: float, x1: float, y1: float) -> None:
        z = p.ZONE(self.b)
        z.SetIsRuleArea(True)
        z.SetZoneName(CAL_AREA)
        z.SetLayerSet(layers(p.F_Cu, p.B_Cu))
        for setter in ("SetDoNotAllowTracks", "SetDoNotAllowVias", "SetDoNotAllowPads",
                       "SetDoNotAllowFootprints", "SetDoNotAllowZoneFills"):
            getattr(z, setter)(False)
        o = z.Outline()
        o.NewOutline()
        for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
            o.Append(mm(x), mm(y))
        self.b.Add(z)

    def outline(self) -> None:
        x1, y1 = X0 + W, Y0 + H
        segs = [((X0 + R, Y0), (x1 - R, Y0)), ((x1, Y0 + R), (x1, y1 - R)),
                ((x1 - R, y1), (X0 + R, y1)), ((X0, y1 - R), (X0, Y0 + R))]
        for a, c in segs:
            s = p.PCB_SHAPE(self.b, p.SHAPE_T_SEGMENT)
            s.SetStart(pt(*a))
            s.SetEnd(pt(*c))
            s.SetLayer(p.Edge_Cuts)
            s.SetWidth(mm(0.05))
            self.b.Add(s)
        for cx, cy, sx, sy in ((X0 + R, Y0 + R, X0, Y0 + R), (x1 - R, Y0 + R, x1 - R, Y0),
                               (x1 - R, y1 - R, x1, y1 - R), (X0 + R, y1 - R, X0 + R, y1)):
            s = p.PCB_SHAPE(self.b, p.SHAPE_T_ARC)
            s.SetCenter(pt(cx, cy))
            s.SetStart(pt(sx, sy))
            s.SetArcAngleAndEnd(p.EDA_ANGLE(90, p.DEGREES_T), True)
            s.SetLayer(p.Edge_Cuts)
            s.SetWidth(mm(0.05))
            self.b.Add(s)


def clear(board: p.BOARD) -> None:
    # Collect everything first: removing while KiCad's lists are being
    # iterated crashes the SWIG bindings.
    doomed = [*board.GetTracks()]
    doomed += [d for d in board.GetDrawings() if d.GetLayer() in (p.F_Cu, p.B_Cu, p.Edge_Cuts)]
    doomed += [fp for fp in board.GetFootprints() if fp.GetReference().startswith("CAL")]
    doomed += [z for z in board.Zones() if z.GetIsRuleArea() and z.GetZoneName() == CAL_AREA]
    for item in doomed:
        board.Delete(item)
    nets = [n for name, n in board.GetNetsByName().items() if str(name).startswith("CAL_")]
    for n in nets:
        board.Remove(n)


def place_template_parts(board: p.BOARD) -> None:
    inset = 3.5
    spots = {"H1": (X0 + inset, Y0 + inset), "H2": (X0 + W - inset, Y0 + inset),
             "H3": (X0 + inset, Y0 + H - inset), "H4": (X0 + W - inset, Y0 + H - inset),
             "TP1": (X0 + W - 9.0, Y0 + 4.0)}
    for fp in board.GetFootprints():
        if fp.GetReference() in spots:
            fp.SetPosition(pt(*spots[fp.GetReference()]))


def update_dimensions(board: p.BOARD) -> None:
    for d in board.GetDrawings():
        if d.GetClass() != "PCB_DIM_ALIGNED":
            continue
        vertical = d.GetStart().x == d.GetEnd().x
        if vertical:
            d.SetStart(pt(X0, Y0 + H))
            d.SetEnd(pt(X0, Y0))
        else:
            d.SetStart(pt(X0, Y0))
            d.SetEnd(pt(X0 + W, Y0))
        d.Update()


def main(path: str) -> None:
    # Deleted items linger in the SWIG list proxies, and a second LoadBoard in
    # the same process misbehaves: clear in a child process, then build here.
    subprocess.run([sys.executable, __file__, path, "--clear"], check=True)
    board = p.LoadBoard(path)
    b = Builder(board)
    b.outline()
    place_template_parts(board)
    update_dimensions(board)

    L = X0 + 6.0  # left column, clear of the H1/H3 mounting holes
    Rc = X0 + 44.0  # right column
    b.text("pcbcam CAL v1", X0 + 8.0, Y0 + 3.5, size=2.0)
    b.text("V30 0.1 / 0.08 deep", X0 + 8.0, Y0 + 6.6, size=1.2)
    b.f_marker(X0 + W - 22.0, Y0 + 2.0)

    # Left: clearance ladder and track widths
    b.text("GAP", L, Y0 + 10.0, size=1.2)
    b.clearance_ladder(L, Y0 + 12.5, [0.10, 0.125, 0.15, 0.175, 0.20, 0.25, 0.30, 0.40], pitch=2.6)
    b.text("WIDTH", L, Y0 + 33.5, size=1.2)
    b.width_comb(L, Y0 + 36.0, [0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50, 0.80, 1.00], pitch=2.2)

    # Right: fine pitch rows (label "pitch/pad")
    b.text("PITCH/PAD", Rc, Y0 + 10.0, size=1.2)
    rows = [(0.4, 0.20), (0.5, 0.20), (0.5, 0.25), (0.5, 0.30), (0.5, 0.35), (0.65, 0.30), (0.65, 0.40),
            (0.8, 0.40), (1.27, 0.60)]
    for k, (pitch, pad_w) in enumerate(rows):
        n = 16 if pitch < 0.7 else (12 if pitch < 1 else 8)
        b.pad_row(Rc, Y0 + 12.5 + k * 2.3, pitch, pad_w, n, 1.2,
                  f"{pitch:g}/{pad_w:.2f}".replace("0.", "."), f"P{k}")

    # Right, lower: real footprints
    b.qfn_fanout(Rc + 4.0, Y0 + 38.0)
    b.library("Package_SO", "TSSOP-20_4.4x6.5mm_P0.65mm", "CAL_U2", Rc + 27.0, Y0 + 38.0)

    # Bottom right: holes
    b.hole_grid(Rc + 1.0, Y0 + 45.5)

    # Leveling witnesses, one per corner (inside the mounting holes)
    for tag, (x, y) in {"TL": (X0 + 16.0, Y0 + 10.0), "TR": (X0 + W - 13.5, Y0 + 10.0),
                        "BL": (X0 + 16.0, Y0 + H - 2.0), "BR": (X0 + W - 13.5, Y0 + H - 2.0)}.items():
        b.witness(x, y, tag)

    # Exempt the test patterns that probe the limits (ladder, widths, fine rows,
    # QFN/TSSOP, whose gaps depend on the bit used) from the process rules
    b.rule_area(L - 1.0, Y0 + 11.0, X0 + 42.5, Y0 + H - 3.0)
    b.rule_area(Rc - 1.0, Y0 + 11.0, X0 + W - 1.0, Y0 + 43.5)

    board.BuildConnectivity()
    p.SaveBoard(path, board)
    print(f"wrote {path}: {len(board.GetFootprints())} footprints, {len(board.GetTracks())} tracks")


if __name__ == "__main__":
    if sys.argv[2:] == ["--clear"]:
        brd = p.LoadBoard(sys.argv[1])
        if brd.GetCopperLayerCount() != 2:
            raise SystemExit("apply the makera_z1_2l stackup first (2 copper layers)")
        clear(brd)
        p.SaveBoard(sys.argv[1], brd)
    else:
        main(sys.argv[1])
