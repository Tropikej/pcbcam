#!/usr/bin/env python3
"""
Generates docs/CONFIG_REFERENCE.md from the comments of the configuration
files, so the reference can't drift from the files:
- kibot_settings.yaml: the comment block above each key
- kibot_yaml/kibot_main.yaml, `definitions:`: `# Title ===` sections, comment
  blocks (for the group of entries below them) and inline comments
- stackup profiles (kibot_resources/stackups/*.yaml): entries described below
- kibot_launch.sh: the --help text

Usage: gen_config_reference.py            write docs/CONFIG_REFERENCE.md
       gen_config_reference.py --check    fail if it is outdated, or if a
                                          setting/definition has no comment
No dependency (the CI runs it on the host python).
"""
import argparse
import glob
import os
import re
import sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
SETTINGS = 'kibot_settings.yaml'
MAIN = 'kibot_yaml/kibot_main.yaml'
LAUNCHER = 'kibot_launch.sh'
STACKUPS = 'kibot_resources/stackups'
OUTPUT = 'docs/CONFIG_REFERENCE.md'
# Sections of names used internally: documented as a group (section comment)
INTERNAL_SECTIONS = ('Output directories', 'Layer names', 'Filters names', 'Output names', 'Set by kibot_launch.sh')
# Entries of the stackup profiles (kibot_resources/stackups/*.yaml)
PROFILE_KEYS = {
    'name': 'Stackup name, shown in the fabrication document and by `--stackup list`',
    'description': 'One line description, shown by `--stackup list`',
    'copper_finish': 'Surface finish of the physical stackup, i.e. `HAL lead-free`, `ENIG`, `None`',
    'mask_color': 'Solder mask color (stackup and fabrication notes)',
    'silk_color': 'Silkscreen color (stackup and fabrication notes)',
    'mask_thickness': 'Solder mask thickness in mm (added to the board thickness)',
    'mask_epsilon_r': 'Solder mask dielectric constant',
    'solder_mask': '`false`: no solder mask layers in the stackup (i.e. CNC milled boards). Default `true`',
    'silkscreen': '`false`: no silkscreen layers in the stackup. Default `true`',
    'layers': 'Top to bottom: `{copper: NAME, type: signal|power|mixed|jumper, thickness}` and '
              '`{dielectric: core|prepreg, material, thickness, epsilon_r, loss_tangent}`',
    'design_rules': 'File of kibot_resources/stackups copied to the project `.kicad_dru` (kept if customized)',
    'fabrication_notes': 'File of kibot_resources/stackups copied to kibot_resources/templates/fabrication_notes.txt '
                         '(kept if customized)',
    'board_rules': '*Board Setup → Constraints* minimums (`.kicad_pro` keys, i.e. `min_track_width`, in mm)',
    'via_sizes': 'Pre-defined via sizes `[[diameter, drill], ...]` in mm',
    'impedance': 'Rows of the impedance table of the fabrication document (`line, impedance, tolerance, layer, '
                 'width, gap, gnd_gap, ref`), `[]` for *No controlled impedance*',
    'netclasses': 'Net classes created or updated (`name, track_width, clearance, diff_pair_width, diff_pair_gap, '
                  'diff_pair_via_gap, via_diameter, via_drill`), `Default` included',
    'netclass_minimums': 'The other net classes are raised to these values (never lowered)',
}


def read(path):
    with open(os.path.join(ROOT, path), encoding='utf-8') as f:
        return f.read()


def cell(text):
    return text.replace('|', '\\|').replace('\n', ' ').strip()


def join_comments(lines):
    """ One paragraph, keeping the line breaks of indented (list) lines """
    out = ''
    for ln in lines:
        txt = re.sub(r'^\s*#\s?', '', ln)
        if not txt.strip():
            continue
        if out and txt[:1].isspace():
            out += '<br>' + txt.strip()
        else:
            out += (' ' if out else '') + txt.strip()
    return out


def parse_settings(text):
    """ [(key, default, description)]: the comment block right above each key """
    out = []
    block = []
    for line in text.splitlines():
        if line.startswith('#'):
            block.append(line)
            continue
        m = re.match(r'^([a-z_]+):\s*(.*)$', line)
        if m:
            value = re.sub(r'\s+#.*$', '', m.group(2)).strip()
            out.append((m.group(1), value, join_comments(block)))
        block = []
    return out


