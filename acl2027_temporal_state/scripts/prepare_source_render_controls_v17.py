#!/usr/bin/env python3
"""Freeze the two unchanged authored renderer controls and reviewed v17 consumer."""
import argparse
import json
from pathlib import Path
import render_source_blocks_lo_v17 as lo


def prepare(inputs, runtime_receipt, output):
    inputs = Path(inputs)
    paths = ['scripts/render_source_blocks_lo_v17.py',
             'scripts/run_source_render_controls_v17.py',
             'scripts/prepare_source_render_controls_v17.py',
             'scripts/cgroup_guard_v16_2.py', 'scripts/process_group_rss_v16.py',
             'scripts/probe_source_render_lo_v16.py', 'scripts/render_source_blocks_v16.py',
             'src/temporal_state/typed_reader_v15_1.py', 'tests/test_source_render_v16.py',
             'docs/source_render_lo_pipeline_contract_v17.txt']
    receipts = ['results/owned_rss_audit_closure_v17.json',
                'results/source_render_code_review_v17.json',
                'results/source_render_pipeline_preflight_v16_1.json',
                'results/source_render_authored_visual_qa_v16.json',
                'results/source_render_root_visual_review_v16.json']
    parent = json.loads((lo.ROOT / receipts[2]).read_text())
    controls = []
    for name in ['unicode', 'overflow']:
        path = inputs / (name + '.source.xml')
        digest = lo.source_render.digest(path)
        if digest != parent['control_source_bindings'][path.name]:
            raise ValueError('original_control_hash_mismatch')
        c = {'name': name, 'filename': path.name, 'bytes': path.stat().st_size,
             'sha256': digest, 'max_pages': 1, 'expected_pages': 1 if name == 'unicode' else 2,
             'expected_status': 'rendered_pending_full_visual_and_css_review' if name == 'unicode'
                                else 'page_cap_exceeded_partial_pdf_retained'}
        if name == 'unicode':
            c['expected_unicode'] = 'Café margin £'
        controls.append(c)
    runtime = json.loads(Path(runtime_receipt).read_text())
    for path, digest in runtime.items():
        if lo.source_render.digest(path) != digest:
            raise ValueError('runtime_binding_mismatch')
    review = json.loads((lo.ROOT / receipts[1]).read_text())
    if review.get('execution_gate_closed') is not True:
        raise ValueError('independent_consumer_gate_not_closed')
    protocol = {'schema_version': 'source_render_authored_control_protocol_v17',
                'frozen_at_utc': lo.source_render.utc(), 'controls': controls,
                'code_bindings': {p: lo.source_render.digest(lo.ROOT / p) for p in paths},
                'input_bindings': {p: lo.source_render.digest(lo.ROOT / p) for p in receipts},
                'runtime_bindings': runtime,
                'runtime_scope': 'interpreter, renderer modules, Poppler binary and LO program files; not full font/OS dependency closure',
                'resource_policy': 'unchanged_approved_small_process_v16_2_with_verified_cleanup_v17',
                'natural_sources_inspected': 0, 'model_calls': 0}
    lo.write_json(Path(output), protocol)
    return protocol


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--inputs', required=True)
    p.add_argument('--runtime-receipt', required=True)
    p.add_argument('--output', required=True)
    a = p.parse_args()
    r = prepare(a.inputs, a.runtime_receipt, a.output)
    print(json.dumps({'controls': len(r['controls']), 'protocol_sha256': lo.source_render.digest(a.output)}))
