"""Matched objective replay over unchanged v0.5 caches; no natural QA/gold.

Only this script's two result files are written. The authored scores are fixed
at their existing values; historical and active-support modes share exactly
the same candidates, correction policies, semantic feasibility and budgets.
"""
from dataclasses import asdict
from hashlib import sha256
import json
from math import isclose
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from temporal_state.correction_io import load_correction_cache
from temporal_state.correction_pipeline import answer_corrected, decode_correction_cache
from temporal_state.models import Question
from temporal_state.objective_pipeline import answer_objective, decode_objective
from temporal_state.objectives import MODES, objective_breakdown, objective_spec
from temporal_state.pipeline import METHODS

DATA = ROOT / 'data/correction_diagnostics_v05'
SOURCE_DATA = ROOT / 'data/correction_pilot_v05/caches'
PREDICTIONS = ROOT / 'results/objective_v06_predictions.json'
REPLAY = ROOT / 'results/objective_v06_replay.json'
SEED, RESTARTS = 7, 10


def write(path, obj):
    path.write_text(json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False,
                               allow_nan=False) + '\n')


def digest(obj):
    return sha256(json.dumps(obj, sort_keys=True, separators=(',', ':'),
                             ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def deterministic(obj):
    if isinstance(obj, dict):
        return {k: deterministic(v) for k, v in obj.items() if k != 'elapsed_seconds'}
    if isinstance(obj, (list, tuple)):
        return [deterministic(v) for v in obj]
    return obj


def answer_semantics(prediction):
    return {key: value for key, value in asdict(prediction).items() if key != 'diagnostics'}


def selection(state):
    return (state.result.reading_ids, state.result.link_ids,
            state.result.restatement_components)


def chosen_corrections(cache, state):
    links = {link.link_id: link for link in cache.base.problem.links}
    return sorted(lid for _, lid in state.result.link_ids
                  if lid is not None and links[lid].relation == 'CORRECTS')


def rescore(cache, state, mode):
    mentions = {mention.mention_id: mention for mention in cache.base.problem.mentions}
    selected = {mid: next(r for r in mentions[mid].readings if r.reading_id == rid)
                for mid, rid in state.result.reading_ids}
    links = {link.link_id: link for link in cache.base.problem.links}
    chosen = tuple(links[lid] if lid else None for _, lid in state.result.link_ids)
    return objective_breakdown(cache.base.problem, selected, chosen,
                               state.result.restatement_components, mode=mode)


def main():
    manifest_path, questions_path = DATA / 'manifest.json', DATA / 'questions.json'
    manifest = json.loads(manifest_path.read_text())
    question_rows = json.loads(questions_path.read_text())['questions']
    paths = {('authored', row['case_id']): DATA / row['cache_file']
             for row in manifest['cases']}
    paths.update({('source_record', name): SOURCE_DATA / f'{name}.json'
                  for name in ('certificate_enabled', 'certificate_omitted')})
    watched = [manifest_path, questions_path, *paths.values()]
    input_hashes = {str(p.relative_to(ROOT)): sha256(p.read_bytes()).hexdigest()
                    for p in watched}
    caches = {key: load_correction_cache(path) for key, path in paths.items()}
    states, legacy_states, records, checks = {}, {}, [], []

    def check(name, passed, corpus=None, case=None, mode=None, method=None, qid=None):
        checks.append({'check': name, 'passed': bool(passed), 'corpus': corpus,
                       'case_id': case, 'objective_mode': mode, 'method': method,
                       'question_id': qid})

    for (corpus, case), cache in caches.items():
        for method in METHODS:
            legacy = decode_correction_cache(cache, method, restarts=RESTARTS, seed=SEED)
            legacy_states[corpus, case, method] = legacy
            for mode in MODES:
                state = decode_objective(cache, method, objective_mode=mode,
                                         restarts=RESTARTS, seed=SEED)
                states[corpus, case, mode, method] = state
                parts = state.memory.diagnostics['objective_ablation']
                ledger = state.memory.diagnostics['correction_ledger']
                historical_compatible = None
                if mode == 'historical':
                    historical_compatible = {
                        'selection_and_components': selection(state) == selection(legacy),
                        'objective': state.result.objective == legacy.result.objective,
                        'active_claims': state.memory.claims == legacy.memory.claims,
                        'correction_ledger': ledger == legacy.memory.diagnostics['correction_ledger'],
                    }
                    for field, passed in historical_compatible.items():
                        check('v05_historical_' + field, passed, corpus, case, mode, method)
                check('breakdown_matches_selected_objective', isclose(parts['total'],
                    state.result.objective, abs_tol=1e-9, rel_tol=1e-12), corpus, case, mode, method)
                check('removed_mass_matches_historical_difference', isclose(
                    parts['historical_minus_selected_objective'],
                    parts['removed_inactive_advantage'], abs_tol=1e-9, rel_tol=1e-12),
                    corpus, case, mode, method)
                check('same_frozen_cache_identity', state.cache_digest == cache.digest,
                      corpus, case, mode, method)
                original_claims = {claim.claim_id: claim for claim in cache.base.claims.values()}
                check('active_claim_content_unchanged', all(original_claims[c.claim_id] == c
                    for c in state.memory.claims), corpus, case, mode, method)
                if corpus == 'source_record':
                    check('source_forecast_modality_and_unknown_bounds_preserved', all(
                        c.modality == 'announced_future' and c.start.lower is None
                        and c.start.upper is None and c.end.lower is None and c.end.upper is None
                        and not c.state_observed_at for c in state.memory.claims),
                        corpus, case, mode, method)
                records.append({'corpus': corpus, 'case_id': case, 'method': method,
                    'objective_mode': mode, 'correction_cache_sha256': cache.digest,
                    'base_cache_sha256': cache.base.digest,
                    'objective_identity_sha256': state.objective_digest,
                    'objective_spec_sha256': digest(objective_spec(mode)),
                    'decoder': deterministic(asdict(state.result)),
                    'objective_breakdown': parts,
                    'same_selection_other_objective_breakdown': rescore(cache, state,
                        'active_support' if mode == 'historical' else 'historical'),
                    'active_records': [asdict(c) for c in state.memory.claims],
                    'active_values': [c.value for c in state.memory.claims],
                    'selected_correction_link_ids': chosen_corrections(cache, state),
                    'correction_ledger': ledger,
                    'v05_historical_compatibility': historical_compatible})

    predictions = []
    for row in question_rows:
        case, question = row['case_id'], Question(**row['question'])
        cache = caches['authored', case]
        for method in METHODS:
            legacy = answer_corrected(cache, legacy_states['authored', case, method],
                                      question, query_mode=row['query_mode'])
            for mode in MODES:
                state = states['authored', case, mode, method]
                prediction = answer_objective(cache, state, question, query_mode=row['query_mode'])
                compatible = answer_semantics(prediction) == answer_semantics(legacy)
                if mode == 'historical':
                    check('v05_historical_answer_and_evidence', compatible, 'authored', case,
                          mode, method, question.question_id)
                predictions.append({'case_id': case, 'method': method,
                    'objective_mode': mode, 'query_mode': row['query_mode'],
                    'correction_cache_sha256': cache.digest,
                    'base_cache_sha256': cache.base.digest,
                    'objective_identity_sha256': state.objective_digest,
                    'objective_spec_sha256': digest(objective_spec(mode)),
                    'objective_breakdown': state.memory.diagnostics['objective_ablation'],
                    'prediction': asdict(prediction),
                    'matches_v05_answer_and_evidence': compatible,
                    'comparison_is_accuracy': False})

    comparisons = []
    for (corpus, case), cache in caches.items():
        for method in METHODS:
            historical = states[corpus, case, 'historical', method]
            active = states[corpus, case, 'active_support', method]
            old_links, new_links = chosen_corrections(cache, historical), chosen_corrections(cache, active)
            comparisons.append({'corpus': corpus, 'case_id': case, 'method': method,
                'correction_cache_sha256': cache.digest,
                'selection_changed': selection(historical) != selection(active),
                'active_ledger_changed': historical.memory.claims != active.memory.claims,
                'historical_correction_link_ids': old_links,
                'active_support_correction_link_ids': new_links,
                'removed_selected_correction_link_ids': sorted(set(old_links) - set(new_links)),
                'historical_active_values': [c.value for c in historical.memory.claims],
                'active_support_active_values': [c.value for c in active.memory.claims],
                'historical_selection_scored_under_active_support': rescore(cache, historical, 'active_support')['total'],
                'active_support_selection_scored_under_active_support': active.result.objective})
            check('objective_identity_separates_modes', historical.objective_digest != active.objective_digest,
                  corpus, case, method=method)
        for mode in MODES:
            exact = states[corpus, case, mode, 'joint_exact'].result.objective
            for method in METHODS:
                check('exact_objective_dominates_shared_method', exact + 1e-9 >=
                    states[corpus, case, mode, method].result.objective, corpus, case, mode, method)

    # Admissibility controls use the unchanged caches, not evaluation labels.
    for corpus, case in (('authored', 'missing_certificate'), ('authored', 'unrelated_issuer'),
                         ('source_record', 'certificate_omitted')):
        cache = caches[corpus, case]
        for mode in MODES:
            for method in METHODS:
                check('invalid_or_missing_certificate_cannot_be_scored_into_selection',
                    not chosen_corrections(cache, states[corpus, case, mode, method]),
                    corpus, case, mode, method)
    for method in METHODS:
        h = states['source_record', 'certificate_enabled', 'historical', method]
        a = states['source_record', 'certificate_enabled', 'active_support', method]
        check('unchanged_lyft_scores_expose_correction_suppression',
              [c.value for c in h.memory.claims] == ['approximately 50 basis points']
              and set(c.value for c in a.memory.claims) ==
                  {'approximately 500 basis points', 'approximately 50 basis points'}
              and rescore(caches['source_record', 'certificate_enabled'], h, 'active_support')['total']
                  < a.result.objective,
              'source_record', 'certificate_enabled', method=method)

    for relative, before in input_hashes.items():
        check('input_file_not_modified', sha256((ROOT / relative).read_bytes()).hexdigest() == before,
              case=relative)
    check('authored_decode_count', sum(key[0] == 'authored' for key in states) == 80)
    check('authored_qa_prediction_count', len(predictions) == 88)
    check('source_record_decode_count', sum(key[0] == 'source_record' for key in states) == 16)

    summary = {}
    for corpus in ('authored', 'source_record'):
        pairs = [row for row in comparisons if row['corpus'] == corpus]
        summary[corpus] = {'matched_method_case_pairs': len(pairs),
            'pairs_with_selection_change': sum(row['selection_changed'] for row in pairs),
            'pairs_with_active_ledger_change': sum(row['active_ledger_changed'] for row in pairs),
            'pairs_with_correction_suppression': sum(bool(row['removed_selected_correction_link_ids']) for row in pairs),
            'historical_selected_correction_links': sum(len(row['historical_correction_link_ids']) for row in pairs),
            'active_support_selected_correction_links': sum(len(row['active_support_correction_link_ids']) for row in pairs)}
    active_predictions = [row for row in predictions if row['objective_mode'] == 'active_support']
    summary['authored']['method_question_pairs_with_answer_or_evidence_change'] = sum(
        not row['matches_v05_answer_and_evidence'] for row in active_predictions)
    summary['authored']['method_question_comparison_pairs'] = len(active_predictions)
    ledger = {'schema_version': '0.6', 'evidence_level': 'matched_authored_objective_ablation',
        'source_record_evidence_level': 'source_derived_development_illustration_with_authored_scores',
        'model_executed': False, 'score_retuning': False, 'natural_qa_predictions': 0,
        'natural_gold_read': False, 'authored_expectations_read': False,
        'timing_policy': 'Elapsed seconds excluded; no runtime performance comparison.',
        'objective_modes': list(MODES), 'methods': list(METHODS),
        'search_parameters': {'restarts': RESTARTS, 'seed': SEED, 'limits': 'shared decoder defaults'},
        'input_file_sha256': input_hashes, 'records': records, 'predictions': predictions,
        'comparisons': comparisons, 'observed_behavior': summary}
    write(PREDICTIONS, deterministic(ledger))
    report = {'schema_version': '0.6', 'all_checks_passed': all(c['passed'] for c in checks),
        'check_count': len(checks), 'authored_cases': 10, 'authored_questions': len(question_rows),
        'objective_modes': list(MODES), 'methods': list(METHODS),
        'authored_method_case_mode_decodes': 80, 'authored_method_question_mode_predictions': len(predictions),
        'source_record_variants': 2, 'source_record_method_variant_mode_decodes': 16,
        'legacy_compatibility_decodes': len(legacy_states), 'natural_qa_predictions': 0,
        'prediction_sha256': sha256(PREDICTIONS.read_bytes()).hexdigest(),
        'observed_behavior': summary, 'checks': checks,
        'interpretation': 'Removing inactive evidence scores suppresses otherwise valid correction edges under these unchanged authored scores. This exposes an objective-design problem; it is not an accuracy improvement.',
        'limitations': ['The same existing authored admission/link scores are used without retuning.',
            'Historical and active-support totals are different objectives and cannot be compared as accuracy.',
            'The source replay is a forecast record ledger with unknown event bounds; no temporal QA or actual financial result is evaluated.',
            'No gold, natural question file, or authored expectation file is read by this runner.',
            'The source certificate is an annotated semantic input, not an independently verified authority/entailment result.',
            'Exact optimization is conditional on the candidate cache and chosen objective; it does not establish factual truth or methodological superiority.']}
    write(REPLAY, report)
    print(json.dumps({key: report[key] for key in ('authored_method_case_mode_decodes',
        'authored_method_question_mode_predictions', 'source_record_method_variant_mode_decodes',
        'legacy_compatibility_decodes', 'check_count', 'all_checks_passed', 'observed_behavior')}, indent=2))
    if not report['all_checks_passed']:
        print(json.dumps([item for item in checks if not item['passed']], indent=2))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
