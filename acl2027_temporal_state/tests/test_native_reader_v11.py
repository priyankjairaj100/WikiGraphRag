"""Safety and provenance boundaries for the bounded reader; no inference."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import native_reader_v11 as reader


class NativeReaderBoundaryTests(unittest.TestCase):
    def request(self):
        return {'items': [{'item_id': ident, 'messages': [
            {'role': 'system', 'content': 'Return JSON without quotations.'},
            {'role': 'user', 'content': 'A neutral authored placeholder.'}]}
            for ident in ('first', 'last')]}

    def read_request(self, value):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'request.json'
            path.write_text(json.dumps(value))
            return reader.request_items(path)

    def prompt(self, count=10):
        text = 'neutral input<think>\n\n</think>\n\n'
        tokens = list(range(count))
        return {'rendered_prompt': text, 'input_token_ids': tokens,
                'rendered_prompt_sha256': hashlib.sha256(text.encode()).hexdigest(),
                'input_token_ids_sha256': reader.base.canonical_hash(tokens), 'input_tokens': count}

    def response(self, prompt):
        settings = reader.config()['completion_parameters'] | {
            'temperature': 0.0, 'generation_prompt': '', 'lora': [], 'backend_sampling': False}
        return {'prompt': prompt['rendered_prompt'], 'model': str(reader.backend.DEFAULT_ASSETS /
                reader.backend.config()['model_file']), 'truncated': False,
                'tokens_evaluated': prompt['input_tokens'], 'tokens_predicted': 3,
                'timings': {'cache_n': 0, 'prompt_n': prompt['input_tokens'], 'predicted_n': 3},
                'stop': True, 'stop_type': 'eos', 'content': '{}', 'tokens': [1, 2],
                'generation_settings': settings}

    def test_references_cannot_be_passed_as_item_metadata(self):
        request = self.request()
        request['items'][0]['reference'] = 'excluded'
        with self.assertRaisesRegex(ValueError, 'reference'):
            self.read_request(request)

    def test_extra_items_duplicate_ids_and_assistant_messages_rejected(self):
        for mutation in ('extra', 'duplicate', 'assistant'):
            with self.subTest(mutation=mutation):
                request = self.request()
                if mutation == 'extra':
                    request['items'].append(copy.deepcopy(request['items'][0]))
                elif mutation == 'duplicate':
                    request['items'][1]['item_id'] = 'first'
                else:
                    request['items'][0]['messages'][1]['role'] = 'assistant'
                with self.assertRaises(ValueError):
                    self.read_request(request)

    def test_full_generation_reserve_enforced_at_boundary(self):
        reader.validate_prompt(self.prompt(3712))
        with self.assertRaisesRegex(ValueError, 'context'):
            reader.validate_prompt(self.prompt(3713))

    def test_token_hash_changes_detected(self):
        prompt = self.prompt()
        prompt['input_token_ids'][0] = 1000
        with self.assertRaisesRegex(ValueError, 'token hash'):
            reader.validate_prompt(prompt)

    def test_cold_cache_and_complete_prompt_required(self):
        prompt = self.prompt()
        for mutation in ('cache', 'truncated', 'incomplete'):
            with self.subTest(mutation=mutation):
                response = self.response(prompt)
                if mutation == 'cache':
                    response['timings']['cache_n'] = 1
                elif mutation == 'truncated':
                    response['truncated'] = True
                else:
                    response['tokens_evaluated'] -= 1
                with self.assertRaises(ValueError):
                    reader.validate_response(prompt, response, reader.backend.DEFAULT_ASSETS)

    def test_budget_stop_is_retained_failure_and_invalid_json_not_repaired(self):
        prompt = self.prompt()
        response = self.response(prompt)
        response.update(content='{"unfinished":', stop_type='limit', tokens_predicted=384)
        response['timings']['predicted_n'] = 384
        with self.assertRaisesRegex(ValueError, 'budget exhausted'):
            reader.validate_response(prompt, response, reader.backend.DEFAULT_ASSETS)
        projected = reader.response_projection('first', response)
        self.assertFalse(projected['valid_json'])
        self.assertTrue(projected['output_budget_reached'])
        self.assertNotIn('content', projected)
        self.assertNotIn('tokens', projected)
        self.assertEqual(projected['content_sha256'], hashlib.sha256(response['content'].encode()).hexdigest())

    def test_sampler_changes_rejected(self):
        prompt = self.prompt()
        response = self.response(prompt)
        response['generation_settings']['samplers'] = ['top_k', 'temperature']
        with self.assertRaisesRegex(ValueError, 'samplers'):
            reader.validate_response(prompt, response, reader.backend.DEFAULT_ASSETS)

    def test_source_artifacts_cannot_be_stored_in_project(self):
        with self.assertRaisesRegex(ValueError, 'external'):
            reader.external_path(reader.PROJECT / 'fulltext')


if __name__ == '__main__':
    unittest.main()
