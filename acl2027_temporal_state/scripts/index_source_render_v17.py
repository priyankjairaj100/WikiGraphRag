#!/usr/bin/env python3
"""Index every frozen rendering outcome without exposing source text in Git."""
import argparse
from collections import Counter
import json
from pathlib import Path
import render_source_blocks_lo_v17 as lo


def execute(protocol_path, result_path, external_directory, public_output):
    protocol_path, result_path = Path(protocol_path), Path(result_path)
    root, public = Path(external_directory).resolve(), Path(public_output).resolve()
    if lo.ROOT.parent == root or lo.ROOT.parent in root.parents or root in public.parents or public == root:
        raise ValueError('invalid_external_public_boundary')
    index_path = root / 'review_index.json'
    if index_path.exists() or public.exists():
        raise ValueError('output_exists')
    protocol, result = json.loads(protocol_path.read_text()), json.loads(result_path.read_text())
    if result['protocol_sha256'] != lo.source_render.digest(protocol_path):
        raise ValueError('protocol_result_binding_mismatch')
    if len(protocol['sources']) != len(result['sources']):
        raise ValueError('source_denominator_mismatch')
    index = {'schema_version': 'source_render_review_index_v17',
             'protocol_sha256': lo.source_render.digest(protocol_path),
             'result_sha256': lo.source_render.digest(result_path), 'sources': []}
    counts = {'selected_table': Counter(), 'document_order_neighbor': Counter()}
    css_flags, omitted_head_links, omitted_other_styles = Counter(), 0, 0
    pages = 0
    for i, (source, outcome) in enumerate(zip(protocol['sources'], result['sources'])):
        if source['source_path'] != outcome['source_path'] or len(source['blocks']) != len(outcome['blocks']):
            raise ValueError('source_or_block_population_mismatch')
        item = {'source_index': i, 'source_path': source['source_path'],
                'source_sha256': source['source_sha256'], 'external_filename': source['external_filename'],
                'blocks': []}
        for j, (block, entry) in enumerate(zip(source['blocks'], outcome['blocks'])):
            if block['block_id'] != entry['block_id'] or block['anchor'] != entry['anchor']:
                raise ValueError('block_identity_mismatch')
            folder = root / f'source_{i:02d}' / f'block_{j:02d}'
            artifacts = {}
            for name in ['source-fragment.xml', 'source-assembly.html', 'source-block.html',
                         'rendered/source-block.pdf', 'rendered/source-block.txt']:
                path = folder / name
                if path.is_file():
                    artifacts[name] = lo.file_record(path)
            pngs = [lo.file_record(p) for p in sorted((folder / 'raster').glob('page-*.png'))]
            pages += len(pngs)
            check = entry.get('resource_check', {})
            css_flags.update(check.get('css_property_review_flags', []))
            omitted_head_links += entry.get('external_stylesheet_links_not_loaded', 0)
            omitted_other_styles += entry.get('out_of_head_style_blocks_not_added', 0)
            counts[block['role']][entry['status']] += 1
            item['blocks'].append(dict(block, block_index=j, status=entry['status'],
                                       artifacts=artifacts, pngs=pngs,
                                       resource_check=check,
                                       external_stylesheet_links_not_loaded=entry.get('external_stylesheet_links_not_loaded'),
                                       out_of_head_style_blocks_not_added=entry.get('out_of_head_style_blocks_not_added'),
                                       receipt_path=str(root / f'source_{i:02d}' / f'block_{j:02d}_receipt.json')))
        index['sources'].append(item)
    lo.write_json(index_path, index)
    summary = {'schema_version': 'source_render_conversion_summary_v17',
               'protocol_sha256': index['protocol_sha256'], 'result_sha256': index['result_sha256'],
               'source_denominator': len(index['sources']), 'selected_table_denominator': sum(counts['selected_table'].values()),
               'unique_neighbor_denominator': sum(counts['document_order_neighbor'].values()),
               'requested_blocks': result['requested_blocks'], 'rendered_blocks': result['rendered_blocks'],
               'retained_failed_or_unrenderable_blocks': result['retained_failed_or_unrenderable_blocks'],
               'status_counts_by_role': {k: dict(v) for k, v in counts.items()},
               'available_raster_pages': pages, 'css_flags_block_counts': dict(css_flags),
               'external_stylesheet_links_not_loaded_block_sum': omitted_head_links,
               'out_of_head_style_blocks_not_added_block_sum': omitted_other_styles,
               'external_review_index': lo.file_record(index_path),
               'qualified_visual_admission_performed_by_this_summary': False,
               'reference_admission_performed': False, 'browser_fidelity_claimed': False,
               'natural_QA_predictions': 0, 'model_calls': 0,
               'limitations': ['LibreOffice inspection view only; source borders, width and font/layout behavior may differ.',
                               'Every block requires source CSS, glyph, clipping and all-page visual review.',
                               'Table labels, units, footnote markers and every selected numeric-column mapping require review.',
                               'Neighbor document order does not establish semantic attachment or complete evidence.']}
    lo.write_json(public, summary)
    return summary


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--protocol', required=True); p.add_argument('--result', required=True)
    p.add_argument('--external-directory', required=True); p.add_argument('--output', required=True)
    a = p.parse_args()
    r = execute(a.protocol, a.result, a.external_directory, a.output)
    print(json.dumps({k: r[k] for k in ['requested_blocks', 'rendered_blocks', 'retained_failed_or_unrenderable_blocks', 'available_raster_pages']}))
