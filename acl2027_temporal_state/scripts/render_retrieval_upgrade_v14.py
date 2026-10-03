#!/usr/bin/env python3
"""Render the standalone v14 manuscript addendum without touching frozen v13.

This is a readable ReportLab working document, not an ACL template compilation.
The JSON remains the canonical text and table source. Pending results are
rendered explicitly and never replaced by guessed counts or automatic lookup.
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
    TableStyle,
)

from render_paired_paper_v13 import sha

ROOT = Path(__file__).resolve().parents[1]


def validate_observed_tables(doc: dict) -> dict:
    """Reject stale hand-edited count tables against the frozen summaries."""
    for relative, expected in doc['result_bindings'].items():
        if sha(ROOT / relative) != expected:
            raise ValueError('Observed result binding changed: ' + relative)
    main = json.loads((ROOT / 'results/structured_study_summary_v14.json').read_text())
    dense = json.loads((ROOT / 'results/dense_source_support_summary_v14.json').read_text())
    if main['status'] != 'completed_descriptive_development' or main['native_response_count'] != 32:
        raise ValueError('Main summary is not the completed bounded study.')
    if dense['status'] != 'complete_consistent_source_support':
        raise ValueError('Dense support review is incomplete/inconsistent.')
    fields = ('reference_content_correct', 'scope_correct', 'citation_grounded',
              'format_ok', 'appropriate_abstention', 'joint_content_scope_citation',
              'native_output_budget_failure')
    main_rows = [[r['condition'].title()] + [str(r[k]) for k in fields]
                 for r in main['aggregation']]
    context_rows = [[r['condition'].title()] +
                    [str(r['context_support_counts'].get(k, 0))
                     for k in ('complete', 'partial', 'absent')] +
                    [str(r['dropped_seeds']), '-'.join(map(str, r['input_token_range']))]
                    for r in main['aggregation']]
    conditions = ('bm25_paired', 'bm25_closure', 'dense_paired', 'dense_closure',
                  'rrf_paired', 'rrf_closure')
    labels = ('BM25 paired', 'BM25 closure', 'Dense paired', 'Dense closure',
              'RRF paired', 'RRF closure')
    dense_rows = []
    for condition, label in zip(conditions, labels):
        row = dense['conditions'][condition]
        dense_rows.append([label] + [str(row['contexts'][k])
                          for k in ('complete', 'partial', 'absent')] +
                          [str(row['required_facts']['supported']) + '/21',
                           str(row['native_input_tokens']['minimum']) + '-' +
                           str(row['native_input_tokens']['maximum'])])
    for name, expected in (('outcomes', main_rows), ('context_support', context_rows),
                           ('dense_support', dense_rows)):
        if doc['tables'][name]['rows'] != expected:
            raise ValueError('Manuscript table differs from frozen summary: ' + name)
    if doc['results_state']['main_outcomes'] != main['aggregation']:
        raise ValueError('Manuscript structured outcomes differ from frozen summary.')
    if 'pending' in json.dumps(doc).lower():
        raise ValueError('Completed manuscript contains a stale pending statement.')
    return {'status': 'passed', 'result_table_checks': 3,
            'source_hashes_checked': len(doc['result_bindings']),
            'scope': 'Identity and exact count-table agreement, not semantic recertification'}


def render(source: Path, output: Path, manifest: Path | None = None) -> dict:
    doc = json.loads(source.read_text())
    if doc['schema_version'] != 'retrieval_reader_upgrade_manuscript_v0.14':
        raise ValueError('Unexpected manuscript schema.')
    if output.resolve() == (ROOT / 'paper/paired_reading_study_v13.pdf').resolve():
        raise ValueError('Frozen v13 cannot be replaced.')
    if doc['results_state']['status'] == 'pending':
        if doc['results_state']['any_v14_performance_claim_authorized']:
            raise ValueError('Pending results cannot authorize a performance claim.')
        if 'pending' not in doc['status'].lower():
            raise ValueError('Pending status must be visible in the document.')
    elif doc['results_state']['status'] not in ('observed', 'gate_failed'):
        raise ValueError('Unknown result status; do not silently infer completion.')
    table_validation = (validate_observed_tables(doc)
                        if doc['results_state']['status'] == 'observed' else None)

    # Embed fonts: Base-14 substitution can break word spacing in PDF previews.
    font_dir = Path('/usr/share/fonts/truetype/dejavu')
    font_files = {'UpgradeSerif': font_dir / 'DejaVuSerif.ttf',
                  'UpgradeSerifBold': font_dir / 'DejaVuSerif-Bold.ttf'}
    for name, path in font_files.items():
        if not path.is_file():
            raise FileNotFoundError('Install DejaVu Serif fonts before rendering: ' + str(path))
        pdfmetrics.registerFont(TTFont(name, str(path)))

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle('UpgradeTitle', fontName='UpgradeSerifBold', fontSize=18,
                              leading=21, alignment=TA_CENTER, spaceAfter=9))
    styles.add(ParagraphStyle('UpgradeSub', fontName='UpgradeSerif', fontSize=10.5,
                              leading=13.1, alignment=TA_CENTER, spaceAfter=8))
    styles.add(ParagraphStyle('UpgradeBody', fontName='UpgradeSerif', fontSize=10.5,
                              leading=14, spaceAfter=8))
    styles.add(ParagraphStyle('UpgradeHead', fontName='UpgradeSerifBold', fontSize=12.5,
                              leading=16, spaceBefore=7, spaceAfter=8,
                              keepWithNext=True))
    styles.add(ParagraphStyle('UpgradeSmall', fontName='UpgradeSerif', fontSize=8.5,
                              leading=11.2, spaceAfter=7))
    styles.add(ParagraphStyle('UpgradeCell', fontName='UpgradeSerif', fontSize=9.1,
                              leading=11.4, splitLongWords=True))
    styles.add(ParagraphStyle('UpgradeCellHead', fontName='UpgradeSerifBold', fontSize=9.1,
                              leading=11.4))

    def p(value, style='UpgradeBody'):
        return Paragraph(html.escape(str(value)), styles[style])

    story = [p(doc['title'], 'UpgradeTitle'), p(doc['subtitle'], 'UpgradeSub'),
             p(doc['author'] + ' | ' + doc['date'], 'UpgradeSub'),
             p(doc['status'], 'UpgradeSmall'), p('Abstract', 'UpgradeHead'),
             p(doc['abstract'])]
    for section in doc['sections']:
        if section.get('page_break_before'):
            story.append(PageBreak())
        story.append(p(section['title'], 'UpgradeHead'))
        story.extend(p(value) for value in section.get('paragraphs', []))
        for key in section.get('tables', []):
            spec = doc['tables'][key]
            widths = spec['widths']
            if abs(sum(widths) - 489) > .01:
                raise ValueError('Table width must match the 489-point text frame.')
            if any(len(row) != len(spec['header']) for row in spec['rows']):
                raise ValueError('Ragged table: ' + key)
            rows = [[p(value, 'UpgradeCellHead') for value in spec['header']]]
            for idx, row in enumerate(spec['rows']):
                cells = [p(value, 'UpgradeCell') for value in row]
                if key == 'prior' and idx < len(doc['reference_sources']):
                    url = html.escape(doc['reference_sources'][idx]['url'], quote=True)
                    cells[0] = Paragraph('<link href="' + url + '" color="#204a70">' +
                                         html.escape(str(row[0])) + '</link>', styles['UpgradeCell'])
                rows.append(cells)
            table = Table(rows, colWidths=widths, repeatRows=1, hAlign='LEFT')
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#eef1f4')),
                ('LINEABOVE', (0, 0), (-1, 0), .7, colors.HexColor('#344454')),
                ('LINEBELOW', (0, 0), (-1, 0), .4, colors.grey),
                ('LINEBELOW', (0, -1), (-1, -1), .6, colors.HexColor('#344454')),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('TOPPADDING', (0, 0), (-1, -1), 5),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ]))
            story.append(KeepTogether([Spacer(1, 3), table, Spacer(1, 5),
                                       p(spec['caption'], 'UpgradeSmall')]))
        story.extend(p(value) for value in section.get('after_tables', []))

    def footer(c, d):
        c.saveState()
        c.setStrokeColor(colors.HexColor('#c0c8d0'))
        c.line(53, 39, A4[0] - 53, 39)
        c.setFont('UpgradeSerif', 8)
        c.drawString(53, 27, 'WikiGraphRAG | v0.14 development addendum | ' +
                     doc['results_state']['status'].replace('_', ' ').title())
        c.drawRightString(A4[0] - 53, 27, str(d.page))
        c.restoreState()

    def deterministic_canvas(*args, **kwargs):
        kwargs['invariant'] = 1
        return canvas.Canvas(*args, **kwargs)

    output.parent.mkdir(parents=True, exist_ok=True)
    pdf = SimpleDocTemplate(str(output), pagesize=A4, leftMargin=53, rightMargin=53,
                            topMargin=44, bottomMargin=53,
                            title=doc['title'], author=doc['author'])
    pdf.build(story, onFirstPage=footer, onLaterPages=footer,
              canvasmaker=deterministic_canvas)
    result = {
        'schema_version': 'retrieval_upgrade_render_v0.14',
        'source_path': str(source.resolve()), 'source_sha256': sha(source),
        'renderer_sha256': sha(Path(__file__)),
        'v13_shared_helper_sha256': sha(ROOT / 'scripts/render_paired_paper_v13.py'),
        'embedded_font_sha256': {name: sha(path) for name, path in font_files.items()},
        'pdf_path': str(output.resolve()), 'pdf_sha256': sha(output),
        'page_count': pdf.page,
        'results_status': doc['results_state']['status'],
        'result_table_validation': table_validation,
        'result_bindings': doc.get('result_bindings', {}),
        'pdf_engine': 'ReportLab', 'acl_template_verified': False,
        'visual_qa': 'Not performed by this renderer; inspect rendered pages separately.',
    }
    if manifest:
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps(result, indent=2) + '\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path,
                        default=ROOT / 'paper/retrieval_reader_upgrade_v14.json')
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'paper/retrieval_reader_upgrade_v14.pdf')
    parser.add_argument('--manifest', type=Path)
    args = parser.parse_args()
    print(json.dumps(render(args.source, args.output, args.manifest)))


if __name__ == '__main__':
    main()
