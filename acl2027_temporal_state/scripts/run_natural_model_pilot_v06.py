#!/usr/bin/env python3
"""Strict source-only model graph/ordinal score adapter and eight-way replay.

This runner reads no QA or gold. Raw interpretations are never repaired, pruned,
or rescored. Replays are exploratory conditional states, not accuracy evidence.
"""
import argparse
from dataclasses import asdict
from hashlib import sha256
import importlib.util
import itertools
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
spec = importlib.util.spec_from_file_location('ordinal_v06_adapter',
    ROOT / 'scripts/prepare_ordinal_scoring_v06.py')
ordinal = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ordinal)
old = ordinal.old

from temporal_state.bounded import DayBounds, EvidenceSpan, TemporalClaim, claim_to_dict
from temporal_state.correction_io import dump_correction_cache, make_correction_cache
from temporal_state.corrections import (CorrectionEvidence, CorrectionPolicy,
    SourceAuthority, SupportDependency)
from temporal_state.decoder import Link, Limits, Mention, Problem, Reading, _Engine
from temporal_state.models import Source
from temporal_state.objective_pipeline import decode_objective
from temporal_state.objectives import MODES
from temporal_state.scored_io import make_cache

METHODS = ('independent', 'iterative', 'iterative_restarts', 'joint_exact')
SETTINGS = {
    'schema_version': 'natural_adapter_v0.6', 'beta': 1.0, 'penalties': [],
    'limits': asdict(Limits()), 'restarts': 10, 'seed': 7,
    'objective_modes': list(MODES), 'methods': list(METHODS),
    'overflow_policy': 'fail_no_pruning',
    'score_policy': 'Use each actual ordinal judgment verbatim, including unresolved and no-link.',
    'context_policy': 'All supplied source IDs on problem, mentions, readings, claims and links.',
    'alias_policy': 'Only canonical names matching an exact predicted subject are adapted; other aliases are logged metadata exclusions.',
}


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return sha256(value).hexdigest()


def strict_request(request):
    expected = {'schema_version', 'request_id', 'history_id', 'information_cutoff',
                'extraction_domains', 'scope_filter', 'sources'}
    if set(request) != expected or request['schema_version'] != 'source_request_v0.6':
        raise ValueError('Unexpected source request fields/version')
    if not isinstance(request['sources'], list):
        raise ValueError('Sources must be a list')
    source_fields = {'source_id', 'text', 'text_sha256', 'operational_available_at',
                     'reported_publication_date', 'availability_basis', 'history_id'}
    for source in request['sources']:
        if set(source) != source_fields:
            raise ValueError('Unexpected source metadata fields')
        if source['history_id'] != request['history_id']:
            raise ValueError('Mixed histories in one adapter request')
        if any(not isinstance(source[k], str) for k in source_fields):
            raise ValueError('Source metadata must contain strings')
    scope_fields = {'max_mentions', 'max_resolved_readings_per_mention', 'overflow_policy', 'scope'}
    if set(request['scope_filter']) != scope_fields:
        raise ValueError('Unexpected scope fields')
    if request['scope_filter']['overflow_policy'] != 'fail_no_score_or_label_based_pruning':
        raise ValueError('Only fail-without-pruning requests are supported')


def _span(row):
    return EvidenceSpan(row['start'], row['end'], row['quote'])


