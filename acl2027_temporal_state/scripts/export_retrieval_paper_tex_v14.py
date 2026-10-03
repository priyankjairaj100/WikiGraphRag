#!/usr/bin/env python3
"""Export the canonical working-paper JSON to editable, standalone LaTeX.

This uses a generic article class; it is not an ACL template or a compilation.
The readable PDF is rendered separately by render_retrieval_upgrade_v14.py.
"""
import argparse
import hashlib
import json
from pathlib import Path


def escape(value):
    table = {'\\': r'\textbackslash{}', '&': r'\&', '%': r'\%',
             '$': r'\$', '#': r'\#', '_': r'\_', '{': r'\{',
             '}': r'\}', '~': r'\textasciitilde{}', '^': r'\textasciicircum{}'}
    return ''.join(table.get(c, c) for c in str(value))


def export(source):
    raw = source.read_bytes()
    doc = json.loads(raw)
    if doc['schema_version'] != 'retrieval_reader_upgrade_manuscript_v0.14':
        raise ValueError('Unexpected manuscript schema')
    parts = [
        '% Generated from ' + source.name,
        '% Canonical source SHA256: ' + hashlib.sha256(raw).hexdigest(),
        '% Generic working article. Neither ACL formatting nor TeX compilation is claimed.',
        r'\documentclass[11pt]{article}',
        r'\usepackage[a4paper,margin=25mm]{geometry}',
        r'\usepackage{fontspec}',
        r'\setmainfont{DejaVu Serif}',
        r'\usepackage{array,longtable,booktabs,hyperref}',
        r'\hypersetup{colorlinks=true,urlcolor=blue}',
        r'\setlength{\parindent}{0pt}',
        r'\setlength{\parskip}{0.6em}',
        r'\title{' + escape(doc['title']) + r'\\[0.5em]\large ' + escape(doc['subtitle']) + '}',
        r'\author{' + escape(doc['author']) + '}',
        r'\date{' + escape(doc['date']) + '}',
        r'\begin{document}', r'\maketitle', escape(doc['status']),
        r'\begin{abstract}', escape(doc['abstract']), r'\end{abstract}',
    ]
    for section in doc['sections']:
        parts.append(r'\section*{' + escape(section['title']) + '}')
        parts.extend(escape(p) + '\n' for p in section.get('paragraphs', []))
        for key in section.get('tables', []):
            table = doc['tables'][key]
            n = len(table['header'])
            if any(len(row) != n for row in table['rows']):
                raise ValueError('Ragged table: ' + key)
            # Reserve intercolumn padding, then preserve canonical width ratios.
            specs = [r'>{\raggedright\arraybackslash}p{\dimexpr '
                     + f'{w / sum(table["widths"]):.6f}'
                     + r'\textwidth-2\tabcolsep\relax}' for w in table['widths']]
            parts.extend([r'{\small', r'\begin{longtable}{' + ''.join(specs) + '}',
                          r'\toprule', ' & '.join(r'\textbf{' + escape(x) + '}' for x in table['header']) + r' \\',
                          r'\midrule\endhead'])
            parts.extend(' & '.join(escape(x) for x in row) + r' \\' for row in table['rows'])
            parts.extend([r'\bottomrule', r'\end{longtable}', '}',
                          r'{\small ' + escape(table['caption']) + '}'])
        parts.extend(escape(p) + '\n' for p in section.get('after_tables', []))
    parts.append(r'\section*{Primary-source links}')
    for row in doc.get('reference_sources', []):
        url = row['url']
        if any(c in url for c in '{}\\\n'):
            raise ValueError('Unsafe URL in canonical manuscript')
        parts.append(escape(row['key']) + r': \url{' + url + '}\n')
    parts.append(r'\end{document}')
    return '\n\n'.join(parts) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    result = export(args.source)
    if args.check:
        if args.output.read_text() != result:
            raise ValueError('LaTeX source differs from the canonical export')
    else:
        with args.output.open('x') as stream:
            stream.write(result)
    print(json.dumps({'status': 'matched' if args.check else 'exported',
                      'source_sha256': hashlib.sha256(args.source.read_bytes()).hexdigest(),
                      'tex_sha256': hashlib.sha256(args.output.read_bytes()).hexdigest(),
                      'tex_compilation_performed': False, 'acl_template_used': False}))


if __name__ == '__main__':
    main()
