#!/usr/bin/env python3
"""
Sanity checks of the generated manufacturing files, independent of KiCad:

- Gerber/drill sets (Fabrication/Gerbers and the ZIP files sent to the
  manufacturers): files readable and complete, board outline closed, copper
  layers count consistent with the drill files, holes inside the board,
  plated holes on copper on both outer layers.
- Assembly: BoM / pick and place consistency, LCSC codes, JLCPCB pick and
  place positions on the pads of the JLCPCB Gerbers (same origin) and
  rotation of the 2-pad parts.
- PDF documents: no unexpanded ${VARIABLE}, template placeholders, revision.

Usage: check_manufacturing.py [-d DIR] [--markdown FILE]
Exit code 1 when errors are found (warnings don't fail).
"""
import argparse
import csv
import glob
import io
import math
import os
import re
import subprocess
import sys
import zipfile

TOL = 0.005  # mm
PROJECT_DIR = '.'


class Report(object):
    def __init__(self):
        self.lines = []
        self.errors = 0
        self.warnings = 0
        self.title = 'Manufacturing checks'

    def section(self, title):
        self.lines.append(('section', title))

    def ok(self, msg):
        self.lines.append(('ok', msg))

    def info(self, msg):
        self.lines.append(('info', msg))

    def warn(self, msg):
        self.warnings += 1
        self.lines.append(('warn', msg))

    def error(self, msg):
        self.errors += 1
        self.lines.append(('error', msg))

    def text(self):
        tags = {'ok': '  OK   ', 'info': '  INFO ', 'warn': '  WARN ', 'error': '  ERROR'}
        out = []
        for kind, msg in self.lines:
            out.append('\n== ' + msg if kind == 'section' else tags[kind] + ' ' + msg)
        out.append('\n{} error(s), {} warning(s)'.format(self.errors, self.warnings))
        return '\n'.join(out)

    def markdown(self):
        icons = {'ok': '✅', 'info': 'ℹ️', 'warn': '⚠️', 'error': '❌'}
        out = ['### {} ({} error(s), {} warning(s))'.format(self.title, self.errors, self.warnings), '']
        for kind, msg in self.lines:
            if kind == 'section':
                out += ['', '**{}**'.format(msg), '']
            else:
                out.append('- {} {}'.format(icons[kind], msg))
        return '\n'.join(out) + '\n'


# ----------------------------------------------------------------------------------------------------------------------
# Gerber parsing
# ----------------------------------------------------------------------------------------------------------------------

def arc_points(start, end, center, clockwise, steps_per_turn=64):
    a0 = math.atan2(start[1] - center[1], start[0] - center[0])
    a1 = math.atan2(end[1] - center[1], end[0] - center[0])
    r = math.hypot(start[0] - center[0], start[1] - center[1])
    if clockwise:
        while a1 >= a0 - 1e-9:
            a1 -= 2 * math.pi
    else:
        while a1 <= a0 + 1e-9:
            a1 += 2 * math.pi
    n = max(2, int(abs(a1 - a0) / (2 * math.pi) * steps_per_turn))
    return [(center[0] + r * math.cos(a0 + (a1 - a0) * i / n), center[1] + r * math.sin(a0 + (a1 - a0) * i / n))
            for i in range(1, n + 1)]


