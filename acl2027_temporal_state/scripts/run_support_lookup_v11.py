"""Replay support lookups on the preserved v10 annotated controls; no QA model."""
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import replay_correction_gate_v10 as prior
from temporal_state.corrections import CorrectionPolicy, materialize_corrected_selection
from temporal_state.support_answers import SupportQuery, answer_support


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def main():
    index_path = ROOT / 'data/correction_gate_v10/annotation_index.json'
    index = json.loads(index_path.read_bytes())
    cases = []
    for case in index['cases']:
        path = Path(case['annotation_path'])
        assert digest(path) == case['annotation_sha256']
        annotation, raw, sources, manifests, paths = prior.load_inputs(
            path, ROOT.parent / 'tmp/correction_gate_v10/web_excerpts')
        problem, claims, policy, pairs = prior.build_problem(annotation, sources)
        readings = {m.mention_id: 'selected' for m in problem.mentions}
        nulls = {m.mention_id: None for m in problem.mentions}
        links = nulls | {link.mention_id: link.link_id for link in problem.links}
        policies = {
            'no_withdrawal': CorrectionPolicy(source_authorities=policy.source_authorities),
            'direct_withdrawal': CorrectionPolicy(source_authorities=policy.source_authorities,
                                                 correction_evidence=policy.correction_evidence),
            'dependency_withdrawal': policy,
        }
        outputs = []
        for name, variant in policies.items():
            selected_links = nulls if name == 'no_withdrawal' else links
            memory = materialize_corrected_selection(problem, readings, selected_links, claims, variant)
            for claim in claims.values():
                answer = answer_support(SupportQuery(claim.claim_id), memory=memory,
                    problem=problem, reading_ids=readings, link_ids=selected_links,
                    claims=claims, policy=variant)
                assert answer.selected_assertion == claim
                assert claim.start.lower is claim.start.upper is None
                assert claim.end.lower is claim.end.upper is None
                evidence = []
                for item in answer.evidence:
                    row = asdict(item)
                    row['evidence_spans'] = [prior.projected_span(s) for s in row['evidence_spans']]
                    row['evidence_id'] = item.evidence_id
                    evidence.append(row)
                outputs.append({'policy': name, 'assertion_id': answer.assertion_id,
                    'status': answer.status, 'reasons': [asdict(x) for x in answer.reasons],
                    'selected_assertion': prior.projected_claim(asdict(claim)),
                    'evidence': evidence, 'evidence_source_ids': list(answer.evidence_source_ids),
                    'context_source_ids': list(answer.context_source_ids),
                    'operational_cutoff': answer.information_cutoff,
                    'unknown_event_bounds_preserved': True})
        cases.append({'history_id': case['history_id'], 'annotation_sha256': digest(path),
                      'source_bindings': manifests, 'lookups': outputs})
    rows = [row for case in cases for row in case['lookups']]
    report = {'schema_version': 'support_lookup_illustration_v0.11',
        'status': 'completed_annotation_conditioned_assertion_id_lookups',
        'script_sha256': digest(Path(__file__)),
        'module_sha256': digest(ROOT / 'src/temporal_state/support_answers.py'),
        'v10_annotation_index_sha256': digest(index_path), 'histories': cases,
        'counts': {'histories': len(cases), 'lookups': len(rows),
            'active_support': sum(r['status'] == 'active_support' for r in rows),
            'withdrawn_support': sum(r['status'] == 'withdrawn_support' for r in rows),
            'unresolved': sum(r['status'] == 'unresolved' for r in rows)},
        'model_calls': 0, 'natural_language_qa_predictions': 0,
        'input_assertion_ids_and_selections_supplied': True,
        'interpretation': 'An adapter integration check on prior annotations, not extraction, factual truth or a comparison against the native passage reader.',
        'full_sources_or_evidence_quotes_exported': False}
    (ROOT / 'results/support_lookup_v11.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report['counts']))


if __name__ == '__main__':
    main()
