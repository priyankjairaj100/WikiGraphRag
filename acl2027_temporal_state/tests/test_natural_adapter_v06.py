"""Synthetic contract tests; no natural questions, labels or prior predictions."""
from copy import deepcopy
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('natural_adapter_test_target',
    ROOT / 'scripts/run_natural_model_pilot_v06.py')
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


def fixture():
    texts = {'s1': 'Acme forecasts 100 units for 2026. Acme is also named AC.',
             's2': 'Acme corrects the earlier forecast to 10 units for 2026. Acme also uses AC.'}
    request = {'schema_version': 'source_request_v0.6', 'request_id': 'synthetic',
        'history_id': 'synthetic_history', 'information_cutoff': '2026-10-02',
        'extraction_domains': ['explicit_financial_forecasts'],
        'scope_filter': {'max_mentions': 8, 'max_resolved_readings_per_mention': 4,
            'overflow_policy': 'fail_no_score_or_label_based_pruning', 'scope': 'Synthetic forecast'},
        'sources': [{'source_id': sid, 'text': text, 'text_sha256': sha256(text.encode()).hexdigest(),
            'operational_available_at': '2026-10-02', 'reported_publication_date': '2026-10-01',
            'availability_basis': 'synthetic', 'history_id': 'synthetic_history'} for sid, text in texts.items()]}
    def ev(sid, quote):
        return {'source_id': sid, 'quote': quote}
    def reading(rid, sid, value, quote):
        return {'candidate_id': rid, 'subject': 'Acme', 'relation': 'forecast', 'value': value,
            'scope': '2026 units', 'role_qualifier': '', 'polarity': 'positive', 'modality': 'announced_future',
            'reported_at': '2026-10-01', 'start': {'lower': None, 'upper': None, 'precision': 'unknown'},
            'end': {'lower': None, 'upper': None, 'precision': 'unknown'}, 'state_observed_at': [],
            'observation_kind': 'report_only', 'evidence': [ev(sid, quote)], 'derivation_note': 'Synthetic test'}
    q1, q2 = 'Acme forecasts 100 units for 2026.', 'Acme corrects the earlier forecast to 10 units for 2026.'
    graph = {'schema_version': 'candidate_graph_v0.6',
        'mentions': [{'mention_id': 'm1', 'source_id': 's1', 'anchor_quote': q1,
                      'readings': [reading('r1', 's1', '100', q1)], 'unresolved_reason': ''},
                     {'mention_id': 'm2', 'source_id': 's2', 'anchor_quote': q2,
                      'readings': [reading('r2', 's2', '10', q2)], 'unresolved_reason': ''}],
        'links': [{'link_id': 'l1', 'candidate_id': 'r2', 'target_candidate_id': 'r1', 'relation': 'CORRECTS',
                   'evidence': [ev('s2', q2)], 'correction': {'action': 'replace', 'coverage': 'whole_assertion',
                       'reference_evidence': ev('s2', q2)}, 'derivation_note': 'Explicit test replacement'}],
        'aliases': [{'alias_id': 'a1', 'canonical_name': 'Acme', 'alias': 'AC',
                     'evidence': [ev('s1', 'Acme is also named AC.'), ev('s2', 'Acme also uses AC.')],
                     'derivation_note': 'Synthetic alias'}],
        'authorities': [{'source_id': sid, 'authority_id': 'Acme', 'evidence': [ev(sid, q)]}
                       for sid, q in [('s1', q1), ('s2', q2)]],
        'essential_support': [], 'unsupported': []}
    return request, graph


def prepare(request, graph):
    raw = json.dumps(graph)
    scoring_request, manifest = adapter.ordinal.prepare(request, raw)
    scores = {'schema_version': 'ordinal_scorer_v0.6', 'scores': [
        {'item_id': item['item_id'], 'score': {'reading': 3, 'link': 2, 'unresolved': -2, 'no_link': -1}[item['kind']],
         'evidence': [], 'note': 'Synthetic score contract fixture'} for item in scoring_request['items']]}
    return raw, scoring_request, manifest, json.dumps(scores)


def adapted(request=None, graph=None):
    if request is None:
        request, graph = fixture()
    raw, score_request, manifest, scores = prepare(request, graph)
    return adapter.adapt_graph(request, raw, score_request, manifest, scores,
                               {'prompt_manifest_sha256': 'a' * 64, 'test_fixture': True})


