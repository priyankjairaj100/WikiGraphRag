#!/usr/bin/env python3
"""Validate and summarize assistant pack reviews; does not predict model answers."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REVIEWS = ['pfe', 'lly', 'nflx_nvda', 'slb']
POLICIES = ['typed', 'lexical', 'structural', 'decomposition']


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build():
    reference_path = ROOT / 'results/source_references_reviewed_v24.json'
    retrieval_path = ROOT / 'results/retrieval_probe_v24.json'
    refs = json.loads(reference_path.read_text())
    retrieval = json.loads(retrieval_path.read_text())
    supported = {r['item_id']: r for r in refs['records'] if r['status'] == 'supported'}
    actual = {(r['item_id'], r['policy'], r['budget_words']): r for r in retrieval['records']}
    rows, seen, inputs = [], set(), {
        str(p.relative_to(ROOT)): sha(p) for p in (reference_path, retrieval_path)}
    for name in REVIEWS:
        path = ROOT / f'results/semantic_retrieval_review_{name}_v24.json'
        data = json.loads(path.read_text())
        inputs[str(path.relative_to(ROOT))] = sha(path)
        refhash = data.get('reference_sha256', data.get('reviewed_references_sha256',
                      data.get('source_reference_sha256')))
        if refhash != sha(reference_path) or data['protocol_sha256'] != refs['protocol_sha256']:
            raise ValueError('semantic review input identity mismatch')
        for r in data['records']:
            budget = r.get('budget_words', r.get('budget'))
            key = (r['item_id'], r['policy'], budget)
            if key in seen or key[0] not in supported or budget != 4096:
                raise ValueError('duplicate, unscheduled or wrong-budget review')
            seen.add(key)
            pack = actual[key]
            if r['pack_sha256'] != pack['rendered_sha256'] or r['pack_words'] != pack['rendered_words']:
                raise ValueError('semantic review differs from the actual retrieved pack')
            claim_ids = [c['claim_id'] for c in r['claim_reviews']]
            if (len(claim_ids) != len(set(claim_ids))
                    or set(claim_ids) != {c['claim_id'] for c in supported[key[0]]['claims']}):
                raise ValueError('semantic review omitted a frozen claim')
            for c in r['claim_reviews']:
                for field in ('block_ids', 'selected_block_ids', 'partial_evidence_block_ids'):
                    if not set(c.get(field, [])) <= set(pack['selected_block_ids']):
                        raise ValueError('semantic justification cites an unselected block')
            strict = r.get('strict_reference_overall', r['overall'])
            question = r['question_level_overall']
            if strict not in ('sufficient', 'missing', 'uncertain') or question not in ('sufficient', 'missing', 'uncertain'):
                raise ValueError('invalid semantic verdict')
            verdicts = [c['verdict'] for c in r['claim_reviews']]
            if any(v not in ('sufficient', 'missing', 'uncertain') for v in verdicts):
                raise ValueError('invalid claim verdict')
            conjunction = ('missing' if 'missing' in verdicts else
                           'uncertain' if 'uncertain' in verdicts else 'sufficient')
            if strict != conjunction:
                raise ValueError('strict reference verdict contradicts claim judgments')
            rows.append({'item_id': key[0], 'history_id': supported[key[0]]['history_id'],
                         'family': supported[key[0]]['family'], 'policy': key[1], 'budget_words': budget,
                         'strict_reference_overall': strict, 'question_level_overall': question,
                         'review_file': str(path.relative_to(ROOT)), 'pack_sha256': r['pack_sha256']})
    expected = {(i, p, 4096) for i in supported for p in POLICIES}
    if seen != expected:
        raise ValueError('semantic review does not cover all nine questions and four policies')
    by_item = {}
    for item_id, ref in supported.items():
        outcomes = {r['policy']: r['question_level_overall'] for r in rows if r['item_id'] == item_id}
        any_sufficient = any(v == 'sufficient' for v in outcomes.values())
        any_uncertain = any(v == 'uncertain' for v in outcomes.values())
        by_item[item_id] = {'history_id': ref['history_id'], 'family': ref['family'], 'outcomes': outcomes,
                            'classification': 'ordinary_baseline_sufficient' if any_sufficient else
                            'uncertain_residual' if any_uncertain else 'missing_in_all_four_frozen_policies'}
    confirmed = [i for i, r in by_item.items() if r['classification'] == 'missing_in_all_four_frozen_policies']
    histories = {by_item[i]['history_id'] for i in confirmed}
    return {'schema': 'semantic_probe_summary_v24', 'inputs': inputs,
            'protocol_sha256': refs['protocol_sha256'],
            'scheduled_questions': len(refs['records']),
            'source_reference_statuses': dict(Counter(r['status'] for r in refs['records'])),
            'semantic_review_question_count': len(supported), 'semantic_review_pack_count': len(rows),
            'primary_budget_words': 4096,
            'policy_summary': [{'policy': p,
                                'strict_reference': dict(Counter(r['strict_reference_overall'] for r in rows if r['policy'] == p)),
                                'question_level': dict(Counter(r['question_level_overall'] for r in rows if r['policy'] == p))}
                               for p in POLICIES],
            'question_summary': by_item, 'records': rows,
            'conservative_expansion_gate': {'required_questions': 2, 'required_histories': 2,
                                           'confirmed_questions': confirmed, 'confirmed_histories': sorted(histories),
                                           'passed': len(confirmed) >= 2 and len(histories) >= 2,
                                           'uncertain_case_counts_as_success': False,
                                           'scope': 'missing evidence under these four fixed conventional implementations only'},
            'interpretation': [
                'Evidence sufficiency judgments are by separate assistant roles, not reader predictions or independent human gold.',
                'The strict frozen reference conjunction and post-retrieval question-necessity adjudication are both retained.',
                'Unnecessary reference detail and a missing preferred source anchor cannot create a question failure.',
                'The nine supported questions are a source-status subset; all 24 questions remain in the source probe denominator.',
                'Structural versus lexical is a policy-package comparison including source-date routing, not a closure-only ablation.',
                'Typed-card ranking/date routing is not a complete semantic query-to-aspect interpreter; duration omission is a conventional representation limitation.',
                'No new method is evaluated; no source-global insufficiency or automatic coverage guarantee is inferred.'],
            'model_predictions': 0, 'reader_gate_open': False,
            'automatic_coverage_authorized': False, 'confirmatory_study_authorized': False,
            'confirmed_method_advantage': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'results/semantic_probe_summary_v24.json')
    args = parser.parse_args()
    result = build()
    with args.output.open('x') as out:
        json.dump(result, out, indent=2)
        out.write('\n')
    print(json.dumps({k: result[k] for k in ('policy_summary', 'conservative_expansion_gate')}, indent=2))


if __name__ == '__main__':
    main()
