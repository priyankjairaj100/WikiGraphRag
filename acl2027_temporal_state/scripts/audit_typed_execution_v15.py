#!/usr/bin/env python3
"""Independent record/byte replay audit; never invokes the reader or comparator.

Consumes the frozen protocol, public summary, source bytes and external JSONL.
Source-derived quantities are compared only in memory and never enter this audit
report. This verifies recorded execution consistency, not financial correctness.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time

ROOT = Path(__file__).resolve().parents[1]
COUNTERS = ('counts', 'reader_status_counts', 'reference_status_counts', 'binding_status_counts', 'reader_fact_status_counts', 'anchor_counts', 'lookup_counts')
ASPECTS = ('concept', 'entity', 'period', 'unit', 'dimensions')
SAFE_DISCREPANCY = {'fact_ordinal', 'dom_path', 'reason', 'reader_status', 'reference_status', 'reader_issues', 'reference_reason'}
REQUIRED_CODE = {'scripts/reference_numeric_v15.py', 'scripts/inventory_inline_xbrl_v14.py'}


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1048576), b''):
            h.update(block)
    return h.hexdigest()


def canonical(text):
    return isinstance(text, str) and text != '-0' and re.fullmatch(r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]*[1-9])?', text) is not None


def same_counts(left, right):
    return Counter(left) == Counter(right)


def key(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


class Audit:
    def __init__(self):
        self.checks = 0
        self.failures = []

    def check(self, condition, name, *, source=None, ordinal=None):
        self.checks += 1
        if not condition:
            row = {'check': name}
            if source is not None:
                row['source_path'] = source
            if ordinal is not None:
                row['fact_ordinal'] = ordinal
            self.failures.append(row)
        return bool(condition)

    def binding(self, path, expected, name):
        self.check(Path(path).is_file() and sha(path) == expected, name)

    def anchor(self, anchor, data, digest, version, name, source, ordinal=None):
        if not self.check(isinstance(anchor, dict), name + '_exists', source=source, ordinal=ordinal):
            return False
        start, stop = anchor.get('byte_start'), anchor.get('byte_stop')
        valid = type(start) is int and type(stop) is int and 0 <= start < stop <= len(data)
        self.check(valid, name + '_bounds', source=source, ordinal=ordinal)
        self.check(anchor.get('source_sha256') == digest, name + '_source_sha', source=source, ordinal=ordinal)
        self.check(anchor.get('source_version') == version, name + '_source_version', source=source, ordinal=ordinal)
        exact = valid and hashlib.sha256(data[start:stop]).hexdigest() == anchor.get('span_sha256')
        self.check(exact, name + '_span_sha', source=source, ordinal=ordinal)
        return valid and exact and anchor.get('source_sha256') == digest and anchor.get('source_version') == version


def recompute_pairs(pairs, expected, audit, source):
    result = {name: Counter() for name in COUNTERS[:5]}
    c = result['counts']
    c['expected_population'] = expected
    c['reader_observed'] = sum(x.get('typed') is not None for x in pairs)
    c['reference_observed'] = sum(x.get('reference') is not None for x in pairs)
    discrepancies = []
    audit.check(len(pairs) == max(expected, c['reader_observed'], c['reference_observed']), 'full_population_rows', source=source)
    for ordinal, pair in enumerate(pairs):
        audit.check(pair.get('fact_ordinal') == ordinal, 'contiguous_pair_ordinal', source=source, ordinal=ordinal)
        left, right = pair.get('typed'), pair.get('reference')
        ls = left.get('numeric_status', 'missing_status') if left is not None else 'missing_occurrence'
        rs = right.get('status', 'missing_status') if right is not None else 'missing_occurrence'
        result['reader_status_counts'][ls] += 1
        result['reference_status_counts'][rs] += 1
        result['binding_status_counts'][left.get('binding_status', 'missing_status') if left else 'missing_occurrence'] += 1
        result['reader_fact_status_counts'][left.get('status', 'missing_status') if left else 'missing_occurrence'] += 1
        left_path = left.get('anchor', {}).get('dom_path') if left else None
        right_path = right.get('dom_path') if right else None
        reason = None
        if left is None or right is None:
            c['missing_occurrence_pairs'] += 1
            reason = 'missing_occurrence'
        elif not left_path or left_path != right_path:
            c['alignment_failures'] += 1
            reason = 'occurrence_locator_mismatch'
        elif ls == 'normalized' and rs == 'normalized':
            lv, rv = left.get('normalized_value'), right.get('canonical_value')
            if not canonical(lv) or not canonical(rv):
                c['canonical_contract_failures'] += 1
                reason = 'normalized_status_without_canonical_decimal'
            else:
                c['common_normalized_population'] += 1
                if lv == rv:
                    c['exact_numeric_agreements'] += 1
                else:
                    c['exact_numeric_disagreements'] += 1
                    reason = 'exact_numeric_disagreement'
        elif ls == 'nil' and rs == 'nil':
            c['both_nil_no_numeric_comparison'] += 1
        else:
            c['outside_common_normalized_population'] += 1
            if ls != rs:
                reason = 'numeric_status_difference'
        if reason:
            discrepancies.append({'fact_ordinal': ordinal, 'dom_path': left_path if left else right_path,
                                  'reason': reason, 'reader_status': ls, 'reference_status': rs,
                                  'reader_issues': left.get('issues', []) if left else [],
                                  'reference_reason': right.get('reason') if right else None})
    c['count_matches_expected'] = int(c['reader_observed'] == c['reference_observed'] == expected)
    return {name: dict(value) for name, value in result.items()}, discrepancies


def audit_lookup(facts, probes, public, maximum, audit, source, digest, version):
    selected, seen = [], set()
    for fact in facts:
        if fact['status'] != 'normalized' or fact['binding_status'] != 'reported_aspects_resolved':
            continue
        query = {'source_sha256': digest, 'source_version': version, 'scope_mode': 'reported_aspects', **fact['reported_aspects']}
        encoded = key(query)
        if encoded not in seen:
            seen.add(encoded)
            selected.append((fact, query))
        if len(selected) == maximum:
            break
    audit.check(len(selected) == len(probes) == len(public), 'lookup_sample_population', source=source)
    counts = Counter()
    expected_public = []
    for (seed, query), probe in zip(selected, probes):
        ordinal = seed['fact_ordinal']
        counts['selected_distinct_keys'] += 1
        audit.check(probe['seed_ordinal'] == ordinal and probe['query'] == query, 'lookup_first_distinct_key_order', source=source, ordinal=ordinal)
        candidates = [f for f in facts if all(not f['resolved_aspects'][a] or f['reported_aspects'][a] is None or f['reported_aspects'][a] == query[a] for a in ASPECTS)]
        ordinals = [f['fact_ordinal'] for f in candidates]
        values = sorted({f['normalized_value'] for f in candidates if f['status'] == 'normalized'})
        blockers = [f['fact_ordinal'] for f in candidates if f['status'] != 'normalized']
        status = 'blocked' if blockers else 'absent' if not candidates else 'ambiguous' if len(values) > 1 else 'multiple_occurrences_same_value' if len(candidates) > 1 else 'unique_reported_value'
        result = probe['lookup_result']
        for name, expected in [('candidate_ordinals', ordinals), ('candidates', candidates), ('reported_values', values), ('blocking_ordinals', blockers), ('status', status), ('query', query)]:
            audit.check(result.get(name) == expected, 'lookup_replay_' + name, source=source, ordinal=ordinal)
        included = ordinal in ordinals
        counts['seed_candidate_included' if included else 'seed_candidate_missing'] += 1
        wrong = {**query, 'source_sha256': '0' * 64 if digest != '0' * 64 else '1' * 64}
        audit.check(probe.get('wrong_source_query') == wrong, 'wrong_source_probe_only_changes_sha', source=source, ordinal=ordinal)
        blocked = probe.get('wrong_source_sha_blocked') is True and probe.get('wrong_source_result') is None
        audit.check(blocked, 'wrong_source_refusal_recorded', source=source, ordinal=ordinal)
        counts['wrong_source_sha_blocked' if blocked else 'wrong_source_sha_not_blocked'] += 1
        expected_public.append({'seed_ordinal': ordinal, 'seed_candidate_included': included, 'wrong_source_sha_blocked': blocked, 'lookup_status': status})
        audit.check(probe.get('seed_candidate_included') == included and probe.get('lookup_status') == status, 'private_probe_summary', source=source, ordinal=ordinal)
    audit.check(public == expected_public, 'public_lookup_probe_replay', source=source)
    return dict(counts)


def audit_source(entry, public, sources, maximum, audit):
    name, digest = entry['source_path'], entry['expected_sha256']
    version = name + '@sha256:' + digest
    source = sources / entry['external_filename']
    audit.check(source.name == entry['external_filename'], 'source_filename', source=name)
    audit.check(source.stat().st_size == entry['expected_bytes'] and sha(source) == digest, 'source_object_binding', source=name)
    data = source.read_bytes()
    external = public['external_records']
    path = Path(external['path'])
    audit.check(path.stat().st_size == external['bytes'] and sha(path) == external['sha256'], 'external_records_binding', source=name)
    audit.check(public['source_path'] == name and public['source_sha256'] == digest and public['bytes'] == len(data), 'public_source_identity', source=name)
    rows = {kind: [] for kind in ('source', 'contexts', 'units', 'fact_pair', 'lookup_self_consistency')}
    with path.open() as stream:
        for line in stream:
            row = json.loads(line)
            kind = row.get('record_type')
            if audit.check(kind in rows, 'recognized_external_record_type', source=name):
                rows[kind].append(row)
    audit.check(len(rows['source']) == 1 and rows['source'][0]['source'] == entry, 'external_source_header', source=name)
    metadata = rows['source'][0]['reader_metadata']
    if metadata:
        audit.check(metadata['source_sha256'] == digest and metadata['source_version'] == version and metadata['source_bytes'] == len(data), 'reader_source_metadata', source=name)
        audit.check(metadata.get('external_resources_loaded') == 0 and metadata.get('taxonomy_validated') is False and metadata.get('natural_language_qa') is False, 'reader_scope_metadata', source=name)
    else:
        audit.check(any(x.get('component') == 'reader' for x in rows['source'][0]['errors']), 'absent_reader_has_recorded_failure', source=name)
    pairs = rows['fact_pair']
    recomputed, discrepancies = recompute_pairs(pairs, entry['expected_nonfraction_count'], audit, name)
    for counter, expected in recomputed.items():
        audit.check(same_counts(public[counter], expected), 'public_' + counter, source=name)
    audit.check(public['discrepancies'] == discrepancies, 'public_discrepancy_replay', source=name)
    for discrepancy in public['discrepancies']:
        audit.check(set(discrepancy) == SAFE_DISCREPANCY, 'public_discrepancy_safe_keys', source=name, ordinal=discrepancy.get('fact_ordinal'))
        # Only fixed status/reason identifiers and DOM locators are permitted;
        # normalized/raw/transformed values cannot enter the public schema.
        labels = [discrepancy.get(k) for k in ('reason', 'reader_status', 'reference_status', 'reference_reason')]
        labels += discrepancy.get('reader_issues', [])
        audit.check(all(x is None or (isinstance(x, str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', x)) for x in labels), 'public_discrepancy_symbolic_reasons', source=name, ordinal=discrepancy.get('fact_ordinal'))
    facts = [p['typed'] for p in pairs if p.get('typed') is not None]
    resources = {}
    for kind in ('contexts', 'units'):
        pool = {}
        for row in rows[kind]:
            record = row['record']
            pool.setdefault(record['id'], []).append(record)
            audit.anchor(record.get('anchor'), data, digest, version, kind + '_resource', name)
        resources[kind] = pool
    anchors = Counter()
    for ordinal, fact in enumerate(facts):
        audit.check(fact['fact_ordinal'] == ordinal and fact['source_sha256'] == digest and fact['source_version'] == version, 'typed_occurrence_identity', source=name, ordinal=ordinal)
        verified = audit.anchor(fact.get('anchor'), data, digest, version, 'fact_anchor', name, ordinal)
        anchors['fact_anchors_verified' if verified else 'fact_anchors_failed'] += 1
        for kind, ref, pool in [('context', 'context_id', resources['contexts']), ('unit', 'unit_id', resources['units'])]:
            targets = pool.get(fact.get(ref), [])
            if len(targets) != 1:
                anchors[kind + '_reference_not_unique'] += 1
                continue
            target = targets[0]['anchor']
            matches = [a for a in fact['evidence_anchors'] if a.get('dom_path') == target['dom_path']]
            valid = len(matches) == 1 and matches[0] == target
            audit.check(valid, kind + '_dependency_is_bound_target', source=name, ordinal=ordinal)
            anchors[kind + '_anchors_verified' if valid else kind + '_anchors_failed'] += 1
        for anchor in fact['evidence_anchors']:
            valid = audit.anchor(anchor, data, digest, version, 'evidence_anchor', name, ordinal)
            anchors['all_evidence_anchor_bytes_verified' if valid else 'all_evidence_anchor_bytes_failed'] += 1
    audit.check(same_counts(anchors, public['anchor_counts']), 'public_anchor_count_replay', source=name)
    audit.check(not public.get('anchor_failures'), 'no_reported_anchor_failures', source=name)
    lookups = audit_lookup(facts, rows['lookup_self_consistency'], public['lookup_probes'], maximum, audit, name, digest, version)
    audit.check(same_counts(lookups, public['lookup_counts']), 'public_lookup_counts', source=name)
    private_errors = [{k: v for k, v in x.items() if k != 'external_error'} for x in rows['source'][0]['errors']]
    audit.check(private_errors == public['technical_errors'], 'technical_error_record_replay', source=name)
    recomputed['anchor_counts'], recomputed['lookup_counts'] = dict(anchors), lookups
    return {'source_path': name, 'source_sha256': digest, 'external_records_sha256': external['sha256'], 'population_rows': len(pairs), 'recomputed': recomputed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--protocol', type=Path, required=True)
    parser.add_argument('--summary', type=Path, required=True)
    parser.add_argument('--sources', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit('Refusing to overwrite an existing execution audit')
    started = time.perf_counter()
    protocol, summary = json.loads(args.protocol.read_text()), json.loads(args.summary.read_text())
    audit = Audit()
    audit.check(protocol.get('schema_version', '').startswith('typed_reader_execution_protocol_v15') and bool(protocol.get('frozen_at_utc')), 'frozen_protocol_schema')
    audit.check(summary.get('schema_version', '').startswith('typed_reader_execution_v15') and summary.get('status', '').startswith('complete'), 'completed_execution_schema')
    audit.check(summary['protocol_sha256'] == sha(args.protocol), 'protocol_digest')
    matching_runners = [name for name, digest in protocol['code_bindings'].items() if name.startswith('scripts/run_typed_reader_v15') and digest == summary['script_sha256']]
    audit.check(len(matching_runners) == 1, 'runner_digest_bound_in_protocol')
    audit.check(REQUIRED_CODE.issubset(protocol['code_bindings']), 'required_code_bindings')
    for collection in ('code_bindings', 'input_bindings'):
        for name, digest in protocol[collection].items():
            path = (ROOT / name).resolve()
            audit.check(path.is_relative_to(ROOT.resolve()), 'repository_binding_confined')
            audit.binding(path, digest, collection + '_digest')
    audit.binding(ROOT / protocol.get('design_path', 'docs/typed_reader_study_design_v15.txt'), protocol['design_sha256'], 'design_digest')
    runtime = protocol['reference_runtime']
    dependency_bindings = {}
    for name, digest in runtime['files'].items():
        path = (Path(runtime['path']) / name).resolve()
        audit.binding(path, digest, 'reference_runtime_digest')
        dependency_bindings[str(path)] = digest
    for name, digest in runtime.get('additional_files', {}).items():
        audit.binding(name, digest, 'additional_runtime_digest')
        dependency_bindings[str(Path(name).resolve())] = digest
    for row in summary.get('runtime_dependency_receipt', {}).get('imported_files', []):
        audit.check(dependency_bindings.get(str(Path(row['path']).resolve())) == row['sha256'], 'imported_dependency_prebound')
    audit.check(not summary.get('dependency_validation_errors'), 'no_dependency_validation_errors')
    audit.check(protocol['natural_QA_predictions'] == summary['natural_QA_predictions'] == summary['model_calls'] == 0, 'zero_natural_QA_and_model_calls')
    entries, records = protocol['sources'], summary['records']
    audit.check(len(entries) == len(records) == summary['source_denominator'] == 12, 'twelve_object_population')
    audit.check(sum(x['expected_nonfraction_count'] for x in entries) == protocol['expected_total_nonfraction_count'] == summary['expected_nonfraction_denominator'] == 23651, 'historical_full_population')
    historical = json.loads((ROOT / 'data/fresh_source_audit_v14/source_identity_structure.json').read_text())['records']
    audit.check([(x['source_path'], x['expected_sha256'], x['expected_bytes']) for x in entries] == [(x['source_path'], x['source_sha256'], x['bytes']) for x in historical], 'historical_population_and_order')
    maximum = protocol['lookup_integration']['max_distinct_keys_per_source']
    audit.check(maximum == 8 and protocol['lookup_integration']['wrong_source_sha256_probe'] is True, 'lookup_sample_design')
    outputs = [audit_source(e, r, args.sources, maximum, audit) for e, r in zip(entries, records)]
    totals = {name: Counter() for name in COUNTERS}
    for row in outputs:
        for name in COUNTERS:
            totals[name].update(row['recomputed'][name])
    for name, expected in totals.items():
        audit.check(same_counts(summary['totals'][name], expected), 'global_' + name)
    external_dirs = {str(Path(row['external_records']['path']).parent) for row in records}
    audit.check(len(external_dirs) == 1, 'single_attempt_directory')
    if len(external_dirs) == 1:
        folder = Path(next(iter(external_dirs)))
        start, finish = json.loads((folder / 'attempt_started.json').read_text()), json.loads((folder / 'attempt_finished.json').read_text())
        audit.check(start['protocol_sha256'] == finish['protocol_sha256'] == sha(args.protocol), 'attempt_protocol_binding')
        audit.check(finish['public_output_sha256'] == sha(args.summary) and finish['status'] == summary['status'], 'attempt_final_output_binding')
        audit.check(datetime.fromisoformat(protocol['frozen_at_utc']) <= datetime.fromisoformat(start['started_at_utc']) <= datetime.fromisoformat(summary['finished_at_utc']), 'freeze_precedes_execution')
    counts, probes = totals['counts'], totals['lookup_counts']
    technical = (any(r['technical_errors'] for r in records) or summary.get('dependency_validation_errors') or counts.get('count_matches_expected', 0) != 12 or counts.get('alignment_failures', 0) or counts.get('canonical_contract_failures', 0) or any(r.get('anchor_failures') for r in records) or probes.get('seed_candidate_missing', 0) or probes.get('wrong_source_sha_not_blocked', 0))
    expected_status = 'complete_with_technical_failures' if technical else 'complete_with_disagreements' if counts.get('exact_numeric_disagreements', 0) else 'complete'
    audit.check(summary['status'] == expected_status, 'completion_status_replay')
    report = {'schema_version': 'typed_reader_execution_audit_v15', 'status': 'passed' if not audit.failures else 'failed', 'audited_at_utc': datetime.now(timezone.utc).isoformat(), 'audit_script_sha256': sha(Path(__file__)), 'protocol_sha256': sha(args.protocol), 'public_summary_sha256': sha(args.summary), 'checks': audit.checks, 'failures': audit.failures, 'source_records': outputs, 'elapsed_seconds': time.perf_counter() - started, 'scope': 'Separate-agent arithmetic/count/metadata/byte replay from recorded execution; no import or invocation of reader, runner, inventory helper or reference adapter.', 'natural_QA_predictions': 0, 'limitations': ['Record replay does not independently establish parser correctness, taxonomy validation, financial scope, rendered evidence or QA accuracy.', 'Candidate selection is recomputed using stored per-aspect resolution masks; their semantic validity belongs to the separately audited reader contract.', 'Wrong-source refusal is checked as a recorded probe and against authored refusal tests; this audit does not rerun lookup or source decoding.', 'Anchor bytes and resource links are rechecked, while DOM locators are read from recorded outputs; no new independent DOM parser is invoked.']}
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'status': report['status'], 'checks': audit.checks, 'failures': len(audit.failures), 'source_count': len(outputs), 'population_rows': sum(x['population_rows'] for x in outputs), 'output_sha256': sha(args.output)}))
    raise SystemExit(bool(audit.failures))


if __name__ == '__main__':
    main()
