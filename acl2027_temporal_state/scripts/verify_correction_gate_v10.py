"""Offline bindings and policy-record checks; no model or semantic certification."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads((ROOT / path).read_bytes())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--external', action='store_true',
                        help='Also check surviving working sources and annotation spans.')
    args = parser.parse_args()
    index = read('data/correction_gate_v10/annotation_index.json')
    for path, expected in index['inputs'].items():
        assert sha(ROOT / path) == expected, path
    capture = read('data/correction_gate_v10/capture_manifest_v10.json')
    assert capture['source_count'] == len(capture['records']) == 11
    assert all(r['curl_returncode'] == 7 and r['http_status'] == 0
               for r in capture['records'])
    web = read('data/correction_gate_v10/web_excerpt_manifest_v10.json')
    assert web['source_count'] == len(web['records']) == 4
    assert web['web_tool_call_count'] == 12
    register = read('data/correction_gate_v10/screen_register.json')
    assert register['candidate_histories_screened'] == len(register['histories']) == 12
    assert register['full_prefix_admitted'] == 0
    cases = {c['history_id']: c for c in index['cases']}
    rows, external_sources, external_spans = [], set(), 0
    for path in ('results/correction_replay_plug_v10.json',
                 'results/correction_replay_molson_v10.json'):
        result = read(path)
        case = cases[result['history_id']]
        assert result['annotation_sha256'] == case['annotation_sha256']
        assert result['script_sha256'] == sha(ROOT / 'scripts/replay_correction_gate_v10.py')
        assert result['schema_document_sha256'] == sha(ROOT / 'docs/correction_gate_replay_schema_v10.txt')
        for core, expected in result['core_sha256'].items():
            assert sha(ROOT / core) == expected, core
        assert result['source_representation'] == 'web_tool_noncontiguous_excerpt_projection'
        assert result['qa_predictions'] == result['model_calls'] == 0
        assert not result['decoder_run'] and not result['method_advantage_claim']
        assert not result['full_source_text_in_output'] and not result['evidence_quotes_in_output']
        bound = {s['source_id']: s['sha256'] for s in result['source_bindings']}
        assert bound == {s['source_id']: s['sha256'] for s in case['sources']}
        pairs = {(p['replacement_claim_id'], p['target_claim_id']) for p in result['correction_pairs']}
        assert pairs == {(p['replacement_claim_id'], p['target_claim_id']) for p in case['corrections']}
        all_ids = {c['claim_id'] for c in case['claims']}
        targets = {target for _, target in pairs}
        policies = {p['policy']: p for p in result['policies']}
        assert set(policies) == {'no_withdrawal', 'direct_withdrawal', 'dependency_withdrawal'}
        assert set(policies['no_withdrawal']['active_claim_ids']) == all_ids
        assert set(policies['direct_withdrawal']['active_claim_ids']) == all_ids - targets
        assert set(policies['dependency_withdrawal']['active_claim_ids']) == all_ids - targets
        assert result['dependency_additional_inactive_claim_ids'] == []
        for p in policies.values():
            assert p['unknown_endpoints_preserved'] and p['original_selected_records_preserved']
            assert all(p['unaffected_claims_retained'].values())
        if args.external:
            annotation_path = Path(case['annotation_path'])
            assert sha(annotation_path) == case['annotation_sha256']
            annotation = json.loads(annotation_path.read_bytes())
            raw_path = Path(case['raw_annotation_path'])
            assert sha(raw_path) == case['raw_annotation_sha256']
            raw = json.loads(raw_path.read_bytes())
            stripped = dict(annotation)
            del stripped['source_representation']
            assert stripped == raw
            source_text = {}
            for source in annotation['sources']:
                source_path = ROOT.parent / 'tmp/correction_gate_v10/web_excerpts' / source['local_path']
                assert sha(source_path) == source['sha256']
                source_text[source['source_id']] = source_path.read_bytes().decode('utf-8')
                external_sources.add(source['source_id'])
            by_claim = {c['claim_id']: c for c in annotation['claims']}
            spans = [(c['source_id'], e) for c in annotation['claims'] + annotation['authorities']
                     for e in c['evidence_spans']]
            spans += [(by_claim[c['replacement_claim_id']]['source_id'], c['reference_span'])
                      for c in annotation['corrections']]
            spans += [(by_claim[d['dependent_claim_id']]['source_id'], e)
                      for d in annotation['dependencies'] for e in d['evidence_spans']]
            for sid, e in spans:
                assert source_text[sid][e['start']:e['end']] == e['quote']
                external_spans += 1
        rows.append({'history_id': result['history_id'], 'result_path': path,
                     'result_sha256': sha(ROOT / path), 'policies': len(policies),
                     'direct_equals_dependency_active_ids': True})
    out = {'schema_version': 'correction_gate_verification_v0.10',
           'status': 'passed_bindings_and_declared_policy_records',
           'histories': rows, 'policy_states': sum(r['policies'] for r in rows),
           'external_checked': args.external, 'external_sources_checked': len(external_sources),
           'external_span_occurrences_checked': external_spans,
           'semantic_entailment_certified': False, 'model_calls': 0, 'network_calls': 0,
           'source_scope': 'Web-tool excerpt projections only; no complete PDF/version verification.'}
    filename = 'correction_gate_external_verification_v10.json' if args.external else 'correction_gate_verification_v10.json'
    (ROOT / 'results' / filename).write_text(json.dumps(out, indent=2) + '\n')
    print(json.dumps(out))


if __name__ == '__main__':
    main()
