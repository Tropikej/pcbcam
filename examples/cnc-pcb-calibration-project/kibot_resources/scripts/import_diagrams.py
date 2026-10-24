#!/usr/bin/env python3
"""
Imports the diagrams of <project>/Diagrams in the schematic sheets, as
embedded pictures: `Diagrams/<Sheet name>.<ext>` goes in `<Sheet name>.kicad_sch`.

Sources:
- .drawio: exported to Diagrams/export/<name>.png by run_kibot.sh --diagrams
  (draw.io exporter container), then imported by this script
- .svg: rasterized with rsvg-convert
- .png: imported as is

The picture keeps the same uuid (derived from the sheet name): a new import
only replaces the picture data, so its position and size in the sheet are kept.
The first import (or --fit) centers it in the drawing area and scales it to fit.
Diagrams/imported.json records the hash of the imported sources (--check).

Usage: import_diagrams.py [--project DIR] [--fit] [--dpi 300]
       import_diagrams.py --check [--project DIR]    sources changed since the import?
"""
import argparse
import base64
import glob
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid

SOURCES = ('.drawio', '.svg', '.png')
LOCK = 'imported.json'
NAMESPACE = uuid.UUID('5d0c4c3e-8d6e-4c7a-9a55-0e2b3f6a9d11')
# Landscape sizes in mm
PAPER = {'A5': (210, 148), 'A4': (297, 210), 'A3': (420, 297), 'A2': (594, 420), 'A1': (841, 594),
         'A0': (1189, 841), 'A': (279.4, 215.9), 'B': (431.8, 279.4), 'C': (558.8, 431.8), 'D': (863.6, 558.8),
         'E': (1117.6, 863.6), 'USLetter': (279.4, 215.9), 'USLegal': (355.6, 215.9), 'USLedger': (431.8, 279.4)}
# Drawing area of the template sheets: inside the frame, below the page title
# ([#] TITLE text box) and above the title block
MARGIN = 20
TOP = 35
TITLE_BLOCK = 45


def sha256(path):
    """ Hash of a source. Text sources (.drawio, .svg) with LF line endings: the
        same on a Windows (CRLF) and a Linux (CI) checkout """
    with open(path, 'rb') as f:
        data = f.read()
    if os.path.splitext(path)[1].lower() in ('.drawio', '.svg'):
        data = data.replace(b'\r\n', b'\n')
    return hashlib.sha256(data).hexdigest()


def sources(diagrams):
    out = {}
    for f in sorted(glob.glob(os.path.join(diagrams, '*'))):
        stem, ext = os.path.splitext(os.path.basename(f))
        if ext.lower() in SOURCES and os.path.isfile(f):
            if stem in out:
                print('Warning: {} and {}: several sources for the same sheet, using {}'.format(
                      os.path.basename(out[stem]), os.path.basename(f), os.path.basename(out[stem])))
                continue
            out[stem] = f
    return out


