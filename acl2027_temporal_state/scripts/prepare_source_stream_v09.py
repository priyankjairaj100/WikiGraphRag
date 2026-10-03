#!/usr/bin/env python3
"""Build full source-only requests from already frozen v0.9 delivery.

The request schema is unchanged source_annotation_request_v0.8. No annotations,
questions, model items, gold labels, or prior outputs are read or added.
"""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
def digest(data):return sha256(data).hexdigest()
def encoded(value):return (json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode('utf-8')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--capture',type=Path,default=ROOT/'data/source_stream_v09/capture_manifest.json')
    p.add_argument('--delivery',type=Path,default=ROOT/'data/source_stream_v09/delivery_manifest.json')
    p.add_argument('--requests',type=Path,required=True)
    p.add_argument('--receipt',type=Path,default=ROOT/'data/source_stream_v09/request_manifest.json')
    args=p.parse_args()
    dest=args.requests.resolve()
    if dest==ROOT or ROOT in dest.parents:
        raise ValueError('Full external text requests must remain outside the distributable project.')
    cap_bytes=args.capture.read_bytes();delivery_bytes=args.delivery.read_bytes()
    cap=json.loads(cap_bytes);delivery=json.loads(delivery_bytes)
    cap_hash=digest(cap_bytes);delivery_hash=digest(delivery_bytes)
    if delivery['capture_manifest_sha256']!=cap_hash:
        raise ValueError('Delivery is not bound to exact capture bytes.')
    if delivery['status']!='frozen_current_version_controlled_document_stream':
        raise ValueError('Delivery is not frozen.')
    if len(delivery['prefixes'])!=8 or any(p['status']!='ready' for p in delivery['prefixes']):
        raise ValueError('This v0.9 batch requires exactly eight ready prefixes.')
    rows={s['source_id']:s for s in cap['sources']}
    if len(rows)!=len(cap['sources']) or len(rows)!=8:raise ValueError('Expected eight unique source captures.')
    outputs=[];receipts=[]
    for prefix in delivery['prefixes']:
        sources=[]
        expected=[]
        for sid in prefix['source_ids']:
            row=rows[sid];text=Path(row['local_text_path']).read_bytes()
            if digest(text)!=row['extracted_text_sha256']:
                raise ValueError('Source text hash mismatch: '+sid)
            binding={'source_id':sid,'exact_version_sha256':row['exact_version_sha256'],'text_sha256':row['extracted_text_sha256']}
            expected.append(binding)
            sources.append({**binding,'text':text.decode('utf-8')})
        if prefix['source_bindings']!=expected or prefix['missing_source_ids']:
            raise ValueError('Prefix bindings mismatch: '+prefix['prefix_id'])
        req={'schema_version':'source_annotation_request_v0.8','history_id':prefix['history_id'],
            'prefix_id':prefix['prefix_id'],'delivery_batch_index':prefix['delivery_batch_index'],
            'capture_manifest_sha256':cap_hash,'delivery_manifest_sha256':delivery_hash,'sources':sources}
        name=prefix['prefix_id']+'.json'
        if Path(name).name!=name:raise ValueError('Unsafe prefix filename')
        data=encoded(req);path=dest/name;outputs.append((path,data))
        receipts.append({'history_id':prefix['history_id'],'prefix_id':prefix['prefix_id'],
            'request_sha256':digest(data),'request_bytes':len(data),'source_bindings':expected,
            'request_file_name':name,'fulltext_distribution':'analysis_only_full_request_excluded; exact hashes and source receipts retained'})
    manifest={'schema_version':'source_request_manifest_v0.9',
        'created_at_utc':datetime.now(timezone.utc).isoformat(),
        'capture_manifest_sha256':cap_hash,'delivery_manifest_sha256':delivery_hash,
        'capture_selection_plan_sha256':delivery['capture_selection_plan_sha256'],
        'builder_sha256':digest(Path(__file__).read_bytes()),'ready_requests':len(receipts),
        'blocked_prefixes':[],'request_schema_version':'source_annotation_request_v0.8','requests':receipts}
    outputs.append((args.receipt,encoded(manifest)))
    if len({p.resolve() for p,_ in outputs})!=len(outputs) or any(p.exists() for p,_ in outputs):
        raise ValueError('Frozen output exists or collides; no output written.')
    for path,data in outputs:
        path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('xb') as handle:handle.write(data)
    print(json.dumps({'ready_requests':len(receipts),'requests':str(dest),
        'request_manifest_sha256':digest(args.receipt.read_bytes())}))


if __name__=='__main__':main()
