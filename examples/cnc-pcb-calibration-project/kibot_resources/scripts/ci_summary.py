#!/usr/bin/env python3
"""
Writes a Markdown summary of a KiBot run (CI job summary and PR comment):
status, ERC/DRC results from the Reports folder and the generated documents.

Usage: ci_summary.py --variant V [--status success|failure] [--revision R]
                     [--engine E] [--link-base URL] [--artifacts-url URL] [-o FILE]
                     [--boards "hw/main hw/io"] [--append FILE.md]...
"""
import argparse
import collections
import glob
import os
import re
import sys

MARKER = '<!-- kibot-summary -->'
DOCS = (('Schematic', 'Schematic/*.pdf'),
        ('PCB routing', 'PCB/*.pdf'),
        ('Gerber layers', 'Manufacturing/Fabrication/*-gerbers*.pdf'),
        ('Fabrication document', 'Manufacturing/Fabrication/*-fabrication*.pdf'),
        ('Assembly document', 'Manufacturing/Assembly/*-assembly*.pdf'),
        ('Fabrication ZIP', 'Manufacturing/Fabrication/*.zip'),
        ('JLCPCB files', 'Manufacturing/JLCPCB/*'),
        ('Bill of Materials', 'Manufacturing/Assembly/*-bom.*'),
        ('Interactive BoM', 'Manufacturing/Assembly/*-ibom.html'),
        ('3D model', '3D/*.step'),
        ('3D renders', 'Images/*.png'))


def parse_report(fname):
    """ Counts the errors/warnings of a KiCad ERC/DRC report, by type """
    errors, warnings = collections.Counter(), collections.Counter()
    last = None
    unconnected = 0
    with open(fname, encoding='utf-8', errors='replace') as f:
        for line in f:
            m = re.match(r'^\[(\w+)\]:', line)
            if m:
                last = m.group(1)
                continue
            if last and re.search(r';\s*error\s*$', line):
                errors[last] += 1
                last = None
            elif last and re.search(r';\s*warning\s*$', line):
                warnings[last] += 1
                last = None
            m = re.match(r'^\*\* Found (\d+) unconnected', line)
            if m:
                unconnected = int(m.group(1))
    return errors, warnings, unconnected


def link(path, base):
    path = os.path.normpath(path)
    name = os.path.basename(path)
    if base:
        return '[{}]({}/{})'.format(name, base.rstrip('/'), path.replace(os.sep, '/').replace(' ', '%20'))
    return '`{}`'.format(name)


def board_summary(board, args):
    """ ERC/DRC and documents of one board (its output folder) """
    def files(pattern):
        return sorted(f for f in glob.glob(os.path.join(board, pattern)) if os.path.isfile(f))

    out = []
    reports = files('Reports/*-erc.rpt') + files('Reports/*-drc.rpt')
    if reports:
        out += ['### Electrical and design rules checks', '',
                '| Check | Errors | Warnings | Report |', '| --- | --- | --- | --- |']
        details = []
        for rpt in reports:
            kind = 'ERC' if rpt.endswith('-erc.rpt') else 'DRC'
            errors, warnings, unconnected = parse_report(rpt)
            n_err = sum(errors.values()) + unconnected
            html = rpt[:-4] + '.html'
            out.append('| {} | {}{} | {} | {} |'.format(kind, n_err, ' ❌' if n_err else '', sum(warnings.values()),
                                                       link(html if os.path.isfile(html) else rpt, args.link_base)))
            if unconnected:
                errors['unconnected_items'] += unconnected
            if errors or warnings:
                details.append('**{}**: '.format(kind) + ', '.join(
                    ['{} × `{}` (error)'.format(n, t) for t, n in errors.most_common(8)] +
                    ['{} × `{}`'.format(n, t) for t, n in warnings.most_common(8)]))
        if details:
            out += ['', '<details><summary>Issues by type</summary>', ''] + details + ['', '</details>']
        out.append('')
    else:
        out += ['ERC/DRC not run for this status ({}).'.format(args.variant), '']

    docs = []
    for label, pattern in DOCS:
        found = files(pattern)
        if found:
            docs.append('| {} | {} |'.format(label, ' · '.join(link(f, args.link_base)
                                                                 for f in found[:6])))
    if docs:
        out += ['### Documents', '', '| Document | Files |', '| --- | --- |'] + docs + ['']
    return out


def main():
    parser = argparse.ArgumentParser(description='Markdown summary of a KiBot run')
    parser.add_argument('--variant', required=True)
    parser.add_argument('--status', default='success')
    parser.add_argument('--revision', default='')
    parser.add_argument('--engine', default='')
    parser.add_argument('--link-base', default='', help='URL prefix to link the files (i.e. outputs branch)')
    parser.add_argument('--artifacts-url', default='', help='URL of the run artifacts')
    parser.add_argument('-o', '--output', help='Output file (default: stdout)')
    parser.add_argument('--append', action='append', default=[], help='Markdown file(s) to append, if they exist')
    parser.add_argument('--boards', default='', help='Multi-board: folders of the boards (space-separated)')
    args = parser.parse_args()

    ok = args.status == 'success'
    out = [MARKER, '## {} KiBot: {}'.format('✅' if ok else '❌', args.variant), '']
    info = [('Status', 'success' if ok else '**' + args.status + '**')]
    if args.revision:
        info.append(('Revision', args.revision))
    if args.engine:
        info.append(('3D renders', args.engine))
    if args.artifacts_url:
        info.append(('Artifacts', '[outputs and logs]({})'.format(args.artifacts_url)))
    out += ['| | |', '| --- | --- |'] + ['| {} | {} |'.format(k, v) for k, v in info] + ['']

    boards = args.boards.replace(',', ' ').split() or ['.']
    for board in boards:
        if len(boards) > 1:
            out += ['## Board `{}`'.format(board), '']
        out += board_summary(board, args)

    for extra in args.append:
        if os.path.isfile(extra):
            with open(extra, encoding='utf-8') as f:
                out += ['', f.read()]
    text = '\n'.join(out) + '\n'
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(text)
    else:
        sys.stdout.buffer.write(text.encode('utf-8'))


if __name__ == '__main__':
    main()
