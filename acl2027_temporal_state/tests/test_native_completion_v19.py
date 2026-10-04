"""Mocked full-batch/ref-separation/sealing controls; zero native processes."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import native_completion_worker_v19 as worker
import freeze_native_completion_v19 as freezer
import grade_native_completion_v19 as grader
from test_native_completion_runtime_v19 import response

REAL_ROOT = worker.ROOT
PARENT = freezer.PARENT


class BatchControls(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.directory = self.root / 'attempt'
        (self.directory / 'inputs').mkdir(parents=True)
        self.public = self.root / 'results/native_authored_completion_v19.json'
        self.grade_public = self.root / 'results/native_authored_completion_grade_v19.json'
        self.references = self.root / 'references.json'
        self.items = self.root / 'items.json'
        self.combined = self.root / 'combined_controls.json'
        self.attestation = self.root / 'attempt_freezer_attestation.json'
        self.references.write_bytes((REAL_ROOT / 'data/reader_binding_v19/authored_references_v19.json').read_bytes())
        self.items.write_bytes((REAL_ROOT / 'data/reader_binding_v19/authored_requests_v19.json').read_bytes())
        self.combined.write_text('Reference-bearing control must stay closed')
        self.p = json.loads((REAL_ROOT / 'configs/native_authored_completion_v19.json').read_text())
        self.p['attempt_external_path'] = str(self.directory)
        self.c = json.loads((REAL_ROOT / 'configs/binding_reader_v16.json').read_text())
        self.pin = self.root / 'mock_inference_code.py'
        self.pin.write_text('No reference reads')
        for name in worker.INPUT_NAMES:
            source = PARENT / ('requests.json' if name == 'requests.json' else 'native_' + name)
            (self.directory / 'inputs' / name).write_bytes(source.read_bytes())
        attested = {'reference_path': str(self.references), 'reference_sha256': worker.digest(self.references),
                    'request_items_path': str(self.items), 'request_items_sha256': worker.digest(self.items),
                    'privileged_recipe_sha256': {}}
        worker.write(self.attestation, attested)
        self.f = {'schema_version': 'native_completion_frozen_v19', 'profile': self.p, 'native_config': self.c,
                  'public_receipt': str(self.public), 'worker_read_pins': {'mock_inference_code.py': worker.digest(self.pin)},
                  'input_sha256': {name: worker.digest(self.directory / 'inputs' / name) for name in worker.INPUT_NAMES},
                  'opaque_reference_sha256': worker.digest(self.references),
                  'freezer_attestation_sha256': worker.runtime.native.base.canonical_hash(attested)}
        self.save_protocol()
        self.calls = []
        self.failure_at = None
        self.cleanup_ok = True
        self.content = 'this is malformed JSON and is deliberately not graded in prediction'
        self.stop = 'eos'
        for obj, name, value in ((worker, 'ROOT', self.root), (grader, 'ROOT', self.root),
                                 (worker, 'WORKER_READ_PINS', ('mock_inference_code.py',))):
            p = patch.object(obj, name, value)
            p.start(); self.addCleanup(p.stop)
        p = patch.object(worker, 'profile', return_value=(self.p, self.c))
        p.start(); self.addCleanup(p.stop)
        self.prediction_patch = patch.object(worker.runtime, 'complete_one', side_effect=self.complete)
        self.prediction_patch.start(); self.addCleanup(self.prediction_patch.stop)

    def save_protocol(self):
        worker.write(self.directory / 'frozen_protocol.json', self.f)
        self.sha = worker.digest(self.directory / 'frozen_protocol.json')
        receipt = {'status': 'frozen_awaiting_root_execution_approval', 'protocol_sha256': self.sha,
                   'model_processes_started': 0, 'completion_calls_started': 0, 'completion_responses': 0,
                   'expected_completions': 8, 'natural_prompts': 0, 'grading_performed': False,
                   'raw_outputs_sealed': False,
                   'request_states': [{'request_id': x, 'status': 'pending', 'process_started': False,
                                     'completion_started': False, 'response_received': False} for x in self.p['request_ids']]}
        worker.write(self.public, receipt)

    def complete(self, c, p, item, original, folder, notify, history):
        index = len(self.calls)
        self.calls.append((item['request_id'], id(history)))
        folder.mkdir()
        result = {'status': 'technical_passed', 'failure_code': None, 'cleanup_confirmed': self.cleanup_ok,
                  'process_started': True, 'completion_started': True, 'response_received': True}
        notify('process_started', {})
        notify('completion_started', {})
        notify('response_received', {})
        body = response(c, original, content=self.content, stop=self.stop)
        worker.write(folder / 'response.json', body)
        worker.write(folder / 'execution_trace.json', {'api_calls': [], 'cleanup': {'confirmed': self.cleanup_ok}})
        result.update(execution_trace_sha256=worker.digest(folder / 'execution_trace.json'),
                      raw_response_sha256=worker.digest(folder / 'response.json'), output_limit=self.stop == 'limit')
        if index == self.failure_at:
            result.update(status='failed', failure_code='authored_mock_native_accounting_failure')
        worker.write(folder / 'technical_result.json', result)
        return result

    def deny_reference_reads(self):
        real_open = Path.open
        forbidden = {self.references, self.combined, self.attestation, self.items}
        def guarded(path, *args, **kwargs):
            if Path(path) in forbidden:
                raise AssertionError('Prediction or pre-seal validation opened a reference-bearing file')
            return real_open(path, *args, **kwargs)
        return patch.object(Path, 'open', guarded)

    def test_worker_runs_all_eight_with_refs_unavailable_and_bad_text(self):
        self.references.unlink()
        self.combined.unlink()
        with self.deny_reference_reads(), patch.object(worker.runtime.native, 'assessment', side_effect=AssertionError('grader called')):
            r = worker.run(self.directory, self.sha)
        self.assertEqual(r['status'], 'completed_raw_ungraded')
        self.assertEqual([x[0] for x in self.calls], self.p['request_ids'])
        self.assertEqual(len({x[1] for x in self.calls}), 1)
        self.assertEqual(r['completion_calls_started'], 8)
        self.assertTrue(r['raw_outputs_sealed'])
        self.assertFalse(r['grading_performed'])

    def test_technical_abort_keeps_full_denominator_and_seals(self):
        self.failure_at = 0
        r = worker.run(self.directory, self.sha)
        self.assertEqual(len(r['request_states']), 8)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual([x['status'] for x in r['request_states']], ['failed'] + ['not_attempted'] * 7)
        self.assertTrue(r['raw_outputs_sealed'])

    def test_unconfirmed_cleanup_prevents_second_process_and_seal(self):
        self.cleanup_ok = False
        r = worker.run(self.directory, self.sha)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(r['status'], 'failed_cleanup_unconfirmed')
        self.assertFalse(r['raw_outputs_sealed'])
        self.assertFalse((self.directory / 'raw_execution_seal.json').exists())

    def test_changed_input_refuses_before_any_process(self):
        (self.directory / 'inputs/prompt_00.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'inference_input_changed'):
            worker.run(self.directory, self.sha)
        self.assertEqual(self.calls, [])

    def test_second_attempt_refused(self):
        worker.run(self.directory, self.sha)
        with self.assertRaisesRegex(ValueError, 'one_attempt_no_retry'):
            worker.run(self.directory, self.sha)
        self.assertEqual(len(self.calls), 8)

    def test_absent_seal_refuses_before_refs(self):
        with self.deny_reference_reads(), self.assertRaisesRegex(ValueError, 'execution_not_sealed'):
            grader.grade(self.directory, self.grade_public)

    def test_redirected_receipt_rejected_before_reference_read(self):
        self.f['public_receipt'] = str(self.references)
        worker.write(self.directory / 'frozen_protocol.json', self.f)
        with self.deny_reference_reads(), self.assertRaisesRegex(ValueError, 'fixed_worker_receipt_required'):
            grader.verified_seal(self.directory)

    def test_tampered_raw_response_refuses_before_refs(self):
        worker.run(self.directory, self.sha)
        (self.directory / 'completion_00/response.json').write_text('{}')
        with self.deny_reference_reads(), self.assertRaisesRegex(ValueError, 'sealed_artifact_changed'):
            grader.grade(self.directory, self.grade_public)

    def test_incomplete_denominator_refuses_before_refs(self):
        worker.run(self.directory, self.sha)
        seal = worker.read_json(self.directory / 'raw_execution_seal.json')
        seal['request_states'].pop()
        worker.write(self.directory / 'raw_execution_seal.json', seal)
        receipt = worker.read_json(self.public)
        receipt['raw_seal_sha256'] = worker.digest(self.directory / 'raw_execution_seal.json')
        worker.write(self.public, receipt)
        with self.deny_reference_reads(), self.assertRaisesRegex(ValueError, 'sealed_denominator_changed'):
            grader.grade(self.directory, self.grade_public)

    def test_malformed_answer_grading_is_offline_and_all_eight_retained(self):
        worker.run(self.directory, self.sha)
        r = grader.grade(self.directory, self.grade_public)
        self.assertEqual(r['status'], 'authored_gate_failed')
        self.assertEqual(r['assessed_outputs'], 8)
        self.assertEqual(r['authored_correct_count'], 0)
        self.assertTrue(all(not x['schema_valid'] for x in r['request_assessments']))
        self.assertEqual(worker.read_json(self.public)['status'], 'completed_raw_ungraded')

    def test_limit_does_not_change_schedule_but_fails_adequacy(self):
        self.stop = 'limit'
        worker.run(self.directory, self.sha)
        self.assertEqual(len(self.calls), 8)
        summary = {'authored_control_correct': True, 'schema_valid': True,
                   'exposed_support_valid': True, 'semantic_question_correctness': 'authored_control_reference_only'}
        with patch.object(worker.runtime.native, 'assessment', return_value=({}, summary)):
            r = grader.grade(self.directory, self.grade_public)
        self.assertEqual(r['output_limit_count'], 8)
        self.assertEqual(r['authored_correct_count'], 0)
        self.assertTrue(all(x['authored_control_correct_before_limit_rule'] for x in r['request_assessments']))


class FreezeReviewControls(unittest.TestCase):
    def test_exact_privileged_review_map_and_passing_status_required(self):
        good = {'status': 'passed_bounded_pre_freeze_review_root_execution_decision_required',
                'open_blockers': [], 'execution_authorized_by_review': False,
                'reviewed_sha256': {name: 'same' for name in freezer.REVIEW_CODE}}
        with patch.object(worker, 'digest', return_value='same'), patch.object(worker, 'read_json', return_value=good):
            self.assertEqual(freezer.reviewed_recipe(), good['reviewed_sha256'])
        for bad in [good | {'status': 'draft'}, good | {'reviewed_sha256': {}},
                    good | {'reviewed_sha256': good['reviewed_sha256'] | {freezer.REVIEW_CODE[0]: 'stale'}}]:
            with self.subTest(bad=bad['status']), patch.object(worker, 'digest', return_value='same'), \
                 patch.object(worker, 'read_json', return_value=bad), self.assertRaises(ValueError):
                freezer.reviewed_recipe()

    def test_prediction_allowlist_has_no_privileged_reference_files(self):
        forbidden = ('controls', 'references', 'freeze_native', 'grade_native', 'test_', 'attestation')
        self.assertTrue(all(not any(word in name for word in forbidden) for name in worker.WORKER_READ_PINS))
        self.assertIs(worker.runtime.watchdog, worker.runtime.ancestor.watchdog)
        self.assertIs(worker.runtime.cleanup, worker.runtime.ancestor.cleanup)


if __name__ == '__main__':
    unittest.main()
