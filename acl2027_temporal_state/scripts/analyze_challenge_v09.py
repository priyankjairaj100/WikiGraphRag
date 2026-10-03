#!/usr/bin/env python3
"""Join frozen authored references to all 16 cached measurements, with no tuning."""
from pathlib import Path
import hashlib
import json
import math

ROOT = Path(__file__).resolve().parents[1]


def read(name):
    return json.loads((ROOT / name).read_bytes())


def main():
    paths = ['data/larger_scorer_v09/challenge_selection_protocol.json',
             'data/larger_scorer_v09/challenge_reference_review.json',
             'data/larger_scorer_v09/research/frozen_inputs.json',
             'results/larger_scorer_v09.json']
    protocol, references, frozen, result = map(read, paths)
    assert references['scorer_request_sha256'] == frozen['external_request_sha256']
    assert protocol['research_distribution_count'] == len(result['measurements']) == 16
    labels = {x['item_id']: x for x in references['items']}
    assert len(labels) == 4 and sorted(x['expected_label'] for x in labels.values()) == ['No', 'No', 'Yes', 'Yes']
    rows = []
    for row in result['measurements']:
        reference = labels[row['item_id']]
        assert reference['history_id'] == row['history_id']
        odds = row['yes_logprob'] - row['no_logprob']
        assert math.isfinite(odds) and odds == row['logodds']
        decision = 'Yes' if odds > 0 else 'No' if odds < 0 else 'abstain'
        assert row['decision'] == decision
        rows.append(row | {'expected_label': reference['expected_label'],
                           'matches_authored_reference': decision == reference['expected_label']})
    assert {(r['model_key'], r['condition_id'], r['item_id']) for r in rows} == {
        (m, c, i) for m in ['small', 'large'] for c in ['yes_no', 'no_yes'] for i in labels}
    conditions = []
    for model in ['small', 'large']:
        for condition in ['yes_no', 'no_yes']:
            selected = [r for r in rows if r['model_key'] == model and r['condition_id'] == condition]
            conditions.append({'model_key': model, 'condition_id': condition, 'items': 4,
                               'reference_agreements': sum(r['matches_authored_reference'] for r in selected),
                               'abstentions': sum(r['decision'] == 'abstain' for r in selected)})
    out = {'schema_version': 'authored_challenge_analysis_v0.9',
           'status': 'complete_all_frozen_items_and_conditions',
           'analysis_rule': protocol['analysis']['decision'], 'measurements': rows,
           'condition_summaries': conditions,
           'order_sign_flips_by_model': result['order_sign_flips_by_model'],
           'independent_histories_assumed_for_inference': False,
           'uncertainty_intervals_or_significance_tests': None,
           'natural_accuracy_or_calibration_claim': False,
           'scope': 'Two source histories, four balanced authored contrasts. Counts refer to agreement with model-assisted expected labels; no human gold, naturally sampled accuracy, end-to-end QA, or size-only causal claim.',
           'selection': 'All conditions retained; no best-order selection, averaging, rescaling or retuning.',
           'bindings': [{'path': p, 'sha256': hashlib.sha256((ROOT / p).read_bytes()).hexdigest()} for p in paths]}
    (ROOT / 'results/challenge_analysis_v09.json').write_text(json.dumps(out, indent=2) + '\n')
    print(json.dumps({'conditions': conditions, 'order_sign_flips_by_model': out['order_sign_flips_by_model']}))


if __name__ == '__main__':
    main()
