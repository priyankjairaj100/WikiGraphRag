"""Reference-free capture journal. No native execution, grading or provider calls."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tarfile

MAX_CELLS = 16
MAX_ARCHIVE = 128 * 2**20
LIMITS = {'protocol.json': 2**20, 'request.json': 2**20, 'prompt.json': 2**20,
          'tokens.json': 2**20, 'response.bin': 16 * 2**20, 'error.bin': 16 * 2**20,
          'response.partial.bin': 16 * 2**20, 'native.log': 64 * 2**20,
          'resources.json': 16 * 2**20, 'guard.json': 4096, 'cleanup.json': 4096,
          'outcome.json': 4096}
BASE = {'protocol.json', 'request.json', 'resources.json', 'guard.json', 'cleanup.json', 'outcome.json'}
OPTIONAL = set(LIMITS) - BASE
META = {'begin.json', 'seal.json', 'handoff.json', 'tool_ack.json', 'durable_ack.json', 'archive.tar.gz'}
ACK_KEYS = {'schema_version', 'source', 'schedule_sha256', 'cell_id', 'cell_index',
            'seal_sha256', 'archive_sha256', 'archive_bytes', 'provider',
            'object_identity_sha256', 'object_version_sha256',
            'upload_tool_result_sha256', 'readback_tool_result_sha256'}


class CheckpointError(ValueError):
    pass


def require(ok, code):
    if not ok:
        raise CheckpointError(code)


def encoded(obj):
    return (json.dumps(obj, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha_file(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(2**20), b''):
            h.update(chunk)
    return h.hexdigest()


def hash_shape(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def safe(path, *, file=False):
    path = Path(path).absolute()
    require('..' not in path.parts, 'path_traversal')
    require(all(not p.is_symlink() for p in (path, *path.parents)), 'symlink_refused')
    require(path.resolve() == path, 'noncanonical_path')
    if file:
        require(path.is_file() and stat.S_ISREG(path.stat().st_mode), 'regular_file_required')
    return path


def put(path, data):
    path = safe(path)
    require(isinstance(data, bytes), 'bytes_required')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as out:
        out.write(data); out.flush(); os.fsync(out.fileno())
    directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def metadata(path, maximum=2**20):
    path = safe(path, file=True)
    require(path.stat().st_size <= maximum, 'metadata_budget')
    return json.loads(path.read_bytes())


def create(root, protocol_bytes, schedule):
    """Freeze a capture-only full schedule; returns its externally pinned digest."""
    root = safe(root)
    require(not root.exists(), 'one_capture_directory_only')
    require(isinstance(protocol_bytes, bytes) and 0 < len(protocol_bytes) <= LIMITS['protocol.json'], 'protocol_budget')
    require(isinstance(schedule, list) and 0 < len(schedule) <= MAX_CELLS, 'schedule_budget')
    ids = []
    for row in schedule:
        require(set(row) == {'cell_id', 'request_sha256'} and
                re.fullmatch('[a-zA-Z0-9_-]{1,64}', row['cell_id']) and hash_shape(row['request_sha256']), 'schedule_shape')
        ids.append(row['cell_id'])
    require(len(set(ids)) == len(ids), 'duplicate_cell_id')
    value = {'schema_version': 'native_capture_schedule_v20', 'protocol_sha256': sha_bytes(protocol_bytes),
             'schedule': schedule, 'maximum_attempts': len(schedule)}
    root.mkdir(parents=True)
    (root / 'cells').mkdir()
    put(root / 'protocol.json', protocol_bytes)
    put(root / 'schedule.json', encoded(value))
    return sha_file(root / 'schedule.json')


class Checkpoint:
    def __init__(self, root, expected_schedule_sha256):
        self.root = safe(root)
        require(hash_shape(expected_schedule_sha256), 'schedule_digest_required')
        self.expected = expected_schedule_sha256
        self._schedule()

    def _schedule(self):
        require(sha_file(safe(self.root / 'schedule.json', file=True)) == self.expected, 'schedule_changed')
        frozen = metadata(self.root / 'schedule.json')
        require(sha_file(safe(self.root / 'protocol.json', file=True)) == frozen['protocol_sha256'], 'protocol_changed')
        return frozen

    def _cell(self, cell_id):
        schedule = self._schedule()['schedule']
        indexes = [i for i, row in enumerate(schedule) if row['cell_id'] == cell_id]
        require(len(indexes) == 1, 'unknown_cell')
        return indexes[0], safe(self.root / 'cells' / ('%04d' % indexes[0]))

    def _folders(self):
        paths = list(safe(self.root / 'cells').iterdir())
        require(all(x.is_dir() and not x.is_symlink() for x in paths), 'cell_directory_required')
        require(len(paths) <= len(self._schedule()['schedule']) and
                {x.name for x in paths} == {'%04d' % i for i in range(len(paths))}, 'attempted_prefix_changed')
        return paths

    def _local(self, index, folder):
        frozen = self._schedule()
        require(folder.is_dir(), 'cell_not_started')
        names = {x.name for x in folder.iterdir()}
        require(names <= set(LIMITS) | META, 'artifact_name_forbidden')
        require(all(x.is_file() and not x.is_symlink() for x in folder.iterdir()), 'artifact_not_regular')
        begin = metadata(folder / 'begin.json')
        require(begin['schedule_sha256'] == self.expected and begin['cell_index'] == index and
                begin['cell_id'] == frozen['schedule'][index]['cell_id'], 'begin_binding_changed')
        previous = None if index == 0 else sha_file(safe(self.root / 'cells' / ('%04d' % (index - 1)) / 'durable_ack.json', file=True))
        require(begin['previous_durable_ack_sha256'] == previous, 'previous_ack_lineage_changed')
        seal = metadata(folder / 'seal.json')
        require(seal['schedule_sha256'] == self.expected and seal['cell_index'] == index and
                seal['cell_id'] == begin['cell_id'] and seal['begin_sha256'] == sha_file(folder / 'begin.json'), 'seal_binding_changed')
        artifacts = seal['artifacts']
        require(set(artifacts) <= set(LIMITS) and BASE <= set(artifacts), 'sealed_artifact_allowlist')
        require(set(artifacts) == names - META, 'raw_artifact_set_changed')
        for name, record in artifacts.items():
            path = safe(folder / name, file=True)
            require(path.stat().st_size == record['bytes'] <= LIMITS[name] and
                    sha_file(path) == record['sha256'], 'sealed_bytes_changed')
        require(artifacts['protocol.json']['sha256'] == frozen['protocol_sha256'] and
                artifacts['request.json']['sha256'] == frozen['schedule'][index]['request_sha256'], 'input_binding_changed')
        return seal

    def begin_cell(self, cell_id, approved_prefix_ack_sha256=()):
        """Consume one capture slot; not authorization to execute a model."""
        require(not (self.root / 'final.json').exists(), 'journal_finalized')
        index, folder = self._cell(cell_id)
        require(len(approved_prefix_ack_sha256) == index and all(hash_shape(x) for x in approved_prefix_ack_sha256),
                'trusted_prefix_ack_digests_required')
        require(not folder.exists(), 'cell_already_attempted_no_retry')
        existing = {x.name for x in self._folders()}
        require(existing == {'%04d' % i for i in range(index)}, 'schedule_order_changed')
        previous = None
        for i in range(index):
            prior = self.root / 'cells' / ('%04d' % i)
            seal = self._local(i, prior)
            require(seal['technical_status'] == 'technical_passed' and seal['guard_passed'] is True,
                    'earlier_technical_or_guard_failure')
            require(seal['cleanup_confirmed'] is True, 'cleanup_unconfirmed')
            require((prior / 'durable_ack.json').is_file(), 'durable_ack_missing')
            self._confirmed(i, prior, approved_prefix_ack_sha256[i])
            previous = sha_file(prior / 'durable_ack.json')
        folder.mkdir()
        put(folder / 'begin.json', encoded({'cell_id': cell_id, 'cell_index': index,
             'schedule_sha256': self.expected, 'previous_durable_ack_sha256': previous,
             'scope': 'capture_slot_only_no_native_execution_authority'}))
        put(folder / 'protocol.json', (self.root / 'protocol.json').read_bytes())
        return folder

    def seal_cell(self, cell_id):
        index, folder = self._cell(cell_id)
        require(not (self.root / 'final.json').exists(), 'journal_finalized')
        require(folder.is_dir() and not (folder / 'seal.json').exists(), 'seal_requires_new_attempted_cell')
        names = {x.name for x in folder.iterdir()}
        require(names <= set(LIMITS) | {'begin.json'} and BASE <= names, 'missing_or_unexpected_artifact')
        require(all(x.is_file() and not x.is_symlink() for x in folder.iterdir()), 'artifact_not_regular')
        outcome = metadata(folder / 'outcome.json', 4096)
        guard = metadata(folder / 'guard.json', 4096)
        cleanup = metadata(folder / 'cleanup.json', 4096)
        require(set(outcome) == {'status', 'process_started', 'completion_started', 'artifact_states'} and
                outcome['status'] in ('technical_passed', 'technical_failed') and
                all(type(outcome[k]) is bool for k in ('process_started', 'completion_started')), 'outcome_shape')
        require(set(outcome['artifact_states']) == OPTIONAL and
                all(state in ('produced', 'not_produced') for state in outcome['artifact_states'].values()) and
                all((name in names) == (state == 'produced') for name, state in outcome['artifact_states'].items()),
                'artifact_production_declaration_mismatch')
        require(set(guard) == {'telemetry_complete', 'guard_passed'} and
                all(type(v) is bool for v in guard.values()), 'guard_shape')
        require(set(cleanup) == {'confirmed'} and type(cleanup['confirmed']) is bool, 'cleanup_shape')
        require(cleanup['confirmed'], 'cleanup_unconfirmed')
        require(not outcome['completion_started'] or outcome['process_started'], 'completion_without_process')
        require(not outcome['process_started'] or 'native.log' in names, 'native_log_missing')
        require(not outcome['completion_started'] or {'prompt.json', 'tokens.json'} <= names, 'native_input_artifacts_missing')
        if outcome['status'] == 'technical_passed':
            require(outcome['completion_started'] and guard['telemetry_complete'] and guard['guard_passed'] and
                    'response.bin' in names and not ({'error.bin', 'response.partial.bin'} & names), 'technical_pass_contradiction')
        else:
            require('error.bin' in names, 'failure_error_bytes_missing')
        records = {}
        for name in sorted(names - {'begin.json'}):
            path = safe(folder / name, file=True)
            require(path.stat().st_size <= LIMITS[name], 'artifact_budget')
            records[name] = {'bytes': path.stat().st_size, 'sha256': sha_file(path)}
        frozen = self._schedule()
        require(records['request.json']['sha256'] == frozen['schedule'][index]['request_sha256'] and
                records['protocol.json']['sha256'] == frozen['protocol_sha256'], 'input_binding_changed')
        seal = {'schema_version': 'native_capture_cell_seal_v20', 'schedule_sha256': self.expected,
                'cell_id': cell_id, 'cell_index': index, 'begin_sha256': sha_file(folder / 'begin.json'),
                'artifacts': records, 'not_produced': sorted(name for name, state in outcome['artifact_states'].items() if state == 'not_produced'),
                'technical_status': outcome['status'], 'guard_passed': guard['telemetry_complete'] and guard['guard_passed'],
                'cleanup_confirmed': True, 'state': 'local_sealed_not_durable'}
        put(folder / 'seal.json', encoded(seal))
        self._local(index, folder)
        return sha_file(folder / 'seal.json')

    def export_cell(self, cell_id, expected_seal_sha256):
        require(not (self.root / 'final.json').exists(), 'journal_finalized')
        index, folder = self._cell(cell_id)
        seal = self._local(index, folder)
        require(hash_shape(expected_seal_sha256) and sha_file(folder / 'seal.json') == expected_seal_sha256,
                'local_seal_digest_changed')
        require(not (folder / 'handoff.json').exists(), 'handoff_already_exists')
        members = {'seal.json': folder / 'seal.json', 'begin.json': folder / 'begin.json'}
        members.update({'artifacts/' + name: folder / name for name in seal['artifacts']})
        require(sum(path.stat().st_size for path in members.values()) < MAX_ARCHIVE - 2**20, 'archive_uncompressed_budget')
        archive = safe(folder / 'archive.tar.gz')
        require(not archive.exists(), 'archive_exists_no_overwrite')
        with tarfile.open(archive, 'x:gz') as tar:
            for name, path in sorted(members.items()):
                info = tarfile.TarInfo(name); info.size = path.stat().st_size; info.mode = 0o600; info.mtime = 0
                with path.open('rb') as stream:
                    tar.addfile(info, stream)
        with archive.open('rb') as stream:
            os.fsync(stream.fileno())
        require(archive.stat().st_size <= MAX_ARCHIVE, 'archive_budget')
        handoff = {'schedule_sha256': self.expected, 'cell_id': cell_id, 'cell_index': index,
                   'seal_sha256': sha_file(folder / 'seal.json'), 'archive_sha256': sha_file(archive),
                   'archive_bytes': archive.stat().st_size,
                   'members': {name: {'sha256': sha_file(path), 'bytes': path.stat().st_size} for name, path in members.items()},
                   'state': 'awaiting_trusted_external_transfer_and_readback'}
        self._archive(archive, handoff)
        self._local(index, folder)
        put(folder / 'handoff.json', encoded(handoff))
        return handoff

    @staticmethod
    def _archive(path, handoff):
        path = safe(path, file=True)
        require(path.stat().st_size == handoff['archive_bytes'] <= MAX_ARCHIVE and
                sha_file(path) == handoff['archive_sha256'], 'archive_identity_changed')
        with tarfile.open(path, 'r:gz') as tar:
            members = tar.getmembers()
            require(all(x.name in ('seal.json', 'begin.json') or
                        (x.name.startswith('artifacts/') and x.name[10:] in LIMITS) for x in members),
                    'archive_path_forbidden')
            require(len(members) == len(handoff['members']) and {x.name for x in members} == set(handoff['members']) and
                    all(x.isfile() and not x.issym() and not x.islnk() for x in members), 'archive_member_set_or_type')
            for item in members:
                expected = handoff['members'][item.name]
                require(item.size == expected['bytes'], 'archive_member_size')
                h = hashlib.sha256()
                with tar.extractfile(item) as stream:
                    for data in iter(lambda: stream.read(2**20), b''):
                        h.update(data)
                require(h.hexdigest() == expected['sha256'], 'archive_member_hash')

    def acknowledge(self, cell_id, acknowledgment_path, approved_ack_sha256, readback_archive):
        """Trusted orchestrator supplies actual tool attestation and separate readback."""
        require(not (self.root / 'final.json').exists(), 'journal_finalized')
        index, folder = self._cell(cell_id)
        self._local(index, folder)
        handoff = metadata(folder / 'handoff.json')
        ackpath = safe(acknowledgment_path, file=True)
        require(hash_shape(approved_ack_sha256) and sha_file(ackpath) == approved_ack_sha256, 'unapproved_acknowledgment')
        ack = metadata(ackpath, 16384)
        self._ack_bindings(ack, handoff, index, folder)
        readback = safe(readback_archive, file=True)
        require(not readback.is_relative_to(self.root) and
                (readback.stat().st_dev, readback.stat().st_ino) !=
                ((folder / 'archive.tar.gz').stat().st_dev, (folder / 'archive.tar.gz').stat().st_ino), 'readback_not_independent_file')
        self._archive(readback, handoff)
        self._archive(folder / 'archive.tar.gz', handoff)
        self._local(index, folder)
        record = {'approved_ack_sha256': approved_ack_sha256,
                  'handoff_sha256': sha_file(folder / 'handoff.json'),
                  'readback_sha256_verified': handoff['archive_sha256'],
                  'state': 'durable_transfer_attested_readback_bytes_verified',
                  'trust_limit': 'External provider operation provenance is trusted to root; retention/future availability is not proven by this component.'}
        put(folder / 'tool_ack.json', ackpath.read_bytes())
        put(folder / 'durable_ack.json', encoded(record))
        return record

    def _ack_bindings(self, ack, handoff, index, folder):
        require(set(ack) == ACK_KEYS and ack['schema_version'] == 'private_archive_tool_ack_v20' and
                ack['source'] == 'trusted_root_orchestrator' and ack['provider'] == 'chatgpt_library', 'tool_ack_shape')
        require(handoff['schedule_sha256'] == self.expected and handoff['cell_index'] == index and
                handoff['cell_id'] == self._schedule()['schedule'][index]['cell_id'] and
                handoff['seal_sha256'] == sha_file(folder / 'seal.json'), 'handoff_binding_changed')
        seal = self._local(index, folder)
        expected_members = {'artifacts/' + key: value for key, value in seal['artifacts'].items()}
        expected_members.update({name: {'bytes': (folder / name).stat().st_size, 'sha256': sha_file(folder / name)}
                                 for name in ('seal.json', 'begin.json')})
        require(handoff['members'] == expected_members, 'handoff_members_changed')
        for key in ('schedule_sha256', 'cell_id', 'cell_index', 'seal_sha256', 'archive_sha256', 'archive_bytes'):
            require(ack[key] == handoff[key], 'tool_ack_binding_changed')
        fields = ('object_identity_sha256', 'object_version_sha256', 'upload_tool_result_sha256', 'readback_tool_result_sha256')
        require(all(hash_shape(ack[k]) for k in fields) and
                ack['upload_tool_result_sha256'] != ack['readback_tool_result_sha256'], 'immutable_object_or_tool_receipt_missing')

    def _confirmed(self, index, folder, approved_ack_sha256):
        self._local(index, folder)
        record = metadata(folder / 'durable_ack.json')
        handoff = metadata(folder / 'handoff.json')
        ack = metadata(folder / 'tool_ack.json', 16384)
        require(hash_shape(approved_ack_sha256) and sha_file(folder / 'tool_ack.json') == approved_ack_sha256 ==
                record['approved_ack_sha256'], 'trusted_ack_digest_changed')
        self._ack_bindings(ack, handoff, index, folder)
        require(record['handoff_sha256'] == sha_file(folder / 'handoff.json') and
                record['readback_sha256_verified'] == handoff['archive_sha256'], 'durable_record_changed')
        self._archive(folder / 'archive.tar.gz', handoff)
        return record

    def finalize(self, stop_reason=None, approved_prefix_ack_sha256=()):
        """Seal full denominator, including unattempted/unsealed failures; never grade."""
        require(not (self.root / 'final.json').exists(), 'journal_finalized')
        require(stop_reason in (None, 'technical_failure', 'capture_failure', 'guard_failure', 'operator_stop'), 'stop_reason_forbidden')
        self._folders()
        frozen = self._schedule(); rows = []; seals = []; acks = []; attempted = 0; durable = 0
        for index, row in enumerate(frozen['schedule']):
            folder = safe(self.root / 'cells' / ('%04d' % index))
            state = {'cell_id': row['cell_id'], 'status': 'not_attempted'}
            if folder.exists():
                attempted += 1
                state['status'] = 'attempted_unsealed'
                if (folder / 'seal.json').exists():
                    seal = self._local(index, folder); state.update(status='local_sealed', technical_status=seal['technical_status'])
                    seals.append({'cell_index': index, 'sha256': sha_file(folder / 'seal.json')})
                    if (folder / 'durable_ack.json').exists():
                        require(index < len(approved_prefix_ack_sha256), 'trusted_prefix_ack_digests_required')
                        self._confirmed(index, folder, approved_prefix_ack_sha256[index]); durable += 1; state['status'] = 'durable_attested'
                        acks.append({'cell_index': index, 'sha256': sha_file(folder / 'durable_ack.json')})
            rows.append(state)
        require(attempted == len(rows) or stop_reason is not None, 'partial_schedule_requires_explicit_stop')
        require(attempted == durable or stop_reason is not None, 'incomplete_capture_requires_explicit_stop')
        require(len(approved_prefix_ack_sha256) == durable, 'trusted_prefix_ack_count_changed')
        final = {'schema_version': 'native_capture_prefix_final_v20', 'schedule_sha256': self.expected,
                 'rows': rows, 'expected_cells': len(rows), 'attempted_cells': attempted,
                 'durable_attested_cells': durable, 'ordered_cell_seals': seals, 'ordered_durable_acks': acks,
                 'stop_reason': stop_reason, 'all_attempted_raw_capture_durable_attested': attempted > 0 and attempted == durable,
                 'grading_performed': False, 'native_execution_authorized': False}
        put(self.root / 'final.json', encoded(final))
        return final
