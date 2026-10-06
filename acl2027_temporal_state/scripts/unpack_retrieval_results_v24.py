#!/usr/bin/env python3
"""Materialize byte-identical result JSON from the small lossless release archives."""
import gzip
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def unpack():
    manifest = json.loads((ROOT / 'data/task_probe_v24/result_archives.json').read_text())
    verified = []
    for row in manifest['files']:
        archive = (ROOT / row['archive']).read_bytes()
        if len(archive) != row['archive_bytes'] or digest(archive) != row['archive_sha256']:
            raise ValueError('compressed result identity mismatch')
        raw = gzip.decompress(archive)
        if len(raw) != row['raw_bytes'] or digest(raw) != row['raw_sha256']:
            raise ValueError('uncompressed result identity mismatch')
        destination = ROOT / row['uncompressed_path']
        if destination.exists():
            if destination.read_bytes() != raw:
                raise ValueError('refusing to replace a different result file')
        else:
            with destination.open('xb') as out:
                out.write(raw)
            if destination.read_bytes() != raw:
                raise IOError('result materialization readback failed')
        verified.append(row['uncompressed_path'])
    return verified


if __name__ == '__main__':
    print(json.dumps({'status': 'passed', 'materialized_or_verified': unpack()}, indent=2))