def adapt_graph(request, candidate_raw, scoring_request, scoring_manifest, score_raw, input_binding):
    """Return (immutable correction cache, adapter manifest) without decoding.

    input_binding contains hashes/receipts supplied by the file-loading boundary.
    In-memory callers must independently bind their execution provenance. Exact
    spans and cache consistency do not certify the truth of those declarations.
    """
    strict_request(request)
    validation = ordinal.validator.validate_graph(candidate_raw, request)
    expected_request, expected_manifest = ordinal.prepare(request, candidate_raw)
    if scoring_request != expected_request or scoring_manifest != expected_manifest:
        raise ValueError('Scoring request/manifest differs from deterministic neutral preparation')
    score_validation = ordinal.validate_scores(score_raw, scoring_request)
    graph, score_data = old.strict_json(candidate_raw), old.strict_json(score_raw)
    scores = {}
    for row in score_data['scores']:
        target = scoring_manifest['item_mapping'][row['item_id']]
        key = (target['kind'], target['mention_id'], target['candidate_id'], target['link_id'])
        if key in scores:
            raise ValueError('Duplicate adapter score target')
        scores[key] = row['score']
    by_source = {s['source_id']: s for s in request['sources']}
    context = tuple(sorted(by_source))
    sources = tuple(Source(s['source_id'], s['text'], s['operational_available_at'],
        uri='', availability_basis=s['availability_basis'],
        provenance='source_request_v0.6; unversioned source-only candidate and ordinal scorer context')
        for s in request['sources'])
    claims, mentions, owner = {}, [], {}
    metadata, alignments = [], {'readings': {}, 'links': {}, 'aliases': {}, 'authorities': {}, 'dependencies': []}
    used_scores = set()
    def score(kind, mid, rid=None, lid=None):
        key = kind, mid, rid, lid
        used_scores.add(key)
        if key not in scores:
            raise ValueError('Missing actual model score; no numeric fallback')
        return scores[key]
    for mention in graph['mentions']:
        mid, sid = mention['mention_id'], mention['source_id']
        anchor = old.align_evidence([{'source_id': sid, 'quote': mention['anchor_quote']}], by_source)[0]
        readings = []
        for raw in mention['readings']:
            rid = raw['candidate_id']
            owner[rid] = mid
            evidence = old.align_evidence(raw['evidence'], by_source)
            alignments['readings'][rid] = evidence
            primary = tuple(_span(row) for row in evidence if row['source_id'] == sid)
            claim = TemporalClaim(claim_id=rid, source_id=sid,
                subject=raw['subject'], relation=raw['relation'], value=raw['value'],
                scope=raw['scope'], polarity=raw['polarity'], modality=raw['modality'],
                reported_at=raw['reported_at'],
                start=DayBounds(raw['start']['lower'], raw['start']['upper']),
                end=DayBounds(raw['end']['lower'], raw['end']['upper']),
                state_observed_at=tuple(raw['state_observed_at']), evidence_spans=primary,
                context_source_ids=context, raw_assertion_id=rid,
                derivation_note=raw['derivation_note'], role_qualifier=raw['role_qualifier'],
                operation='ASSERT')
            claims[mid, rid] = claim
            exact_start = claim.start.lower if claim.start.lower == claim.start.upper else None
            exact_end = claim.end.lower if claim.end.lower == claim.end.upper else None
            readings.append(Reading(rid, claim.key, claim.value, score('reading', mid, rid),
                effective_start=exact_start, effective_end=exact_end,
                observed_at=min(claim.state_observed_at) if claim.state_observed_at else None,
                context_source_ids=context))
            metadata.append({'kind': 'claim_schema_metadata', 'candidate_id': rid,
                'retained_outside_claim': {'start_precision': raw['start']['precision'],
                    'end_precision': raw['end']['precision'], 'observation_kind': raw['observation_kind']},
                'reason': 'No matching TemporalClaim fields; exact bounds/observations preserved unchanged.'})
        unresolved_id = '__unresolved__:' + mid
        if any(r.reading_id == unresolved_id for r in readings):
            raise ValueError('Candidate ID collides with reserved unresolved reading ID')
        readings.append(Reading(unresolved_id, None, None, score('unresolved', mid),
                                context_source_ids=context))
        mentions.append(Mention(mid, sid, anchor['start'], anchor['end'], tuple(readings),
                                null_score=score('no_link', mid), context_source_ids=context))
    links, certificates = [], []
    for raw in graph['links']:
        lid, rid, target = raw['link_id'], raw['candidate_id'], raw['target_candidate_id']
        aligned = old.align_evidence(raw['evidence'], by_source)
        alignments['links'][lid] = aligned
        reference = None
        if raw['correction'] is not None:
            cert = raw['correction']
            ref = old.align_evidence([cert['reference_evidence']], by_source)[0]
            reference = (ref['start'], ref['end'])
            certificates.append(CorrectionEvidence(lid, _span(ref), cert['action'], cert['coverage']))
        links.append(Link(lid, owner[rid], rid, owner[target], target, raw['relation'],
                          score('link', owner[rid], rid, lid), context_source_ids=context,
                          reference_span=reference))
    if used_scores != set(scores):
        raise ValueError('Actual scores were not consumed exactly once by the adapter')
    aliases, subjects = [], {claim.subject for claim in claims.values()}
    for raw in graph['aliases']:
        aligned = old.align_evidence(raw['evidence'], by_source)
        alignments['aliases'][raw['alias_id']] = aligned
        if raw['canonical_name'] not in subjects:
            metadata.append({'kind': 'excluded_non_subject_alias', 'alias_id': raw['alias_id'],
                'canonical_name': raw['canonical_name'], 'alias': raw['alias'],
                'reason': 'Current alias schema supports predicted subject names only; raw alias retained without rewriting.'})
            continue
        for sid in sorted({span['source_id'] for span in aligned}):
            aliases.append({'subject': raw['canonical_name'], 'alias': raw['alias'], 'source_id': sid,
                'evidence_spans': [asdict(_span(span)) for span in aligned if span['source_id'] == sid]})
    authorities = []
    for raw in graph['authorities']:
        aligned = old.align_evidence(raw['evidence'], by_source)
        alignments['authorities'][raw['source_id']] = aligned
        authorities.append(SourceAuthority(raw['source_id'], raw['authority_id'],
                                             tuple(_span(row) for row in aligned)))
    dependencies = []
    for raw in graph['essential_support']:
        aligned = old.align_evidence(raw['evidence'], by_source)
        alignments['dependencies'].append({'dependent_candidate_id': raw['dependent_candidate_id'],
            'prerequisite_candidate_id': raw['prerequisite_candidate_id'], 'evidence': aligned})
        dependencies.append(SupportDependency(raw['dependent_candidate_id'], raw['prerequisite_candidate_id'],
                                               tuple(_span(row) for row in aligned)))
    problem = Problem(request['information_cutoff'], sources, tuple(mentions), tuple(links),
        scoring_manifest['score_definition'], context_source_ids=context, penalties=(), beta=SETTINGS['beta'])
    # Validate conservative enumeration limits before constructing or replaying a
    # cache; exceeding the budget does not permit score-driven truncation.
    engine = _Engine(problem, Limits(**SETTINGS['limits']))
    config = {'settings': SETTINGS, 'input_binding': input_binding,
              'candidate_validation': validation, 'score_validation': score_validation,
              'metadata_projection': metadata, 'aligned_evidence': alignments,
              'source_metadata': [{k: v for k, v in s.items() if k != 'text'} for s in request['sources']],
              'source_uri_policy': 'Input request supplies no URI field; source URI stays empty.',
              'unsupported_items_retained_in_raw': len(graph['unsupported']),
              'link_notes_and_unsupported_rows': 'Preserved in exact raw output bound by digest; not used as numeric scores.'}
    config_sha = digest(canonical(config))
    prompt_sha = input_binding['prompt_manifest_sha256']
    provenance = {'evidence_type': 'unversioned_model_scores',
        'candidate_model_id': 'unversioned_conversational_candidate', 'candidate_model_revision': None,
        'scorer_model_id': 'unversioned_conversational_scorer', 'scorer_model_revision': None,
        'prompt_sha256': prompt_sha, 'config_sha256': config_sha,
        'score_definition': problem.score_provenance}
    base = make_cache(problem, claims, aliases, provenance)
    policy = CorrectionPolicy(source_authorities=tuple(authorities),
        correction_evidence=tuple(certificates), support_dependencies=tuple(dependencies))
    cache = make_correction_cache(base, policy, {
        'evidence_type': 'source_derived_annotation', 'policy_builder_id': 'unversioned_conversational_candidate',
        'policy_builder_revision': None, 'prompt_sha256': prompt_sha, 'config_sha256': config_sha,
        'policy_description': 'Direct translation of source-only candidate authority, correction and support annotations; exact spans are checked, semantic truth is not certified.'})
    manifest = {'schema_version': 'natural_adapter_manifest_v0.6', 'configuration': config,
        'configuration_sha256': config_sha, 'base_cache_sha256': base.digest,
        'shared_correction_cache_sha256': cache.digest, 'source_context_ids': list(context),
        'counts': {'mentions': len(mentions), 'resolved_readings': len(claims),
            'unresolved_readings': len(mentions), 'links': len(links), 'scored_items': len(scores),
            'subject_alias_rows': len(aliases), 'excluded_non_subject_aliases': sum(
                row['kind'] == 'excluded_non_subject_alias' for row in metadata),
            'correction_certificates': len(certificates), 'essential_support_dependencies': len(dependencies)},
        'state_ceiling': engine.state_ceiling,
        'adapter_semantic_repairs': [], 'numeric_score_defaults': [], 'pruned_candidates_or_links': [],
        'gold_or_questions_read': False,
        'evidence_scope': 'One selected development source history with real unversioned model judgments; no held-out accuracy, model-generation reproducibility, or generalization claim.'}
    return cache, manifest


