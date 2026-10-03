"""Offline adapter contracts; synthetic values here are tests, never results."""
from copy import deepcopy
from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import unittest

from temporal_state.correction_io import load_correction_cache, make_correction_cache
from temporal_state.scored_io import make_cache

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('likelihood_adapter_v07',
    ROOT / 'scripts/run_likelihood_pilot_v07.py')
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


class LikelihoodAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parent = load_correction_cache(ROOT / 'data/natural_model_pilot_v06/shared_cache.json')
        cls.mapping = json.loads((ROOT / 'data/natural_model_pilot_v06/scoring_manifest.json').read_text())['item_mapping']
        cls.scores = {item_id: index / 7 - 2 for index, item_id in enumerate(sorted(cls.mapping))}

    def derive(self, **kwargs):
        args = dict(parent=self.parent, mapping=self.mapping, scores=self.scores,
                    condition='yes_no', scorer={'model_id': 'TEST_ONLY/scorer', 'model_sha256': 'c' * 64},
                    prompt_sha256='d' * 64, lineage={'purpose': 'synthetic unit test, not an experiment'})
        args.update(kwargs)
        return adapter.derive(**args)

    def test_every_scalar_replaced_once_and_all_non_score_inputs_preserved(self):
        child, counts = self.derive()
        self.assertEqual(counts, {'reading': 7, 'unresolved': 7, 'link': 11, 'no_link': 7})
        targets = adapter.validate_mapping(self.parent, self.mapping)
        actual = {('reading', m.mention_id, r.reading_id): r.unary_score
                  for m in child.base.problem.mentions for r in m.readings}
        actual.update({('no_link', m.mention_id, None): m.null_score for m in child.base.problem.mentions})
        actual.update({('link', link.mention_id, link.link_id): link.score for link in child.base.problem.links})
        self.assertEqual(actual, {targets[item_id]: score for item_id, score in self.scores.items()})
        self.assertEqual(adapter.non_score_payload(child), adapter.non_score_payload(self.parent))
        self.assertEqual(dict(child.provenance), dict(self.parent.provenance))
        self.assertIsNone(child.base.provenance['candidate_model_revision'])
        self.assertEqual(child.base.provenance['evidence_type'], 'pinned_scorer_unversioned_candidates')

    def test_missing_each_score_kind_rejected_without_defaults(self):
        for kind in ('reading', 'unresolved', 'link', 'no_link'):
            item = next(key for key, row in self.mapping.items() if row['kind'] == kind)
            missing = {key: value for key, value in self.scores.items() if key != item}
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, 'Missing or extra'):
                self.derive(scores=missing)

    def test_extra_nonfinite_and_boolean_scores_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Missing or extra'):
            self.derive(scores={**self.scores, 'injected': 0})
        first = next(iter(self.scores))
        for value in (float('inf'), float('-inf'), float('nan'), True, '1', None):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'finite real'):
                self.derive(scores={**self.scores, first: value})

    def test_missing_or_duplicate_target_mapping_rejected(self):
        first = next(iter(self.mapping))
        mapping = {key: value for key, value in self.mapping.items() if key != first}
        with self.assertRaisesRegex(ValueError, 'Missing scores'):
            self.derive(mapping=mapping)
        with self.assertRaisesRegex(ValueError, 'Duplicate mapped'):
            self.derive(mapping={**self.mapping, 'duplicate': self.mapping[first]})

    def test_wrong_resolved_or_link_reading_mapping_rejected(self):
        for kind in ('reading', 'link'):
            mapping = deepcopy(self.mapping)
            item = next(key for key, row in mapping.items() if row['kind'] == kind)
            mapping[item]['candidate_id'] = 'different_reading'
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.derive(mapping=mapping)

    def test_null_mapping_cannot_smuggle_reading_identity(self):
        for kind in ('unresolved', 'no_link'):
            mapping = deepcopy(self.mapping)
            item = next(key for key, row in mapping.items() if row['kind'] == kind)
            mapping[item]['candidate_id'] = 'r1'
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.derive(mapping=mapping)

    def test_undeclared_condition_and_unpinned_asset_fail_closed(self):
        with self.assertRaisesRegex(ValueError, 'Undeclared'):
            self.derive(condition='best_condition')
        with self.assertRaises(ValueError):
            self.derive(scorer={'model_id': 'TEST_ONLY/scorer', 'model_sha256': 'main'})

    def test_hidden_weight_change_rejected(self):
        base = self.parent.base
        changed = make_cache(replace(base.problem, beta=2), base.claims,
                             adapter.thaw(base.aliases), adapter.thaw(base.provenance))
        parent = make_correction_cache(changed, self.parent.policy, adapter.thaw(self.parent.provenance))
        with self.assertRaisesRegex(ValueError, 'beta=1'):
            self.derive(parent=parent)

    def test_raw_response_reproduces_literal_label_values(self):
        smoke = json.loads((ROOT / 'results/local_backend_v07_smoke.json').read_text())
        ids = {label: values[0] for label, values in smoke['label_tokens'].items()}
        values = adapter.verify_response(smoke['response'], smoke['prompt'], ids)
        self.assertEqual(set(values), {'Yes', 'No'})
        for label, token_id in ids.items():
            self.assertEqual(values[label], next(row['logprob'] for row in
                smoke['response']['completion_probabilities'][0]['top_logprobs'] if row['id'] == token_id))

    def test_raw_response_truncation_prompt_and_sampling_mutations_fail(self):
        smoke = json.loads((ROOT / 'results/local_backend_v07_smoke.json').read_text())
        ids = {label: values[0] for label, values in smoke['label_tokens'].items()}
        for name in ('truncated', 'prompt', 'tokens_evaluated', 'samplers', 'logit_bias', 'grammar'):
            response = deepcopy(smoke['response'])
            if name == 'truncated':
                response[name] = True
            elif name == 'prompt':
                response[name] += 'another item'
            elif name == 'tokens_evaluated':
                response[name] -= 1
            else:
                response['generation_settings'][name] = {'samplers': ['top_k'],
                    'logit_bias': [[9454, 10.0]], 'grammar': 'label_only'}[name]
            with self.subTest(name=name), self.assertRaises(ValueError):
                adapter.verify_response(response, smoke['prompt'], ids)

    def test_missing_duplicate_or_wrong_literal_label_raw_probability_fails(self):
        smoke = json.loads((ROOT / 'results/local_backend_v07_smoke.json').read_text())
        ids = {label: values[0] for label, values in smoke['label_tokens'].items()}
        for mutation in ('missing', 'duplicate', 'text', 'nonfinite'):
            response = deepcopy(smoke['response'])
            entries = response['completion_probabilities'][0]['top_logprobs']
            index = next(i for i, row in enumerate(entries) if row['id'] == ids['Yes'])
            if mutation == 'missing':
                entries.pop(index)
            elif mutation == 'duplicate':
                entries[-1] = deepcopy(entries[index])
            elif mutation == 'text':
                entries[index]['token'] = 'Yes, actually'
            else:
                entries[index]['logprob'] = float('nan')
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                adapter.verify_response(response, smoke['prompt'], ids)

    def test_paired_plan_preserves_every_request_and_primary_item_order(self):
        folder = ROOT / 'data/likelihood_scoring_v07'
        request = json.loads((folder / 'request.json').read_text())
        plan = json.loads((folder / 'execution_plan.json').read_text())
        order = adapter.validate_execution_plan(request, plan)
        self.assertEqual(len(order), 64)
        self.assertEqual(order[:4], [('yes_no', 's0001'), ('no_yes', 's0001'),
                                    ('yes_no', 's0002'), ('no_yes', 's0002')])
        self.assertEqual(set(order), {(item['condition_id'], item['item_id']) for item in request['items']})

    def test_missing_duplicate_reversed_or_reordered_execution_items_fail(self):
        folder = ROOT / 'data/likelihood_scoring_v07'
        request = json.loads((folder / 'request.json').read_text())
        original = json.loads((folder / 'execution_plan.json').read_text())
        for mutation in ('missing', 'duplicate', 'label_order', 'item_order', 'unknown'):
            plan = deepcopy(original)
            order = plan['execution_order']
            if mutation == 'missing':
                order.pop()
            elif mutation == 'duplicate':
                order[-1] = deepcopy(order[0])
            elif mutation == 'label_order':
                order[0], order[1] = order[1], order[0]
            elif mutation == 'item_order':
                order[:4] = order[2:4] + order[:2]
            else:
                order[-1]['item_id'] = 'unseen'
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                adapter.validate_execution_plan(request, plan)

    def test_execution_plan_cannot_bind_changed_prompt_bytes(self):
        folder = ROOT / 'data/likelihood_scoring_v07'
        request = json.loads((folder / 'request.json').read_text())
        plan = json.loads((folder / 'execution_plan.json').read_text())
        request['items'][0]['messages'][0]['content'] += 'changed source-scoring instruction'
        with self.assertRaisesRegex(ValueError, 'bind the frozen request'):
            adapter.validate_execution_plan(request, plan)


if __name__ == '__main__':
    unittest.main()
