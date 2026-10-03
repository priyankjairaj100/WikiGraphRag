"""Check every v0.9 packaged file; only four checkpoint bookkeeping files may change."""
from pathlib import Path
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = {'PROJECT_STATE.json', 'TASKS.json', 'README.md', 'paper/README.txt'}


def main():
    baseline = ROOT / 'data/provenance/parent_manifest_v09.json'
    manifest = json.loads(baseline.read_bytes())
    protected, changed = [], []
    for entry in manifest['files']:
        path = ROOT / entry['path']
        assert path.is_file(), entry['path']
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != entry['sha256']:
            assert entry['path'] in ALLOWED, entry['path']
            changed.append({'path': entry['path'], 'old_sha256': entry['sha256'], 'new_sha256': actual})
        else:
            protected.append(entry['path'])
    out = {'schema_version': 'input_preservation_v0.10', 'status': 'passed',
           'parent_manifest_sha256': hashlib.sha256(baseline.read_bytes()).hexdigest(),
           'parent_files_checked': len(manifest['files']), 'byte_identical_files': len(protected),
           'changed_bookkeeping_files': changed, 'missing_files': 0,
           'previous_code_data_results_and_manuscript_sources_unchanged': True}
    (ROOT / 'results/input_preservation_v10.json').write_text(json.dumps(out, indent=2) + '\n')
    print(json.dumps(out))


if __name__ == '__main__':
    main()
