#!/usr/bin/env python3
"""Narrow single-block draft readback; no rendering or runtime invocation."""
import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DRAFT = 'data/reader_binding_v18/source_render_nflx_dependency_recovery_draft_v18_1.json'
BINDINGS = {
    DRAFT: 'c28fca0ab6cf5b39b30409cbd20f54c41629eae61d4474c0d58e123fa3cff9d2',
    'data/reader_binding_v18/source_render_dependencies_protocol_v18.json': '0af419d76331858e7a469a83ac1028db40dd0ffbd9f19d642d78d89c3e7fcf66',
    'results/source_render_dependencies_execution_v18.json': '502db820bf8bc575cf371141d42f7e78395dc31ab2ede3c87482d1664e5c453c',
    'results/source_render_population_review_v18.json': '98152a8593b126e07b2f0622bba9d99d01bc132f1f9d10b5cb89b29d92df6250',
    'results/source_view_availability_summary_v18.json': '7fac96825e0836130f862d3e083703f2d9b2a9729a4475e802e930a05c6e057c',
    'data/reader_binding_v18/source_render_review_plan_v18.json': 'f6d2bce90d8e68244d886a36c1d343ba9ba993cee070e2ea1ecb0f4edb2ccd5a',
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    output = ROOT / 'results/source_render_nflx_recovery_protocol_review_v18_1.json'
    assert not output.exists()
    docs = {}
    for path, expected in BINDINGS.items():
        assert sha(ROOT / path) == expected, path
        docs[path] = json.loads((ROOT / path).read_text())
    draft = docs[DRAFT]
    parent = docs['data/reader_binding_v18/source_render_dependencies_protocol_v18.json']
    result = docs['results/source_render_dependencies_execution_v18.json']
    for group in ('code_bindings', 'input_bindings'):
        for path, expected in draft[group].items():
            assert sha(ROOT / path) == expected, path
        assert all(draft[group][path] == value for path, value in parent[group].items())
    assert set(draft['code_bindings']) - set(parent['code_bindings']) == {'scripts/prepare_nflx_dependency_recovery_v18_1.py'}
    assert draft['runtime_bindings'] == parent['runtime_bindings'] and len(draft['runtime_bindings']) == 256
    assert draft['unchanged_limits'] == parent['unchanged_limits'] and draft['max_pages'] == parent['max_pages']
    changed = {k for k in set(draft) | set(parent) if draft.get(k) != parent.get(k)}
    assert changed == {'amendment_version', 'block_denominator', 'code_bindings', 'declaration_timing', 'frozen_at_utc',
                       'input_bindings', 'metadata', 'mode', 'planned_external_output', 'planned_public_result',
                       'prepared_at_utc', 'protocol_status', 'source_denominator', 'sources'}
    original_source = next(s for s in parent['sources'] if s['original_source_index'] == 3)
    original_block = next(b for b in original_source['blocks'] if b['block_id'] == 'paragraph_746')
    selected = copy.deepcopy(original_source)
    selected['blocks'] = [original_block]
    assert draft['sources'] == [selected] and draft['source_denominator'] == draft['block_denominator'] == 1
    failed = [(s, b) for s in result['sources'] for b in s['blocks'] if b['status'] != 'rendered_pending_full_visual_and_css_review']
    assert len(failed) == 1
    fs, fb = failed[0]
    assert fs['source_path'] == selected['source_path']
    assert fb['block_id'] == original_block['block_id'] and fb['anchor'] == original_block['anchor'] and fb['dom_path'] == original_block['dom_path']
    assert fb['status'] == 'stopped_at_process_group_telemetry_unavailable'
    conversion = fb['conversion']
    assert conversion['owned_group_cleanup']['cleanup_confirmed'] and conversion['owned_group_cleanup']['status'] == 'group_absent'
    assert any(x.get('error_code') == 'live_process_rss_missing' for x in conversion['process_group_samples'])
    assert conversion['baseline_snapshot']['events'] == conversion['final_snapshot']['events']
    assert fb['resource_check']['status'] == 'passive_resource_profile_passed' and fb['compatibility']['patch_count'] == 0
    preparation = {k: fb[k] for k in ('ancestor_openings', 'head_stylesheets', 'source_assembly_sha256', 'render_html_sha256')}
    assert draft['metadata']['original_preparation_bindings'] == preparation
    source = Path('/dev/shm/wikigraph_v15/external/fresh_sources') / selected['external_filename']
    raw = source.read_bytes()
    assert len(raw) == selected['source_bytes'] and hashlib.sha256(raw).hexdigest() == selected['source_sha256']
    a = original_block['anchor']
    fragment = raw[a['byte_start']:a['byte_stop']]
    assert len(fragment) == 379 and hashlib.sha256(fragment).hexdigest() == a['span_sha256']
    for ancestor in fb['ancestor_openings']:
        opening = raw[ancestor['byte_start']:ancestor['byte_stop']]
        assert hashlib.sha256(opening).hexdigest() == ancestor['span_sha256']
    original_external = Path('/dev/shm/wikigraph_v15/external/source_render_dependencies_v18/attempt01/source_03/block_00')
    assert (original_external / 'source-fragment.xml').read_bytes() == fragment
    assert sha(original_external / 'source-assembly.html') == fb['source_assembly_sha256']
    assert sha(original_external / 'source-block.html') == fb['render_html_sha256']
    assert not Path(draft['planned_external_output']).exists() and not (ROOT / draft['planned_public_result']).exists()
    assert draft['automatic_additional_retries_permitted'] == 0 and not draft['author_release_allowed']
    assert not draft['metadata']['original_result_relabeling_permitted']
    bindings = dict(BINDINGS)
    bindings[str(Path(__file__).relative_to(ROOT))] = sha(__file__)
    receipt = {
        'schema_version': 'single_caption_recovery_draft_review_v18_1',
        'reviewed_at_utc': datetime.now(timezone.utc).isoformat(),
        'status': 'closed_draft_readback_before_freeze_and_execution', 'open_blocking_findings': 0,
        'reviewer': 'table_views_v16', 'input_bindings': bindings,
        'adapter_binding': {'path': 'scripts/prepare_nflx_dependency_recovery_v18_1.py',
                            'sha256': draft['code_bindings']['scripts/prepare_nflx_dependency_recovery_v18_1.py']},
        'population': {'source_count': 1, 'block_count': 1, 'original_source_index': 3,
                       'source_path': selected['source_path'], 'source_sha256': selected['source_sha256'],
                       'block_id': original_block['block_id'], 'dom_path': original_block['dom_path'], 'anchor': a,
                       'unverified_candidate_bundles': original_block['candidate_bundles']},
        'checks': {'exact_single_original_failure_only': True, 'source_bytes_and_fragment_rehashed': True,
                   'ancestor_opening_spans_rehashed': True, 'ancestor_count': len(fb['ancestor_openings']),
                   'original_saved_fragment_assembly_render_input_verified': True,
                   'all_parent_implementation_and_input_pins_preserved_and_rehashed': True,
                   'unchanged_runtime_pin_map_entries_compared': 256, 'runtime_files_rehashed_in_this_narrow_review': 0,
                   'unchanged_limits_and_cleanup_policy': True, 'original_outcome_and_denominator_preserved': True,
                   'fresh_output_paths': True},
        'runtime_review_basis': 'Exact equality to the 256-entry parent map, whose content was verified in the bound v18 population review; no full-runtime or full40 review repeated. Renderer must verify actual bytes before execution.',
        'permitted_freeze_delta': ['protocol_status', 'frozen_at_utc', 'input_bindings addition of this review and frozen v18 review plan'],
        'freeze_does_not_authorize_execution': True, 'root_exact_frozen_hash_approval_required': True,
        'execution_authority_granted': False, 'native_launches': 0, 'model_calls': 0,
        'actual_recovery_view_admitted': False, 'semantic_dependency_attachment_verified': False,
        'identity_cards_constructed': 0, 'questions_authored': 0, 'references_authored': 0,
        'review_scope': 'Source-free contract, parent hash, fixed source and unchanged pin/budget readback only. Actual future source/CSS/raster inspection remains required. Original39/40 is immutable.',
    }
    with output.open('x') as handle:
        json.dump(receipt, handle, indent=2, sort_keys=True); handle.write('\n')
    print(json.dumps({'path': str(output.relative_to(ROOT)), 'sha256': sha(output), 'status': receipt['status']}))


if __name__ == '__main__':
    main()
