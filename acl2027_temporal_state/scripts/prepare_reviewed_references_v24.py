#!/usr/bin/env python3
"""Apply recorded blind source-review repairs without changing original annotations."""
import argparse
import copy
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAIRS = ('pfe_lly', 'nflx_nvda', 'brk_slb')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build():
    protocol_path = ROOT / 'data/task_probe_v24/protocol.json'
    protocol = json.loads(protocol_path.read_text())
    records, inputs, changes = [], {}, []
    for pair in PAIRS:
        original = ROOT / f'results/source_questions_{pair}_v24.json'
        review_path = ROOT / f'results/source_review_{pair}_v24.json'
        data, review = (json.loads(p.read_text()) for p in (original, review_path))
        for p in (original, review_path):
            inputs[str(p.relative_to(ROOT))] = digest(p)
        reviewed_hash = review.get('reviewed_reference_sha256', review.get('reviewed_artifact_sha256'))
        if reviewed_hash != digest(original):
            raise ValueError('review does not bind the original source references')
        if data['protocol_sha256'] != digest(protocol_path) or review['protocol_sha256'] != digest(protocol_path):
            raise ValueError('protocol mismatch')
        if review.get('automatic_retrieval_outputs_viewed', False) or not review.get('blind_to_retrieval_outcomes', True):
            raise ValueError('source reviewer inspected retrieval outcomes')
        reviews = {r['item_id']: r for r in review['records']}
        if set(reviews) != {r['item_id'] for r in data['records']}:
            raise ValueError('incomplete source review')
        for raw in data['records']:
            row = copy.deepcopy(raw)
            rr = reviews[row['item_id']]
            status = rr.get('reviewed_status', rr.get('recommended_status'))
            if status != row['status']:
                raise ValueError('status change needs explicit versioned adjudication')
            cr = {c['claim_id']: c for c in rr['claim_reviews']}
            if set(cr) != {c['claim_id'] for c in row['claims']}:
                raise ValueError('incomplete claim review')
            for claim in row['claims']:
                rc = cr[claim['claim_id']]
                for witness in rc.get('additional_witness_sets', []):
                    if witness not in claim['witness_sets']:
                        claim['witness_sets'].append(copy.deepcopy(witness))
                        changes.append({'item_id': row['item_id'], 'claim_id': claim['claim_id'],
                                        'operation': 'add_source_reviewed_alternative_witness'})
                for fix in rc.get('corrections', []):
                    operation = fix['operation']
                    if operation == 'replace_statement':
                        claim['statement'] = fix['value']
                    elif operation == 'append_anchor_to_witness_set':
                        claim['witness_sets'][fix['witness_set_index']].append(copy.deepcopy(fix['anchor']))
                    elif operation == 'optional_comparative_witness_set':
                        continue  # Replaced with a noncomparative statement; no new comparative claim.
                    else:
                        raise ValueError('unknown review repair')
                    changes.append({'item_id': row['item_id'], 'claim_id': claim['claim_id'], 'operation': operation})
            if row['history_id'] == 'NFLX' and row['family'] == 'currency_growth':
                for claim in review.get('additional_claims', []):
                    row['claims'].append(copy.deepcopy(claim))
                    changes.append({'item_id': row['item_id'], 'claim_id': claim['claim_id'],
                                    'operation': 'explicit_derived_claim_already_in_original_summary'})
            row['source_review_file'] = str(review_path.relative_to(ROOT))
            records.append(row)
    order = {item['item_id']: i for i, item in enumerate(protocol['items'])}
    if len(records) != len(order) or {r['item_id'] for r in records} != set(order):
        raise ValueError('references do not match the frozen question schedule')
    records.sort(key=lambda r: order[r['item_id']])
    return {'schema': 'reviewed_source_references_v24', 'protocol_sha256': digest(protocol_path),
            'inputs': inputs, 'script_sha256': digest(Path(__file__)),
            'sources': [{'filename': s['external_filename'], 'sha256': s['expected_sha256']}
                        for s in protocol['sources']],
            'records': records, 'applied_changes': changes,
            'model_assisted_review': True, 'human_gold': False,
            'source_review_blind_to_retrieval_outcomes': True,
            'source_status_changes': 0, 'held_out_items': 0,
            'limitation': 'Separate assistant author/reviewer roles; no human gold or global insufficiency certificate.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'results/source_references_reviewed_v24.json')
    args = parser.parse_args()
    result = build()
    with args.output.open('x') as out:
        json.dump(result, out, indent=2, ensure_ascii=False)
        out.write('\n')
    print(json.dumps({'questions': len(result['records']),
                      'claims': sum(len(r['claims']) for r in result['records']),
                      'applied_changes': len(result['applied_changes']),
                      'sha256': digest(args.output)}))


if __name__ == '__main__':
    main()
