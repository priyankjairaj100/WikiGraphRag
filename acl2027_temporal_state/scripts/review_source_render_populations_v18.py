#!/usr/bin/env python3
"""Read back the two exact v18 freezes without native rendering or alpha transforms."""
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter
import gc
import hashlib
import json
import re

import render_source_blocks_lo_v17_2 as renderer

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = Path('/dev/shm/wikigraph_v15/external/fresh_sources')
PROTOCOLS = {
    'data/reader_binding_v18/source_render_dependencies_protocol_v18.json': '0af419d76331858e7a469a83ac1028db40dd0ffbd9f19d642d78d89c3e7fcf66',
    'data/reader_binding_v18/source_render_nvda_identity_recovery_protocol_v18.json': '9e19568a240b8066231d48761ba567687572c203cdeda5ec8603f9370b9e679c',
}
CACHE = {}


def require(value, reason):
    if not value:
        raise ValueError(reason)


def digest(path):
    path = Path(path).resolve()
    if path not in CACHE:
        h = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                h.update(chunk)
        CACHE[path] = h.hexdigest()
    return CACHE[path]


def read(relative):
    return json.loads((ROOT / relative).read_text())


def main():
    output = ROOT / 'results/source_render_population_review_v18.json'
    require(not output.exists(), 'review_receipt_exists')
    base = read('data/reader_binding_v17/source_render_identity_protocol_v17_2.json')
    inventory_path = 'results/pre_author_bundle_inventory_v17.json'
    require(digest(ROOT / inventory_path) == 'b912a697a6ce1635fbd66ab7f7201fa69084f83b1e60a4ba585870d1ef02fe1b', 'inventory_changed')
    inventory = read(inventory_path)
    inventory_map = {(b['source_index'], b['block_id']): b for b in inventory['additional_dependency_blocks']}
    require(len(inventory_map) == 40, 'dependency_population_not40')
    prior = read('results/source_render_identity_execution_v17_2.json')
    modes = []
    for relative, expected in PROTOCOLS.items():
        path = ROOT / relative
        require(digest(path) == expected, 'protocol_changed')
        protocol = read(relative)
        require(protocol['schema_version'] == base['schema_version'] == renderer.SCHEMA, 'renderer_schema_changed')
        require(protocol['runtime_bindings'] == base['runtime_bindings'], 'runtime_population_changed')
        for group in ('code_bindings', 'input_bindings'):
            for name, checksum in protocol[group].items():
                require(digest(ROOT / name) == checksum, 'frozen_code_or_input_changed')
        require({k: v for k, v in protocol['code_bindings'].items() if k != 'scripts/prepare_source_render_populations_v18.py'} == base['code_bindings'], 'inherited_code_population_changed')
        for name, checksum in protocol['runtime_bindings'].items():
            require(digest(name) == checksum, 'runtime_bytes_changed')
        require(protocol['max_pages'] == base['max_pages'] == 8, 'page_limit_changed')
        limits = protocol['unchanged_limits']
        require(limits == {'worker_count': 1, 'reserve_bytes': renderer.LO_RESERVE_BYTES, 'owned_group_rss_bytes': renderer.LO_GROUP_RSS_LIMIT_BYTES, 'proxy_bytes': renderer.guard.PROXY_LIMIT_BYTES, 'kernel_max_bytes': renderer.guard.KERNEL_MAX_BYTES, 'conversion_seconds': 300, 'raster_seconds': 60, 'page_cap': 8, 'sentinel_page': 9, 'block_output_bytes': renderer.MAX_BLOCK_BYTES, 'attempt_output_bytes': renderer.MAX_BYTES, 'cleanup_failure_stops_further_workers': True, 'automatic_additional_retries_permitted': 0}, 'declared_resource_limits_changed')
        require(protocol['automatic_additional_retries_permitted'] == protocol['model_calls'] == protocol['natural_QA_predictions'] == protocol['complete_evidence_admitted'] == 0, 'unexpected_retry_or_admission')
        require(protocol['author_release_allowed'] is False and protocol['identity_admission_performed'] is False, 'unapproved_release')
        require(not Path(protocol['planned_external_output']).exists() and not (ROOT / protocol['planned_public_result']).exists(), 'execution_already_started_before_review')
        population, sources, special_zero_fact_blocks = set(), [], []
        for source in protocol['sources']:
            index = source['original_source_index']
            original_source = base['sources'][index]
            require(all(source[k] == original_source[k] for k in ('source_path', 'external_filename', 'source_bytes', 'source_sha256')), 'source_identity_changed')
            source_path = SOURCE_ROOT / source['external_filename']
            require(not source_path.is_symlink() and source_path.stat().st_size == source['source_bytes'] and digest(source_path) == source['source_sha256'], 'source_file_changed')
            raw = source_path.read_bytes()
            nodes = renderer.source_render.byte_reader._parse(raw)
            lookup = {n.path: n for n in nodes}
            prepared = renderer.source_render.prepare_blocks(raw, source)
            require(len(prepared) == len(source['blocks']), 'prepared_population_changed')
            source_record = {'original_source_index': index, 'source_path': source['source_path'], 'source_sha256': source['source_sha256'], 'blocks': []}
            for block, item in zip(source['blocks'], prepared):
                key = index, block['block_id']
                require(key not in population, 'duplicate_block')
                population.add(key)
                node = lookup[block['dom_path']]
                require(renderer.source_render.bound_anchor(raw, node) == block['anchor'] == item['anchor'], 'complete_original_node_anchor_mismatch')
                require(raw[node.start:node.stop] == item['fragment'], 'fragment_changed')
                ancestors = list(reversed(list(node.ancestors())))
                expected_ancestors = [dict(dom_path=n.path, **renderer.source_render.bound_anchor(raw, n, opening=True)) for n in ancestors]
                require(item['ancestor_openings'] == expected_ancestors, 'ancestor_opening_chain_changed')
                heads = [n for n in nodes if n.tag == renderer.source_render.XH + 'head']
                styles = [n for n in nodes if n.tag == renderer.source_render.XH + 'style' and any(a in heads for a in n.ancestors())]
                qname = lambda n: re.match(rb'<([^\s/>]+)', raw[n.start:n.opening_stop]).group(1)
                assembled = (raw[ancestors[0].start:ancestors[0].opening_stop] + b'<head>' + b''.join(raw[n.start:n.stop] for n in styles) + b'</head>' + b''.join(raw[n.start:n.opening_stop] for n in ancestors[1:]) + item['fragment'] + b''.join(b'</' + qname(n) + b'>' for n in reversed(ancestors)))
                require(assembled == item['html'] and len(assembled) <= renderer.source_render.MAX_FRAGMENT_BYTES, 'assembly_recipe_or_limit_changed')
                resource = renderer.resource_guard(assembled)
                if protocol['mode'] == 'dependencies':
                    authority = inventory_map[key]
                    require({k: v for k, v in block.items() if k != 'role'} == {k: authority[k] for k in ('block_id', 'kind', 'dom_path', 'anchor', 'roles', 'candidate_bundles')}, 'dependency_metadata_changed')
                    require(block['role'] == 'additional_dependency', 'dependency_indexing_role_changed')
                    if block['kind'] in ('table', 'table_row'):
                        require(node.tag == renderer.source_render.XH + ('table' if block['kind'] == 'table' else 'tr'), 'special_dependency_node_kind_changed')
                        require(sum(n.tag == renderer.IX + 'nonFraction' for n in node.walk()) == 0, 'special_dependency_contains_numeric_occurrence')
                        special_zero_fact_blocks.append({'original_source_index': index, 'block_id': block['block_id'], 'kind': block['kind']})
                else:
                    require(index == 4 and block == base['sources'][4]['blocks'][0] and block['block_id'] == 'identity_142881', 'single_recovery_population_changed')
                    old = prior['sources'][4]['blocks'][0]
                    require(old['anchor'] == block['anchor'] and old['status'] == 'stopped_at_process_group_telemetry_unavailable', 'original_failure_changed')
                    require(old['conversion']['owned_group_cleanup']['cleanup_confirmed'] is True and old['conversion']['owned_group_cleanup']['status'] == 'group_absent', 'prior_cleanup_not_confirmed')
                    require(any(s.get('error_code') == 'live_process_rss_missing' for s in old['conversion']['process_group_samples']), 'prior_failure_reason_changed')
                source_record['blocks'].append({'block_id': block['block_id'], 'dom_path': block['dom_path'], 'anchor': block['anchor'], 'ancestor_openings': expected_ancestors, 'prepared_assembly_bytes': len(assembled), 'prepared_assembly_sha256': hashlib.sha256(assembled).hexdigest(), 'passive_resource_readback': resource['status'], 'resource_reason_codes': resource['issues']})
            sources.append(source_record)
            del raw, nodes, lookup, prepared
            gc.collect()
        if protocol['mode'] == 'dependencies':
            require(population == set(inventory_map), 'dependency_population_incomplete')
            require(Counter(b['kind'] for b in special_zero_fact_blocks) == Counter(table=2, table_row=2), 'zero_fact_special_population_changed')
        else:
            require(population == {(4, 'identity_142881')}, 'recovery_not_exactly_one')
        require(len(sources) == protocol['source_denominator'] and len(population) == protocol['block_denominator'], 'declared_denominator_mismatch')
        modes.append({'protocol': relative, 'protocol_sha256': expected, 'mode': protocol['mode'], 'sources': sources, 'source_denominator': len(sources), 'block_denominator': len(population), 'special_zero_fact_blocks_verified': special_zero_fact_blocks, 'code_bindings_verified': len(protocol['code_bindings']), 'input_bindings_verified': len(protocol['input_bindings']), 'runtime_bindings_verified': len(protocol['runtime_bindings'])})
    receipt = {'schema_version': 'source_render_population_review_v18', 'reviewed_at_utc': datetime.now(timezone.utc).isoformat(), 'status': 'passed_exact_population_anchor_ancestor_and_binding_readback_before_launch', 'reviewer': 'table_views_v16', 'checker': {'filename': str(Path(__file__).resolve().relative_to(ROOT)), 'sha256': digest(__file__)}, 'parent_checkpoint': 'f77f70c51b12c935ccf897ba64bf19501d44c427', 'populations': modes, 'original_source11_zero_additional_dependencies': 'SLB_2023 has zero additional dependencies in the authoritative40-block inventory; it is deliberately absent from the11-nonempty-source dependency renderer population.', 'review_assignments': {'table_views_v16': {'dependency_original_source_indices': list(range(6)), 'dependency_blocks': 28, 'separate_NVDA_form_recovery_blocks': 1}, 'root': {'dependency_original_source_indices': list(range(6, 12)), 'dependency_blocks': 12}}, 'limits': ['This verifies exact frozen inputs and source assembly using the shared pinned byte-parser lineage; it does not claim a new independent parser implementation.', 'Passive-resource readback is not CSS/browser fidelity; unchanged alpha-profile and resource/runtime guards remain execution gates.', 'Every actual raster page, original source/CSS and saved ledger still require qualified inspection review.', 'No dependency semantic attachment, scope support, identity card or question/reference is admitted from conversion.'], 'v17_artifacts_modified': False, 'native_render_or_model_calls': 0, 'alpha_transform_invocations': 0, 'questions_authored': 0, 'launch_authority': 'Root approval of each exact protocol hash is required before one-pass execution; this reviewer performs no launch.'}
    with output.open('x') as stream:
        json.dump(receipt, stream, indent=2, sort_keys=True)
        stream.write('\n')
    print(json.dumps({'receipt': str(output.relative_to(ROOT)), 'sha256': digest(output), 'populations': [p['block_denominator'] for p in modes]}))


if __name__ == '__main__':
    main()
