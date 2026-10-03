"""Check v0.10 bytes; only four current-state bookkeeping files may change."""
from hashlib import sha256
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT / 'data/provenance/parent_manifest_v10.json'
ALLOWED = {'PROJECT_STATE.json', 'TASKS.json', 'README.md', 'paper/README.txt'}

def digest(p):
    return sha256(p.read_bytes()).hexdigest()

def main():
    parent = json.loads(PARENT.read_bytes())
    assert digest(PARENT) == '641d1303b720075d66baa13790b527f6a8848b255abb8ce1de0dfd10fdd0f2d7'
    changed, same = [], 0
    for row in parent['files']:
        path = ROOT / row['path']
        assert path.is_file(), f'Missing parent file: {path}'
        new = digest(path)
        if new == row['sha256']:
            assert path.stat().st_size == row['bytes']
            same += 1
        else:
            assert row['path'] in ALLOWED, f'Unexpected parent modification: {path}'
            changed.append({'path':row['path'], 'old_sha256':row['sha256'], 'new_sha256':new})
    out = {'schema_version':'input_preservation_v0.11','status':'passed',
           'parent_manifest_sha256':digest(PARENT),'parent_files_checked':len(parent['files']),
           'byte_identical_files':same,'changed_bookkeeping_files':changed,'missing_files':0,
           'previous_code_data_results_and_manuscript_sources_unchanged':True}
    (ROOT / 'results/input_preservation_v11.json').write_text(json.dumps(out,indent=2)+'\n')
    print(json.dumps({'status':'passed','checked':len(parent['files']),'identical':same,'bookkeeping_changes':len(changed)}))

if __name__ == '__main__':
    main()
