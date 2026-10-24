#!/usr/bin/env python3
"""
Track widths of grounded coplanar lines (50 ohm single-ended, 90/100 ohm
edge-coupled pairs) for a 2 layers board, bottom layer = ground plane.

2D quasi-static finite-volume solver of the cross-section (nonuniform grid),
with the groove the V-bit cuts into the FR4 of a milled board, and an optional
solder mask coating (rough: fills the gaps).
Checked against the closed form (conductor-backed CPW, within 1.5 %) and the
JLCPCB impedance calculator (JLC0216A coated coplanar values between the
uncoated and coated results of this model, 2-5 % apart).

Used for the approximate impedance classes of the makera_z1_2l profile:
  python3 coplanar_impedance.py --er 4.5 --h 1.43 --t 0.035 --gap 0.3 --groove 0.05
Needs numpy and scipy (pip install numpy scipy), not in the KiBot image: run
it on the host.
"""
import argparse

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla
from scipy.optimize import brentq

C0 = 299792458.0
EPS0 = 8.8541878128e-12


def axis(breaks, fine, coarse, lo, hi):
    """ Grid lines: `fine` steps at the breaks, growing by 20 % up to `coarse` """
    pts = sorted(p for p in set(breaks) | {lo, hi} if lo <= p <= hi)
    out = [pts[0]]
    for a, b in zip(pts[:-1], pts[1:]):
        L = b - a
        left = []
        acc = 0.0
        s = fine
        while acc + 2 * s < L:
            left.append(s)
            acc += 2 * s
            s = min(s * 1.2, coarse)
        seg = left + ([L - acc] if L - acc > 1e-9 else []) + left[::-1]
        x = a
        for d in seg:
            x += d
            out.append(x)
        out[-1] = b
    return np.array(out)


def energy(x, y, eps_cell, fixed, values):
    """ Solves div(eps grad phi) = 0, returns 1/2 sum eps |grad phi|^2 (per eps0, per unit length) """
    nx, ny = len(x), len(y)
    dx, dy = np.diff(x), np.diff(y)
    I, J = np.meshgrid(np.arange(nx - 1), np.arange(ny), indexing='ij')
    # Horizontal edges: cells above and below
    c = np.zeros(I.shape)
    c[:, 1:] += eps_cell[:, :] * dy[None, :] / 2
    c[:, :-1] += eps_cell[:, :] * dy[None, :] / 2
    h_n1 = (J * nx + I).ravel()
    h_w = (c / dx[:, None]).ravel()
    I, J = np.meshgrid(np.arange(nx), np.arange(ny - 1), indexing='ij')
    c = np.zeros(I.shape)
    c[1:, :] += eps_cell[:, :] * dx[:, None] / 2
    c[:-1, :] += eps_cell[:, :] * dx[:, None] / 2
    v_n1 = (J * nx + I).ravel()
    v_w = (c / dy[None, :]).ravel()
    n1 = np.concatenate([h_n1, v_n1])
    n2 = np.concatenate([h_n1 + 1, v_n1 + nx])
    w = np.concatenate([h_w, v_w])
    N = nx * ny
    A = sp.coo_matrix((np.concatenate([w, w, -w, -w]), (np.concatenate([n1, n2, n1, n2]),
                                                          np.concatenate([n1, n2, n2, n1]))), shape=(N, N)).tocsr()
    fixed = fixed.T.ravel()
    phi = np.where(fixed, values.T.ravel(), 0.0)
    free = ~fixed
    phi[free] = spla.spsolve(A[free][:, free].tocsc(), -A[free][:, fixed] @ phi[fixed])
    return 0.5 * np.sum(w * (phi[n1] - phi[n2]) ** 2)