class Gerber(object):
    """ Minimal RS-274X reader: flashes, draws (with arcs) and regions """

    def __init__(self, name, text):
        self.name = name
        self.function = ''
        self.flashes = []   # (x, y, half_w, half_h)
        self.draws = []     # list of point lists (polylines), with aperture half width
        self.regions = []   # polygons
        self.complete = False
        self.errors = []
        self.parse(text)

    def parse(self, text):
        scale = 1e-6
        unit = 1.0
        apertures = {}
        macros = {}
        cur_ap = None
        x = y = 0.0
        mode = 'G01'
        region = None
        m = re.search(r'TF\.FileFunction,([^*]*)\*', text)
        self.function = m.group(1) if m else ''
        # Extended commands %...% can span several lines
        tokens = re.findall(r'%[^%]*%|[^%*\n]+\*', text)
        if not tokens:
            self.errors.append('empty file')
            return
        for tok in tokens:
            tok = tok.strip()
            if tok.startswith('%'):
                body = tok.strip('%').replace('\n', '')
                m = re.match(r'FSLAX(\d)(\d)Y(\d)(\d)', body)
                if m:
                    scale = 10 ** -int(m.group(2))
                    continue
                if body.startswith('MOIN'):
                    unit = 25.4
                    continue
                m = re.match(r'AM(\w+)\*', body)
                if m:
                    macros[m.group(1)] = body
                    continue
                m = re.match(r'ADD(\d+)([A-Za-z_][\w.]*)(?:,([^*]*))?\*', body)
                if m:
                    params = [float(v) for v in (m.group(3) or '').split('X') if v.strip()]
                    kind = m.group(2)
                    hw = hh = 0.0
                    if kind == 'C' and params:
                        hw = hh = params[0] / 2
                    elif kind in ('R', 'O') and len(params) >= 2:
                        hw, hh = params[0] / 2, params[1] / 2
                    elif kind == 'P' and params:
                        hw = hh = params[0] / 2
                    elif kind == 'RoundRect' and len(params) >= 9:
                        xs, ys = params[1:9:2], params[2:9:2]
                        hw = max(abs(v) for v in xs) + params[0]
                        hh = max(abs(v) for v in ys) + params[0]
                    apertures[int(m.group(1))] = (hw * unit, hh * unit)
                continue
            body = tok.rstrip('*')
            if body.startswith('G04') or not body:
                continue
            if body == 'M02':
                self.complete = True
                continue
            if body.startswith('G36'):
                region = []
                continue
            if body.startswith('G37'):
                if region and len(region) >= 3:
                    self.regions.append(region)
                region = None
                continue
            m = re.match(r'^(?:G54)?D(\d+)$', body)
            if m and int(m.group(1)) >= 10:
                cur_ap = int(m.group(1))
                continue
            m = re.match(r'^(G0?[123])?(?:X(-?\d+))?(?:Y(-?\d+))?(?:I(-?\d+))?(?:J(-?\d+))?(?:D0?([123]))?$', body)
            if not m or body in ('G75', 'G74', 'G90', 'G71', 'G70'):
                if m and m.group(1):
                    mode = 'G0' + m.group(1)[-1]
                continue
            if m.group(1):
                mode = 'G0' + m.group(1)[-1]
            if m.group(6) is None:
                continue
            nx = int(m.group(2)) * scale * unit if m.group(2) else x
            ny = int(m.group(3)) * scale * unit if m.group(3) else y
            op = m.group(6)
            if op == '2':
                if region is not None:
                    if len(region) >= 3:
                        self.regions.append(region)
                    region = [(nx, ny)]
            elif op == '1':
                if mode in ('G02', 'G03'):
                    i = int(m.group(4)) * scale * unit if m.group(4) else 0.0
                    j = int(m.group(5)) * scale * unit if m.group(5) else 0.0
                    pts = arc_points((x, y), (nx, ny), (x + i, y + j), mode == 'G02')
                else:
                    pts = [(nx, ny)]
                if region is not None:
                    region.extend(pts)
                else:
                    hw = apertures.get(cur_ap, (0, 0))[0]
                    self.draws.append(([(x, y)] + pts, hw))
            elif op == '3':
                hw, hh = apertures.get(cur_ap, (0, 0))
                self.flashes.append((nx, ny, hw, hh))
            x, y = nx, ny

    def pad_centres(self):
        centres = [(f[0], f[1]) for f in self.flashes]
        for poly in self.regions:
            centres.append((sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly)))
        return centres

    def covers(self, px, py):
        """ Is the point on copper (flash, region or track)? """
        for x, y, hw, hh in self.flashes:
            if abs(px - x) <= hw + TOL and abs(py - y) <= hh + TOL:
                return True
        for poly in self.regions:
            if point_in_polygon(px, py, poly):
                return True
        for pts, hw in self.draws:
            for a, b in zip(pts, pts[1:]):
                if segment_distance(px, py, a, b) <= hw + TOL:
                    return True
        return False