def validate_receipt(receipt, output_bytes, required_input_hashes, label, known_input_hashes=None):
    if receipt.get('backend') != 'unversioned_conversational_agent' or receipt.get('model_revision', 'MISSING') is not None:
        raise ValueError(label + ' receipt must declare the unversioned backend and null revision')
    if receipt.get('output_sha256') != digest(output_bytes):
        raise ValueError(label + ' receipt output hash mismatch')
    if not isinstance(receipt.get('task_text'), str) or not receipt['task_text'].strip():
        raise ValueError(label + ' receipt requires exact task text')
    recorded = receipt.get('inputs')
    if not isinstance(recorded, list) or any(not isinstance(row, dict) or not isinstance(row.get('sha256'), str) for row in recorded):
        raise ValueError(label + ' receipt requires explicit input hashes')
    recorded_hashes = {row['sha256'] for row in recorded}
    if not set(required_input_hashes) <= recorded_hashes:
        raise ValueError(label + ' receipt input hash mismatch')
    known = set(required_input_hashes if known_input_hashes is None else known_input_hashes)
    if not recorded_hashes <= known:
        raise ValueError(label + ' receipt contains an input not bound to an explicit unchanged file')


def bind_files(paths):
    raw = {key: path.read_bytes() for key, path in paths.items()}
    parsed = {key: old.strict_json(raw[key].decode()) for key in (
        'candidate_request', 'scoring_request', 'scoring_manifest', 'candidate_receipt', 'scorer_receipt')}
    candidate_inputs = [digest(raw[key]) for key in ('candidate_request', 'candidate_prompt', 'candidate_schema')]
    if 'repair_protocol' in raw:
        preliminary_lineage = old.strict_json(raw['repair_protocol'].decode())
        candidate_inputs.extend([digest(raw['original_candidates']), digest(raw['repair_protocol']),
                                 preliminary_lineage.get('first_failure_sha256')])
    validate_receipt(parsed['candidate_receipt'], raw['candidates'], candidate_inputs, 'Candidate')
    validate_receipt(parsed['scorer_receipt'], raw['scores'],
        [digest(raw[key]) for key in ('scoring_request', 'scorer_prompt', 'scorer_schema')], 'Scorer')
    lineage = None
    if 'repair_protocol' in raw:
        lineage = old.strict_json(raw['repair_protocol'].decode())
        if (lineage.get('schema_version') != 'validation_repair_protocol_v0.6'
                or lineage.get('repair_budget') != 1 or lineage.get('automatic_retry_loop') is not False):
            raise ValueError('Unsupported candidate repair lineage protocol')
        for field, key in {'original_candidate_raw_sha256': 'original_candidates',
                'original_prompt_sha256': 'original_candidate_prompt',
                'original_schema_sha256': 'original_candidate_schema',
                'repair_prompt_sha256': 'candidate_prompt', 'repair_schema_sha256': 'candidate_schema'}.items():
            if lineage.get(field) != digest(raw[key]):
                raise ValueError('Candidate repair lineage hash mismatch: ' + field)
        failures = [value for key, value in raw.items() if key.startswith('lineage_input_')
                    and digest(value) == lineage.get('first_failure_sha256')]
        if len(failures) != 1:
            raise ValueError('Candidate repair requires the exact original validation-failure artifact')
        failure = old.strict_json(failures[0].decode())
        if (failure.get('accepted') is not False
                or failure.get('candidate_raw_output_sha256') != digest(raw['original_candidates'])
                or failure.get('candidate_request_file_sha256') != digest(raw['candidate_request'])
                or failure.get('error', {}).get('message') != lineage.get('first_failure')):
            raise ValueError('Original validation failure does not match candidate repair lineage')
        original_receipt = old.strict_json(raw['original_candidate_receipt'].decode())
        validate_receipt(original_receipt, raw['original_candidates'],
            [digest(raw[key]) for key in ('candidate_request', 'original_candidate_prompt', 'original_candidate_schema')],
            'Original candidate')
    prompt_manifest = {key: digest(raw[key]) for key in ('candidate_prompt', 'scorer_prompt')}
    prompt_manifest.update({key + '_task_text_sha256': digest(parsed[key]['task_text'].encode())
                           for key in ('candidate_receipt', 'scorer_receipt')})
    binding = {'files': {key: {'path': str(paths[key].relative_to(ROOT))
                             if paths[key].is_relative_to(ROOT) else str(paths[key]),
                             'sha256': digest(value)} for key, value in sorted(raw.items())},
        'prompt_manifest': prompt_manifest, 'prompt_manifest_sha256': digest(canonical(prompt_manifest)),
        'upstream_repair_protocol': lineage,
        'receipts_checked': True, 'receipt_verification_scope': 'Declared input/output hashes and backend fields; not independent attestation of execution.'}
    return parsed, raw, binding


