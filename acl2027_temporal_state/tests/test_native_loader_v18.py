"""Authored/mocked loader admission controls: never load a model."""
import importlib.util
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import native_loader_v18 as loader


def snapshot(proxy=2 * 2**30, current=8 * 2**30 - 1):
    return {'telemetry_ok': True, 'pressure_proxy_bytes': proxy,
            'memory_current_bytes': current, 'memory_max_bytes': 8 * 2**30,
            'events': {'max': 0, 'oom': 0, 'oom_kill': 0},
            'components': {key: proxy if key == 'anon' else 0 for key in loader.pressure.COMPONENTS}}


class NativeLoaderControls(unittest.TestCase):
    def test_profile_keeps_native_pins_and_caps(self):
        p, c = loader.profile()
        self.assertEqual(p['maximum_completions'], 0)
        self.assertEqual(c['address_space_limit_bytes'], int(5.5 * 2**30))
        self.assertEqual((c['maximum_context_tokens'], c['maximum_input_tokens'], c['maximum_generated_tokens']), (8192, 6144, 768))
        self.assertEqual(p['components'], list(loader.pressure.COMPONENTS))
        self.assertIn('sock', p['components'])

    def test_reserve_equality_refuses_before_launch(self):
        reserve = int(4.25 * 2**30)
        s = snapshot(proxy=7 * 2**30 - reserve)
        self.assertEqual(loader.preflight_reason(s, reserve=reserve), 'cgroup_pressure_proxy_limit')
        s['pressure_proxy_bytes'] -= 1
        s['components']['anon'] -= 1
        self.assertIsNone(loader.preflight_reason(s, reserve=reserve))

    def test_total_boundary_is_diagnostic_and_missing_telemetry_fails_closed(self):
        for current in (8 * 2**30 - 1, 8 * 2**30, 8 * 2**30 + 4096):
            self.assertIsNone(loader.preflight_reason(snapshot(current=current), reserve=int(4.25 * 2**30)))
        self.assertEqual(loader.preflight_reason({'telemetry_ok': False}), 'cgroup_telemetry_unavailable')

    def test_oom_counter_increase_refuses(self):
        s = snapshot()
        s['events']['oom_kill'] = 1
        self.assertEqual(loader.preflight_reason(s, snapshot()), 'new_cgroup_oom_event')

    def test_live_group_missing_zero_and_boundary_fail(self):
        cap = int(4.25 * 2**30)
        for s in [{'telemetry_ok': False}, {'telemetry_ok': True, 'group_members_observed': False, 'rss_bytes': 0},
                  {'telemetry_ok': True, 'group_members_observed': True, 'rss_bytes': 0},
                  {'telemetry_ok': True, 'group_members_observed': True, 'rss_bytes': cap}]:
            self.assertIsNotNone(loader.owned_reason(s, live=True, rss_limit=cap))
        s = {'telemetry_ok': True, 'group_members_observed': True, 'rss_bytes': cap - 1}
        self.assertIsNone(loader.owned_reason(s, live=True, rss_limit=cap))

    def test_generation_endpoint_never_reaches_network(self):
        with patch.object(loader.urllib.request, 'urlopen') as network:
            for endpoint in ['completion', 'completions', 'v1/chat/completions', 'embedding']:
                with self.assertRaisesRegex(ValueError, 'endpoint_forbidden'):
                    loader.api(18162, 'POST', endpoint, {}, Path('/unused'), [])
            network.assert_not_called()

    def test_signals_use_original_inner_group_not_outer(self):
        process = Mock(pid=7)
        with patch.object(loader.os, 'killpg') as kill:
            loader.signal_inner(process, signal.SIGTERM)
            kill.assert_called_once_with(7, signal.SIGTERM)

    def test_watchdog_kills_independently_while_api_is_blocked(self):
        process = Mock(pid=7)
        api_blocked = threading.Event()
        release_api = threading.Event()
        kill_seen = threading.Event()
        stopped = threading.Event()
        failures = []
        def blocked_api():
            api_blocked.set()
            release_api.wait(2)
        def kill(inner_pgid, sig):
            self.assertEqual((inner_pgid, sig), (7, signal.SIGKILL))
            kill_seen.set()
        api_thread = threading.Thread(target=blocked_api)
        api_thread.start()
        try:
            self.assertTrue(api_blocked.wait(1))
            with patch.object(loader.os, 'killpg', side_effect=kill):
                watcher = threading.Thread(target=loader.watchdog,
                          args=(process, Mock(side_effect=ValueError('owned_group_rss_limit')),
                                stopped, failures, 0.001))
                watcher.start()
                self.assertTrue(kill_seen.wait(1))
                self.assertTrue(api_thread.is_alive())
                watcher.join(1)
                self.assertFalse(watcher.is_alive())
            self.assertEqual(failures, ['owned_group_rss_limit'])
        finally:
            stopped.set()
            release_api.set()
            api_thread.join(1)

    def test_cleanup_counts_live_descendant_after_leader_exit(self):
        process = Mock(pid=7, returncode=0)
        process.poll.return_value = 0
        anchor = {'telemetry_ok': True, 'outer_pid': 90007}
        live = {'telemetry_ok': True, 'members': [{'state': 'S', 'outer_pid': 90008}], 'rss_bytes': 50}
        empty = {'telemetry_ok': True, 'members': [], 'rss_bytes': 0}
        with patch.object(loader.owned, 'read_group_rss', side_effect=[live, empty]) as read, \
             patch.object(loader, 'signal_inner') as kill, patch.object(loader.time, 'sleep'):
            result = loader.cleanup(process, anchor)
        self.assertTrue(result['confirmed'])
        self.assertEqual(read.call_count, 2)
        kill.assert_called_with(process, signal.SIGTERM)

    def test_cleanup_missing_telemetry_is_not_success(self):
        process = Mock(pid=7, returncode=0)
        process.poll.return_value = 0
        with patch.object(loader.owned, 'read_group_rss', return_value={'telemetry_ok': False}), \
             patch.object(loader, 'signal_inner'):
            self.assertFalse(loader.cleanup(process, {'telemetry_ok': True})['confirmed'])

    def prepare_frozen(self, root):
        c = json.loads((ROOT / 'configs/binding_reader_v16.json').read_text())
        model = root / 'fake.gguf'
        model.write_bytes(b'authored')
        c['model'] = dict(c['model'], path=str(model), bytes=8)
        p = loader.profile()[0]
        external = root / 'attempt'
        public = ROOT / 'results' / ('authored_loader_test_' + root.name + '.json')
        p = dict(p, attempt_external_path=str(external), public_receipt_path=str(public.relative_to(ROOT)))
        pending_receipts = {'results/native_loader_guard_controls_v18.json', 'results/native_loader_independent_review_v18.json'}
        with patch.object(loader, 'profile', return_value=(p, c)), \
             patch.object(loader, 'RECIPE', [x for x in loader.RECIPE if x not in pending_receipts]):
            loader.freeze(external, public)
        self.addCleanup(lambda: public.unlink(missing_ok=True))
        return p, c, external, public

    def test_prelaunch_refusal_retains_all_eight_and_prevents_retry(self):
        with tempfile.TemporaryDirectory() as d:
            p, c, external, public = self.prepare_frozen(Path(d))
            protocol_hash = loader.digest(external / 'frozen_protocol.json')
            with patch.object(loader, 'profile', return_value=(p, c)), \
                 patch.object(loader.pressure, 'read_snapshot', return_value=snapshot(proxy=7 * 2**30)), \
                 patch.object(loader.native, 'verify_assets') as assets, \
                 patch.object(loader.subprocess, 'Popen') as popen:
                result = loader.run(external, protocol_hash)
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(len(result['request_states']), 8)
                self.assertTrue(all(x['status'] == 'not_attempted' for x in result['request_states']))
                self.assertEqual(result['model_processes_started'], 0)
                assets.assert_not_called()
                popen.assert_not_called()
                with self.assertRaisesRegex(ValueError, 'one_attempt_no_retry'):
                    loader.run(external, protocol_hash)

    def test_freeze_copies_no_references_and_binds_all_requests(self):
        with tempfile.TemporaryDirectory() as d:
            _, _, external, _ = self.prepare_frozen(Path(d))
            frozen = loader.load(external / 'frozen_protocol.json')
            requests = loader.load(external / 'requests.json')
            self.assertFalse(frozen['references_copied'])
            self.assertEqual(len(requests), 8)
            self.assertTrue(all(set(x) == {'request_id', 'messages'} for x in requests))
            self.assertFalse((external / 'references.json').exists())

    def test_denominator_truncation_refuses_before_attempt_marker(self):
        with tempfile.TemporaryDirectory() as d:
            p, c, external, public = self.prepare_frozen(Path(d))
            result = loader.load(public)
            result['request_states'] = result['request_states'][:-1]
            loader.write(public, result)
            with patch.object(loader, 'profile', return_value=(p, c)), self.assertRaisesRegex(ValueError, 'full_request_count_required'):
                loader.run(external, loader.digest(external / 'frozen_protocol.json'))
            self.assertFalse((external / 'attempt_started.json').exists())

    def mock_native_run(self, root, fail_tokenization=None, snapshot_function=None):
        p, c, external, public = self.prepare_frozen(root)
        process = Mock(pid=7, returncode=0)
        process.poll.return_value = None
        counter = {'tokenize': 0}
        def launch(*args, **kwargs):
            self.assertTrue(kwargs['start_new_session'])
            (external / 'server.log').write_text('llama_context: n_rs_seq = 0\nCPU compute buffer size = 20.00 MiB\n')
            return process
        def api(port, method, endpoint, payload, folder, calls, timeout=30):
            self.assertIn((method, endpoint), loader.ALLOWED)
            calls.append({'method': method, 'endpoint': endpoint})
            if endpoint == 'health':
                return {'status': 'ok'}
            if endpoint == 'props':
                return {'model_path': c['model']['path'], 'total_slots': 1,
                        'default_generation_settings': {'n_ctx': 8192}, 'build_info': 'b11146-7fe450e19'}
            if endpoint == 'apply-template':
                return {'prompt': 'wholly authored rendered prompt'}
            counter['tokenize'] += 1
            if counter['tokenize'] == fail_tokenization:
                raise ValueError('authored_tokenizer_failure')
            return {'tokens': [1, 2, 3]}
        with patch.object(loader, 'profile', return_value=(p, c)), \
             patch.object(loader.pressure, 'read_snapshot', side_effect=snapshot_function or (lambda: snapshot())), \
             patch.object(loader.native, 'verify_assets', return_value={'authored': True}), \
             patch.object(loader.subprocess, 'Popen', side_effect=launch), \
             patch.object(loader.owned, 'bind_group', return_value={'telemetry_ok': True}), \
             patch.object(loader.owned, 'read_group_rss', return_value={'telemetry_ok': True, 'group_members_observed': True, 'rss_bytes': 2**20}), \
             patch.object(loader, 'cleanup', return_value={'confirmed': True}), \
             patch.object(loader, 'signal_inner'), \
             patch.object(loader, 'api', side_effect=api), patch.object(loader.socket, 'socket'):
            result = loader.run(external, loader.digest(external / 'frozen_protocol.json'))
        return result

    def test_one_mocked_load_exact_eight_tokenizations_and_zero_completions(self):
        with tempfile.TemporaryDirectory() as d:
            result = self.mock_native_run(Path(d))
        self.assertEqual(result['status'], 'loader_tokenizer_passed')
        self.assertEqual(result['model_processes_started'], 1)
        self.assertEqual(result['tokenized_prompts'], 8)
        self.assertEqual(result['completion_calls_started'], 0)
        self.assertEqual(result['api_endpoint_counts'], {'health': 1, 'props': 1, 'apply-template': 8, 'tokenize': 8})

    def test_mocked_tokenization_failure_retains_full_denominator(self):
        with tempfile.TemporaryDirectory() as d:
            result = self.mock_native_run(Path(d), fail_tokenization=2)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['tokenized_prompts'], 1)
        self.assertEqual([x['status'] for x in result['request_states']],
                         ['tokenized', 'failed'] + ['not_attempted'] * 6)
        self.assertEqual(result['completion_calls_started'], 0)


    def test_max_increase_is_diagnostic_but_all_event_resets_fail(self):
        base = snapshot()
        now = snapshot()
        now['events']['max'] = 10
        self.assertIsNone(loader.preflight_reason(now, base))
        later = snapshot()
        later['events']['max'] = 9
        self.assertEqual(loader.preflight_reason(later, now), 'cgroup_event_counter_reset')
        for event in loader.pressure.EVENTS:
            before, after = snapshot(), snapshot()
            before['events'][event] = 10
            after['events'][event] = 9
            self.assertEqual(loader.preflight_reason(after, before), 'cgroup_event_counter_reset')
        for event in ('oom', 'oom_kill'):
            changed = snapshot()
            changed['events'][event] = 1
            self.assertEqual(loader.preflight_reason(changed, base), 'new_cgroup_oom_event')

    def test_required_event_values_fail_closed(self):
        for event in loader.pressure.EVENTS:
            for invalid in (None, -1, 1.5, '2', True):
                changed = snapshot()
                changed['events'][event] = invalid
                self.assertEqual(loader.preflight_reason(changed), 'cgroup_telemetry_unavailable')
            changed = snapshot()
            del changed['events'][event]
            self.assertEqual(loader.preflight_reason(changed), 'cgroup_telemetry_unavailable')

    def test_reader_requires_well_formed_max_and_retains_boundary(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'memory.current').write_text(str(8 * 2**30 + 4096))
            (root / 'memory.max').write_text(str(8 * 2**30))
            (root / 'memory.stat').write_text(''.join(key + ' 0\n' for key in loader.pressure.COMPONENTS))
            (root / 'memory.events').write_text('max 8\noom 0\noom_kill 0\n')
            observed = loader.pressure.read_snapshot(root)
            self.assertTrue(observed['telemetry_ok'])
            self.assertTrue(observed['total_boundary_observed'])
            self.assertEqual(observed['events']['max'], 8)
            self.assertIsNone(loader.preflight_reason(observed))
            for invalid in ('oom 0\noom_kill 0\n', 'max -1\noom 0\noom_kill 0\n',
                            'max x\noom 0\noom_kill 0\n', 'max 1\nmax 2\noom 0\noom_kill 0\n'):
                (root / 'memory.events').write_text(invalid)
                self.assertFalse(loader.pressure.read_snapshot(root)['telemetry_ok'])
            (root / 'memory.events').unlink()
            self.assertFalse(loader.pressure.read_snapshot(root)['telemetry_ok'])

    def test_kernel_max_change_remains_a_stop(self):
        changed = snapshot()
        changed['memory_max_bytes'] += 4096
        self.assertEqual(loader.preflight_reason(changed), 'unexpected_kernel_memory_max')

    def test_mocked_equal_total_and_increasing_max_complete_only_tokenizations(self):
        calls = {'n': 0}
        def observe():
            calls['n'] += 1
            observed = snapshot(current=8 * 2**30 + (4096 if calls['n'] % 2 else 0))
            observed['events']['max'] = calls['n']
            return observed
        with tempfile.TemporaryDirectory() as d:
            result = self.mock_native_run(Path(d), snapshot_function=observe)
        self.assertEqual(result['status'], 'loader_tokenizer_passed')
        self.assertEqual(result['tokenized_prompts'], 8)
        self.assertEqual(result['completion_calls_started'], 0)

    def test_mocked_counter_drop_above_initial_baseline_stops_and_cleans(self):
        values = iter((3, 4, 10, 9))
        def observe():
            observed = snapshot()
            observed['events']['max'] = next(values, 9)
            return observed
        with tempfile.TemporaryDirectory() as d:
            result = self.mock_native_run(Path(d), snapshot_function=observe)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['failure_code'], 'cgroup_event_counter_reset')
        self.assertTrue(result['cleanup_confirmed'])
        self.assertEqual(result['tokenized_prompts'], 0)
        self.assertEqual(len(result['request_states']), 8)

    def test_each_remaining_resource_failure_immediately_kills_owned_group(self):
        cases = []
        cases.append((snapshot(proxy=7 * 2**30), 'cgroup_pressure_proxy_limit'))
        cases.append(({'telemetry_ok': False}, 'cgroup_telemetry_unavailable'))
        for event in ('oom', 'oom_kill'):
            value = snapshot()
            value['events'][event] = 1
            cases.append((value, 'new_cgroup_oom_event'))
        changed = snapshot()
        changed['memory_max_bytes'] -= 4096
        cases.append((changed, 'unexpected_kernel_memory_max'))
        for observed, expected in cases:
            def check():
                reason = loader.preflight_reason(observed, snapshot())
                loader.require(reason is None, reason)
            process = Mock(pid=7)
            stopped = Mock()
            stopped.wait.return_value = False
            failures = []
            with patch.object(loader.os, 'killpg') as kill:
                loader.watchdog(process, check, stopped, failures, 0.1)
            self.assertEqual(failures, [expected])
            kill.assert_called_once_with(7, signal.SIGKILL)

    def test_only_one_configured_real_attempt_destination(self):
        p, c = loader.profile()
        with self.assertRaisesRegex(ValueError, 'single_fixed_attempt_destination'):
            loader.freeze(Path('/workspace/scratch/bdef663e3dfc/not_the_configured_attempt'),
                          ROOT / 'results/not_the_configured_receipt.json')


if __name__ == '__main__':
    unittest.main()
