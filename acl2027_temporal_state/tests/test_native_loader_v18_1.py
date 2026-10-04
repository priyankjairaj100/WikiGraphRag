"""Focused logging-only amendment controls; every native launch is mocked."""
import ast
import inspect
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import native_loader_v18 as parent
import native_loader_v18_1 as loader
import test_native_loader_v18 as prior_controls


class LoggingAmendmentControls(unittest.TestCase):
    def setUp(self):
        patched = patch.object(prior_controls, 'loader', loader)
        patched.start()
        self.addCleanup(patched.stop)

    def prepare_frozen(self, root):
        p, c = loader.profile()
        c = json.loads(json.dumps(c))
        model = root / 'fake.gguf'
        model.write_bytes(b'authored')
        c['model'] = dict(c['model'], path=str(model), bytes=8)
        external = root / 'attempt'
        public = ROOT / 'results' / ('authored_logging_test_' + root.name + '.json')
        p = dict(p, attempt_external_path=str(external), public_receipt_path=str(public.relative_to(ROOT)))
        pending = {'results/native_loader_logging_controls_v18_1.json',
                   'results/native_loader_independent_review_v18_1.json'}
        with patch.object(loader, 'profile', return_value=(p, c)), \
             patch.object(loader, 'RECIPE', [x for x in loader.RECIPE if x not in pending]):
            loader.freeze(external, public)
        self.addCleanup(lambda: public.unlink(missing_ok=True))
        return p, c, external, public

    mock_native_run = prior_controls.NativeLoaderControls.mock_native_run

    def test_command_adds_exactly_trace_verbosity_only(self):
        p, c = loader.profile()
        self.assertEqual(c, parent.profile()[1])
        self.assertEqual(loader.command(c), loader.native.command(c) + ['--log-verbosity', '4'])
        self.assertEqual(loader.command(c).count('--log-verbosity'), 1)
        self.assertEqual(p['native_log_verbosity'], 4)

    def test_run_ast_changes_only_popen_command_builder(self):
        before = ast.parse(inspect.getsource(parent.run))
        after = ast.parse(inspect.getsource(loader.run))
        class RestoreCommand(ast.NodeTransformer):
            replacements = 0
            def visit_Call(self, node):
                if isinstance(node.func, ast.Name) and node.func.id == 'command':
                    node.func = ast.Attribute(value=ast.Name(id='native', ctx=ast.Load()),
                                              attr='command', ctx=ast.Load())
                    self.replacements += 1
                return self.generic_visit(node)
        restore = RestoreCommand()
        after = restore.visit(after)
        self.assertEqual(restore.replacements, 1)
        self.assertEqual(ast.dump(after), ast.dump(before))
        for name in ('preflight_reason', 'owned_reason', 'watchdog', 'cleanup', 'api', 'signal_inner'):
            self.assertEqual(inspect.getsource(getattr(loader, name)), inspect.getsource(getattr(parent, name)))

    def test_all_caps_and_authored_request_pin_are_unchanged(self):
        before, old_config = parent.profile()
        after, new_config = loader.profile()
        exempt = {'schema_version', 'attempt_external_path', 'public_receipt_path',
                  'parent_failure_sha256', 'parent_frozen_protocol_sha256'}
        self.assertEqual({k: v for k, v in before.items() if k not in exempt},
                         {k: after[k] for k in before if k not in exempt})
        self.assertEqual(new_config, old_config)
        self.assertNotEqual(after['attempt_external_path'], before['attempt_external_path'])

    def test_zero_observation_allows_only_eight_mocked_tokenizations(self):
        with tempfile.TemporaryDirectory() as d:
            result = self.mock_native_run(Path(d))
        self.assertEqual(result['status'], 'loader_tokenizer_passed')
        self.assertEqual(result['tokenized_prompts'], 8)
        self.assertEqual(result['completion_calls_started'], 0)
        self.assertEqual(result['api_endpoint_counts'], {'health': 1, 'props': 1, 'apply-template': 8, 'tokenize': 8})
        self.assertTrue(result['cleanup_confirmed'])

    def test_missing_and_nonzero_rollback_observations_still_stop(self):
        original = Path.write_text
        for content in ('no rollback field\n', 'llama_context: n_rs_seq = 3\n'):
            def write(path, text, *args, **kwargs):
                return original(path, content if path.name == 'server.log' else text, *args, **kwargs)
            with tempfile.TemporaryDirectory() as d, patch.object(Path, 'write_text', write):
                result = self.mock_native_run(Path(d))
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['failure_code'], 'native_rollback_state_not_zero_or_unobserved')
            self.assertEqual(result['tokenized_prompts'], 0)
            self.assertTrue(result['cleanup_confirmed'])
            self.assertEqual(len(result['request_states']), 8)
            self.assertTrue(all(x['status'] == 'not_attempted' for x in result['request_states']))

    def test_actual_attempt_destination_is_separate_and_fixed(self):
        p, _ = loader.profile()
        self.assertTrue(p['attempt_external_path'].endswith('native_loader_attempt02_logging_observation'))
        with self.assertRaisesRegex(ValueError, 'single_fixed_attempt_destination'):
            loader.freeze(Path(parent.profile()[0]['attempt_external_path']),
                          ROOT / parent.profile()[0]['public_receipt_path'])

    def test_inherited_watchdog_and_cleanup_controls(self):
        prior = prior_controls.NativeLoaderControls()
        prior.test_watchdog_kills_independently_while_api_is_blocked()
        prior.test_cleanup_counts_live_descendant_after_leader_exit()
        prior.test_cleanup_missing_telemetry_is_not_success()
        prior.test_each_remaining_resource_failure_immediately_kills_owned_group()


if __name__ == '__main__':
    unittest.main()
