"""Verify the initial repository snapshot and unchanged imported checkpoint."""
from hashlib import sha256
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]

def verify():
    manifest = json.loads((ROOT / 'REPOSITORY_MANIFEST.json').read_bytes())
    for row in manifest['files']:
        path = ROOT / row['path']
        data = path.read_bytes()
        assert len(data) == row['bytes'], row['path']
        assert sha256(data).hexdigest() == row['sha256'], row['path']
    checkpoint = ROOT / 'acl2027_temporal_state'
    frozen = json.loads((checkpoint / 'CHECKPOINT_MANIFEST.json').read_bytes())
    for row in frozen['files']:
        data = (checkpoint / row['path']).read_bytes()
        assert len(data) == row['bytes'] and sha256(data).hexdigest() == row['sha256'], row['path']
    count = 0
    with zipfile.ZipFile(checkpoint / 'reference/original_submission.zip') as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            prefix = 'wikigraphrag-code-and-data/'
            assert info.filename.startswith(prefix)
            path = ROOT / 'original_submission/code-and-data' / info.filename[len(prefix):]
            assert path.read_bytes() == archive.read(info), info.filename
            count += 1
    return {'status': 'passed', 'repository_files_verified': len(manifest['files']),
            'checkpoint_entries_verified': len(frozen['files']),
            'original_archive_members_verified': count, 'model_calls': 0, 'network_calls': 0}

if __name__ == '__main__':
    print(json.dumps(verify(), sort_keys=True))
