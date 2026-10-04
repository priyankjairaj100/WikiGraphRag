#!/usr/bin/env python3
"""Render v19 canonical manuscript to a working PDF and editable generic TeX.

The final manuscript is refused until all declared evidence hashes and numerical
table cells match public result files. This does not certify semantic claims.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def nested(value, keys, zero_if_absent=False):
    for index, key in enumerate(keys):
        if zero_if_absent and index == len(keys) - 1 and isinstance(value, dict) and key not in value:
            return 0
        value = value[key]
    return value


def validate(doc, draft=False):
    if doc['schema_version'] != 'reader_readiness_manuscript_v19':
        raise ValueError('Unexpected manuscript schema')
    if not draft and doc['status'] != 'completed_preparation_checkpoint':
        raise ValueError('Final publication requires terminal preparation outcomes')
    if doc['claims_scope'] != {'novelty': False, 'natural_qa_gain': False, 'full_xbrl_conformance': False, 'generally_strong_reader': False, 'browser_fidelity_verified': False}:
        raise ValueError('This working addendum must retain its declared limited scope')
    observed = {}
    for relative, expected in doc['evidence_bindings'].items():
        path = ROOT / relative
        if not path.resolve().is_relative_to(ROOT):
            raise ValueError('Evidence path must stay in the repository')
        if sha(path) != expected:
            raise ValueError('Evidence digest changed: ' + relative)
        if path.suffix == '.json':
            observed[relative] = json.loads(path.read_text())
    bound_cells = set()
    for binding in doc.get('cell_bindings', []):
        cell = (binding['table'], binding['row'], binding['column'])
        if cell in bound_cells:
            raise ValueError('Duplicate table-cell binding: ' + str(cell))
        bound_cells.add(cell)
        if 'components' in binding:
            terms = [nested(observed[c['source']], c['keys'], c.get('zero_if_absent', False))
                     for c in binding['components']]
            expected = binding['format'].format(*terms)
        else:
            expected = str(nested(observed[binding['source']], binding['keys'], binding.get('zero_if_absent', False)))
        actual = str(doc['tables'][binding['table']]['rows'][binding['row']][binding['column']])
        if actual != expected:
            raise ValueError('Printed numerical table differs from bound result: ' + str(binding))
    for name, table in doc['tables'].items():
        for row_index, row in enumerate(table['rows']):
            for column_index, value in enumerate(row):
                if re.fullmatch(r'-?\d+(?:\.\d+)?', str(value)) and (name, row_index, column_index) not in bound_cells:
                    raise ValueError('Unbound numerical table cell: ' + str((name, row_index, column_index)))
    for execution in doc.get('execution_bindings', []):
        result = observed[execution['source']]
        actual = nested(result, execution.get('status_keys', ['status']))
        if actual != execution['expected_status']:
            raise ValueError('Execution status differs from its stated outcome')
    for required in doc.get('terminal_requirements', []):
        if not draft and not required['terminal']:
            raise ValueError('Final manuscript requires terminal evidence: ' + required['name'])
    if not draft:
        if not doc.get('cell_bindings'):
            raise ValueError('Final manuscript needs explicit numerical table bindings')
        printable = json.dumps({k: doc[k] for k in ('abstract', 'sections', 'tables')}).lower()
        if 'pending' in printable or 'tbd' in printable:
            raise ValueError('Final manuscript contains unfinished text')
    return {'status': 'draft' if draft else 'passed', 'evidence_hashes_checked': len(doc['evidence_bindings']),
            'table_cells_checked': len(doc.get('cell_bindings', [])),
            'execution_outcomes_checked': len(doc.get('execution_bindings', [])),
            'scope': 'File identity and declared table-cell agreement only; not semantic recertification.'}


def tex_escape(text):
    chars = {'\\': r'\textbackslash{}', '&': r'\&', '%': r'\%', '$': r'\$', '#': r'\#',
             '_': r'\_', '{': r'\{', '}': r'\}', '~': r'\textasciitilde{}', '^': r'\textasciicircum{}'}
    return ''.join(chars.get(c, c) for c in str(text))


def export_tex(doc):
    lines = [r'\documentclass[10pt,a4paper]{article}', r'\usepackage[margin=20mm]{geometry}',
             r'\usepackage{fontspec}', r'\setmainfont{DejaVu Serif}', r'\usepackage{longtable,booktabs,array}',
             r'\usepackage[hidelinks]{hyperref}', r'\setlength{\parindent}{0pt}',
             r'\setlength{\parskip}{6pt}', r'\begin{document}',
             r'\begin{center}{\Large\bfseries ' + tex_escape(doc['title']) + r'}\par',
             tex_escape(doc['subtitle']) + r'\par', tex_escape(doc['date']) + r'\end{center}',
             r'\textit{Working addendum. Generic TeX source; ACL template and compilation are not verified.}',
             r'\section*{Abstract}', tex_escape(doc['abstract'])]
    for section in doc['sections']:
        if section.get('page_break_before'):
            lines.append(r'\clearpage')
        lines.append(r'\section*{' + tex_escape(section['title']) + '}')
        lines.extend(tex_escape(p) + '\n' for p in section.get('paragraphs', []))
        for name in section.get('tables', []):
            table = doc['tables'][name]
            spec = ''.join('p{' + str(round(width / 489 * .94, 4)) + r'\textwidth}' for width in table['widths'])
            lines.append(r'\begin{longtable}{@{}' + spec + r'@{}}\toprule')
            lines.append(' & '.join(r'\textbf{' + tex_escape(v) + '}' for v in table['header']) + r' \\ \midrule\endhead')
            lines.extend(' & '.join(tex_escape(v) for v in row) + r' \\' for row in table['rows'])
            lines.extend([r'\bottomrule\end{longtable}', r'{\small ' + tex_escape(table['caption']) + '}'])
        lines.extend(tex_escape(p) + '\n' for p in section.get('after_tables', []))
    if doc.get('references'):
        lines.append(r'\section*{References}')
        for reference in doc['references']:
            lines.append(tex_escape(reference['text']) + ' ' + r'\url{' + reference['url'] + '}\n')
    lines.append(r'\end{document}')
    return '\n'.join(lines) + '\n'


def render(source, output, tex, manifest, draft=False):
    doc = json.loads(source.read_text())
    validation = validate(doc, draft)
    font_dir = Path('/usr/share/fonts/truetype/dejavu')
    fonts = {'TypedSerif': font_dir / 'DejaVuSerif.ttf', 'TypedSerifBold': font_dir / 'DejaVuSerif-Bold.ttf'}
    for name, path in fonts.items():
        pdfmetrics.registerFont(TTFont(name, str(path)))
    styles = getSampleStyleSheet()
    for name, args in {
        'TypedTitle': dict(fontName='TypedSerifBold', fontSize=18, leading=22, alignment=TA_CENTER, spaceAfter=10),
        'TypedSub': dict(fontSize=10, leading=13, alignment=TA_CENTER, spaceAfter=8),
        'TypedBody': dict(fontSize=10.4, leading=14.1, spaceAfter=9),
        'TypedHead': dict(fontName='TypedSerifBold', fontSize=12.5, leading=16, spaceBefore=5, spaceAfter=8, keepWithNext=True),
        'TypedSmall': dict(fontSize=8.7, leading=11.5, spaceAfter=7),
        'TypedCell': dict(fontSize=9.1, leading=11.8, splitLongWords=True),
        'TypedCellHead': dict(fontName='TypedSerifBold', fontSize=9.1, leading=11.8),
    }.items():
        styles.add(ParagraphStyle(name, fontName=args.pop('fontName', 'TypedSerif'), **args))

    def p(text, style='TypedBody'):
        return Paragraph(html.escape(str(text)), styles[style])

    story = [p(doc['title'], 'TypedTitle'), p(doc['subtitle'], 'TypedSub'), p(doc['date'], 'TypedSub'),
             p('Working addendum | preparation and failure analysis | no natural QA evaluation', 'TypedSmall'),
             p('Abstract', 'TypedHead'), p(doc['abstract'])]
    for section in doc['sections']:
        if section.get('page_break_before'):
            story.append(PageBreak())
        story.append(p(section['title'], 'TypedHead'))
        story.extend(p(t) for t in section.get('paragraphs', []))
        for name in section.get('tables', []):
            spec = doc['tables'][name]
            if abs(sum(spec['widths']) - 489) > .01:
                raise ValueError('Table widths must sum to 489: ' + name)
            if any(len(row) != len(spec['header']) for row in spec['rows']):
                raise ValueError('Ragged table: ' + name)
            rows = [[p(v, 'TypedCellHead') for v in spec['header']]] + [[p(v, 'TypedCell') for v in row] for row in spec['rows']]
            table = Table(rows, colWidths=spec['widths'], repeatRows=1, hAlign='LEFT')
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#eaf0f5')),
                ('LINEABOVE', (0, 0), (-1, 0), .7, colors.HexColor('#334c60')),
                ('LINEBELOW', (0, 0), (-1, 0), .4, colors.HexColor('#8194a4')),
                ('LINEBELOW', (0, -1), (-1, -1), .6, colors.HexColor('#334c60')),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ]))
            story.append(KeepTogether([table, Spacer(1, 5), p(spec['caption'], 'TypedSmall')]))
        story.extend(p(t) for t in section.get('after_tables', []))

    if doc.get('references'):
        story.append(p('References', 'TypedHead'))
        for reference in doc['references']:
            story.append(p(reference['text'] + ' ' + reference['url'], 'TypedSmall'))

    def footer(c, d):
        c.saveState()
        c.setStrokeColor(colors.HexColor('#c0c8d0'))
        c.line(53, 39, A4[0] - 53, 39)
        c.setFont('TypedSerif', 8)
        c.drawString(53, 27, 'WikiGraphRAG | v19 reader readiness | ' + ('Draft' if draft else 'Preparation checkpoint'))
        c.drawRightString(A4[0] - 53, 27, str(d.page))
        c.restoreState()

    def stable_canvas(*args, **kwargs):
        kwargs['invariant'] = 1
        return canvas.Canvas(*args, **kwargs)

    pdf = SimpleDocTemplate(str(output), pagesize=A4, leftMargin=53, rightMargin=53, topMargin=45, bottomMargin=53,
                            title=doc['title'], author='WikiGraphRAG research project')
    pdf.build(story, onFirstPage=footer, onLaterPages=footer, canvasmaker=stable_canvas)
    tex.write_text(export_tex(doc))
    result = {'schema_version': 'reader_readiness_render_v19', 'source_path': str(source), 'source_sha256': sha(source),
              'renderer_sha256': sha(Path(__file__)), 'pdf_path': str(output), 'pdf_sha256': sha(output),
              'tex_path': str(tex), 'tex_sha256': sha(tex), 'page_count': pdf.page,
              'embedded_fonts': {name: sha(path) for name, path in fonts.items()}, 'validation': validation,
              'pdf_engine': 'ReportLab', 'tex_compiled': False, 'acl_template_verified': False,
              'visual_qa': 'Separate rendered-page inspection required', 'evidence_bindings': doc['evidence_bindings']}
    manifest.write_text(json.dumps(result, indent=2) + '\n')
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source', type=Path, default=ROOT / 'paper/reader_readiness_update_v19.json')
    ap.add_argument('--output', type=Path, default=ROOT / 'paper/reader_readiness_update_v19.pdf')
    ap.add_argument('--tex', type=Path, default=ROOT / 'paper/reader_readiness_update_v19.tex')
    ap.add_argument('--manifest', type=Path, default=ROOT / 'paper/reader_readiness_render_manifest_v19.json')
    ap.add_argument('--draft', action='store_true')
    ap.add_argument('--validate-only', action='store_true')
    ap.add_argument('--tex-only', action='store_true')
    args = ap.parse_args()
    if args.validate_only or args.tex_only:
        doc = json.loads(args.source.read_text())
        validation = validate(doc, args.draft)
        if args.tex_only:
            args.tex.write_text(export_tex(doc))
        print(json.dumps(validation))
        return
    print(json.dumps(render(args.source, args.output, args.tex, args.manifest, args.draft)))


if __name__ == '__main__':
    main()
