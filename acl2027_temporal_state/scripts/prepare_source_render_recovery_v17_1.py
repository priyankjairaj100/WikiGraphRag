#!/usr/bin/env python3
"""Freeze exactly one post-failure-selected retry of the three RSS-stopped blocks."""
import argparse
import copy
import json
from pathlib import Path
import render_source_blocks_lo_v17 as lo


def prepare(output):
    pp = lo.ROOT / 'data/reader_binding_v17/source_render_natural_protocol_v17.json'
    rp = lo.ROOT / 'results/source_render_natural_execution_v17.json'
    prior, result = json.loads(pp.read_text()), json.loads(rp.read_text())
    if result['protocol_sha256'] != lo.source_render.digest(pp):
        raise ValueError('original_protocol_result_mismatch')
    expected = {(8, 'paragraph_1186'), (8, 'table_145'), (10, 'table_161')}
    selected, sources, lineage = set(), [], []
    for i, (source, outcome) in enumerate(zip(prior['sources'], result['sources'])):
        failed = []
        for block, entry in zip(source['blocks'], outcome['blocks']):
            if block['block_id'] != entry['block_id'] or block['anchor'] != entry['anchor']:
                raise ValueError('original_block_binding_mismatch')
            if entry['status'] == 'rendered_pending_full_visual_and_css_review':
                continue
            if entry['status'] != 'stopped_at_process_group_telemetry_unavailable':
                raise ValueError('unexpected_original_failure')
            selected.add((i, block['block_id']))
            failed.append(copy.deepcopy(block))
            lineage.append({'original_source_index': i, 'source_path': source['source_path'],
                            'block_id': block['block_id'], 'anchor': block['anchor'],
                            'original_status': entry['status']})
        if failed:
            new = copy.deepcopy(source)
            new['blocks'] = failed
            new['selected_tables'] = sum(b['role'] == 'selected_table' for b in failed)
            new['original_source_index'] = i
            sources.append(new)
    if selected != expected:
        raise ValueError('recovery_denominator_not_exactly_three_original_failures')
    protocol = copy.deepcopy(prior)
    protocol.update(frozen_at_utc=lo.source_render.utc(),
        declaration_timing='after_original_101_block_attempt_before_any_question_or_prediction',
        recovery_amendment_version='v17.1',
        recovery_population_selection='post_failure_selected_exactly_the_three_original_telemetry_stops',
        automatic_additional_retries_permitted=0,
        original_attempt_result_relabeling_permitted=False,
        original_attempt_counts={'requested_blocks':101, 'rendered_blocks':98, 'failed_blocks':3},
        source_denominator=len(sources), selected_table_denominator=2,
        block_denominator_including_unique_neighbors=3, sources=sources,
        recovery_original_block_lineage=lineage,
        selection_rule='exactly three original telemetry-failed blocks, once, with unchanged bytes and execution implementation')
    protocol['code_bindings']['scripts/prepare_source_render_recovery_v17_1.py'] = lo.source_render.digest(__file__)
    protocol['input_bindings'].update({str(pp.relative_to(lo.ROOT)):lo.source_render.digest(pp),
        str(rp.relative_to(lo.ROOT)):lo.source_render.digest(rp),
        'results/source_render_conversion_summary_v17.json':lo.source_render.digest(lo.ROOT/'results/source_render_conversion_summary_v17.json')})
    # Recheck every unchanged bound code/input/runtime byte before freezing.
    for group in ('code_bindings', 'input_bindings'):
        for path, digest in protocol[group].items():
            if lo.source_render.digest(lo.ROOT/path) != digest:
                raise ValueError('unchanged_binding_mismatch')
    for path, digest in protocol['runtime_bindings'].items():
        if lo.source_render.digest(path) != digest:
            raise ValueError('unchanged_runtime_binding_mismatch')
    lo.write_json(Path(output), protocol)
    return protocol


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True)
    a=p.parse_args();r=prepare(a.output)
    print(json.dumps({'source_denominator':r['source_denominator'],'blocks':3,'tables':2,
                      'protocol_sha256':lo.source_render.digest(a.output)}))
