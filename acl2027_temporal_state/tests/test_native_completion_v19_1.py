"""Versioned-entrypoint controls: exact parent scope plus unchanged batch boundary."""
import ast
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import native_completion_worker_v19_1 as current
import grade_native_completion_v19_1 as grader
import native_completion_runtime_v19 as oldruntime
import test_native_completion_v19 as prior

ROOT = current.ROOT


class AmendedBatchControls(unittest.TestCase):
    def setUp(self):
        for name, module in [('worker', current), ('grader', grader)]:
            p = patch.object(prior, name, module)
            p.start(); self.addCleanup(p.stop)
        self.h = prior.BatchControls(methodName='runTest')
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        self.h.p['schema_version'] = 'native_authored_completion_profile_v19_1'
        self.h.f['schema_version'] = 'native_completion_frozen_v19_1'
        self.h.grade_public = self.h.root / 'results/native_authored_completion_grade_v19_1.json'
        self.h.save_protocol()

    def test_reference_free_full_eight_bad_outputs(self):
        self.h.test_worker_runs_all_eight_with_refs_unavailable_and_bad_text()

    def test_limit_keeps_eight_and_offline_failure(self):
        self.h.test_limit_does_not_change_schedule_but_fails_adequacy()

    def test_cleanup_failure_blocks_next_process(self):
        self.h.test_unconfirmed_cleanup_prevents_second_process_and_seal()


class RecipeControls(unittest.TestCase):
    def test_worker_and_grader_ast_only_version_names_changed(self):
        for name in ['native_completion_worker', 'grade_native_completion']:
            original = (ROOT / f'scripts/{name}_v19.py').read_text()
            amended = (ROOT / f'scripts/{name}_v19_1.py').read_text().replace('_v19_1', '_v19')
            self.assertEqual(ast.dump(ast.parse(original)), ast.dump(ast.parse(amended)))

    def test_profile_preserves_every_parent_execution_field(self):
        old = json.loads((ROOT / 'configs/native_authored_completion_v19.json').read_text())
        new = json.loads((ROOT / 'configs/native_authored_completion_v19_1.json').read_text())
        renamed = {'schema_version', 'attempt_external_path', 'public_receipt_path'}
        self.assertTrue(all(new[k] == value for k, value in old.items() if k not in renamed))
        self.assertEqual(new['prior_completions_count_toward_gate'], 0)
        self.assertEqual(set(new) - set(old), {'transport_parent_worker_receipt_sha256',
                         'transport_parent_raw_seal_sha256', 'transport_diagnosis_sha256',
                         'transport_rebind_change', 'prior_completions_count_toward_gate'})
        self.assertEqual(current.runtime.PARAMETERS, oldruntime.PARAMETERS)
        for name in ['command', 'watchdog', 'cleanup', 'preflight_reason', 'owned_reason']:
            self.assertIs(getattr(current.runtime, name), getattr(oldruntime, name))


if __name__ == '__main__':
    unittest.main()
