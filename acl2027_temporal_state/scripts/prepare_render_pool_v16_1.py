#!/usr/bin/env python3
"""Freeze all selected table and neighbor anchors before LO source rendering."""
import argparse
import json
from pathlib import Path
import shutil
import sys
import fitz
import pymupdf
from render_source_blocks_lo_v16_1 import ROOT, SCHEMA, WRAPPER, source_render, write_json


def prepare(pool_records, output):
    pool_path = ROOT / 'results/reader_pool_v16.json'
    parent_path = ROOT / 'data/reader_binding_v16/table_view_protocol_v16_1.json'
    pool, parent = json.loads(pool_path.read_text()), json.loads(parent_path.read_text())
    public = {s['source_path']: s for s in pool['records']}
    if set(public) != {s['source_path'] for s in parent['sources']}:
        raise ValueError('full_source_population_mismatch')
    sources, input_artifacts = [], []
    selected_tables = 0
    for expected in parent['sources']:
        record = public[expected['source_path']]
        authors = [a for a in record['external_artifacts'] if a['filename'].endswith('.author.jsonl')]
        if len(authors) != 1:
            raise ValueError('author_packet_count_mismatch')
        artifact = authors[0]
        path = Path(pool_records) / artifact['filename']
        if (path.stat().st_size != artifact['bytes']
                or source_render.digest(path) != artifact['sha256'] or not artifact['complete']):
            raise ValueError('author_packet_binding_mismatch')
        input_artifacts.append({'filename': artifact['filename'], 'bytes': artifact['bytes'],
                                'sha256': artifact['sha256']})
        packets = [json.loads(line) for line in path.read_text().splitlines()]
        if len(packets) != record['selected_tables']:
            raise ValueError('selected_table_population_mismatch')
        selected_tables += len(packets)
        blocks = {}
        for packet in packets:
            if packet['source_card']['source_sha256'] != expected['expected_sha256']:
                raise ValueError('packet_source_identity_mismatch')
            table_number = packet['table_ordinal']
            candidates = [('table_' + str(table_number), packet, 'selected_table')]
            candidates += [('paragraph_' + str(n['paragraph_ordinal']), n, 'document_order_neighbor')
                           for n in packet['neighbor_paragraphs']]
            for block_id, node, role in candidates:
                value = {'block_id': block_id, 'dom_path': node['dom_path'], 'anchor': node['anchor'],
                         'role': role, 'candidate_table_ordinals': [table_number]}
                if block_id in blocks:
                    old = blocks[block_id]
                    if old['anchor'] != value['anchor'] or old['dom_path'] != value['dom_path']:
                        raise ValueError('shared_neighbor_anchor_conflict')
                    old['candidate_table_ordinals'] = sorted(set(old['candidate_table_ordinals'] + [table_number]))
                else:
                    blocks[block_id] = value
        sources.append({'source_path': expected['source_path'],
                        'external_filename': expected['external_filename'],
                        'source_sha256': expected['expected_sha256'], 'source_bytes': expected['expected_bytes'],
                        'selected_tables': len(packets),
                        'blocks': sorted(blocks.values(), key=lambda b: b['anchor']['byte_start'])})
    paths = ['scripts/render_source_blocks_lo_v16_1.py', 'scripts/prepare_render_pool_v16_1.py',
             'scripts/probe_source_render_lo_v16.py', 'scripts/render_source_blocks_v16.py',
             'src/temporal_state/typed_reader_v15_1.py', 'tests/test_source_render_lo_pipeline_v16_1.py',
             'docs/source_render_lo_pipeline_contract_v16_1.txt']
    program = WRAPPER.parents[2] / 'native/libreoffice-headless/libreoffice/program'
    runtime = {Path(sys.executable).resolve(), WRAPPER.resolve(), Path(shutil.which('pdftoppm')).resolve(),
               Path(fitz.__file__).resolve(), Path(pymupdf.__file__).resolve(),
               Path(pymupdf._mupdf.__file__).resolve()}
    runtime.update(p.resolve() for p in program.iterdir() if p.is_file())
    inputs = ['results/reader_pool_v16.json', 'data/reader_binding_v16/table_view_protocol_v16_1.json',
              'results/source_render_authored_visual_qa_v16.json', 'results/source_render_feasibility_v16.json']
    protocol = {'schema_version': SCHEMA, 'frozen_at_utc': source_render.utc(),
                'declaration_timing': 'after_authored_geometry_review_before_any_natural_block_render',
                'code_bindings': {p: source_render.digest(ROOT / p) for p in paths},
                'input_bindings': {p: source_render.digest(ROOT / p) for p in inputs},
                'runtime_bindings': {str(p): source_render.digest(p) for p in sorted(runtime)},
                'runtime_scope': 'interpreter renderer module and extension Poppler binary LO launcher and program files; not a complete dependency/font closure',
                'author_packet_bindings': input_artifacts,
                'source_denominator': len(sources), 'selected_table_denominator': selected_tables,
                'block_denominator_including_unique_neighbors': sum(len(s['blocks']) for s in sources),
                'selection_rule': 'all frozen selected tables plus all existing two-before/two-after neighbors; deduplicate identical source blocks only',
                'max_pages': 8, 'sources': sources, 'natural_QA_predictions': 0,
                'admission': 'Every rendered block remains unadmitted until actual visual and full source CSS review; all failures retained.'}
    write_json(Path(output), protocol)
    return protocol


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pool-records', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = prepare(args.pool_records, args.output)
    print(json.dumps({k: result[k] for k in ['source_denominator', 'selected_table_denominator',
                                          'block_denominator_including_unique_neighbors']}))
