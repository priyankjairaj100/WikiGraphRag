"""Invented local fixtures only: no native runs, provider calls or real remote save."""
import io
import json
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import native_completion_checkpoint_v20 as cp


class CaptureFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / 'capture'
        self.requests = [cp.encoded({'request': 'invented-%d' % i}) for i in range(3)]
        self.schedule = [{'cell_id': 'cell_%02d' % i, 'request_sha256': cp.sha_bytes(self.requests[i])} for i in range(3)]
        self.schedule_sha = cp.create(self.root, b'{"protocol":"invented only"}\n', self.schedule)
        self.journal = cp.Checkpoint(self.root, self.schedule_sha)

    def make_cell(self, index=0, *, acknowledgments=(), failed=False, guard=True, cleanup=True, started=True, partial=False):
        folder = self.journal.begin_cell(self.schedule[index]['cell_id'], acknowledgments)
        raw = {'request.json': self.requests[index], 'resources.json': b'{"samples":"invented"}\n',
               'guard.json': cp.encoded({'telemetry_complete': guard, 'guard_passed': guard}),
               'cleanup.json': cp.encoded({'confirmed': cleanup})}
        if started:
            raw.update({'native.log': b'invented native log\n', 'prompt.json': b'{"prompt":"invented"}',
                        'tokens.json': b'[10,20,30]'})
        if failed:
            raw['error.bin'] = b'invented opaque transport error\x00'
            if partial:
                raw['response.partial.bin'] = b'partial malformed output {'
        else:
            raw['response.bin'] = b'invented deliberately malformed answer content'
        raw['outcome.json'] = cp.encoded({'status': 'technical_failed' if failed else 'technical_passed',
                             'process_started': started, 'completion_started': started,
                             'artifact_states': {key: 'produced' if key in raw else 'not_produced' for key in cp.OPTIONAL}})
        for name, value in raw.items():
            cp.put(folder / name, value)
        return folder

    def export(self, index=0):
        cell = self.schedule[index]['cell_id']
        seal_sha = self.journal.seal_cell(cell)
        handoff = self.journal.export_cell(cell, seal_sha)
        return handoff

    def invented_ack(self, index=0, handoff=None):
        """Simulate trusted root metadata; intentionally does NOT simulate a remote API."""
        folder = self.root / 'cells' / ('%04d' % index)
        handoff = handoff or cp.metadata(folder / 'handoff.json')
        ack = {key: handoff[key] for key in ('schedule_sha256', 'cell_id', 'cell_index',
                                            'seal_sha256', 'archive_sha256', 'archive_bytes')}
        ack.update(schema_version='private_archive_tool_ack_v20', source='trusted_root_orchestrator',
                   provider='chatgpt_library', object_identity_sha256=cp.sha_bytes(b'fictional object'),
                   object_version_sha256=cp.sha_bytes(('fictional version%d' % index).encode()),
                   upload_tool_result_sha256=cp.sha_bytes(b'fictional upload result'),
                   readback_tool_result_sha256=cp.sha_bytes(b'fictional download result'))
        ackpath = self.base / ('invented_ack_%02d.json' % index)
        cp.put(ackpath, cp.encoded(ack))
        downloaded = self.base / ('simulated_readback_%02d.tar.gz' % index)
        shutil.copyfile(folder / 'archive.tar.gz', downloaded)
        return ackpath, cp.sha_file(ackpath), downloaded

    def confirm(self, index=0):
        args = self.invented_ack(index)
        record = self.journal.acknowledge(self.schedule[index]['cell_id'], *args)
        self.assertIn('attested', record['state'])
        return args[1]


