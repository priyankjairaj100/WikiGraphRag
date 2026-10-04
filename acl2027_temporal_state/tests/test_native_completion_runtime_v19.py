"""Authored native-lifecycle controls: all process, HTTP and cgroup calls mocked."""
import copy
import hashlib
import json
from pathlib import Path
import signal
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import native_completion_runtime_v19 as rt


def snapshot(*, maximum=0, oom=0, kill=0):
    return {'telemetry_ok': True, 'memory_current_bytes': 8589934592,
            'memory_max_bytes': 8589934592, 'pressure_proxy_bytes': 1000000,
            'components': {k: 1000000 if k == 'anon' else 0 for k in rt.pressure.COMPONENTS},
            'events': {'max': maximum, 'oom': oom, 'oom_kill': kill}}


def prompt():
    text, tokens = 'Authored placeholder prompt', [11, 12]
    return {'rendered_prompt': text, 'input_token_ids': tokens, 'input_tokens': 2,
            'rendered_prompt_sha256': hashlib.sha256(text.encode()).hexdigest(),
            'input_token_ids_sha256': rt.native.base.canonical_hash(tokens)}


def response(c, p, content='malformed answer text', stop='eos'):
    settings = {k: v for k, v in rt.PARAMETERS.items() if k not in ('temperature', 'cache_prompt', 'return_tokens')}
    settings.update(generation_prompt='', lora=[], backend_sampling=False, temperature=0.0)
    return {'prompt': p['rendered_prompt'], 'model': c['model']['path'], 'truncated': False,
            'tokens_evaluated': p['input_tokens'], 'tokens_predicted': 1, 'tokens': [13],
            'timings': {'cache_n': 0, 'prompt_n': p['input_tokens'], 'predicted_n': 1},
            'stop': True, 'stop_type': stop, 'content': content, 'generation_settings': settings}


class QuietThread:
    def __init__(self, *args, **kwargs):
        pass
    def start(self):
        pass
    def join(self, timeout):
        pass
    def is_alive(self):
        return False


