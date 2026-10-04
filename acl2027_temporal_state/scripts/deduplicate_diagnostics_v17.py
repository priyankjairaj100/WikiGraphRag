#!/usr/bin/env python3
"""Single authorized hardlink dedup of six pinned immutable diagnostic pairs."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import time

import cgroup_guard_v16_2 as guard

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / 'results/project_owned_artifact_identity_inventory_v17.json'
OUT = ROOT / 'results/diagnostic_hardlink_dedup_v17.json'
EXPECTED_INVENTORY = 'b3e1cde577408007ab8e334836896093e0e0c9dfec97bfb679a76d5b8902604d'


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def metadata(path):
    s = Path(path).lstat()
    return {'path': str(path), 'device': s.st_dev, 'inode': s.st_ino,
            'bytes': s.st_size, 'allocated_bytes': s.st_blocks * 512,
            'nlink': s.st_nlink, 'mode': stat.S_IMODE(s.st_mode),
            'uid': s.st_uid, 'gid': s.st_gid, 'mtime_ns': s.st_mtime_ns}


def save(record):
    OUT.write_text(json.dumps(record, indent=2) + '\n')


def main():
    assert not OUT.exists(), 'one_storage_attempt_receipt_already_exists'
    assert sha(INVENTORY) == EXPECTED_INVENTORY, 'inventory_changed'
    pairs = json.loads(INVENTORY.read_text())['candidate_pairs']
    assert len(pairs) == 6 and all(x['exact_duplicate'] and not x['same_inode'] for x in pairs)
    leases, staged, changed, breaks = [], [], [], []
    previous_handler = signal.signal(signal.SIGIO, lambda signum, frame: breaks.append(time.time()))
    record = {'schema_version': 'diagnostic_hardlink_dedup_v17', 'status': 'started',
              'script_sha256': sha(__file__), 'inventory_sha256': EXPECTED_INVENTORY,
              'started_unix_seconds': time.time(), 'rows': [], 'lease_break_requests': breaks,
              'model_processes': 0, 'cache_eviction_requests': 0,
              'runtime_archive_or_unrelated_files_modified': False,
              'pressure_before': guard.read_snapshot(),
              'shm_available_before_bytes': os.statvfs('/dev/shm').f_bavail * os.statvfs('/dev/shm').f_frsize}
    save(record)
    try:
        markers = []
        for attempt in ('typed_reader_attempt01', 'typed_reader_attempt02'):
            p = Path('/dev/shm/wikigraph_v15/external') / attempt / 'attempt_finished.json'
            obj = json.loads(p.read_text())
            assert 'finished_at_utc' in obj and obj['status'] not in ('running', 'preflight', 'started')
            markers.append({'path': str(p), 'sha256': sha(p), 'status': obj['status']})
        record['terminal_producer_markers'] = markers
        active = []
        cmdline_readable = 0
        for proc in Path('/proc').iterdir():
            if not proc.name.isdecimal():
                continue
            try:
                args = (proc / 'cmdline').read_bytes().split(b'\0')
            except (FileNotFoundError, ProcessLookupError):
                continue
            cmdline_readable += 1
            if any(Path(arg.decode(errors='replace')).name in ('run_typed_reader_v15.py', 'run_typed_reader_v15_1.py') for arg in args):
                active.append(int(proc.name))
        record['active_producer_check'] = {'matching_processes': active, 'cmdlines_readable': cmdline_readable}
        assert not active, 'active_project_producer'
        # Kernel read-lease acquisition refuses existing writable opens. Holding
        # all leases excludes new writers during hashing and atomic replacement.
        for pair in pairs:
            for side in ('first', 'second'):
                expected = pair[side]
                path = Path(expected['path'])
                parent = '/dev/shm/wikigraph_v15/external/typed_reader_attempt' + ('01' if side == 'first' else '02')
                assert str(path.parent) == parent and path.name.endswith('.diagnostic.jsonl')
                assert stat.S_ISREG(path.lstat().st_mode) and not path.is_symlink()
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                leases.append(fd)
                fcntl.fcntl(fd, fcntl.F_SETLEASE, fcntl.F_RDLCK)
                assert fcntl.fcntl(fd, fcntl.F_GETLEASE) == fcntl.F_RDLCK
                before = metadata(path)
                assert before['device'] == expected['device'] and before['inode'] == expected['inode']
                assert before['nlink'] == 1 and before['bytes'] == expected['bytes']
                assert sha(path) == expected['sha256'] and not breaks
                assert metadata(path) == before
            row = {'first_before': metadata(pair['first']['path']),
                   'second_before': metadata(pair['second']['path']), 'expected_sha256': pair['first']['sha256']}
            assert all(row['first_before'][k] == row['second_before'][k] for k in ('device', 'mode', 'uid', 'gid', 'bytes'))
            record['rows'].append(row)
        record['kernel_read_leases_acquired'] = len(leases)
        assert len(leases) == 12 and not breaks
        save(record)
        for pair, row in zip(pairs, record['rows']):
            first, second = Path(pair['first']['path']), Path(pair['second']['path'])
            backup = second.with_name(second.name + '.dedup_v17_backup')
            link = second.with_name(second.name + '.dedup_v17_newlink')
            assert not backup.exists() and not link.exists() and not breaks
            os.link(second, backup)
            staged.append((link, backup))
            os.link(first, link)
            os.replace(link, second)
            changed.append((second, backup))
            assert not breaks
            assert sha(first) == sha(second) == row['expected_sha256']
            row.update(first_after=metadata(first), second_after=metadata(second),
                       full_post_hashes_equal=True)
            assert row['first_after']['inode'] == row['second_after']['inode']
            assert row['first_after']['nlink'] == row['second_after']['nlink'] == 2
            assert not breaks and all(fcntl.fcntl(fd, fcntl.F_GETLEASE) == fcntl.F_RDLCK for fd in leases)
            save(record)
        assert len(changed) == 6 and not breaks
        # Backups are newly-created transaction links to redundant old inodes;
        # both original diagnostic pathnames now resolve to verified same bytes.
        for second, backup in changed:
            backup.unlink()
        for directory in {Path(x['first']['path']).parent for x in pairs} | {Path(x['second']['path']).parent for x in pairs}:
            fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        record.update(status='completed', preserved_original_paths=12, deduplicated_pairs=6,
                      unique_allocated_bytes_before=sum(x['first_before']['allocated_bytes'] + x['second_before']['allocated_bytes'] for x in record['rows']),
                      unique_allocated_bytes_after=sum(x['first_after']['allocated_bytes'] for x in record['rows']))
        record['allocated_bytes_saved'] = record['unique_allocated_bytes_before'] - record['unique_allocated_bytes_after']
    except BaseException as error:
        for second, backup in reversed(changed):
            if backup.exists():
                os.replace(backup, second)
        for link, backup in staged:
            if link.exists():
                link.unlink()
            if backup.exists():
                backup.unlink()
        record.update(status='failed', error_type=type(error).__name__, error=str(error), rollback_attempted=True)
        raise
    finally:
        for fd in leases:
            try:
                fcntl.fcntl(fd, fcntl.F_SETLEASE, fcntl.F_UNLCK)
            finally:
                os.close(fd)
        signal.signal(signal.SIGIO, previous_handler)
        record['leases_released'] = len(leases)
        record['pressure_after'] = guard.read_snapshot()
        record['shm_available_after_bytes'] = os.statvfs('/dev/shm').f_bavail * os.statvfs('/dev/shm').f_frsize
        record['finished_unix_seconds'] = time.time()
        record['measurement_limit'] = 'Shared cgroup and filesystem free-space deltas include concurrent activity; inode-based saved allocation is attributable to these six pairs. No stable model capacity is promised.'
        save(record)
    print(json.dumps({'status': record['status'], 'pairs': record.get('deduplicated_pairs', 0),
                      'allocated_bytes_saved': record.get('allocated_bytes_saved', 0),
                      'fresh_pressure_proxy_bytes': record['pressure_after'].get('pressure_proxy_bytes'),
                      'receipt_sha256': sha(OUT)}))


if __name__ == '__main__':
    main()
