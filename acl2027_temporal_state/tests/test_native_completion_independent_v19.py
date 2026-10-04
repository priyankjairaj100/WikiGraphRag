"""Independent authored checks of the grader's pre-reference read boundary."""
from pathlib import Path
import unittest
from unittest.mock import patch

import test_native_completion_v19 as owner


class IndependentSealBoundaryControls(unittest.TestCase):
    def setUp(self):
        self.fixture = owner.BatchControls(methodName='runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def reseal(self, mutate):
        h = self.fixture
        seal = owner.worker.read_json(h.directory / 'raw_execution_seal.json')
        receipt = owner.worker.read_json(h.public)
        mutate(seal, receipt)
        owner.worker.write(h.directory / 'raw_execution_seal.json', seal)
        receipt['raw_seal_sha256'] = owner.worker.digest(h.directory / 'raw_execution_seal.json')
        owner.worker.write(h.public, receipt)

    def test_valid_complete_seal_can_be_verified_with_all_references_closed(self):
        h = self.fixture
        owner.worker.run(h.directory, h.sha)
        with h.deny_reference_reads():
            _, receipt, seal = owner.grader.verified_seal(h.directory)
        self.assertEqual(receipt['status'], 'completed_raw_ungraded')
        self.assertEqual(len(seal['request_states']), 8)
        self.assertFalse(seal['grading_performed'])

    def test_entire_artifact_name_set_checked_before_any_raw_file_read(self):
        h = self.fixture
        owner.worker.run(h.directory, h.sha)
        self.reseal(lambda seal, receipt: seal['artifact_sha256'].update({
            'inputs/references.json': '0' * 64,
            'completion_00/references.json': '1' * 64,
        }))
        real_digest = owner.grader.digest

        def forbid_raw_digest(path):
            relative = Path(path).relative_to(h.directory)
            if relative.parts[0] == 'inputs' or relative.parts[0].startswith('completion_'):
                raise AssertionError('Raw file opened before complete filename allowlist check')
            return real_digest(path)

        with h.deny_reference_reads(), patch.object(owner.grader, 'digest', side_effect=forbid_raw_digest):
            with self.assertRaisesRegex(ValueError, 'sealed_artifact_name_forbidden'):
                owner.grader.verified_seal(h.directory)

    def test_reference_alias_in_public_receipt_is_rejected_before_open(self):
        h = self.fixture
        h.public.unlink()
        h.public.symlink_to(h.references)
        real_open = Path.open

        def forbid_alias(path, *args, **kwargs):
            if Path(path) in {h.public, h.references}:
                raise AssertionError('Reference-bearing receipt alias was opened')
            return real_open(path, *args, **kwargs)

        with patch.object(Path, 'open', forbid_alias):
            with self.assertRaisesRegex(ValueError, 'fixed_worker_receipt_required'):
                owner.grader.verified_seal(h.directory)

    def test_unconfirmed_cleanup_prevents_reference_access_even_with_matching_seal(self):
        h = self.fixture
        owner.worker.run(h.directory, h.sha)

        def change_cleanup(seal, receipt):
            seal['all_started_processes_cleanup_confirmed'] = False
            receipt['all_started_processes_cleanup_confirmed'] = False

        self.reseal(change_cleanup)
        with h.deny_reference_reads():
            with self.assertRaisesRegex(ValueError, 'seal_not_terminal'):
                owner.grader.grade(h.directory, h.grade_public)


if __name__ == '__main__':
    unittest.main()
