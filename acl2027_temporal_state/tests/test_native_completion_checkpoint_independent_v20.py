"""Independent invented capture controls: no provider, native or remote calls.

The acknowledged archive in these fixtures is merely a local copy.  Successful
byte verification therefore demonstrates the trusted-attestation boundary, not
an actual upload, download, retention test or native execution authorization.
"""
import os
from pathlib import Path
import sys
import tarfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_completion_checkpoint_v20 import CaptureFixture, cp


class IndependentCaptureControls(CaptureFixture):
    def test_local_copy_acceptance_is_attestation_not_remote_proof(self):
        folder = self.make_cell()
        self.export()
        ack, approved, copied_readback = self.invented_ack()
        linked_readback = self.base / 'same_inode_outside_journal.tar.gz'
        os.link(folder / 'archive.tar.gz', linked_readback)
        with self.assertRaisesRegex(cp.CheckpointError, 'readback_not_independent_file'):
            self.journal.acknowledge('cell_00', ack, approved, linked_readback)
        self.assertFalse((folder / 'durable_ack.json').exists())
        record = self.journal.acknowledge('cell_00', ack, approved, copied_readback)
        self.assertEqual(record['state'], 'durable_transfer_attested_readback_bytes_verified')
        self.assertIn('provenance is trusted to root', record['trust_limit'])
        self.assertIn('retention/future availability is not proven', record['trust_limit'])
        final = self.journal.finalize('operator_stop', [approved])
        self.assertEqual(final['durable_attested_cells'], 1)
        self.assertFalse(final['native_execution_authorized'])

    def test_consistent_local_ack_rewrite_cannot_replace_external_pin(self):
        folder = self.make_cell()
        self.export()
        approved = self.confirm()
        ack = cp.metadata(folder / 'tool_ack.json')
        ack['object_version_sha256'] = cp.sha_bytes(b'locally substituted fictional version')
        (folder / 'tool_ack.json').write_bytes(cp.encoded(ack))
        record = cp.metadata(folder / 'durable_ack.json')
        record['approved_ack_sha256'] = cp.sha_file(folder / 'tool_ack.json')
        (folder / 'durable_ack.json').write_bytes(cp.encoded(record))
        with self.assertRaisesRegex(cp.CheckpointError, 'trusted_ack_digest_changed'):
            self.journal.begin_cell('cell_01', [approved])
        self.assertFalse((self.root / 'cells/0001').exists())
        with self.assertRaisesRegex(cp.CheckpointError, 'trusted_ack_digest_changed'):
            self.journal.finalize('capture_failure', [approved])
        self.assertFalse((self.root / 'final.json').exists())

    def test_failed_partial_output_captured_exactly_but_never_advances(self):
        folder = self.make_cell(failed=True, partial=True)
        partial = b'opaque malformed partial\x00\xff{"answer":'
        (folder / 'response.partial.bin').write_bytes(partial)
        self.export()
        approved = self.confirm()
        with tarfile.open(folder / 'archive.tar.gz', 'r:gz') as archive:
            self.assertEqual(archive.extractfile('artifacts/response.partial.bin').read(), partial)
            self.assertEqual(archive.extractfile('artifacts/error.bin').read(), (folder / 'error.bin').read_bytes())
        with self.assertRaisesRegex(cp.CheckpointError, 'earlier_technical_or_guard_failure'):
            self.journal.begin_cell('cell_01', [approved])
        with self.assertRaisesRegex(cp.CheckpointError, 'cell_already_attempted_no_retry'):
            self.journal.begin_cell('cell_00')
        final = self.journal.finalize('technical_failure', [approved])
        self.assertEqual([row['status'] for row in final['rows']],
                         ['durable_attested', 'not_attempted', 'not_attempted'])
        self.assertEqual(final['rows'][0]['technical_status'], 'technical_failed')
        self.assertFalse(final['grading_performed'])

    def test_unconfirmed_cleanup_retains_unsealed_failure_and_final_denominator(self):
        folder = self.make_cell(failed=True, cleanup=False, partial=True)
        with self.assertRaisesRegex(cp.CheckpointError, 'cleanup_unconfirmed'):
            self.journal.seal_cell('cell_00')
        self.assertFalse((folder / 'seal.json').exists())
        self.assertTrue((folder / 'error.bin').is_file())
        self.assertTrue((folder / 'response.partial.bin').is_file())
        final = self.journal.finalize('capture_failure')
        self.assertEqual([row['status'] for row in final['rows']],
                         ['attempted_unsealed', 'not_attempted', 'not_attempted'])
        self.assertEqual((final['expected_cells'], final['attempted_cells'], final['durable_attested_cells']), (3, 1, 0))
        self.assertFalse(final['all_attempted_raw_capture_durable_attested'])
        with self.assertRaisesRegex(cp.CheckpointError, 'journal_finalized'):
            self.journal.begin_cell('cell_01', ['0' * 64])


if __name__ == '__main__':
    unittest.main()