class NaturalAdapterTests(unittest.TestCase):
    def test_exact_translation_keeps_all_scores_and_full_context(self):
        cache, manifest = adapted()
        self.assertEqual(cache.base.provenance['evidence_type'], 'unversioned_model_scores')
        self.assertIsNone(cache.base.provenance['candidate_model_revision'])
        self.assertEqual(len(cache.base.aliases), 2)
        for mention in cache.base.problem.mentions:
            self.assertEqual(mention.null_score, -1)
            self.assertEqual(mention.context_source_ids, ('s1', 's2'))
            self.assertEqual([r.unary_score for r in mention.readings], [3, -2])
            self.assertEqual(mention.readings[-1].context_source_ids, ('s1', 's2'))
        for claim in cache.base.claims.values():
            self.assertEqual(claim.context_source_ids, ('s1', 's2'))
            self.assertIsNone(claim.start.lower)
            self.assertEqual(claim.modality, 'announced_future')
        self.assertEqual(cache.base.problem.links[0].score, 2)
        self.assertEqual(manifest['counts']['scored_items'], 7)
        self.assertEqual(manifest['adapter_semantic_repairs'], [])
        self.assertEqual(manifest['numeric_score_defaults'], [])

    def test_correction_policy_and_eight_way_replay_are_real_translations(self):
        cache, manifest = adapted()
        self.assertEqual(cache.policy.correction_evidence[0].link_id, 'l1')
        result = adapter.replay(cache, manifest)
        self.assertEqual(len(result['runs']), 8)
        self.assertEqual(len(result['state_comparisons']), 28)
        self.assertTrue(all(row['active_claim_ids'] == ['r2'] for row in result['runs']
                            if row['objective_mode'] == 'historical'))
        self.assertTrue(all(row['active_claim_ids'] == ['r1', 'r2'] for row in result['runs']
                            if row['objective_mode'] == 'active_support'))
        self.assertFalse(result['qa_or_gold_loaded'])
        self.assertFalse(result['performance_or_accuracy_claim'])
        self.assertNotIn('elapsed_seconds', json.dumps(result))

    def test_non_subject_alias_is_explicit_metadata_exclusion(self):
        request, graph = fixture()
        graph['aliases'].append({**deepcopy(graph['aliases'][0]),
            'alias_id': 'a2', 'canonical_name': 'Units', 'alias': 'U'})
        cache, manifest = adapted(request, graph)
        self.assertEqual(len(cache.base.aliases), 2)
        self.assertEqual(manifest['counts']['excluded_non_subject_aliases'], 1)
        excluded = [r for r in manifest['configuration']['metadata_projection']
                    if r['kind'] == 'excluded_non_subject_alias']
        self.assertEqual(excluded[0]['alias_id'], 'a2')

    def test_cross_source_evidence_is_kept_in_sidecar_and_context(self):
        request, graph = fixture()
        graph['mentions'][1]['readings'][0]['evidence'].append(
            deepcopy(graph['mentions'][0]['readings'][0]['evidence'][0]))
        cache, manifest = adapted(request, graph)
        self.assertEqual(len(cache.base.claims['m2', 'r2'].evidence_spans), 1)
        self.assertEqual(len(manifest['configuration']['aligned_evidence']['readings']['r2']), 2)
        self.assertEqual(cache.base.claims['m2', 'r2'].context_source_ids, ('s1', 's2'))

    def test_ambiguous_evidence_is_rejected_without_first_match_fallback(self):
        request, graph = fixture()
        request['sources'][0]['text'] += ' ' + graph['mentions'][0]['anchor_quote']
        request['sources'][0]['text_sha256'] = sha256(request['sources'][0]['text'].encode()).hexdigest()
        with self.assertRaisesRegex(ValueError, 'unique_exact_match'):
            adapted(request, graph)

    def test_bad_forecast_observation_is_rejected_without_semantic_repair(self):
        request, graph = fixture()
        graph['mentions'][0]['readings'][0]['state_observed_at'] = ['2026-10-01']
        with self.assertRaisesRegex(ValueError, 'plan_with_actual_state_observation'):
            adapted(request, graph)

    def test_missing_judgment_fails_instead_of_defaulting_score(self):
        request, graph = fixture()
        raw, score_request, manifest, scores = prepare(request, graph)
        scores = json.loads(scores)
        scores['scores'].pop()
        with self.assertRaisesRegex(ValueError, 'Missing scoring items'):
            adapter.adapt_graph(request, raw, score_request, manifest, json.dumps(scores),
                                {'prompt_manifest_sha256': 'a' * 64})

    def test_mapping_tamper_and_unknown_request_fields_are_rejected(self):
        request, graph = fixture()
        raw, score_request, manifest, scores = prepare(request, graph)
        first = next(iter(manifest['item_mapping']))
        manifest['item_mapping'][first]['mention_id'] = 'forged'
        with self.assertRaisesRegex(ValueError, 'deterministic neutral'):
            adapter.adapt_graph(request, raw, score_request, manifest, scores,
                                {'prompt_manifest_sha256': 'a' * 64})
        request['gold'] = 'should not enter adapter'
        with self.assertRaisesRegex(ValueError, 'Unexpected source request'):
            adapted(request, graph)

    def test_overbudget_is_rejected_without_pruning(self):
        request, graph = fixture()
        request['scope_filter']['max_mentions'] = 1
        with self.assertRaisesRegex(ValueError, 'mention_overflow_no_pruning'):
            adapted(request, graph)

    def test_full_numeric_bounds_polarity_and_partial_precision_preserved(self):
        request, graph = fixture()
        graph['links'] = []
        reading = graph['mentions'][0]['readings'][0]
        reading.update(polarity='negative', modality='uncertain')
        reading['start'] = {'lower': '2026-01-01', 'upper': '2026-12-31', 'precision': 'year_only'}
        cache, manifest = adapted(request, graph)
        claim = cache.base.claims['m1', 'r1']
        self.assertEqual((claim.start.lower, claim.start.upper), ('2026-01-01', '2026-12-31'))
        self.assertEqual((claim.polarity, claim.modality), ('negative', 'uncertain'))
        self.assertIsNone(cache.base.problem.mentions[0].readings[0].effective_start)
        self.assertEqual(manifest['configuration']['metadata_projection'][0]['retained_outside_claim']['start_precision'], 'year_only')

    def test_receipt_rejects_changed_output_unknown_revision_or_extra_unbound_input(self):
        blob = b'output'
        receipt = {'backend': 'unversioned_conversational_agent', 'model_revision': None,
            'output_sha256': sha256(blob).hexdigest(), 'task_text': 'Exact synthetic task',
            'inputs': [{'sha256': 'a' * 64}]}
        adapter.validate_receipt(receipt, blob, ['a' * 64], 'Test')
        for change in ({'output_sha256': 'b' * 64}, {'model_revision': 'unknown'},
                       {'inputs': [{'sha256': 'a' * 64}, {'sha256': 'b' * 64}]}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                adapter.validate_receipt({**receipt, **change}, blob, ['a' * 64], 'Test')

    def test_file_binding_never_opens_receipt_paths_and_enforces_input_roles(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            blobs = {'candidate_request': b'{"synthetic":1}', 'scoring_request': b'{"synthetic":2}',
                'scoring_manifest': b'{"synthetic":3}', 'candidates': b'raw candidates',
                'scores': b'raw scores', 'candidate_prompt': b'candidate prompt',
                'candidate_schema': b'candidate schema', 'scorer_prompt': b'scorer prompt',
                'scorer_schema': b'scorer schema'}
            def receipt(output, names):
                return {'backend': 'unversioned_conversational_agent', 'model_revision': None,
                    'output_sha256': sha256(blobs[output]).hexdigest(), 'task_text': 'Synthetic exact task',
                    'inputs': [{'path': '/nonexistent/original/workspace/' + name,
                                'sha256': sha256(blobs[name]).hexdigest()} for name in names]}
            candidate = receipt('candidates', ('candidate_request', 'candidate_prompt', 'candidate_schema'))
            scorer = receipt('scores', ('scoring_request', 'scorer_prompt', 'scorer_schema'))
            blobs.update(candidate_receipt=json.dumps(candidate).encode(), scorer_receipt=json.dumps(scorer).encode())
            paths = {name: root / (name + '.txt') for name in blobs}
            for name, value in blobs.items():
                paths[name].write_bytes(value)
            _, _, binding = adapter.bind_files(paths)
            self.assertTrue(binding['receipts_checked'])
            # Scores are explicitly bound files but are forbidden candidate inputs.
            candidate['inputs'].append({'path': '/nonexistent/scores', 'sha256': sha256(blobs['scores']).hexdigest()})
            paths['candidate_receipt'].write_text(json.dumps(candidate))
            with self.assertRaisesRegex(ValueError, 'input not bound'):
                adapter.bind_files(paths)


if __name__ == '__main__':
    unittest.main()
