#!/usr/bin/env python3
"""
Initializes a project created from the template:
- sets the metadata of kibot_yaml/kibot_main.yaml (PROJECT_NAME, BOARD_NAME,
  COMPANY, DESIGNER)
- optionally renames the KiCad project files (--name), updating the references
  inside the project, schematics and PCB.
- multi-board repository (--dir hw/io): the project of that folder, and its
  metadata in hw/io/kibot_board.yaml (overrides of kibot_main.yaml
  definitions for this board).

Values not given on the command line are asked interactively (when possible).

Usage: init_project.py [--project TEXT] [--board TEXT] [--company TEXT]
                       [--designer TEXT] [--name NEW_FILE_NAME] [--dir DIR] [-y]
"""
import argparse
import glob
import os
import re
import sys

MAIN = 'kibot_yaml/kibot_main.yaml'
BOARD_FILE = 'kibot_board.yaml'
BOARD_HEADER = """# Definitions of kibot_yaml/kibot_main.yaml for this board only (multi-board
# repository), `KEY: value`, i.e. BOARD_NAME, PANEL_ROWS. Read by kibot_launch.sh.
"""
FIELDS = (('project', 'PROJECT_NAME', 'Project name'),
          ('board', 'BOARD_NAME', 'Board name'),
          ('company', 'COMPANY', 'Company'),
          ('designer', 'DESIGNER', 'Designer'))


def current(text, key):
    m = re.search(r'^  {}: (.*)$'.format(key), text, re.M)
    return m.group(1).strip().strip('\'"') if m else ''


def board_value(text, key):
    m = re.search(r'^{}:[ \t]*(.*)$'.format(key), text, re.M)
    return m.group(1).strip().strip('\'"') if m else None


def quote(value):
    return "'" + value.replace("'", "''") + "'"


def rename_project(new, directory='.'):
    here = os.getcwd()
    os.chdir(directory)
    try:
        return _rename_project(new)
    finally:
        os.chdir(here)


def _rename_project(new):
    pros = [p for p in glob.glob('*.kicad_pro') if not re.match(r'kibot_.{8}[.]kicad_pro$', p)]
    if len(pros) != 1:
        print('Error: expected one .kicad_pro, found {}'.format(pros), file=sys.stderr)
        return 1
    old = pros[0][:-len('.kicad_pro')]
    if old == new:
        return 0
    if not re.match(r'^[\w.-]+$', new):
        print('Error: use only letters, digits, `.`, `_` and `-` in the file name', file=sys.stderr)
        return 1
    for ext in ('kicad_pro', 'kicad_sch', 'kicad_pcb', 'kicad_dru', 'kicad_prl'):
        src = '{}.{}'.format(old, ext)
        if os.path.isfile(src):
            os.rename(src, '{}.{}'.format(new, ext))
    # References to the project name inside the files
    for f in glob.glob('*.kicad_sch') + glob.glob('*.kicad_pro') + glob.glob('*.kicad_pcb'):
        with open(f, encoding='utf-8') as fh:
            text = fh.read()
        if f.endswith('.kicad_pro'):
            # File names stored in the project (netlist, STEP, BoM...)
            new_text = re.sub(r'\b{}(?=[.-])'.format(re.escape(old)), new, text)
        else:
            new_text = text.replace('(project "{}"'.format(old), '(project "{}"'.format(new))
        if new_text != text:
            with open(f, 'w', encoding='utf-8', newline='\n') as fh:
                fh.write(new_text)
    print('Renamed the project files: {} -> {}'.format(old, new))
    return 0


def main():
    parser = argparse.ArgumentParser(description='Initialize a project created from the template')
    for arg, _, label in FIELDS:
        parser.add_argument('--' + arg, help=label)
    parser.add_argument('--name', help='New name for the KiCad project files (i.e. my_board)')
    parser.add_argument('--dir', default='.',
                        help='Multi-board: folder of the board (metadata in its {})'.format(BOARD_FILE))
    parser.add_argument('-y', '--yes', action='store_true', help="Don't ask, keep the current values")
    args = parser.parse_args()
    interactive = sys.stdin.isatty() and not args.yes
    board_dir = os.path.normpath(args.dir)
    if not os.path.isdir(board_dir):
        print('Error: {} is not a folder'.format(board_dir), file=sys.stderr)
        return 1
    per_board = board_dir != '.'

    with open(MAIN, encoding='utf-8') as f:
        main_text = f.read()
    out_file = os.path.join(board_dir, BOARD_FILE) if per_board else MAIN
    if not per_board:
        text = main_text
    elif os.path.isfile(out_file):
        with open(out_file, encoding='utf-8') as f:
            text = f.read()
    else:
        text = BOARD_HEADER
    for arg, key, label in FIELDS:
        value = getattr(args, arg)
        if per_board:
            cur = board_value(text, key)
            cur = current(main_text, key) if cur is None else cur
        else:
            cur = current(text, key)
        if value is None and interactive:
            value = input('{} [{}]: '.format(label, cur)).strip() or None
        if value is None:
            continue
        if not per_board:
            text = re.sub(r'^(  {}: ).*$'.format(key), lambda m: m.group(1) + quote(value), text, count=1,
                          flags=re.M)
        elif board_value(text, key) is not None:
            text = re.sub(r'^({}: ).*$'.format(key), lambda m: m.group(1) + quote(value), text, count=1,
                          flags=re.M)
        else:
            text = text.rstrip('\n') + '\n{}: {}\n'.format(key, quote(value))
        print('{}: {}'.format(key, value))
    if per_board or text != main_text:
        with open(out_file, 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)

    name = args.name
    if name is None and interactive:
        pros = glob.glob(os.path.join(board_dir, '*.kicad_pro'))
        cur = os.path.basename(pros[0])[:-len('.kicad_pro')] if pros else ''
        name = input('KiCad project file name [{}]: '.format(cur)).strip() or None
    if name:
        return rename_project(name, board_dir)
    return 0


if __name__ == '__main__':
    sys.exit(main())