class LifecycleControls(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.c = json.loads((rt.ancestor.ROOT / 'configs/binding_reader_v16.json').read_text())
        self.c['model']['path'] = str(self.root / 'unused-authored.gguf')
        self.profile = {'worker_reserve_bytes': 4563402752, 'owned_rss_stop_bytes': 4563402752}
        self.p = prompt()
        self.item = {'request_id': 'fictional_B1', 'messages': [{'role': 'user', 'content': 'Fictional only'}]}
        self.events, self.endpoints = [], []
        self.proc = type('Process', (), {'pid': 17, 'poll': lambda s: None})()
        self.body = response(self.c, self.p)
        self.logtext = b'llama_context: n_rs_seq = 0\n'
        def popen(*args, **kwargs):
            self.assertTrue(kwargs['start_new_session'])
            kwargs['stdout'].write(self.logtext)
            kwargs['stdout'].flush()
            return self.proc
        def api(port, method, endpoint, payload, folder, calls, timeout=30):
            calls.append({'index': len(calls), 'endpoint': endpoint, 'method': method})
            self.endpoints.append(endpoint)
            if endpoint == 'health':
                return {'status': 'ok'}
            if endpoint == 'props':
                return {'model_path': self.c['model']['path'], 'total_slots': 1,
                        'default_generation_settings': {'n_ctx': 8192}, 'build_info': 'b11146-7fe450e19'}
            if endpoint == 'apply-template':
                return {'prompt': self.p['rendered_prompt']}
            if endpoint == 'tokenize':
                return {'tokens': self.p['input_token_ids']}
            if endpoint == 'completion':
                self.assertEqual(payload, rt.PARAMETERS | {'prompt': self.p['input_token_ids']})
                return self.body
            raise AssertionError(endpoint)
        self.patches = [patch.object(rt.native, 'verify_assets', return_value={'mocked': True}),
                        patch.object(rt.pressure, 'read_snapshot', side_effect=lambda: snapshot()),
                        patch.object(rt.subprocess, 'Popen', side_effect=popen),
                        patch.object(rt.socket, 'socket'), patch.object(rt.threading, 'Thread', QuietThread),
                        patch.object(rt.owned, 'bind_group', return_value={'telemetry_ok': True}),
                        patch.object(rt.owned, 'read_group_rss', return_value={
                            'telemetry_ok': True, 'group_members_observed': 1, 'rss_bytes': 1000000}),
                        patch.object(rt, 'signal_inner'),
                        patch.object(rt, 'cleanup', return_value={'confirmed': True}),
                        patch.object(rt, 'api', side_effect=api)]
        self.mocks = [p.start() for p in self.patches]
        for p in self.patches:
            self.addCleanup(p.stop)
        self.launch = self.mocks[2]
        self.kill = self.mocks[7]
        self.clean = self.mocks[8]
        self.read_snapshot = self.mocks[1]

    def run_one(self, n=0, history=None, original=None):
        return rt.complete_one(self.c, self.profile, self.item, original or self.p,
                               self.root / ('completion_%02d' % n),
                               lambda stage, info: self.events.append(stage),
                               {} if history is None else history)

    def test_semantically_bad_output_is_technical_pass(self):
        with patch.object(rt.native, 'assessment', side_effect=AssertionError('grader called')):
            result = self.run_one()
        self.assertEqual(result['status'], 'technical_passed')
        self.assertEqual(self.endpoints.count('completion'), 1)
        self.assertEqual(self.launch.call_count, 1)
        self.assertTrue(result['cleanup_confirmed'])
        self.assertEqual(self.events, ['process_started', 'completion_started', 'response_received'])

    def test_limit_output_is_technical_pass(self):
        self.body['stop_type'] = 'limit'
        result = self.run_one()
        self.assertEqual(result['status'], 'technical_passed')
        self.assertTrue(result['output_limit'])

    def test_wrong_native_identity_and_cache_are_technical_failures(self):
        for index, (field, value, code) in enumerate([
            ('model', 'different', 'returned_model_changed'),
            ('timings', {'cache_n': 1, 'prompt_n': 2, 'predicted_n': 1}, 'cold_accounting_changed'),
            ('tokens', [], 'returned_token_list_count_mismatch'),
            ('tokens_predicted', 769, 'output_over_budget')]):
            with self.subTest(field=field):
                self.body = response(self.c, self.p)
                self.body[field] = value
                result = self.run_one(index)
                self.assertEqual(result['failure_code'], code)
                self.assertTrue(result['cleanup_confirmed'])
        self.assertEqual(self.kill.call_count, 4)

    def test_changed_parent_prompt_stops_before_completion(self):
        changed = copy.deepcopy(self.p)
        changed['input_token_ids'] = [11, 13]
        result = self.run_one(original=changed)
        self.assertEqual(result['failure_code'], 'native_prompt_changed')
        self.assertNotIn('completion', self.endpoints)

    def test_absent_rollback_observation_stops(self):
        self.logtext = b'no rollback telemetry\n'
        result = self.run_one()
        self.assertEqual(result['failure_code'], 'native_rollback_state_not_zero_or_unobserved')
        self.assertNotIn('completion', self.endpoints)

    def test_cleanup_failure_is_not_a_pass(self):
        self.clean.return_value = {'confirmed': False}
        result = self.run_one()
        self.assertEqual(result['failure_code'], 'owned_cleanup_unconfirmed')
        self.assertFalse(result['cleanup_confirmed'])

    def test_between_process_new_oom_and_counter_reset_block_next_launch(self):
        for n, bad in enumerate([snapshot(maximum=2, oom=1), snapshot(maximum=1)]):
            history = {}
            self.read_snapshot.side_effect = lambda: snapshot(maximum=2)
            first = self.run_one(n * 2, history)
            self.assertEqual(first['status'], 'technical_passed')
            launched = self.launch.call_count
            self.read_snapshot.side_effect = lambda: copy.deepcopy(bad)
            second = self.run_one(n * 2 + 1, history)
            self.assertEqual(second['status'], 'failed')
            self.assertEqual(second['failure_code'], 'cgroup_event_counter_reset' if n else 'new_cgroup_oom_event')
            self.assertEqual(self.launch.call_count, launched)

    def test_post_cleanup_oom_is_terminal(self):
        values = [snapshot()] * 6 + [snapshot(oom=1)]
        self.read_snapshot.side_effect = values
        result = self.run_one()
        self.assertEqual(result['failure_code'], 'new_cgroup_oom_event')
        self.assertTrue(result['cleanup_confirmed'])

    def test_missing_between_process_telemetry_refuses_without_launch(self):
        self.read_snapshot.side_effect = lambda: {'telemetry_ok': False}
        result = self.run_one(history={'last': snapshot()})
        self.assertEqual(result['failure_code'], 'cgroup_telemetry_unavailable')
        self.assertEqual(self.launch.call_count, 0)

    def test_completion_endpoint_rejects_changed_decode_without_http(self):
        with patch.object(rt.urllib.request, 'urlopen', side_effect=AssertionError('HTTP forbidden')):
            for change in ({'temperature': 0.0}, {'seed': 1}, {'n_predict': 769}, {'cache_prompt': True}):
                with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'completion_payload_changed'):
                    # Use the real API function: this test's lifecycle stub is bypassed.
                    self.patches[-1].stop()
                    try:
                        rt.api(1, 'POST', 'completion', rt.PARAMETERS | {'prompt': [1]} | change,
                               self.root, [])
                    finally:
                        self.patches[-1].start()


if __name__ == '__main__':
    unittest.main()
