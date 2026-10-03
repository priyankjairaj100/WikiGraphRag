#!/usr/bin/env python3
"""Freeze source-only per-item label likelihood requests; never invokes a model."""
import argparse
from hashlib import sha256
import importlib.util
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location('ordinal_likelihood_v07', ROOT / 'scripts/prepare_ordinal_scoring_v06.py')
ordinal = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ordinal)
old = ordinal.old
PROTOCOL_PATH = ROOT / 'configs/likelihood_scoring_v07.json'
PROMPT_PATH = ROOT / 'configs/likelihood_scoring_v07_prompt.txt'


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return sha256(value).hexdigest()


def load_json(path):
    return old.strict_json(Path(path).read_text())


def _hash(value, name):
    if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
        raise ValueError(f'{name} must be a lowercase SHA256 digest')


def prepare(source_request, candidate_raw):
    """Return deterministic request + private item mapping, with no scores read."""
    neutral, neutral_manifest = ordinal.prepare(source_request, candidate_raw)
    protocol_bytes, prompt_bytes = PROTOCOL_PATH.read_bytes(), PROMPT_PATH.read_bytes()
    protocol = old.strict_json(protocol_bytes.decode())
    if protocol['primary_condition'] != 'yes_no' or protocol['labels'] != {'positive': 'Yes', 'negative': 'No'}:
        raise ValueError('Unexpected fixed label policy')
    if protocol['conditions'] != [
            {'condition_id': 'yes_no', 'label_order': ['Yes', 'No'], 'role': 'primary'},
            {'condition_id': 'no_yes', 'label_order': ['No', 'Yes'], 'role': 'label_order_diagnostic'}]:
        raise ValueError('Unexpected fixed condition policy')
    context = {k: neutral[k] for k in ('information_cutoff', 'extraction_domains', 'scope_filter', 'sources')}
    source_prefix = 'SOURCE_CONTEXT\n' + canonical(context).decode() + '\n\nITEM\n'
    items = []
    definitions = {'Yes': 'Yes: the complete option is adequately supported under the rule for ITEM.kind.',
                   'No': 'No: the complete option is not adequately supported under the rule for ITEM.kind.'}
    # Item IDs route outputs only; they are not part of the model prompt.
    for condition in protocol['conditions']:
        for original in neutral['items']:
            item = {k: v for k, v in original.items() if k != 'item_id'}
            suffix = '\n\nLABELS\n' + '\n'.join(definitions[label] for label in condition['label_order'])
            suffix += '\n\nIs this complete option adequately supported? Answer with exactly one label.'
            messages = [{'role': 'system', 'content': prompt_bytes.decode()},
                        {'role': 'user', 'content': source_prefix + canonical(item).decode() + suffix}]
            items.append({'item_id': original['item_id'], 'condition_id': condition['condition_id'],
                          'kind': original['kind'], 'messages': messages,
                          'messages_sha256': digest(canonical(messages))})
    request = {
        'schema_version': 'likelihood_scoring_request_v0.7',
        'protocol_sha256': digest(protocol_bytes), 'prompt_sha256': digest(prompt_bytes),
        'source_request_sha256': digest(canonical(source_request)),
        'candidate_raw_output_sha256': digest(candidate_raw.encode()),
        'neutral_request_sha256': digest(canonical(neutral)),
        'max_context_tokens': protocol['max_context_tokens'],
        'labels': protocol['labels'], 'primary_condition': protocol['primary_condition'],
        'items': items}
    manifest = {
        'schema_version': 'likelihood_scoring_manifest_v0.7',
        'request_sha256': digest(canonical(request)),
        'protocol_sha256': request['protocol_sha256'], 'prompt_sha256': request['prompt_sha256'],
        'source_request_sha256': request['source_request_sha256'],
        'candidate_raw_output_sha256': request['candidate_raw_output_sha256'],
        'neutral_request_sha256': request['neutral_request_sha256'],
        'item_mapping': neutral_manifest['item_mapping'],
        'items_per_condition': len(neutral['items']), 'prompt_count': len(items),
        'condition_ids': [c['condition_id'] for c in protocol['conditions']],
        'primary_condition': protocol['primary_condition'],
        'score_definition': protocol['score_definition'],
        'decoder': protocol['decoder'], 'validation': neutral_manifest['validation'],
        'prepared_without_model_execution': True,
        'input_token_count_verified': False,
        'source_characters': {s['source_id']: len(s['text']) for s in source_request['sources']},
        'prompt_content_characters': {'min': min(sum(len(m['content']) for m in i['messages']) for i in items),
                                      'max': max(sum(len(m['content']) for m in i['messages']) for i in items)},
        'provenance_limit': 'Candidates are the recorded unversioned v0.6 generation; only the new scorer may be pinned. This protocol freezes scoring before its outputs are read, not a prospective untouched benchmark.'}
    return request, manifest


def validate_prepared(source_request, candidate_raw, request, manifest):
    expected_request, expected_manifest = prepare(source_request, candidate_raw)
    if request != expected_request or manifest != expected_manifest:
        raise ValueError('Likelihood request or manifest differs from deterministic source-only preparation')
    return {'items_per_condition': manifest['items_per_condition'], 'prompt_count': manifest['prompt_count'],
            'request_sha256': manifest['request_sha256']}