def replay(cache, manifest):
    rows = []
    for mode in MODES:
        for method in METHODS:
            state = decode_objective(cache, method, objective_mode=mode,
                limits=Limits(**SETTINGS['limits']), restarts=SETTINGS['restarts'], seed=SETTINGS['seed'])
            diagnostics = asdict(state.result.diagnostics)
            diagnostics.pop('elapsed_seconds')
            rows.append({'objective_mode': mode, 'method': method,
                'objective_identity_sha256': state.objective_digest,
                'objective': state.result.objective,
                'selected_reading_ids': dict(state.result.reading_ids),
                'selected_link_ids': dict(state.result.link_ids),
                'active_claim_ids': sorted(c.claim_id for c in state.memory.claims),
                'active_claims': [claim_to_dict(c) for c in state.memory.claims],
                'restatement_components': [list(group) for group in state.result.restatement_components],
                'correction_ledger': state.memory.diagnostics['correction_ledger'],
                'objective_components': state.memory.diagnostics['objective_ablation'],
                'search_diagnostics_without_timing': diagnostics})
    comparisons = []
    for left, right in itertools.combinations(rows, 2):
        comparisons.append({'left': [left['objective_mode'], left['method']],
            'right': [right['objective_mode'], right['method']],
            'same_selected_readings': left['selected_reading_ids'] == right['selected_reading_ids'],
            'same_selected_links': left['selected_link_ids'] == right['selected_link_ids'],
            'same_active_claims': left['active_claim_ids'] == right['active_claim_ids'],
            'interpretation': 'Exploratory state comparison; agreement/disagreement is not correctness.'})
    return {'schema_version': 'natural_model_pilot_replay_v0.6',
        'shared_correction_cache_sha256': cache.digest, 'base_cache_sha256': cache.base.digest,
        'adapter_configuration_sha256': manifest['configuration_sha256'],
        'counts': manifest['counts'], 'state_ceiling': manifest['state_ceiling'],
        'settings': SETTINGS, 'runs': rows, 'state_comparisons': comparisons,
        'model_calls_during_replay': 0, 'qa_or_gold_loaded': False,
        'score_type': 'Actual unversioned conversational ordinal compatibility judgments; no calibrated probability interpretation.',
        'timings_omitted': True, 'performance_or_accuracy_claim': False,
        'limits': ['One development history and source bundle; no held-out evaluation.',
            'Operational source availability is development metadata, not verified historical first-public timing.',
            'All scored atomic items share full source context; their judgments are not statistically independent.',
            'Model revisions are unknown; stored-cache replay does not establish reproducible model generation.',
            'Search optimality concerns the supplied heuristic score and feasible state space, not factual truth.',
            'No correction or dependency contrast is tested if the source-only graph has no such edges.']}


