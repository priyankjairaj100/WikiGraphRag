"""Render one canonical working manuscript to editable TeX and a readable PDF.

The PDF uses ReportLab; it is not a compilation or validation of an ACL template.
Result tables are loaded from the frozen descriptive summary, never hand copied.
"""
import hashlib
import html
import json
import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfgen import canvas
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether, PageBreak

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tex_escape(text):
    mapping = {'\\': r'\textbackslash{}', '&': r'\&', '%': r'\%', '$': r'\$',
               '#': r'\#', '_': r'\_', '{': r'\{', '}': r'\}', '~': r'\textasciitilde{}',
               '^': r'\textasciicircum{}'}
    return ''.join(mapping.get(ch, ch) for ch in text)


def main():
    source = ROOT / 'paper/paired_reading_study_v13.json'
    summary_path = ROOT / 'results/paired_study_summary_v13.json'
    interpretation_path = ROOT / 'paper/layout_interpretation_v13.txt'
    doc = json.loads(source.read_text())
    summary = json.loads(summary_path.read_text())
    assert summary['native_response_count'] == 32
    cells = {(r['batch'], r['condition']): r for r in summary['aggregation']}
    assert cells[('main', 'ordinary')]['joint_content_scope_citation'] == cells[('main', 'paired')]['joint_content_scope_citation']
    substitutions = {'main_joint': str(cells[('main', 'ordinary')]['joint_content_scope_citation']),
                     'layout_ordinary_joint': str(cells[('layout', 'ordinary')]['joint_content_scope_citation']),
                     'layout_paired_joint': str(cells[('layout', 'paired')]['joint_content_scope_citation']),
                     'layout_interpretation': interpretation_path.read_text().strip()}

    def replace(value):
        if isinstance(value, str):
            value = re.sub(r'\{\{(\w+)\}\}', lambda m: substitutions[m.group(1)], value)
            assert '{{' not in value
        elif isinstance(value, list):
            value = [replace(v) for v in value]
        elif isinstance(value, dict):
            value = {k: replace(v) for k, v in value.items()}
        return value

    doc = replace(doc)
    tables = {
        'outcomes': {'caption': 'Table 1. Descriptive answer counts, each out of 8. Joint requires content, scope and citation support. Format is reported separately.',
                     'header': ['Rendering', 'Retrieval', 'Content', 'Scope', 'Citation', 'Format', 'Joint'],
                     'rows': [], 'widths': [85, 83, 65, 60, 66, 65, 65]},
        'support': {'caption': 'Table 2. Context-only evidence sufficiency, each out of 8. Layout improves one financial period binding but does not change case-level completeness.',
                    'header': ['Rendering', 'Retrieval', 'Complete', 'Partial', 'Absent'],
                    'rows': [], 'widths': [108, 108, 91, 91, 91]},
        'execution': {'caption': 'Table 3. Native execution. Wall time includes setup and verification and is descriptive, not an efficiency comparison. Two short competence-control calls are additional.',
                      'header': ['Batch', 'Calls', 'Input tokens', 'Output tokens', 'Wall minutes'],
                      'rows': [], 'widths': [86, 57, 123, 111, 112]}}
    for batch in ('main', 'layout'):
        for condition in ('ordinary', 'paired'):
            r = cells[(batch, condition)]
            label = 'Normalized' if batch == 'main' else 'Source layout'
            tables['outcomes']['rows'].append([label, condition.title()] + [str(r[k]) for k in ('reference_content_correct', 'scope_correct', 'citation_grounded', 'format_ok', 'joint_content_scope_citation')])
            tables['support']['rows'].append([label, condition.title()] + [str(r['context_support_counts'].get(k, 0)) for k in ('complete', 'partial', 'absent')])
        ex = summary['execution'][batch]
        tables['execution']['rows'].append([batch.title(), str(ex['responses']), '-'.join(map(str, ex['input_tokens_range'])), '-'.join(map(str, ex['generated_tokens_range'])), f"{ex['wall_seconds']/60:.2f}"])
    doc['result_tables'] = tables
    resolved = ROOT / 'paper/paired_reading_study_v13.resolved.json'
    resolved.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + '\n')

    tex = [r'\documentclass[11pt]{article}', r'\usepackage[utf8]{inputenc}', r'\usepackage[T1]{fontenc}',
           r'\usepackage[margin=1in]{geometry}', r'\usepackage{booktabs}', r'\usepackage{hyperref}',
           r'\title{' + tex_escape(doc['title']) + r'\\\large ' + tex_escape(doc['subtitle']) + '}',
           r'\author{' + tex_escape(doc['author']) + '}', r'\date{' + tex_escape(doc['date']) + '}',
           r'\begin{document}', r'\maketitle', r'\noindent\textit{' + tex_escape(doc['status']) + '}',
           r'\begin{abstract}', tex_escape(doc['abstract']), r'\end{abstract}']
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle('PaperBody', fontName='Times-Roman', fontSize=10.5, leading=14.1, spaceAfter=8))
    styles.add(ParagraphStyle('PaperTitle', fontName='Times-Bold', fontSize=19, leading=22, alignment=TA_CENTER, spaceAfter=9))
    styles.add(ParagraphStyle('PaperSubtitle', fontName='Times-Roman', fontSize=11, leading=14, alignment=TA_CENTER, spaceAfter=9))
    styles.add(ParagraphStyle('PaperHead', fontName='Times-Bold', fontSize=12.5, leading=16, spaceBefore=11, spaceAfter=6, keepWithNext=True))
    styles.add(ParagraphStyle('PaperSmall', fontName='Times-Roman', fontSize=8.5, leading=11, spaceAfter=7))
    styles.add(ParagraphStyle('PaperCell', fontName='Times-Roman', fontSize=8.5, leading=10.5))
    styles.add(ParagraphStyle('PaperCellHead', fontName='Times-Bold', fontSize=8.5, leading=10.5))

    def p(value, style='PaperBody'):
        return Paragraph(html.escape(value), styles[style])

    story = [p(doc['title'], 'PaperTitle'), p(doc['subtitle'], 'PaperSubtitle'),
             p(doc['author'] + ' | ' + doc['date'], 'PaperSubtitle'), p(doc['status'], 'PaperSmall'),
             p('Abstract', 'PaperHead'), p(doc['abstract'])]
    for section in doc['sections']:
        tex.extend([r'\section*{' + tex_escape(section['title']) + '}'])
        story.append(p(section['title'], 'PaperHead'))
        for paragraph in section['paragraphs']:
            tex.extend([tex_escape(paragraph), ''])
            story.append(p(paragraph))
        for key in section.get('tables', []):
            table = tables[key]
            spec = '@{}' + 'l' * len(table['header']) + '@{}'
            tex.extend([r'\begin{table}[ht]', r'\centering\small', r'\begin{tabular}{' + spec + '}', r'\toprule',
                        ' & '.join(map(tex_escape, table['header'])) + r' \\', r'\midrule'])
            for row in table['rows']:
                tex.append(' & '.join(map(tex_escape, row)) + r' \\')
            tex.extend([r'\bottomrule', r'\end{tabular}', r'\caption*{' + tex_escape(table['caption']) + '}', r'\end{table}'])
            rendered = Table([[p(c, 'PaperCellHead') for c in table['header']]] + [[p(c, 'PaperCell') for c in row] for row in table['rows']],
                             colWidths=table['widths'], repeatRows=1, hAlign='LEFT')
            rendered.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#eef1f4')),
                                          ('LINEABOVE', (0, 0), (-1, 0), .7, colors.HexColor('#344454')),
                                          ('LINEBELOW', (0, 0), (-1, 0), .4, colors.grey),
                                          ('LINEBELOW', (0, -1), (-1, -1), .6, colors.HexColor('#344454')),
                                          ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                                          ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5)]))
            story.append(KeepTogether([Spacer(1, 6), rendered, Spacer(1, 5), p(table['caption'], 'PaperSmall')]))
        for paragraph in section.get('after_tables', []):
            tex.extend([tex_escape(paragraph), ''])
            story.append(p(paragraph))
    tex.extend([r'\clearpage', r'\section*{References}', r'\begin{sloppypar}'])
    story.append(PageBreak())
    story.append(p('References', 'PaperHead'))
    for ref in doc['references']:
        tex.extend([r'\noindent ' + tex_escape(ref) + r'\par\medskip'])
        story.append(p(ref, 'PaperSmall'))
    tex.extend([r'\end{sloppypar}', r'\end{document}'])
    tex_path = ROOT / 'paper/paired_reading_study_v13.tex'
    # caption* is supplied by caption; no claim that local TeX has compiled.
    tex.insert(6, r'\usepackage{caption}')
    tex_path.write_text('\n'.join(tex) + '\n')
    pdf_path = ROOT / 'paper/paired_reading_study_v13.pdf'

    def footer(c, d):
        c.saveState()
        c.setStrokeColor(colors.HexColor('#c0c8d0'))
        c.line(53, 39, A4[0] - 53, 39)
        c.setFont('Times-Roman', 8)
        c.drawString(53, 27, 'WikiGraphRAG | Development study v0.13 | 3 October 2026')
        c.drawRightString(A4[0] - 53, 27, str(d.page))
        c.restoreState()

    pdf = SimpleDocTemplate(str(pdf_path), pagesize=A4, leftMargin=53, rightMargin=53,
                            topMargin=45, bottomMargin=53, title=doc['title'], author=doc['author'])
    def deterministic_canvas(*args, **kwargs):
        kwargs['invariant'] = 1
        return canvas.Canvas(*args, **kwargs)

    pdf.build(story, onFirstPage=footer, onLaterPages=footer,
              canvasmaker=deterministic_canvas)
    manifest = {'schema_version': 'paired_paper_render_v0.13',
                'input_sha256': {str(p.relative_to(ROOT)): sha(p) for p in (source, summary_path, interpretation_path, Path(__file__))},
                'output_sha256': {str(p.relative_to(ROOT)): sha(p) for p in (resolved, tex_path, pdf_path)},
                'pdf_engine': 'ReportLab', 'tex_compiled': False, 'acl_template_verified': False,
                'semantic_source_shared': True, 'visual_qa': 'Separate results/paper_visual_qa_v13.json records actual page review.'}
    (ROOT / 'paper/render_manifest_v13.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({'pdf': str(pdf_path), 'tex': str(tex_path), 'result_table_count': len(tables)}))


if __name__ == '__main__':
    main()