def point_in_polygon(x, y, poly):
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def segment_distance(px, py, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    if dx == dy == 0:
        return math.hypot(px - a[0], py - a[1])
    t = max(0, min(1, ((px - a[0]) * dx + (py - a[1]) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - a[0] - t * dx, py - a[1] - t * dy)


def outline_polygons(g):
    """ Chains the outline draws into closed polygons. Returns (polygons, open_ends, vcuts).
        V-cuts (JLCPCB: on the outline layer) are isolated horizontal/vertical lines """
    def key(p):
        return (round(p[0], 3), round(p[1], 3))
    segs = [pts for pts, _ in g.draws if len(pts) >= 2]
    ends = {}
    for idx, pts in enumerate(segs):
        for k in (key(pts[0]), key(pts[-1])):
            ends.setdefault(k, []).append(idx)
    vcuts = [pts for pts in segs if len(pts) == 2 and len(ends[key(pts[0])]) == 1 and len(ends[key(pts[1])]) == 1 and
             (abs(pts[0][0] - pts[1][0]) < 1e-3 or abs(pts[0][1] - pts[1][1]) < 1e-3)]
    if vcuts:
        segs = [pts for pts in segs if not any(pts is v for v in vcuts)]
        ends = {}
        for idx, pts in enumerate(segs):
            for k in (key(pts[0]), key(pts[-1])):
                ends.setdefault(k, []).append(idx)
    open_ends = [k for k, v in ends.items() if len(v) % 2]
    used = set()
    polys = []
    for idx in range(len(segs)):
        if idx in used:
            continue
        used.add(idx)
        poly = list(segs[idx])
        while True:
            last = key(poly[-1])
            nxt = [i for i in ends.get(last, []) if i not in used]
            if not nxt:
                break
            i = nxt[0]
            used.add(i)
            pts = segs[i] if key(segs[i][0]) == last else list(reversed(segs[i]))
            poly.extend(pts[1:])
        polys.append(poly)
    return polys, open_ends, vcuts


def polygon_area(poly):
    return abs(sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1]
                   for i in range(len(poly)))) / 2


def parse_drill(text):
    """ Excellon: returns (function, holes [(x, y, diameter)]) """
    m = re.search(r'TF\.FileFunction,([^\n]*)', text)
    function = m.group(1).strip() if m else ''
    unit = 25.4 if re.search(r'^INCH', text, re.M) else 1.0
    tools = {int(t): float(d) * unit for t, d in re.findall(r'^T(\d+)C([\d.]+)', text, re.M)}
    holes = []
    tool = None
    body = text.split('\n%', 1)[1] if '\n%' in text else text
    for line in body.splitlines():
        line = line.strip()
        m = re.match(r'^T(\d+)$', line)
        if m:
            tool = int(m.group(1))
            continue
        m = re.match(r'^X(-?[\d.]+)Y(-?[\d.]+)(?:G85X(-?[\d.]+)Y(-?[\d.]+))?$', line)
        if m and tool is not None:
            holes.append((float(m.group(1)) * unit, float(m.group(2)) * unit, tools.get(tool, 0)))
            if m.group(3):
                holes.append((float(m.group(3)) * unit, float(m.group(4)) * unit, tools.get(tool, 0)))
    return function, holes


def load_set(source):
    """ Gerber/drill files of a directory or a ZIP: {name: text} """
    files = {}
    if source.endswith('.zip'):
        with zipfile.ZipFile(source) as z:
            for n in z.namelist():
                if not n.endswith('/'):
                    files[os.path.basename(n)] = z.read(n).decode('utf-8', 'replace')
    else:
        for f in glob.glob(os.path.join(source, '*')):
            if os.path.isfile(f) and not f.endswith('.pdf'):
                with open(f, encoding='utf-8', errors='replace') as fh:
                    files[os.path.basename(f)] = fh.read()
    return files


def check_gerber_set(rep, source):
    files = load_set(source)
    gerbers = {}
    drills = []
    for name, text in sorted(files.items()):
        if re.search(r'^M48', text, re.M) and name.lower().endswith(('.drl', '.xln', '.txt')):
            drills.append((name, parse_drill(text)))
        elif '%FS' in text:
            g = Gerber(name, text)
            if 'Drillmap' in g.function or 'Drawing' in g.function:
                continue
            gerbers[name] = g
    if not gerbers:
        rep.warn('no Gerber file in {}'.format(source))
        return
    bad = [n for n, g in gerbers.items() if g.errors or not g.complete]
    if bad:
        rep.error('incomplete or unreadable Gerbers: ' + ', '.join(bad))
    else:
        rep.ok('{} Gerber files readable and complete (M02)'.format(len(gerbers)))

    copper = {n: g for n, g in gerbers.items() if g.function.startswith('Copper')}
    outline = [g for g in gerbers.values() if g.function.startswith('Profile')]
    for need in ('Soldermask,Top', 'Soldermask,Bot', 'Legend,Top'):
        if not any(g.function.startswith(need) for g in gerbers.values()):
            rep.warn('no {} layer'.format(need))
    # Layers count from the drill files (Plated,1,N,PTH)
    spans = [int(m.group(1)) for _, (f, _) in drills for m in [re.match(r'(?:Non)?Plated,1,(\d+)', f)] if m]
    if spans:
        if len(copper) == spans[0]:
            rep.ok('{} copper layers, consistent with the drill files'.format(len(copper)))
        else:
            rep.error('{} copper Gerbers but the drill files are for {} layers'.format(len(copper), spans[0]))
    else:
        rep.info('{} copper layers'.format(len(copper)))

    board = None
    if len(outline) != 1:
        rep.error('{} board outline (Profile) files'.format(len(outline)))
    else:
        polys, open_ends, vcuts = outline_polygons(outline[0])
        if open_ends:
            rep.error('board outline not closed ({} open ends, i.e. at {})'.format(len(open_ends), open_ends[0]))
        elif not polys:
            rep.error('empty board outline')
        else:
            polys.sort(key=polygon_area, reverse=True)
            board = polys
            xs = [p[0] for p in polys[0]]
            ys = [p[1] for p in polys[0]]
            rep.ok('board outline closed: {:.2f} x {:.2f} mm{}{}'.format(
                max(xs) - min(xs), max(ys) - min(ys),
                ', {} cut-out(s)'.format(len(polys) - 1) if len(polys) > 1 else '',
                ', {} V-cut line(s)'.format(len(vcuts)) if vcuts else ''))

    def on_board(x, y):
        return point_in_polygon(x, y, board[0]) and not any(point_in_polygon(x, y, p) for p in board[1:])

    if not drills:
        rep.warn('no drill file')
        return
    tops = [g for g in copper.values() if 'Top' in g.function]
    bots = [g for g in copper.values() if 'Bot' in g.function]
    for side, found in (('top', tops), ('bottom', bots)):
        if not found:
            rep.error('no {} copper layer'.format(side))
    if board is None:
        rep.warn('holes position vs board outline not checked (no valid outline)')
    for name, (function, holes) in drills:
        plated = function.startswith('Plated') or 'NPTH' not in name.upper()
        no_cu = []
        outside = [h for h in holes if board and not on_board(h[0], h[1])]
        if outside:
            rep.error('{}: {} hole(s) outside the board, i.e. at ({:.3f}, {:.3f})'.format(
                name, len(outside), outside[0][0], outside[0][1]))
        if plated and tops and bots:
            no_cu = [h for h in holes if not (tops[0].covers(h[0], h[1]) and bots[0].covers(h[0], h[1]))]
            if no_cu:
                rep.error('{}: {} plated hole(s) without copper on both outer layers, i.e. at ({:.3f}, {:.3f})'.format(
                    name, len(no_cu), no_cu[0][0], no_cu[0][1]))
        if not outside and (not plated or not tops or not no_cu):
            rep.ok('{}: {} {} hole(s){}'.format(name, len(holes), 'plated' if plated else 'non-plated',
                                              ' on copper,' if plated else ',', ) + ' inside the board')
    return copper


# ----------------------------------------------------------------------------------------------------------------------
# Assembly files
# ----------------------------------------------------------------------------------------------------------------------

def read_csv(pattern):
    files = sorted(glob.glob(pattern))
    if not files:
        return None, None
    with open(files[0], encoding='utf-8', errors='replace') as f:
        return files[0], list(csv.DictReader(f))


def split_refs(text):
    return [r for r in re.split(r'[\s,;]+', text or '') if r]


def check_assembly(rep, jlc_copper):
    # Main run: NAME-bom.csv, assembly variant: NAME-bom_VARIANT.csv
    bom_file, bom = read_csv('Manufacturing/Assembly/*-bom.csv')
    if bom is None:
        bom_file, bom = read_csv('Manufacturing/Assembly/*-bom_*.csv')
    fitted = {}
    if bom is not None:
        for row in bom:
            for ref in split_refs(row.get('References')):
                fitted[ref] = (row.get('LCSC') or '').strip()
        rep.ok('{}: {} fitted parts'.format(os.path.basename(bom_file), len(fitted)))
    jbom_file, jbom = read_csv('Manufacturing/JLCPCB/*_bom_jlc.csv')
    jcpl_file, jcpl = read_csv('Manufacturing/JLCPCB/*_cpl_jlc.csv')
    if jbom is None and jcpl is None:
        rep.info('no JLCPCB assembly files')
        return
    jbom_refs = {}
    values_by_code = {}
    for row in jbom or []:
        code = (row.get('LCSC Part #') or '').strip()
        values_by_code.setdefault(code, []).append('`{}` ({})'.format(row.get('Comment'), row.get('Designator')))
        for ref in split_refs(row.get('Designator')):
            if ref in jbom_refs:
                rep.error('JLCPCB BoM: {} is on several lines'.format(ref))
            jbom_refs[ref] = code
        if not re.match(r'^C\d+$', code):
            rep.error('JLCPCB BoM: invalid LCSC code `{}` for {}'.format(code, row.get('Designator')))
    # i.e. a variant overriding the value but not the LCSC field: the factory fits the same part
    for code, values in sorted(values_by_code.items()):
        if code and len(values) > 1:
            rep.warn('JLCPCB BoM: {} is used for different values: {}. Override the LCSC field with the value.'.format(
                code, ', '.join(values)))
    cpl_refs = [row['Designator'] for row in jcpl or []]
    if fitted:
        unknown = sorted(set(jbom_refs) - set(fitted))
        if unknown:
            rep.error('JLCPCB BoM lists parts that are not fitted (DNP?): ' + ', '.join(unknown))
        not_bom = sorted(set(cpl_refs) - set(fitted))
        if not_bom:
            # i.e. fiducials: excluded from the BoM but kept in the position files, ignored by JLCPCB
            rep.info('placed parts excluded from the BoM (fiducials...): ' + ', '.join(not_bom))
        diff = [r for r, c in jbom_refs.items() if r in fitted and fitted[r] and fitted[r] != c]
        if diff:
            rep.error('LCSC code differs between the BoM and the JLCPCB BoM: ' + ', '.join(diff))
    not_placed = sorted(set(jbom_refs) - set(cpl_refs))
    if not_placed:
        rep.warn('in the JLCPCB BoM but not in the pick and place (through hole with the SMD-only template?): ' +
                 ', '.join(not_placed))
    no_code = sorted(set(cpl_refs) - set(jbom_refs))
    rep.ok('JLCPCB BoM: {} part(s) with an LCSC code, {} placed part(s) without code (not assembled)'.format(
        len(jbom_refs), len(no_code)))
    # Pick and place vs JLCPCB Gerbers
    if not jcpl or not jlc_copper:
        return
    sides = {'top': [g for g in jlc_copper.values() if 'Top' in g.function],
             'bottom': [g for g in jlc_copper.values() if 'Bot' in g.function]}
    pads = {s: (v[0].pad_centres() if v else []) for s, v in sides.items()}
    far, rot_bad = [], []
    for row in jcpl:
        ref, x, y = row['Designator'], float(row['Mid X']), float(row['Mid Y'])
        rot, side = float(row['Rotation']), row['Layer'].strip().lower()
        near = [p for p in pads.get(side, []) if math.hypot(p[0] - x, p[1] - y) < 6]
        if not near:
            far.append(ref)
            continue
        if ref[0] in 'CRL' and not ref.startswith('CN'):
            best = None
            for i in range(len(near)):
                for j in range(i + 1, len(near)):
                    a, b = near[i], near[j]
                    pitch = math.hypot(a[0] - b[0], a[1] - b[1])
                    if not 0.3 < pitch < 3.5:
                        continue
                    off = math.hypot((a[0] + b[0]) / 2 - x, (a[1] + b[1]) / 2 - y)
                    k = (off > 0.02, pitch if off <= 0.02 else off)
                    if best is None or k < best[0]:
                        best = (k, off, a, b)
            if best is None or best[1] > 0.02:
                far.append(ref)
            elif (abs(best[2][0] - best[3][0]) > abs(best[2][1] - best[3][1])) != (round(rot) % 180 == 0):
                rot_bad.append(ref)
    if far:
        rep.error('JLCPCB pick and place: parts not on their pads (origin?): ' + ', '.join(far))
    if rot_bad:
        rep.error('JLCPCB pick and place: rotation inconsistent with the pads: ' + ', '.join(rot_bad))
    if not far and not rot_bad:
        rep.ok('JLCPCB pick and place: {} parts on their pads (same origin as the Gerbers), 2-pad rotations '
               'consistent'.format(len(jcpl)))


# ----------------------------------------------------------------------------------------------------------------------
# PDF documents
# ----------------------------------------------------------------------------------------------------------------------

def check_pdfs(rep):
    pdfs = sorted(set(glob.glob('Schematic/*.pdf') + glob.glob('PCB/*.pdf') + glob.glob('Manufacturing/*/*.pdf')))
    pdfs = [p for p in pdfs if '_drill_map' not in p]
    if not pdfs:
        rep.info('no PDF document')
        return
    revision = ''
    pros = glob.glob('*.kicad_pro') or glob.glob(os.path.join(PROJECT_DIR, '*.kicad_pro'))
    if pros:
        with open(pros[0], encoding='utf-8') as f:
            m = re.search(r'"REVISION":\s*"([^"]*)"', f.read())
            revision = m.group(1) if m else ''
    placeholders = ('Board Name', 'Project Name', 'Company Name')
    for pdf in pdfs:
        try:
            text = subprocess.run(['pdftotext', '-layout', pdf, '-'], capture_output=True, text=True,
                                  check=True).stdout
        except (OSError, subprocess.CalledProcessError) as e:
            rep.warn('{}: not checked ({})'.format(pdf, e))
            continue
        name = os.path.basename(pdf)
        pages = text.split('\f')
        unexpanded = sorted(set(re.findall(r'\$\{[^}\s]{1,40}\}', text)))
        if unexpanded:
            bad_pages = [str(i + 1) for i, p in enumerate(pages) if '${' in p]
            rep.error('{}: unexpanded variables {} (page {})'.format(name, ', '.join(unexpanded[:5]),
                                                                    ', '.join(bad_pages[:5])))
        found = [p for p in placeholders if p in text]
        if found:
            rep.warn('{}: template placeholders still there: {}'.format(name, ', '.join(found)))
        if revision and revision not in text:
            rep.warn('{}: revision `{}` not found'.format(name, revision))
        if not unexpanded:
            rep.ok('{}: {} page(s), no unexpanded variable'.format(name, len([p for p in pages if p.strip()])))


def main():
    parser = argparse.ArgumentParser(description='Sanity checks of the manufacturing files')
    parser.add_argument('-d', '--dir', default='.', help='Project/output directory')
    parser.add_argument('--markdown', help='Also write the report in Markdown to this file')
    parser.add_argument('--title', default='Manufacturing checks', help='Title of the Markdown report')
    args = parser.parse_args()
    global PROJECT_DIR
    PROJECT_DIR = os.getcwd()
    if not os.path.isdir(args.dir):
        print('ERROR: output directory {} not found (generation failed?)'.format(args.dir))
        return 1
    os.chdir(args.dir)
    rep = Report()
    rep.title = args.title

    sets = []
    if glob.glob('Manufacturing/Fabrication/Gerbers/*'):
        sets.append('Manufacturing/Fabrication/Gerbers')
    sets += sorted(glob.glob('Manufacturing/JLCPCB/*.zip'))
    if glob.glob('Manufacturing/Panel/Gerbers/*'):
        sets.append('Manufacturing/Panel/Gerbers')
    jlc_copper = None
    for s in sets:
        rep.section('Gerbers and drill: ' + s)
        copper = check_gerber_set(rep, s)
        if 'JLCPCB' in s:
            jlc_copper = copper
    if not sets:
        rep.section('Gerbers and drill')
        rep.info('no Gerber set (DRAFT?)')
    rep.section('Assembly files')
    check_assembly(rep, jlc_copper)
    rep.section('PDF documents')
    check_pdfs(rep)

    out = rep.text()
    sys.stdout.buffer.write((out + '\n').encode('utf-8'))
    if args.markdown:
        with open(args.markdown, 'w', encoding='utf-8') as f:
            f.write(rep.markdown())
    return 1 if rep.errors else 0


if __name__ == '__main__':
    sys.exit(main())