def parse_definitions(text):
    """ [(section, section_intro, [(name, default, description)])] """
    body = text[text.index('\ndefinitions:'):].splitlines()[1:]
    sections = []
    section = ['General', '', []]
    block = []           # comments above the current group
    group_desc = ''      # description of the current group (block above it)
    after_header = False
    for line in body:
        s = line.strip()
        if not s:
            if after_header and block:
                section[1] = join_comments(block)
                block = []
            after_header = False
            group_desc = ''
            continue
        if s.startswith('#'):
            if '===' in s:
                if section[2] or section[1]:
                    sections.append(section)
                title = s.strip('#= ').strip().rstrip('.').strip()
                section = [title, '', []]
                block = []
                after_header = True
            else:
                block.append(s)
            continue
        m = re.match(r'^  ([A-Z0-9_]+):\s*(.*?)\s*$', line)
        if not m:
            continue
        name, rest = m.groups()
        inline = ''
        mv = re.match(r"^('(?:[^']|'')*'|\"[^\"]*\"|[^#]*?)\s*(?:#\s*(.*))?$", rest)
        value, inline = (mv.group(1).strip(), (mv.group(2) or '').strip()) if mv else (rest, '')
        if block:
            if after_header and not section[2] and not section[1]:
                # Comment right under the header: description of the section
                section[1] = join_comments(block)
            else:
                group_desc = join_comments(block)
            block = []
        after_header = False
        desc = ' '.join(d for d in (group_desc, inline) if d)
        section[2].append((name, value, desc))
    sections.append(section)
    return [s for s in sections if s[2]]


def parse_help(text):
    body = text[text.index('function display_help()'):]
    body = body[:body.index('\n    exit 0')]
    lines = []
    for line in body.splitlines():
        m = re.match(r'^\s*echo(?: -e)?(?: "(.*)")?\s*$', line)
        if m:
            lines.append(m.group(1) or '')
    txt = '\n'.join(lines)
    for var, rep in (('$settings_file', SETTINGS), ('$kibot_config', MAIN), ('$variant', '<variant>')):
        txt = txt.replace(var, rep)
    return txt.replace('\\"', '"')


