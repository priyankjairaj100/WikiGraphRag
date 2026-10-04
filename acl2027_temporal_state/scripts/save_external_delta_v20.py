"""Save and verify the v20 private delta after all included work is terminal.

Exact environment paths are intentional recovery provenance. This script saves
no repository checkout or model weights and performs no upload.
"""
import datetime
import hashlib
import io
import json
import tarfile
from pathlib import Path

W = Path('/workspace/scratch/bdef663e3dfc')
(W/'v20_checkpoint').mkdir(exist_ok=True)
PROJECT = Path('/dev/shm/wikigraph_v15')
OUT = W/'WikiGraphRag-v20-private-recovery.tar.gz'

def sha(p):
    h = hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024), b''): h.update(b)
    return h.hexdigest()

old = {}
bases = []
for version in (16, 17, 18, 19):
    p = W/f'WikiGraphRag-v{version}-private-recovery.tar.gz'
    with tarfile.open(p) as tf:
        m = json.load(tf.extractfile(f'PRIVATE_RECOVERY_MANIFEST_v{version}.json'))
    for e in m['files']:
        name = e['path']
        if version == 16 and name.startswith('external/'):
            old['project_external/'+name[len('external/'):]] = e
        elif version in (17, 18, 19) and name.startswith('project_external/'):
            old[name] = e
    bases.append({'filename':p.name,'sha256':sha(p)})

files = []
unchanged = 0
for prefix, base in [('project_external',PROJECT/'external'),('native_v20',W/'wikigraph_v20_external')]:
    for p in sorted(base.rglob('*')):
        if p.is_symlink() or not p.is_file() or '__pycache__' in p.parts or p.suffix in {'.pyc','.gguf'}:
            continue
        size = p.stat().st_size
        assert size < 150*1024*1024, (p,size)
        name = prefix+'/'+p.relative_to(base).as_posix()
        h = sha(p)
        if old.get(name,{}).get('sha256') == h:
            unchanged += 1
            continue
        files.append({'path':name,'bytes':size,'sha256':h,'local_path':str(p)})

for log in sorted((W/'v20_checkpoint').glob('unit-suite*.log')):
    files.append({'path':'verification/'+log.name,'bytes':log.stat().st_size,'sha256':sha(log),'local_path':str(log)})
m = {'schema_version':'private_external_delta_v20','created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
     'required_archives_in_order':bases,
     'restore_roots':{'project_external':str(PROJECT/'external'),'native_v20':str(W/'wikigraph_v20_external'),
                      'verification':str(W/'v20_checkpoint')},
     'scope':'New or changed exact external source-bearing v20 artifacts. Apply after v16 base and v17/v18/v19 deltas; no Git checkout or model weights. Surviving earlier failures remain in the chain; missing v19 native originals are not restored.',
     'availability_gap':{'incident':'workspace_artifact_loss_v19',
         'original_native_v19_and_v19_1_raw_artifacts':'unavailable_after_successful_offline_grading',
         'included_native_raw_artifact_count':sum(e['path'].startswith('native_v20/') for e in files),
         'cause':'unknown',
         'limitation':'This archive preserves new source reconciliation artifacts. It does not restore missing native v19/v19.1 raw execution evidence.'},
     'baseline_unchanged_external_files_omitted':unchanged,
     'files':[{k:v for k,v in e.items() if k!='local_path'} for e in files]}
raw = (json.dumps(m,indent=2)+'\n').encode()
assert not OUT.exists()
with tarfile.open(OUT,'w:gz',compresslevel=6) as tf:
    info=tarfile.TarInfo('PRIVATE_RECOVERY_MANIFEST_v20.json');info.size=len(raw)
    tf.addfile(info,io.BytesIO(raw))
    for e in files:
        p=Path(e['local_path']);assert sha(p)==e['sha256'];tf.add(p,arcname=e['path'],recursive=False)
with tarfile.open(OUT) as tf:
    assert json.load(tf.extractfile('PRIVATE_RECOVERY_MANIFEST_v20.json'))==m
    for e in files:
        h=hashlib.sha256();n=0
        member=tf.extractfile(e['path'])
        for b in iter(lambda:member.read(1024*1024),b''):
            h.update(b);n+=len(b)
        assert h.hexdigest()==e['sha256'] and n==e['bytes'],e['path']
receipt={'filename':OUT.name,'bytes':OUT.stat().st_size,'sha256':sha(OUT),
         'files':len(files),'logical_bytes':sum(e['bytes'] for e in files),
         'baseline_unchanged_files':unchanged,'all_members_hash_verified':True,
         'required_archives_in_order':bases}
(W/'v20_checkpoint/private_archive_receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
print(json.dumps(receipt))
