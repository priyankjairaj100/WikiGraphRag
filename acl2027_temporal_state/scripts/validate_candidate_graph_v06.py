#!/usr/bin/env python3
"""Validate a raw question-free candidate graph; never repair or score it.

This validates declared structure and exact evidence, not semantic entailment.
The general graph-to-scored-cache adapter remains a separate required step.
"""
import argparse
from hashlib import sha256
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('v03_validation_primitives',
                                             ROOT / 'scripts/model_pilot_job_template.py')
old = importlib.util.module_from_spec(spec)
spec.loader.exec_module(old)


def unique(items, field):
    ids = [item[field] for item in items]
    if len(ids) != len(set(ids)):
        raise ValueError('duplicate_' + field)


def validate_graph(raw, request):
    graph = old.strict_json(raw)
    schema = old.strict_json((ROOT / 'configs/candidate_schema_v06.json').read_text())
    old.validate_schema(graph, schema)
    unique(request['sources'], 'source_id')
    sources = {s['source_id']: s for s in request['sources']}
    old.valid_day(request['information_cutoff'])
    for source in sources.values():
        old.valid_day(source['operational_available_at'])
        if source['operational_available_at'] > request['information_cutoff']:
            raise ValueError('future_source_in_request')
        if old.digest(source['text'].encode()) != source['text_sha256']:
            raise ValueError('source_hash_mismatch')
    for group, field in (('mentions', 'mention_id'), ('links', 'link_id'),
                         ('aliases', 'alias_id'), ('authorities', 'source_id')):
        unique(graph[group], field)
    cap = request.get('scope_filter') or {}
    if len(graph['mentions']) > cap.get('max_mentions', 64):
        raise ValueError('mention_overflow_no_pruning')
    readings, mention_of, aligned = {}, {}, {}
    for mention in graph['mentions']:
        sid = mention['source_id']
        if sid not in sources:
            raise ValueError('mention_unknown_source')
        anchor = old.align_evidence([{'source_id': sid, 'quote': mention['anchor_quote']}], sources)[0]
        if len(mention['readings']) > cap.get('max_resolved_readings_per_mention', 4):
            raise ValueError('reading_overflow_no_pruning')
        if not mention['readings'] and not mention['unresolved_reason'].strip():
            raise ValueError('empty_mention_needs_unresolved_reason')
        for reading in mention['readings']:
            rid = reading['candidate_id']
            if rid in readings:
                raise ValueError('duplicate_candidate_id')
            readings[rid], mention_of[rid] = reading, mention
            spans = old.align_evidence(reading['evidence'], sources)
            if not any(span['source_id'] == sid and span['start'] < anchor['end']
                       and anchor['start'] < span['end'] for span in spans):
                raise ValueError('primary_evidence_must_overlap_anchor')
            old.validate_bounds(reading)
            reported = reading['reported_at']
            available = sources[sid]['operational_available_at']
            if reported is not None and reported > available:
                raise ValueError('report_after_source_availability')
            if reading['modality'] == 'reported_actual' and any(
                    day > (reported or available) for day in reading['state_observed_at']):
                raise ValueError('actual_observation_after_report')
            aligned[rid] = {'mention_id': mention['mention_id'], 'source_id': sid,
                            'anchor': anchor, 'evidence': spans,
                            'context_source_ids': sorted(sources)}
    authorities = {}
    for row in graph['authorities']:
        spans = old.align_evidence(row['evidence'], sources)
        if row['source_id'] not in sources or any(s['source_id'] != row['source_id'] for s in spans):
            raise ValueError('authority_evidence_must_be_in_declared_source')
        authorities[row['source_id']] = row['authority_id']
    for link in graph['links']:
        left, right = link['candidate_id'], link['target_candidate_id']
        if left not in readings or right not in readings:
            raise ValueError('unknown_link_endpoint')
        if mention_of[left]['mention_id'] == mention_of[right]['mention_id']:
            raise ValueError('link_endpoints_require_distinct_mentions')
        old.align_evidence(link['evidence'], sources)
        if link['relation'] == 'CORRECTS':
            cert = link['correction']
            if cert is None:
                raise ValueError('correction_missing_certificate')
            sid, tid = mention_of[left]['source_id'], mention_of[right]['source_id']
            if cert['reference_evidence']['source_id'] != sid:
                raise ValueError('correction_reference_requires_updating_source')
            old.align_evidence([cert['reference_evidence']], sources)
            if sid not in authorities or authorities[sid] != authorities.get(tid):
                raise ValueError('correction_requires_same_declared_authority')
            if sources[tid]['operational_available_at'] > sources[sid]['operational_available_at']:
                raise ValueError('correction_target_is_later_source')
        elif link['correction'] is not None:
            raise ValueError('noncorrection_link_has_certificate')
    dependencies, seen = {}, set()
    for row in graph['essential_support']:
        left, right = row['dependent_candidate_id'], row['prerequisite_candidate_id']
        if left not in readings or right not in readings or left == right or (left, right) in seen:
            raise ValueError('invalid_or_duplicate_dependency')
        seen.add((left, right))
        spans = old.align_evidence(row['evidence'], sources)
        sid, tid = mention_of[left]['source_id'], mention_of[right]['source_id']
        if any(s['source_id'] != sid for s in spans):
            raise ValueError('dependency_evidence_requires_dependent_source')
        if sources[tid]['operational_available_at'] > sources[sid]['operational_available_at']:
            raise ValueError('dependency_on_later_source')
        dependencies.setdefault(left, []).append(right)
    def visit(node, path):
        if node in path:
            raise ValueError('essential_dependency_cycle')
        for parent in dependencies.get(node, []):
            visit(parent, path | {node})
    for node in dependencies:
        visit(node, set())
    for row in graph['aliases']:
        old.align_evidence(row['evidence'], sources)
    for row in graph['unsupported']:
        spans = old.align_evidence(row['evidence'], sources)
        if row['source_id'] not in sources or row['source_id'] not in {s['source_id'] for s in spans}:
            raise ValueError('unsupported_item_missing_primary_evidence')
    return {'schema_version': 'candidate_validation_v0.6',
            'raw_output_sha256': sha256(raw.encode()).hexdigest(),
            'request_sha256': old.digest(old.canonical(request)),
            'mentions': len(graph['mentions']), 'resolved_readings': len(readings),
            'links': len(graph['links']), 'unsupported_items': len(graph['unsupported']),
            'aligned_readings': aligned,
            'validation_scope': 'Schema, prefix, exact evidence, references and basic temporal checks; not entailment, final cache validity or calibrated scores.',
            'evidence_type': 'unscored_candidate_output', 'validator_performed_repair': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--raw-output', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    request = old.strict_json(args.request.read_text())
    report = validate_graph(args.raw_output.read_text(), request)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_bytes(old.canonical(report) + b'\n')
    print(json.dumps({k: report[k] for k in ('mentions', 'resolved_readings', 'links', 'validator_performed_repair')}))


if __name__ == '__main__':
    main()
