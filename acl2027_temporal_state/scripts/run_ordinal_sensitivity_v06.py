#!/usr/bin/env python3
"""Replay a frozen ordinal cache under three declared monotone score codings.

No model is called and no QA or gold file is read. Mappings are taken from the
predeclared plan; no transform is chosen on the basis of the resulting states.
"""
import argparse
from collections.abc import Mapping
from dataclasses import asdict, replace
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from temporal_state.bounded import claim_to_dict
from temporal_state.correction_io import (dump_correction_cache, load_correction_cache,
    make_correction_cache, policy_to_dict)
from temporal_state.decoder import Limits
from temporal_state.objective_pipeline import decode_objective
from temporal_state.scored_io import make_cache


METHODS = ('independent', 'iterative', 'iterative_restarts', 'joint_exact')
MAPPINGS = ('identity', 'signed_square', 'compressed_extremes')
SETTINGS = {'objective_mode': 'historical', 'methods': list(METHODS),
            'limits': asdict(Limits()), 'restarts': 10, 'seed': 7,
            'beta': 1, 'penalties': [], 'overflow_policy': 'fail_no_pruning'}


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return sha256(value).hexdigest()


def thaw(value):
    if isinstance(value, Mapping):
        return {key: thaw(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [thaw(item) for item in value]
    return value


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate JSON key: ' + key)
            result[key] = value
        return result

    def constant(value):
        raise ValueError('Nonfinite JSON value: ' + value)

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def validate_plan(plan):
    expected = {'schema_version', 'purpose', 'declaration_timing', 'baseline',
                'transformations', 'apply_to', 'objective', 'methods',
                'observables', 'prohibited_claims', 'limit'}
    if set(plan) != expected or plan['schema_version'] != 'ordinal_sensitivity_plan_v0.6':
        raise ValueError('Unexpected sensitivity plan fields/version')
    if (plan['baseline'] != 'identity' or plan['objective'] != 'historical'
            or plan['methods'] != list(METHODS)
            or set(plan['transformations']) != set(MAPPINGS)):
        raise ValueError('Only the declared three-map historical comparison is supported')
    expected_keys = {str(value) for value in range(-3, 4)}
    for name, mapping in plan['transformations'].items():
        if set(mapping) != expected_keys:
            raise ValueError('A mapping must cover each ordinal integer from -3 through 3')
        values = [mapping[str(value)] for value in range(-3, 4)]
        if any(type(value) is not int for value in values):
            raise ValueError('Mapping values must be integer scores')
        if any(left >= right for left, right in zip(values, values[1:])):
            raise ValueError('Each mapping must be strictly increasing')
        if name == 'identity' and values != list(range(-3, 4)):
            raise ValueError('Identity baseline must preserve every score')


def non_score_payload(cache):
    """Capture unchanged inference inputs; omit only score values/provenance."""
    problem = asdict(cache.base.problem)
    problem.pop('score_provenance')
    for mention in problem['mentions']:
        mention.pop('null_score')
        for reading in mention['readings']:
            reading.pop('unary_score')
    for link in problem['links']:
        link.pop('score')
    return {'problem_without_scores': problem,
            'claims': [{'mention_id': key[0], 'reading_id': key[1],
                        'claim': claim_to_dict(claim)}
                       for key, claim in sorted(cache.base.claims.items())],
            'aliases': thaw(cache.base.aliases), 'policy': policy_to_dict(cache.policy)}


def derive(parent, mapping_name, mapping, lineage):
    problem = parent.base.problem
    if problem.beta != 1 or problem.penalties:
        raise ValueError('Ordinal recoding requires beta=1 and no penalties')
    if (any(reading.violations for mention in problem.mentions for reading in mention.readings)
            or any(link.violations for link in problem.links)):
        raise ValueError('Ordinal recoding cannot silently transform penalty terms')
    count = {'resolved_unary': 0, 'unresolved_unary': 0, 'pair': 0, 'null': 0}

    def mapped(value, kind):
        if type(value) is not int or str(value) not in mapping:
            raise ValueError('Parent cache must contain actual ordinal integers in [-3,3]')
        count[kind] += 1
        return mapping[str(value)]

    definition = ('Deterministically transformed cached ordinal scores; no new model execution. '
                  f'Predeclared mapping={mapping_name}; applied identically to resolved/unresolved '
                  'unary, pair and null scores; beta=1, no penalties. Additive compatibility '
                  'heuristic, not calibrated probability or measured correctness.')
    transformed = replace(problem,
        mentions=tuple(replace(mention,
            readings=tuple(replace(reading, unary_score=mapped(reading.unary_score,
                'unresolved_unary' if reading.unresolved else 'resolved_unary'))
                for reading in mention.readings),
            null_score=mapped(mention.null_score, 'null')) for mention in problem.mentions),
        links=tuple(replace(link, score=mapped(link.score, 'pair')) for link in problem.links),
        score_provenance=definition)
    config_sha = digest(canonical(lineage))
    provenance = thaw(parent.base.provenance)
    provenance.update(config_sha256=config_sha, score_definition=definition)
    base = make_cache(transformed, parent.base.claims, thaw(parent.base.aliases), provenance)
    policy_provenance = thaw(parent.provenance)
    policy_provenance.update(config_sha256=config_sha,
        policy_description=policy_provenance['policy_description'] +
            ' Policy copied unchanged into an ordinal-recoding child cache; no new annotation execution.')
    child = make_correction_cache(base, parent.policy, policy_provenance)
    if non_score_payload(child) != non_score_payload(parent):
        raise ValueError('Recoding changed a non-score inference input')
    for field in ('evidence_type', 'candidate_model_id', 'candidate_model_revision',
                  'scorer_model_id', 'scorer_model_revision', 'prompt_sha256'):
        if child.base.provenance[field] != parent.base.provenance[field]:
            raise ValueError('Recoding changed originating execution provenance: ' + field)
    return child, count


def replay(child, mapping_name):
    rows = []
    for method in METHODS:
        state = decode_objective(child, method, objective_mode='historical',
            limits=Limits(**SETTINGS['limits']), restarts=SETTINGS['restarts'], seed=SETTINGS['seed'])
        diagnostics = asdict(state.result.diagnostics)
        diagnostics.pop('elapsed_seconds')
        rows.append({'mapping': mapping_name, 'method': method,
            'objective_mode': 'historical', 'objective_identity_sha256': state.objective_digest,
            'derived_correction_cache_sha256': child.digest,
            'objective_within_mapping_only': state.result.objective,
            'selected_reading_ids': dict(state.result.reading_ids),
            'selected_link_ids': dict(state.result.link_ids),
            'active_claim_ids': sorted(claim.claim_id for claim in state.memory.claims),
            'active_interval_envelopes': {claim_id: asdict(envelope)
                for claim_id, envelope in sorted(state.memory.envelopes.items())},
            'restatement_components': [list(group) for group in state.result.restatement_components],
            'search_diagnostics_without_timing': diagnostics})
    exact = next(row for row in rows if row['method'] == 'joint_exact')
    for row in rows:
        gap = exact['objective_within_mapping_only'] - row['objective_within_mapping_only']
        if gap < 0:
            raise ValueError('Heuristic score exceeded exhaustive optimum in the same map')
        row['within_mapping_exact_gap'] = gap
    return rows


def compare_identity(rows):
    baseline = {row['method']: row for row in rows if row['mapping'] == 'identity'}
    comparisons = []
    for row in rows:
        ref = baseline[row['method']]
        readings = sorted(mid for mid in ref['selected_reading_ids']
                          if row['selected_reading_ids'][mid] != ref['selected_reading_ids'][mid])
        links = sorted(mid for mid in ref['selected_link_ids']
                       if row['selected_link_ids'][mid] != ref['selected_link_ids'][mid])
        active, ref_active = set(row['active_claim_ids']), set(ref['active_claim_ids'])
        envelopes = sorted(claim_id for claim_id in active | ref_active
            if row['active_interval_envelopes'].get(claim_id) != ref['active_interval_envelopes'].get(claim_id))
        comparisons.append({'mapping': row['mapping'], 'method': row['method'],
            'reference': {'mapping': 'identity', 'method': row['method']},
            'changed_reading_mentions': readings, 'changed_link_mentions': links,
            'new_active_claim_ids': sorted(active-ref_active),
            'removed_active_claim_ids': sorted(ref_active-active),
            'changed_interval_envelope_claim_ids': envelopes,
            'same_selected_readings': not readings, 'same_selected_links': not links,
            'same_active_claim_ids': active == ref_active,
            'same_active_interval_envelopes': not envelopes,
            'score_across_mappings_compared': False})
    return comparisons


def main():
    folder = ROOT / 'data/natural_model_pilot_v06'
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, default=folder / 'ordinal_sensitivity_plan.json')
    parser.add_argument('--parent-cache', type=Path, default=folder / 'shared_cache.json')
    parser.add_argument('--cache-dir', type=Path, default=folder / 'sensitivity')
    parser.add_argument('--result', type=Path, default=ROOT / 'results/ordinal_sensitivity_v06.json')
    args = parser.parse_args()
    plan_bytes, parent_bytes = args.plan.read_bytes(), args.parent_cache.read_bytes()
    plan = strict_json(plan_bytes)
    validate_plan(plan)
    parent = load_correction_cache(args.parent_cache)
    if (parent.base.provenance['evidence_type'] != 'unversioned_model_scores'
            or parent.base.provenance['candidate_model_revision'] is not None
            or parent.base.provenance['scorer_model_revision'] is not None):
        raise ValueError('This pilot expects the declared unversioned parent model cache')
    parent_hash, plan_hash = digest(parent_bytes), digest(plan_bytes)
    unchanged_hash = digest(canonical(non_score_payload(parent)))
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    rows, cache_records = [], []
    for mapping_name in MAPPINGS:
        mapping = plan['transformations'][mapping_name]
        lineage = {'schema_version': 'ordinal_recoding_lineage_v0.6',
            'parent_cache_file_sha256': parent_hash, 'parent_correction_cache_sha256': parent.digest,
            'parent_base_cache_sha256': parent.base.digest,
            'parent_score_provenance': thaw(parent.base.provenance),
            'plan_file_sha256': plan_hash, 'mapping_name': mapping_name, 'mapping': mapping,
            'mapping_sha256': digest(canonical(mapping)), 'settings': SETTINGS,
            'runner_sha256': digest(Path(__file__).read_bytes()),
            'unchanged_non_score_inference_payload_sha256': unchanged_hash,
            'new_model_executions': 0}
        child, counts = derive(parent, mapping_name, mapping, lineage)
        cache_path = args.cache_dir / f'{mapping_name}_cache.json'
        lineage_path = args.cache_dir / f'{mapping_name}_lineage.json'
        dump_correction_cache(child, cache_path)
        record = {'lineage': lineage, 'derived_correction_cache_sha256': child.digest,
            'derived_base_cache_sha256': child.base.digest,
            'derived_cache_file_sha256': digest(cache_path.read_bytes()),
            'configuration_sha256': child.base.provenance['config_sha256'],
            'transformed_score_counts': counts, 'non_score_inputs_identical': True}
        lineage_path.write_bytes(canonical(record) + b'\n')
        cache_records.append(record)
        rows.extend(replay(child, mapping_name))
    if args.plan.read_bytes() != plan_bytes or args.parent_cache.read_bytes() != parent_bytes:
        raise ValueError('Parent cache or plan changed while sensitivity replay was running')
    comparisons = compare_identity(rows)
    changed = [row for row in comparisons if not all(row[key] for key in (
        'same_selected_readings', 'same_selected_links', 'same_active_claim_ids',
        'same_active_interval_envelopes'))]
    result = {'schema_version': 'ordinal_sensitivity_replay_v0.6',
        'plan': plan, 'plan_file_sha256': plan_hash,
        'parent_correction_cache_sha256': parent.digest, 'parent_cache_file_sha256': parent_hash,
        'settings': SETTINGS, 'derived_caches': cache_records, 'runs': rows,
        'comparisons_to_identity': comparisons,
        'number_of_replays': len(rows), 'number_of_mappings': len(MAPPINGS),
        'changed_mapping_method_pairs_vs_identity': len(changed),
        'qa_or_gold_loaded': False, 'new_model_executions': 0,
        'best_mapping_selected': False, 'accuracy_measured': False,
        'timings_omitted': True,
        'interpretation': ('Within each map, exact gaps are optimization diagnostics only. '
            'State agreement across these three monotone score codings is not correctness, '
            'calibration, or invariance to all possible ordinal recodings.'),
        'limits': ['One previously inspected development history and one unversioned model candidate/scoring run.',
            'Score categories have an ordinal judgment interpretation; additive spacing remains heuristic.',
            'All alternatives retain their original source context and declared evidence; no new candidates were generated.',
            'Historical objective only; this sensitivity probe does not test active-support scoring.',
            'All twelve searches were actually executed; derived cache provenance does not claim twelve model calls.',
            'Interval-envelope comparisons inspect conditional temporal state without querying QA or gold.']}
    args.result.parent.mkdir(parents=True, exist_ok=True)
    args.result.write_bytes(canonical(result) + b'\n')
    print(json.dumps({'replays': len(rows), 'mappings': len(MAPPINGS),
        'changed_mapping_method_pairs_vs_identity': len(changed),
        'parent_cache_unchanged': True, 'qa_or_gold_loaded': False, 'new_model_executions': 0}))


if __name__ == '__main__':
    main()