def main():
    folder = ROOT / 'data/natural_model_pilot_v06'
    parser = argparse.ArgumentParser(description=__doc__)
    defaults = {'candidate-request': folder / 'candidate_request.json',
        'candidates': folder / 'candidate_output_raw.json', 'candidate-receipt': folder / 'candidate_run_receipt.json',
        'scoring-request': folder / 'scoring_request.json', 'scoring-manifest': folder / 'scoring_manifest.json',
        'scores': folder / 'scorer_output_raw.json', 'scorer-receipt': folder / 'scorer_run_receipt.json'}
    for name, path in defaults.items():
        parser.add_argument('--' + name, type=Path, default=path)
    parser.add_argument('--candidate-prompt', type=Path, default=ROOT / 'configs/candidate_prompt_v06.txt')
    parser.add_argument('--candidate-schema', type=Path, default=ROOT / 'configs/candidate_schema_v06.json')
    parser.add_argument('--repair-protocol', type=Path)
    parser.add_argument('--original-candidates', type=Path)
    parser.add_argument('--original-candidate-receipt', type=Path)
    parser.add_argument('--lineage-input', type=Path, action='append', default=[])
    parser.add_argument('--cache', type=Path, default=folder / 'shared_cache.json')
    parser.add_argument('--manifest', type=Path, default=folder / 'adapter_manifest.json')
    parser.add_argument('--result', type=Path, default=ROOT / 'results/natural_model_pilot_v06.json')
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    repair_paths = (args.repair_protocol, args.original_candidates, args.original_candidate_receipt)
    if any(repair_paths) and not all(repair_paths):
        parser.error('Repair lineage requires repair-protocol, original-candidates and original-candidate-receipt together')
    paths = {name.replace('-', '_'): getattr(args, name.replace('-', '_')).resolve() for name in defaults}
    paths.update({name: ROOT / rel for name, rel in {
        'scorer_prompt': 'configs/scorer_ordinal_prompt_v06.txt', 'scorer_schema': 'configs/scorer_ordinal_schema_v06.json',
        'adapter_code': 'scripts/run_natural_model_pilot_v06.py',
        'candidate_validator_code': 'scripts/validate_candidate_graph_v06.py',
        'scoring_preparer_code': 'scripts/prepare_ordinal_scoring_v06.py'}.items()})
    paths.update(candidate_prompt=args.candidate_prompt.resolve(), candidate_schema=args.candidate_schema.resolve())
    if args.repair_protocol:
        paths.update(repair_protocol=args.repair_protocol.resolve(),
            original_candidates=args.original_candidates.resolve(),
            original_candidate_receipt=args.original_candidate_receipt.resolve(),
            original_candidate_prompt=ROOT / 'configs/candidate_prompt_v06.txt',
            original_candidate_schema=ROOT / 'configs/candidate_schema_v06.json')
    for index, path in enumerate(args.lineage_input):
        paths[f'lineage_input_{index}'] = path.resolve()
    parsed, raw, binding = bind_files(paths)
    cache, manifest = adapt_graph(parsed['candidate_request'], raw['candidates'].decode(),
        parsed['scoring_request'], parsed['scoring_manifest'], raw['scores'].decode(), binding)
    if args.validate_only:
        print(json.dumps({'validated': True, 'counts': manifest['counts'], 'state_ceiling': manifest['state_ceiling']}))
        return
    result = replay(cache, manifest)
    for path in (args.cache, args.manifest, args.result):
        path.parent.mkdir(parents=True, exist_ok=True)
    dump_correction_cache(cache, args.cache)
    args.manifest.write_bytes(canonical(manifest) + b'\n')
    args.result.write_bytes(canonical(result) + b'\n')
    print(json.dumps({'cache_sha256': cache.digest, 'counts': manifest['counts'],
        'replays': len(result['runs']), 'qa_or_gold_loaded': False}))


if __name__ == '__main__':
    main()