class CaptureControls(CaptureFixture):
    def test_full_predeclared_schedule_advances_only_after_attested_readbacks(self):
        approved = []
        for index in range(3):
            self.make_cell(index, acknowledgments=approved)
            self.export(index)
            approved.append(self.confirm(index))
        result = self.journal.finalize(approved_prefix_ack_sha256=approved)
        self.assertEqual(result['expected_cells'], 3)
        self.assertEqual(result['attempted_cells'], 3)
        self.assertEqual(result['durable_attested_cells'], 3)
        self.assertFalse(result['grading_performed'])
        self.assertFalse(result['native_execution_authorized'])

    def test_local_seal_and_archive_are_not_durable_ack(self):
        self.make_cell(); self.export()
        with self.assertRaisesRegex(cp.CheckpointError, 'durable_ack_missing'):
            self.journal.begin_cell('cell_01', ['0' * 64])
        self.assertFalse((self.root / 'cells/0001').exists())

    def test_missing_external_prefix_digest_cannot_advance(self):
        self.make_cell(); self.export(); self.confirm()
        with self.assertRaisesRegex(cp.CheckpointError, 'trusted_prefix_ack_digests_required'):
            self.journal.begin_cell('cell_01')

    def test_bad_or_cross_cell_ack_cannot_advance(self):
        self.make_cell(); self.export()
        path, digest, readback = self.invented_ack()
        with self.assertRaisesRegex(cp.CheckpointError, 'unapproved_acknowledgment'):
            self.journal.acknowledge('cell_00', path, '0' * 64, readback)
        obj = cp.metadata(path); obj['cell_id'] = 'cell_01'; path.write_bytes(cp.encoded(obj))
        with self.assertRaisesRegex(cp.CheckpointError, 'tool_ack_binding_changed'):
            self.journal.acknowledge('cell_00', path, cp.sha_file(path), readback)

    def test_boolean_without_tool_object_version_evidence_rejected(self):
        self.make_cell(); self.export()
        path, digest, readback = self.invented_ack()
        path.write_bytes(cp.encoded({'durable': True}))
        with self.assertRaisesRegex(cp.CheckpointError, 'tool_ack_shape'):
            self.journal.acknowledge('cell_00', path, cp.sha_file(path), readback)

    def test_readback_mismatch_and_same_inode_rejected(self):
        folder = self.make_cell(); self.export()
        path, digest, readback = self.invented_ack()
        with self.assertRaisesRegex(cp.CheckpointError, 'readback_not_independent_file'):
            self.journal.acknowledge('cell_00', path, digest, folder / 'archive.tar.gz')
        readback.write_bytes(b'not matching archive')
        with self.assertRaisesRegex(cp.CheckpointError, 'archive_identity_changed'):
            self.journal.acknowledge('cell_00', path, digest, readback)

    def test_mutated_raw_after_ack_blocks_next(self):
        folder = self.make_cell(); self.export(); approved = self.confirm()
        (folder / 'response.bin').write_bytes(b'changed bytes')
        with self.assertRaisesRegex(cp.CheckpointError, 'sealed_bytes_changed'):
            self.journal.begin_cell('cell_01', [approved])

    def test_rewritten_raw_and_seal_cannot_use_old_durable_ack(self):
        folder = self.make_cell(); self.export(); approved = self.confirm()
        (folder / 'response.bin').write_bytes(b'changed bytes')
        seal = cp.metadata(folder / 'seal.json')
        seal['artifacts']['response.bin'] = {'bytes': len(b'changed bytes'), 'sha256': cp.sha_bytes(b'changed bytes')}
        (folder / 'seal.json').write_bytes(cp.encoded(seal))
        with self.assertRaisesRegex(cp.CheckpointError, 'handoff_binding_changed'):
            self.journal.begin_cell('cell_01', [approved])

    def test_expected_seal_hash_required_before_export(self):
        self.make_cell(); self.journal.seal_cell('cell_00')
        with self.assertRaisesRegex(cp.CheckpointError, 'local_seal_digest_changed'):
            self.journal.export_cell('cell_00', '0' * 64)

    def test_failure_partial_bytes_sealed_and_full_denominator_preserved(self):
        self.make_cell(failed=True, guard=False, partial=True); self.export(); approved = self.confirm()
        with self.assertRaisesRegex(cp.CheckpointError, 'earlier_technical_or_guard_failure'):
            self.journal.begin_cell('cell_01', [approved])
        final = self.journal.finalize('guard_failure', [approved])
        self.assertEqual([x['status'] for x in final['rows']], ['durable_attested', 'not_attempted', 'not_attempted'])
        seal = cp.metadata(self.root / 'cells/0000/seal.json')
        self.assertIn('response.partial.bin', seal['artifacts'])
        self.assertIn('error.bin', seal['artifacts'])

    def test_cleanup_unconfirmed_and_missing_guard_stop_without_seal(self):
        folder = self.make_cell(failed=True, cleanup=False)
        with self.assertRaisesRegex(cp.CheckpointError, 'cleanup_unconfirmed'):
            self.journal.seal_cell('cell_00')
        self.assertFalse((folder / 'seal.json').exists())
        (folder / 'guard.json').unlink()
        with self.assertRaisesRegex(cp.CheckpointError, 'missing_or_unexpected_artifact'):
            self.journal.seal_cell('cell_00')
        final = self.journal.finalize('capture_failure')
        self.assertFalse(final['all_attempted_raw_capture_durable_attested'])
        self.assertEqual(len(final['rows']), 3)

    def test_explicit_nonproduction_declaration_required(self):
        folder = self.make_cell(failed=True, started=False)
        self.export()
        seal = cp.metadata(folder / 'seal.json')
        self.assertIn('tokens.json', seal['not_produced'])
        self.assertIn('native.log', seal['not_produced'])

    def test_produced_then_omitted_artifact_is_not_relabelled_absent(self):
        folder = self.make_cell(failed=True, partial=True)
        (folder / 'response.partial.bin').unlink()
        with self.assertRaisesRegex(cp.CheckpointError, 'artifact_production_declaration_mismatch'):
            self.journal.seal_cell('cell_00')

    def test_unknown_reference_file_and_symlink_rejected_before_open(self):
        folder = self.make_cell()
        forbidden = folder / 'references.json'; forbidden.write_text('must not be read')
        real_open = Path.open
        def guarded(path, *args, **kwargs):
            if path == forbidden:
                raise AssertionError('reference read')
            return real_open(path, *args, **kwargs)
        with patch.object(Path, 'open', guarded), self.assertRaisesRegex(cp.CheckpointError, 'missing_or_unexpected_artifact'):
            self.journal.seal_cell('cell_00')
        forbidden.unlink(); target = self.base / 'outside'; target.write_bytes(b'not read')
        (folder / 'tokens.json').unlink(); (folder / 'tokens.json').symlink_to(target)
        with self.assertRaisesRegex(cp.CheckpointError, 'artifact_not_regular'):
            self.journal.seal_cell('cell_00')

    def test_traversal_and_archive_link_are_rejected_without_extraction(self):
        with self.assertRaisesRegex(cp.CheckpointError, 'path_traversal'):
            cp.safe(self.base / '..' / 'escape')
        archive = self.base / 'invented_bad.tar.gz'
        with tarfile.open(archive, 'w:gz') as tar:
            info = tarfile.TarInfo('../escape'); info.size = 1
            tar.addfile(info, io.BytesIO(b'x'))
        handoff = {'archive_bytes': archive.stat().st_size, 'archive_sha256': cp.sha_file(archive),
                   'members': {'../escape': {'bytes': 1, 'sha256': cp.sha_bytes(b'x')}}}
        with self.assertRaisesRegex(cp.CheckpointError, 'archive_path_forbidden'):
            cp.Checkpoint._archive(archive, handoff)
        archive.unlink()
        with tarfile.open(archive, 'w:gz') as tar:
            info = tarfile.TarInfo('artifacts/request.json'); info.type = tarfile.SYMTYPE; info.linkname = '../elsewhere'
            tar.addfile(info)
        handoff.update(archive_bytes=archive.stat().st_size, archive_sha256=cp.sha_file(archive),
                       members={'artifacts/request.json': {'bytes': 0, 'sha256': cp.sha_bytes(b'')}})
        with self.assertRaisesRegex(cp.CheckpointError, 'archive_member_set_or_type'):
            cp.Checkpoint._archive(archive, handoff)

    def test_unexpected_directory_gap_and_retry_are_rejected(self):
        self.make_cell()
        with self.assertRaisesRegex(cp.CheckpointError, 'cell_already_attempted_no_retry'):
            self.journal.begin_cell('cell_00')
        (self.root / 'cells/0002').mkdir()
        with self.assertRaisesRegex(cp.CheckpointError, 'attempted_prefix_changed'):
            self.journal.finalize('capture_failure')

    def test_finalized_prefix_rejects_later_state_changes(self):
        self.make_cell(); self.export()
        path, digest, readback = self.invented_ack()
        self.journal.finalize('capture_failure')
        with self.assertRaisesRegex(cp.CheckpointError, 'journal_finalized'):
            self.journal.acknowledge('cell_00', path, digest, readback)
        with self.assertRaisesRegex(cp.CheckpointError, 'journal_finalized'):
            self.journal.export_cell('cell_00', cp.sha_file(self.root / 'cells/0000/seal.json'))
        with self.assertRaisesRegex(cp.CheckpointError, 'journal_finalized'):
            self.journal.begin_cell('cell_01', ['0' * 64])

    def test_full_attempted_but_unacknowledged_prefix_requires_explicit_stop(self):
        approved = []
        for index in range(3):
            self.make_cell(index, acknowledgments=approved)
            self.export(index)
            if index < 2:
                approved.append(self.confirm(index))
        with self.assertRaisesRegex(cp.CheckpointError, 'incomplete_capture_requires_explicit_stop'):
            self.journal.finalize(approved_prefix_ack_sha256=approved)
        final = self.journal.finalize('capture_failure', approved)
        self.assertFalse(final['all_attempted_raw_capture_durable_attested'])


if __name__ == '__main__':
    unittest.main()