def generate():
    problems = []
    out = ['# Configuration reference', '',
           '<!-- Generated by kibot_resources/scripts/gen_config_reference.py from the comments of the '
           'configuration files: do not edit, edit the comments and run the script. -->', '',
           'Every setting of the template, generated from the comments of the files (the CI checks it is up to '
           'date). The workflows are explained in [TEMPLATE_GUIDE.md](TEMPLATE_GUIDE.md).', '',
           '- [kibot_settings.yaml: pipeline settings](#kibot_settingsyaml-pipeline-settings)',
           '- [kibot_main.yaml definitions: KiBot outputs](#kibot_mainyaml-definitions-kibot-outputs)',
           '- [kibot_board.yaml: per board definitions](#kibot_boardyaml-per-board-definitions)',
           '- [Stackup profiles](#stackup-profiles)',
           '- [kibot_launch.sh / run_kibot.sh options](#kibot_launchsh--run_kibotsh-options)', '']

    # Settings
    out += ['## kibot_settings.yaml: pipeline settings', '',
            'Read by the launcher (`kibot_launch.sh`, `run_kibot.sh`, `run_kibot.ps1`) and the GitHub workflow. '
            'Flat `key: value` lines only: shell scripts parse this file.', '',
            '| Key | Default | Description |', '| --- | --- | --- |']
    for key, value, desc in parse_settings(read(SETTINGS)):
        if not desc:
            problems.append('{}: `{}` has no comment'.format(SETTINGS, key))
        out.append('| `{}` | `{}` | {} |'.format(key, cell(value) or ' ', cell(desc)))
    out += ['', 'Shared libraries: `lib_<VAR>_url`, `lib_<VAR>_path`, `lib_<VAR>_branch` (see the commented example '
            'in the file and *Shared libraries* in the guide).', '']

    # Definitions
    out += ['## kibot_main.yaml definitions: KiBot outputs', '',
            'The `definitions:` section at the end of `kibot_yaml/kibot_main.yaml`, used as `@NAME@` in the '
            'KiBot files. Change them in the file, for one run with `./run_kibot.sh -- -E NAME=value`, or for '
            'one board of a multi-board repository in its `kibot_board.yaml`.', '']
    for title, intro, entries in parse_definitions(read(MAIN)):
        internal = title in INTERNAL_SECTIONS
        out += ['### ' + title, '']
        if intro:
            out += [intro, '']
        if internal:
            if not intro:
                problems.append('{}: section `{}` has no description'.format(MAIN, title))
            out += ['Internal names, change them only with the files using them.', '',
                    ' · '.join('`{}` = `{}`'.format(n, cell(v)) for n, v, _ in entries), '']
            continue
        out += ['| Definition | Default | Description |', '| --- | --- | --- |']
        for name, value, desc in entries:
            if not desc:
                problems.append('{}: definition `{}` has no comment'.format(MAIN, name))
            out.append('| `{}` | `{}` | {} |'.format(name, cell(value) or "''", cell(desc)))
        out.append('')

    # Per board
    out += ['## kibot_board.yaml: per board definitions', '',
            'Multi-board repositories (`boards` setting): an optional `kibot_board.yaml` in a board folder '
            'overrides `kibot_main.yaml` definitions for that board, one `NAME: value` per line (no indentation), '
            'i.e. `BOARD_NAME: \'IO board\'`, `PANEL_ROWS: 3`. Written by '
            '`./run_kibot.sh --init --dir <board>`, read by the launcher (passed with `-E`).', '']

    # Stackup profiles
    keys = {}
    for f in sorted(glob.glob(os.path.join(ROOT, STACKUPS, '*.yaml'))):
        for m in re.finditer(r'^([a-z_]+):', read(os.path.relpath(f, ROOT)), re.M):
            keys.setdefault(m.group(1), []).append(os.path.splitext(os.path.basename(f))[0])
    out += ['## Stackup profiles', '',
            'Files of `kibot_resources/stackups`, applied with `./run_kibot.sh --stackup NAME` (see *Stackup and '
            'design rules* in the guide). Profiles: {}.'.format(', '.join('`{}`'.format(os.path.splitext(
                os.path.basename(f))[0]) for f in sorted(glob.glob(os.path.join(ROOT, STACKUPS, '*.yaml'))))), '',
            '| Entry | Description |', '| --- | --- |']
    for key, desc in PROFILE_KEYS.items():
        out.append('| `{}` | {} |'.format(key, cell(desc)))
    for key in sorted(set(keys) - set(PROFILE_KEYS)):
        problems.append('{}: profile entry `{}` ({}) not described in gen_config_reference.py'.format(
            STACKUPS, key, ', '.join(keys[key])))
    out.append('')

    # Launcher
    out += ['## kibot_launch.sh / run_kibot.sh options', '',
            '`run_kibot.sh` (Linux/macOS/WSL/Git Bash) and `run_kibot.ps1` (Windows) start the container and pass '
            'the options to `kibot_launch.sh`.', '', '```', parse_help(read(LAUNCHER)).rstrip(), '```', '']
    return '\n'.join(out), problems


def main():
    parser = argparse.ArgumentParser(description='Generates ' + OUTPUT)
    parser.add_argument('--check', action='store_true', help='Fail if the reference is outdated or incomplete')
    args = parser.parse_args()
    text, problems = generate()
    target = os.path.join(ROOT, OUTPUT)
    for p in problems:
        print('Undocumented: ' + p, file=sys.stderr)
    if args.check:
        current = open(target, encoding='utf-8').read() if os.path.isfile(target) else ''
        if current != text:
            print('{} is outdated: run python3 kibot_resources/scripts/gen_config_reference.py'.format(OUTPUT),
                  file=sys.stderr)
            return 1
        return 1 if problems else 0
    with open(target, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)
    print('{} written{}'.format(OUTPUT, ' ({} undocumented)'.format(len(problems)) if problems else ''))
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