def impedance(w, gap, er, h=1.43, t=0.035, pair_gap=None, groove=0.0, mask=0.0, er_mask=3.8, fine=0.004):
    """ Z0 of a single line, or Zdiff of a pair (pair_gap set). Half structure (symmetry at x=0). """
    diff = pair_gap is not None
    edge = pair_gap / 2 + w if diff else w / 2   # outer edge of the trace
    xg = edge + gap                               # side ground
    X, Ytop = xg + 6.0, h + t + 8.0               # side ground width, air above
    x = axis([0.0, edge, xg] + ([pair_gap / 2] if diff else []), fine, 0.25, 0.0, X)
    y = axis([0.0, h - groove, h, h + t] + ([h + t + mask] if mask else []), fine, 0.25, 0.0, Ytop)
    XC, YC = np.meshgrid((x[:-1] + x[1:]) / 2, (y[:-1] + y[1:]) / 2, indexing='ij')
    in_gap = (XC > edge) & (XC < xg)
    if diff:
        in_gap |= XC < pair_gap / 2

    def eps(dielectric):
        e = np.where(YC < h, er if dielectric else 1.0, 1.0)
        if groove > 0:
            e = np.where(in_gap & (YC > h - groove) & (YC < h), 1.0, e)
        if mask > 0 and dielectric:
            e = np.where((YC > h) & (YC < h + t + mask), er_mask, e)
        return e

    XN, YN = np.meshgrid(x, y, indexing='ij')
    cu = (YN >= h - 1e-9) & (YN <= h + t + 1e-9)
    trace = cu & (XN <= edge + 1e-9)
    if diff:
        trace &= XN >= pair_gap / 2 - 1e-9
    fixed = trace | (cu & (XN >= xg - 1e-9)) | (YN <= 1e-9) | (YN >= Ytop - 1e-9)
    if diff:
        fixed |= XN <= 1e-9   # odd mode: symmetry plane at 0 V
    values = np.where(trace, 1.0, 0.0)
    # Capacitance per line: single = both halves (C = 2 x 2E), odd mode = one line (C = 2E)
    k = 2 if diff else 4
    C = k * energy(x, y, eps(True), fixed, values) * EPS0
    Ca = k * energy(x, y, eps(False), fixed, values) * EPS0
    z = 1 / (C0 * np.sqrt(C * Ca))
    return 2 * z if diff else z


def width_for(target, gap, pair_gap=None, **kw):
    return brentq(lambda w: impedance(w, gap, pair_gap=pair_gap, **kw) - target, 0.1, 5.0, xtol=0.002)


def main():
    ap = argparse.ArgumentParser(description='Grounded coplanar line widths for a 2 layers board')
    ap.add_argument('--er', type=float, default=4.5, help='Dielectric constant of the core')
    ap.add_argument('--h', type=float, default=1.43, help='Core thickness [mm]')
    ap.add_argument('--t', type=float, default=0.035, help='Copper thickness [mm]')
    ap.add_argument('--gap', type=float, default=0.3, help='Gap to the coplanar ground and between the pair [mm]')
    ap.add_argument('--groove', type=float, default=0.0, help='Depth cut into the FR4 in the gaps (milled) [mm]')
    ap.add_argument('--mask', type=float, default=0.0, help='Solder mask thickness [mm], 0 = bare copper')
    ap.add_argument('--targets', default='50,90,100', help='Impedances: first single-ended, then pairs')
    args = ap.parse_args()
    kw = dict(er=args.er, h=args.h, t=args.t, groove=args.groove, mask=args.mask)
    for i, z in enumerate(float(v) for v in args.targets.split(',')):
        pg = None if i == 0 else args.gap
        w = width_for(z, args.gap, pg, **kw)
        # Milling tolerance: V-bit cutting 0.025 mm more / less on each side
        over = impedance(w - 0.05, args.gap + 0.05, pair_gap=None if pg is None else pg + 0.05, **kw)
        under = impedance(w + 0.05, args.gap - 0.05, pair_gap=None if pg is None else pg - 0.05, **kw)
        print('{:>5g} ohm {:<12} width {:.3f} mm, gap {} mm  (cut +/-0.025 mm per side: {:.1f} .. {:.1f} ohm)'.format(
            z, 'single' if pg is None else 'differential', w, args.gap, under, over))


if __name__ == '__main__':
    main()