def validate_measurements(raw, request, *, backend_binding_sha256=None):
    """Validate complete full-vocabulary measurements and compute raw log-odds.

    This verifies data contracts, not whether an executor honestly ran a model.
    Backend provenance and token-prefix verification need the separately bound
    backend receipt; a caller must validate that receipt before constructing a cache.
    """
    value = old.strict_json(raw) if isinstance(raw, str) else raw
    required = {'schema_version', 'request_sha256', 'backend_binding_sha256', 'rows'}
    if not isinstance(value, dict) or set(value) != required or value['schema_version'] != 'likelihood_measurements_v0.7':
        raise ValueError('Unexpected likelihood measurement fields/version')
    for name in ('request_sha256', 'backend_binding_sha256'):
        _hash(value[name], name)
    if value['request_sha256'] != digest(canonical(request)):
        raise ValueError('Measurement request hash mismatch')
    if backend_binding_sha256 is not None and value['backend_binding_sha256'] != backend_binding_sha256:
        raise ValueError('Measurement backend binding hash mismatch')
    expected = {(i['condition_id'], i['item_id']): i for i in request['items']}
    if len(expected) != len(request['items']):
        raise ValueError('Duplicate prepared item/condition')
    if not isinstance(value['rows'], list):
        raise ValueError('Measurement rows must be a list')
    row_fields = {'item_id', 'condition_id', 'messages_sha256', 'rendered_prompt_sha256',
                  'input_token_ids_sha256', 'input_tokens', 'yes_token_id', 'no_token_id',
                  'yes_logprob', 'no_logprob'}
    seen, labels, scores = set(), set(), {}
    for row in value['rows']:
        if not isinstance(row, dict) or set(row) != row_fields:
            raise ValueError('Unexpected likelihood measurement row fields')
        if not isinstance(row['condition_id'], str) or not isinstance(row['item_id'], str):
            raise ValueError('Measurement IDs must be strings')
        key = row['condition_id'], row['item_id']
        if key not in expected or key in seen:
            raise ValueError('Unknown or duplicate measurement item/condition')
        seen.add(key)
        for name in ('messages_sha256', 'rendered_prompt_sha256', 'input_token_ids_sha256'):
            _hash(row[name], name)
        if row['messages_sha256'] != expected[key]['messages_sha256']:
            raise ValueError('Measurement messages hash mismatch')
        if type(row['input_tokens']) is not int or not 1 <= row['input_tokens'] < request['max_context_tokens']:
            raise ValueError('Invalid input token count or context overflow')
        for name in ('yes_token_id', 'no_token_id'):
            if type(row[name]) is not int or row[name] < 0:
                raise ValueError('Label token IDs must be nonnegative integers')
        if row['yes_token_id'] == row['no_token_id']:
            raise ValueError('Yes/No labels must be distinct tokens')
        labels.add((row['yes_token_id'], row['no_token_id']))
        for name in ('yes_logprob', 'no_logprob'):
            lp = row[name]
            if type(lp) not in (int, float) or not math.isfinite(lp) or lp > 1e-6:
                raise ValueError('Label log probability must be finite and nonpositive')
        if math.exp(row['yes_logprob']) + math.exp(row['no_logprob']) > 1.00001:
            raise ValueError('Distinct label probabilities exceed one')
        score = row['yes_logprob'] - row['no_logprob']
        if not math.isfinite(score):
            raise ValueError('Nonfinite log-odds')
        scores[key] = score
    if seen != set(expected):
        raise ValueError('Missing likelihood measurements, including unresolved/no_link or diagnostic condition')
    if len(labels) != 1:
        raise ValueError('Label token IDs changed across prompts or conditions')
    return {'schema_version': 'likelihood_validation_v0.7', 'request_sha256': value['request_sha256'],
            'backend_binding_sha256': value['backend_binding_sha256'], 'rows': len(seen),
            'all_items_and_conditions_measured': True, 'label_token_ids': {'Yes': next(iter(labels))[0], 'No': next(iter(labels))[1]},
            'scores_are_calibrated_probabilities': False,
            'score_definition': 'yes_logprob - no_logprob; natural logarithms; no rescaling',
            'scores': [{'condition_id': key[0], 'item_id': key[1], 'score': scores[key]} for key in sorted(scores)],
            'validation_scope': 'Exact coverage, bound messages and finite label likelihoods; actual full-vocabulary evaluation and token-prefix verification require the separate backend receipt.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-request', type=Path, required=True)
    parser.add_argument('--candidates', type=Path, required=True)
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    args = parser.parse_args()
    request, manifest = prepare(load_json(args.source_request), args.candidates.read_text())
    for path, value in ((args.request, request), (args.manifest, manifest)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(canonical(value) + b'\n')
    print(json.dumps({k: manifest[k] for k in ('items_per_condition', 'prompt_count', 'request_sha256', 'prompt_content_characters')}))


if __name__ == '__main__':
    main()
