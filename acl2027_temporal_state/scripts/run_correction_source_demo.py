"""Replay one source-derived correction with explicitly authored scores.

This is an offline record-ledger demonstration, not temporal QA evaluation.
The source-only annotation is retained unchanged. Both policy variants see the
same complete captured bundle; neither is presented as a historical prefix.
"""
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from temporal_state.bounded import EvidenceSpan, TemporalClaim
from temporal_state.correction_io import dump_correction_cache, load_correction_cache, make_correction_cache
from temporal_state.correction_pipeline import decode_correction_cache
from temporal_state.corrections import CorrectionEvidence, CorrectionPolicy, SourceAuthority
from temporal_state.decoder import Link, Mention, Problem, Reading
from temporal_state.models import Source
from temporal_state.pipeline import METHODS
from temporal_state.scored_io import make_cache

DATA = ROOT / 'data/correction_pilot_v05'
OUTPUT = ROOT / 'results/correction_source_demo_v05.json'


def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False,
                               allow_nan=False) + '\n')


def digest(obj):
    return sha256(json.dumps(obj, sort_keys=True, separators=(',', ':'),
                            ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def compact_bundle(bundle_id, capsules, uri):
    """Explicit noncontiguous excerpt projection with reversible offset maps."""
    chunks, mappings, spans = [], [], {}
    cursor = 0
    separator = '\n[excerpt break]\n'
    for capsule in capsules:
        for excerpt in capsule['excerpts']:
            if chunks:
                chunks.append(separator)
                cursor += len(separator)
            quote = excerpt['text']
            evidence = EvidenceSpan(cursor, cursor + len(quote), quote)
            spans[capsule['source_id'], excerpt['purpose']] = evidence
            mappings.append({'parent_source_id': capsule['source_id'],
                'purpose': excerpt['purpose'], 'compact_start': evidence.start,
                'compact_end': evidence.end, 'parent_start': excerpt['start'],
                'parent_end': excerpt['end'],
                'parent_derived_sha256': capsule['derived_text_sha256'],
                'parent_raw_sha256': capsule['raw_parent_sha256']})
            chunks.append(quote)
            cursor += len(quote)
    source = Source(bundle_id, ''.join(chunks), '2026-10-02', uri=uri,
        availability_basis='current_capture_day_only_not_historical_first_availability',
        provenance='selected_noncontiguous_exact_excerpts; synthetic separators; complete source-only annotation read all three parent records')
    return source, spans, {'bundle_id': bundle_id,
        'parent_source_ids': [c['source_id'] for c in capsules],
        'projection': 'Exact excerpts joined by explicitly synthetic separators; surrounding context omitted.',
        'offsets': 'Python Unicode code points, zero based, end exclusive',
        'span_map': mappings}


def build_inputs():
    annotation_path = DATA / 'source_annotation.json'
    capsule_path = DATA / 'source_capture/evidence_capsules.json'
    annotation = json.loads(annotation_path.read_text())
    capsules = {c['source_id']: c for c in json.loads(capsule_path.read_text())['sources']}
    original_id = annotation['original_version']['source_id']
    corrected_id = annotation['corrected_version']['source_id']
    note_id = annotation['relation']['supporting_source_id']
    annotated_hashes = {Path(f['path']).name: f['sha256']
                       for f in annotation['input_provenance']['files']}
    for parent in (original_id, corrected_id, note_id):
        if capsules[parent]['derived_text_sha256'] != annotated_hashes[parent + '.derived.txt']:
            raise ValueError('Excerpt parent differs from annotated source version')
    # The note and replacement exhibit belong to the same amendment accession.
    # Their co-location in this explicit bundle is a representation projection,
    # not a claim that their HTML was one continuous text string.
    original, old_spans, old_map = compact_bundle('lyft_original_bundle',
        [capsules[original_id]],
        'https://www.sec.gov/Archives/edgar/data/1759509/000175950924000011/0001759509-24-000011.txt')
    replacement, new_spans, new_map = compact_bundle('lyft_amendment_bundle',
        [capsules[corrected_id], capsules[note_id]],
        'https://www.sec.gov/Archives/edgar/data/1759509/000175950924000015/0001759509-24-000015.txt')
    context = (original.source_id, replacement.source_id)
    config = {
        'schema_version': '0.5', 'evidence_level': 'source_derived_development_illustration',
        'annotation_sha256': sha256(annotation_path.read_bytes()).hexdigest(),
        'capsules_sha256': sha256(capsule_path.read_bytes()).hexdigest(),
        'annotation_backend': annotation['backend'], 'model_revision': annotation['model_revision'],
        'annotation_input_sha256': annotation['input_provenance']['full_input_sha256'],
        'sources_seen_by_annotation': annotation['source_ids'],
        'complete_context_declared_in_every_variant': list(context),
        'source_projection': [old_map, new_map],
        'operational_cutoff': '2026-10-02',
        'availability_policy': 'Use current retrieval day for both records. No claim of historical first availability, blind prefix extraction, or intraday order.',
        'scope_projection': 'FY2024; year-over-year change; denominator Gross Bookings; approximate basis points',
        'calendar_endpoint_policy': 'Unknown in annotation and preserved as unknown; no invented fiscal calendar or daily observation.',
        'scores': {'resolved': 10.0, 'unresolved': 0.0, 'correction': 2.0, 'null': 0.0},
        'score_origin': 'Authored admission preferences, not model scores or estimated probabilities.',
        'certificate_origin': 'Source-only annotation plus explicit deterministic projection; authority attribution and entailment not independently verified.',
        'variants': ['certificate_enabled', 'certificate_omitted'],
        'variant_meaning': 'Same complete source bundle, readings, links and scores; remove correction certificate as a semantic control.',
        'not_evaluated': ['temporal QA', 'historical knowledge reconstruction', 'held-out extraction', 'learned scoring', 'method superiority', 'actual financial outcome']}
    assert annotation['temporal_semantics']['fact_claim_target']['calendar_start'] is None
    assert annotation['temporal_semantics']['fact_claim_target']['calendar_end'] is None
    identity = annotation['claim_identity']
    claims, mentions, authorities = {}, [], []
    for index, (version, source, parent, spans) in enumerate((
            (annotation['original_version'], original, original_id, old_spans),
            (annotation['corrected_version'], replacement, corrected_id, new_spans))):
        value_span = spans[parent, 'reported_forecast_value']
        value = f"approximately {version['value']} basis points"
        if value not in value_span.quote:
            raise ValueError('Projected numeric value does not match captured excerpt')
        claim = TemporalClaim(version['claim_id'], source.source_id, identity['issuer'],
            identity['metric'], value, scope=config['scope_projection'],
            modality='announced_future', reported_at='2024-02-13',
            evidence_spans=tuple(spans[parent, purpose] for purpose in
                ('issuer_attribution', 'forecast_target_period', 'forecast_metric', 'reported_forecast_value')),
            context_source_ids=context, raw_assertion_id=version['claim_id'],
            derivation_note='Deterministic projection of retained unversioned source-only annotation. Unknown endpoint bounds and forecast modality preserved; authored scores.')
        mid = f'm{index}'
        claims[mid, 'r'] = claim
        mentions.append(Mention(mid, source.source_id, value_span.start, value_span.end,
            (Reading('r', claim.key, value, 10.0, context_source_ids=context),
             Reading('u', None, None, 0.0, context_source_ids=context)),
            context_source_ids=context))
        authorities.append(SourceAuthority(source.source_id, identity['issuer'],
            (spans[parent, 'issuer_attribution'],)))
    reference = new_spans[note_id, 'correction_authorization']
    link = Link('explicit_replacement', 'm1', 'r', 'm0', 'r', 'CORRECTS', 2.0,
        context_source_ids=context, reference_span=(reference.start, reference.end))
    score_definition = 'Authored development preferences: resolved 10, unresolved 0, CORRECTS 2, null 0; not learned scores.'
    problem = Problem('2026-10-02', (original, replacement), tuple(mentions), (link,),
        score_definition, context_source_ids=context)
    provenance = {'evidence_type': 'authored_diagnostic',
        'candidate_model_id': annotation['backend'], 'candidate_model_revision': None,
        'scorer_model_id': None, 'scorer_model_revision': None,
        'prompt_sha256': annotation['input_provenance']['annotation_task_sha256'],
        'config_sha256': digest(config), 'score_definition': score_definition}
    base = make_cache(problem, claims, [], provenance)
    policies = {
        'certificate_enabled': CorrectionPolicy(source_authorities=tuple(authorities),
            correction_evidence=(CorrectionEvidence(link.link_id, reference),)),
        'certificate_omitted': CorrectionPolicy(source_authorities=tuple(authorities))}
    policy_provenance = {'evidence_type': 'source_derived_annotation',
        'policy_builder_id': 'unversioned_conversational_agent_plus_explicit_projection',
        'policy_builder_revision': None,
        'prompt_sha256': annotation['input_provenance']['annotation_task_sha256'],
        'config_sha256': digest(config),
        'policy_description': 'Source-only annotation of whole-assertion replacement projected onto compact exact excerpts. Variant name identifies omission of certificate. No QA labels, model scoring or independent authority certification.'}
    caches = {}
    for name, policy in policies.items():
        cache = make_correction_cache(base, policy, policy_provenance)
        path = DATA / 'caches' / f'{name}.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        dump_correction_cache(cache, path)
        caches[name] = load_correction_cache(path)
    write(DATA / 'replay_config.json', config)
    return annotation, config, caches


def main():
    annotation, config, caches = build_inputs()
    records = []
    for variant, cache in caches.items():
        for method in METHODS:
            state = decode_correction_cache(cache, method)
            decoder = asdict(state.result)
            decoder['diagnostics'].pop('elapsed_seconds')
            records.append({'variant': variant, 'method': method,
                'correction_cache_sha256': cache.digest, 'base_cache_sha256': cache.base.digest,
                'decoder': decoder,
                'active_records': [asdict(c) for c in state.memory.claims],
                'correction_ledger': state.memory.diagnostics['correction_ledger'],
                'objective_decomposition': state.memory.diagnostics['objective_decomposition']})
    # These are representation contracts for this visible development example,
    # not held-out correctness labels or an accuracy denominator.
    checks = []
    for row in records:
        active = row['active_records']
        expected_count = 1 if row['variant'] == 'certificate_enabled' else 2
        checks.append({'variant': row['variant'], 'method': row['method'],
            'active_record_count_matches_policy_control': len(active) == expected_count,
            'forecast_modality_preserved': all(c['modality'] == 'announced_future' for c in active),
            'unknown_endpoints_preserved': all(c['start'] == {'lower': None, 'upper': None}
                and c['end'] == {'lower': None, 'upper': None} and c['state_observed_at'] == () for c in active),
            'replacement_record_retained': annotation['corrected_version']['claim_id'] in {c['claim_id'] for c in active},
            'base_cache_identical': row['base_cache_sha256'] == caches['certificate_enabled'].base.digest})
    passed = all(all(v for k, v in check.items() if k not in {'variant', 'method'}) for check in checks)
    report = {'schema_version': '0.5', 'evidence_level': config['evidence_level'],
        'source_claim_pairs': 1, 'source_annotation_model_run': 'unversioned conversational annotation only',
        'pinned_extractor_or_scorer_run': False, 'qa_predictions': 0,
        'historical_availability_verified': False, 'method_advantage_claim': False,
        'comparison_interpretation': config['variant_meaning'],
        'all_representation_contracts_passed': passed, 'contract_count': len(checks) * 5,
        'records': records, 'contracts': checks,
        'limits': config['not_evaluated']}
    write(OUTPUT, report)
    print(json.dumps({'source_claim_pairs': 1, 'decodes': len(records),
        'representation_contracts': len(checks) * 5, 'all_passed': passed,
        'natural_accuracy_claim': False}, indent=2))
    if not passed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
