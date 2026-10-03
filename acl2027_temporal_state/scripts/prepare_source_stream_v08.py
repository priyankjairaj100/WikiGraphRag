#!/usr/bin/env python3
"""Freeze current-version delivery manifests and exact, source-only prefix requests.

Full source requests stay in an explicit analysis workspace. The project retains
hash receipts, not full external source texts. This is an initial development
batch, not historical-public-knowledge replay or benchmark admission.
"""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
def digest(b): return hashlib.sha256(b).hexdigest()
def emit(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists(): raise ValueError(f'Refusing to overwrite frozen artifact: {path}')
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
def main():
    p=argparse.ArgumentParser()
    p.add_argument('--capture',type=Path,default=ROOT/'data/source_stream_v08/capture_manifest.json')
    p.add_argument('--plan',type=Path,default=ROOT/'data/source_stream_v08/annotation_batch_plan.json')
    p.add_argument('--delivery',type=Path,default=ROOT/'data/source_stream_v08/delivery_manifest.json')
    p.add_argument('--requests',type=Path,required=True)
    p.add_argument('--receipt',type=Path,default=ROOT/'data/source_stream_v08/request_manifest.json')
    args=p.parse_args()
    capture=json.loads(args.capture.read_bytes());plan=json.loads(args.plan.read_bytes())
    assert capture['attempted_sources']==capture['expected_source_count']==39
    assert plan['target_history_count']==len(plan['histories'])==4
    rows={s['source_id']:s for s in capture['sources']}
    assert len(rows)==len(capture['sources'])==39
    cap_hash=digest(args.capture.read_bytes())
    delivery={'schema_version':'source_delivery_manifest_v0.8','status':'frozen_current_version_controlled_document_stream','frozen_at_utc':datetime.now(timezone.utc).isoformat(),'capture_manifest_sha256':cap_hash,'annotation_batch_plan_sha256':digest(args.plan.read_bytes()),'historical_availability_claim':False,'annotation_or_reference_created_before_freeze':False,'scope':'Initial four preselected development histories; all current-version text intact. Ready prefixes only; blocked captures are not silently substituted.','histories':[],'prefixes':[]}
    for h in plan['histories']:
        cumulative=[];batches=[]
        for batch in h['delivery_batches']:
            cumulative+=batch['source_ids']
            missing=[sid for sid in cumulative if not rows[sid]['content_usability']['provisional_content_usable']]
            bindings=[]
            for sid in cumulative:
                row=rows[sid]
                if sid in missing:continue
                txt=Path(row['local_text_path']).read_bytes()
                assert digest(txt)==row['extracted_text_sha256']
                bindings.append({'source_id':sid,'exact_version_sha256':row['exact_version_sha256'],'text_sha256':row['extracted_text_sha256']})
            pr={'history_id':h['history_id'],'prefix_id':h['history_id']+'_p'+str(batch['delivery_batch_index']),'delivery_batch_index':batch['delivery_batch_index'],'source_ids':list(cumulative),'source_bindings':bindings,'missing_source_ids':missing,'status':'blocked_missing_source' if missing else 'ready'}
            delivery['prefixes'].append(pr)
            batches.append({'delivery_batch_index':batch['delivery_batch_index'],'source_ids':batch['source_ids'],'order_basis':h['delivery_order_basis'],'version_dependencies':[],'historical_first_public_availability':None})
        delivery['histories'].append({'history_id':h['history_id'],'delivery_batches':batches,'current_version_caveat':plan.get('special_constraints',{}).get(h['history_id'],'Current versions are not certified historical originals.'),'event_times_in_text':'Source-only annotation pending; not inferred from delivery date.'})
    # Ensure outputs do not exist before writing any frozen files.
    paths=[args.delivery,args.receipt]+[args.requests/(x['prefix_id']+'.json') for x in delivery['prefixes'] if x['status']=='ready']
    if any(p.exists() for p in paths):raise ValueError('Frozen output already exists; choose new explicit paths')
    emit(args.delivery,delivery);delivery_hash=digest(args.delivery.read_bytes());receipts=[]
    for pre in delivery['prefixes']:
        if pre['status']!='ready':continue
        source_inputs=[]
        for sid in pre['source_ids']:
            row=rows[sid];source_inputs.append({'source_id':sid,'exact_version_sha256':row['exact_version_sha256'],'text_sha256':row['extracted_text_sha256'],'text':Path(row['local_text_path']).read_text()})
        req={'schema_version':'source_annotation_request_v0.8','history_id':pre['history_id'],'prefix_id':pre['prefix_id'],'delivery_batch_index':pre['delivery_batch_index'],'capture_manifest_sha256':cap_hash,'delivery_manifest_sha256':delivery_hash,'sources':source_inputs}
        rp=args.requests/(pre['prefix_id']+'.json');emit(rp,req)
        receipts.append({'history_id':pre['history_id'],'prefix_id':pre['prefix_id'],'request_sha256':digest(rp.read_bytes()),'request_bytes':rp.stat().st_size,'source_bindings':pre['source_bindings'],'request_file_name':rp.name,'fulltext_distribution':'analysis_only_full_request_excluded; exact hashes and source receipts retained'})
    receipt={'schema_version':'source_request_manifest_v0.8','capture_manifest_sha256':cap_hash,'delivery_manifest_sha256':delivery_hash,'annotation_batch_plan_sha256':digest(args.plan.read_bytes()),'builder_sha256':digest(Path(__file__).read_bytes()),'ready_requests':len(receipts),'blocked_prefixes':[p for p in delivery['prefixes'] if p['status']!='ready'],'requests':receipts}
    emit(args.receipt,receipt)
    print(json.dumps({'ready_requests':len(receipts),'blocked_prefixes':len(receipt['blocked_prefixes']),'delivery_sha256':delivery_hash}))
if __name__=='__main__':main()