def load_lock(diagrams):
    try:
        with open(os.path.join(diagrams, LOCK), encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def paper_size(text):
    m = re.search(r'\(paper\s+"([^"]+)"(?:\s+([\d.]+)\s+([\d.]+))?(\s+portrait)?\s*\)', text)
    if not m:
        return PAPER['A4']
    if m.group(1) == 'User' and m.group(2):
        return float(m.group(2)), float(m.group(3))
    w, h = PAPER.get(m.group(1), PAPER['A4'])
    return (h, w) if m.group(4) else (w, h)


def png_data(src, images_dir, dpi, full_color=False):
    """ PNG bytes of a source, at `dpi` (KiCad sizes the pictures with their resolution) """
    from PIL import Image
    ext = os.path.splitext(src)[1].lower()
    stem = os.path.splitext(os.path.basename(src))[0]
    if ext == '.drawio':
        png = os.path.join(images_dir, stem + '.png')
        if not os.path.isfile(png):
            raise RuntimeError('{} not exported: run ./run_kibot.sh --diagrams (or .\\run_kibot.ps1 --diagrams)'.format(
                os.path.basename(src)))
    elif ext == '.svg':
        tmp = tempfile.mkdtemp()
        png = os.path.join(tmp, stem + '.png')
        # 96 px/in is the SVG unit: render at 3x for a sharp print
        subprocess.run(['rsvg-convert', '--zoom', '3', '--background-color', 'white', '-o', png, src], check=True)
    else:
        png = src
    im = Image.open(png)
    im.load()
    if im.mode not in ('RGB', 'RGBA', 'L', 'LA', 'P'):
        im = im.convert('RGBA')
    if not full_color and im.mode != 'P':
        # Diagrams have few colors: a 256 colors palette divides the size (and
        # the .kicad_sch) by ~4 without visible difference
        im = im.convert('RGBA').quantize(256, method=Image.Quantize.FASTOCTREE)
    buf = io.BytesIO()
    im.save(buf, 'PNG', dpi=(dpi, dpi), optimize=True)
    if ext == '.svg':
        shutil.rmtree(os.path.dirname(png), ignore_errors=True)
    return buf.getvalue(), im.size


def image_block(data, at, scale, uid):
    b64 = base64.b64encode(data).decode('ascii')
    chunks = [b64[i:i + 76] for i in range(0, len(b64), 76)]
    lines = ['\t(image', '\t\t(at {:g} {:g})'.format(*at), '\t\t(scale {:g})'.format(scale),
             '\t\t(uuid "{}")'.format(uid), '\t\t(data']
    lines += ['\t\t\t"{}"'.format(c) for c in chunks]
    lines += ['\t\t)', '\t)']
    return '\n'.join(lines) + '\n'


def find_image(text, uid):
    """ (start, end, at, scale) of the image with this uuid """
    for m in re.finditer(r'\n\t\(image\n', text):
        start = m.start() + 1
        depth = 0
        i = start
        while i < len(text):
            if text[i] == '"':
                i = text.index('"', i + 1)
            elif text[i] == '(':
                depth += 1
            elif text[i] == ')':
                depth -= 1
                if depth == 0:
                    break
            i += 1
        block = text[start:i + 1]
        if '(uuid "{}")'.format(uid) in block:
            at = re.search(r'\(at\s+([-\d.]+)\s+([-\d.]+)', block)
            sc = re.search(r'\(scale\s+([-\d.]+)\)', block)
            end = i + 2 if text[i + 1:i + 2] == '\n' else i + 1
            return start, end, (float(at.group(1)), float(at.group(2))), float(sc.group(1)) if sc else 1.0
    return None


def import_one(sheet, src, images_dir, dpi, fit, full_color=False):
    with open(sheet, encoding='utf-8') as f:
        text = f.read()
    data, (wpx, hpx) = png_data(src, images_dir, dpi, full_color)
    uid = str(uuid.uuid5(NAMESPACE, os.path.basename(sheet)))
    found = find_image(text, uid)
    w_mm, h_mm = wpx / dpi * 25.4, hpx / dpi * 25.4
    if found and not fit:
        start, end, at, scale = found
        how = 'updated (position and size kept)'
    else:
        pw, ph = paper_size(text)
        box_w, box_h = pw - 2 * MARGIN, ph - TOP - TITLE_BLOCK - MARGIN / 2
        scale = round(min(box_w / w_mm, box_h / h_mm), 4)
        at = (round(pw / 2, 2), round(TOP + box_h / 2, 2))
        how = 'placed ({:.0f} x {:.0f} mm)'.format(w_mm * scale, h_mm * scale)
    block = image_block(data, at, scale, uid)
    if found:
        start, end = found[0], found[1]
        text = text[:start] + block + text[end:]
    else:
        for anchor in ('\n\t(sheet_instances', '\n\t(embedded_fonts'):
            pos = text.find(anchor)
            if pos >= 0:
                text = text[:pos + 1] + block + text[pos + 1:]
                break
        else:
            pos = text.rstrip().rfind(')')
            text = text[:pos] + block + text[pos:]
    with open(sheet, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)
    print('{}: {} {} ({}x{} px, {} KB)'.format(os.path.basename(sheet), os.path.basename(src), how, wpx, hpx,
                                               len(data) // 1024))


def main():
    parser = argparse.ArgumentParser(description='Import Diagrams/* in the schematic sheets')
    parser.add_argument('--project', default='.', help='Folder of the KiCad project')
    parser.add_argument('--fit', action='store_true', help='Center and fit the pictures again')
    parser.add_argument('--dpi', type=int, default=300, help='Resolution given to the pictures')
    parser.add_argument('--check', action='store_true', help='Fail if a source changed since its import')
    parser.add_argument('--full-color', action='store_true', help="Don't reduce the pictures to 256 colors")
    args = parser.parse_args()
    diagrams = os.path.join(args.project, 'Diagrams')
    srcs = sources(diagrams)
    lock = load_lock(diagrams)
    if args.check:
        stale = [os.path.basename(f) for n, f in srcs.items()
                 if lock.get(os.path.basename(f), {}).get('sha256') != sha256(f)]
        for s in stale:
            print('{}: changed since the last import, run ./run_kibot.sh --diagrams and commit the sheet'.format(
                os.path.join(diagrams, s)))
        return 1 if stale else 0
    if not srcs:
        print('No diagram in {} ({})'.format(diagrams, ', '.join(SOURCES)))
        return 0
    errors = 0
    new_lock = {}
    for stem, src in srcs.items():
        sheet = os.path.join(args.project, stem + '.kicad_sch')
        if not os.path.isfile(sheet):
            print('Error: {}: no sheet {}.kicad_sch (the diagram is named after its sheet)'.format(
                  os.path.basename(src), stem))
            errors += 1
            continue
        try:
            import_one(sheet, src, os.path.join(diagrams, 'export'), args.dpi, args.fit, args.full_color)
        except (RuntimeError, OSError, subprocess.CalledProcessError) as e:
            print('Error: {}: {}'.format(os.path.basename(src), e))
            errors += 1
            continue
        new_lock[os.path.basename(src)] = {'sha256': sha256(src), 'sheet': os.path.basename(sheet)}
    if new_lock:
        lock.update(new_lock)
        with open(os.path.join(diagrams, LOCK), 'w', encoding='utf-8', newline='\n') as f:
            json.dump(lock, f, indent=2, sort_keys=True)
            f.write('\n')
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
