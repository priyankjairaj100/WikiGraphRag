#!/usr/bin/env python3
"""Verify frozen v0.9 capture/delivery metadata without network or model calls.

--require-external additionally verifies current raw/header/text bytes. The default
works on a distributable checkpoint and never claims unavailable external bytes
were reproduced from hashes.
"""
import argparse
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
def digest(p):return sha256(p.read_bytes()).hexdigest()
def require(condition,message):
    if not condition:raise ValueError(message)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--require-external',action='store_true')
    args=parser.parse_args()
    folder=ROOT/'data/source_stream_v09'
    plan_path=folder/'capture_selection_plan.json';cap_path=folder/'capture_manifest.json'
    review_path=folder/'content_review.json';delivery_path=folder/'delivery_manifest.json'
    plan=json.loads(plan_path.read_text());cap=json.loads(cap_path.read_text())
    review=json.loads(review_path.read_text());delivery=json.loads(delivery_path.read_text())
    for label in ('frame','prior_v08_capture_manifest'):
        require(digest(ROOT/plan[label+'_path'])==plan[label+'_sha256'],'Parent changed: '+label)
    frame=json.loads((ROOT/plan['frame_path']).read_text())
    frame_rows={h['history_id']:h for h in frame['histories']}
    require([h['history_id'] for h in plan['histories']]==plan['history_order'],'History order changed')
    require(all(h==frame_rows[h['history_id']] for h in plan['histories']),'Exact selected frame histories changed')
    require(plan['status']=='frozen_before_new_capture','Selection not frozen')
    require(cap['status']=='all_selected_sources_attempted','Capture incomplete')
    require(plan['expected_sources']==cap['expected_source_count']==cap['attempted_sources']==len(cap['sources'])==8,'Expected eight attempts')
    require(len(plan['histories'])==cap['expected_history_count']==4,'Expected four histories')
    require(not cap['raw_v08_restored'] and not plan['raw_v08_restored'],'Fresh capture mislabeled restored')
    require(cap['selection_plan_sha256']==delivery['capture_selection_plan_sha256']==digest(plan_path),'Selection binding changed')
    require(review['capture_manifest_sha256']==delivery['capture_manifest_sha256']==digest(cap_path),'Capture binding changed')
    require(delivery['content_review_sha256']==digest(review_path),'Review binding changed')
    require(cap['wrapper_script_sha256']==digest(ROOT/'scripts/capture_source_stream_v09.py'),'Executed wrapper changed')
    require(cap['reused_extractor_sha256']==digest(ROOT/cap['reused_extractor_path']),'Executed extractor changed')
    require(delivery['builder_sha256']==digest(ROOT/'scripts/freeze_source_stream_v09.py'),'Freeze builder changed')
    clocks=[plan['frozen_at_utc'],cap['started_at'],cap['ended_at'],delivery['frozen_at_utc']]
    require([datetime.fromisoformat(t) for t in clocks]==sorted(datetime.fromisoformat(t) for t in clocks),'Freeze/retrieval clock order invalid')
    rows={s['source_id']:s for s in cap['sources']};reviews={s['source_id']:s for s in review['sources']}
    selected={s['source_id']:s for h in plan['histories'] for s in h['sources']}
    old=json.loads((ROOT/plan['prior_v08_capture_manifest_path']).read_text())
    old_rows={s['source_id']:s for s in old['sources']}
    require(set(rows)==set(selected)==set(reviews) and len(rows)==8,'Missing/duplicate sources')
    external=0
    for sid,row in rows.items():
        require(row['requested_url']==selected[sid]['url'],'Unplanned URL: '+sid)
        require(row['retrieved_at'][:10]=='2026-10-03','Wrong current retrieval date: '+sid)
        require(datetime.fromisoformat(row['capture_started_at'])<=datetime.fromisoformat(row['retrieved_at']),'Invalid transfer clock')
        require(row['historical_first_public_availability'] is None,'Invented historical availability')
        require(row['exact_version_sha256']==row['raw_response_sha256'],'Raw version mismatch')
        require(row['raw_bytes']<=8*1024*1024,'Byte cap exceeded')
        require(row['extracted_text_sha256']==reviews[sid]['text_sha256'],'Review version mismatch')
        comparison=row['v08_hash_comparison'];prior=old_rows[sid]
        require(comparison['previous_raw_response_sha256']==prior['raw_response_sha256'],'Old raw hash mismatch')
        require(comparison['previous_extracted_text_sha256']==prior['extracted_text_sha256'],'Old text hash mismatch')
        require(comparison['raw_hash_matches']==(row['raw_response_sha256']==prior['raw_response_sha256']),'Raw comparison mismatch')
        require(comparison['text_hash_matches']==(row['extracted_text_sha256']==prior['extracted_text_sha256']),'Text comparison mismatch')
        if args.require_external:
            for field,path in [('raw_response_sha256','local_raw_path'),('http_headers_sha256','local_headers_path'),('extracted_text_sha256','local_text_path')]:
                require(row[path] is not None and digest(Path(row[path]))==row[field],'External bytes missing/changed: '+sid+' '+field)
                external+=1
    require(len(delivery['prefixes'])==8,'Expected eight frozen prefixes')
    require(not delivery['historical_availability_claim'] and not delivery['annotation_or_reference_created_before_freeze'],'Delivery scope invalid')
    actual_ready=[]
    for h in plan['histories']:
        ids=[s['source_id'] for s in h['sources']]
        for n in (1,2):
            matches=[p for p in delivery['prefixes'] if p['prefix_id']==h['history_id']+'_p'+str(n)]
            require(len(matches)==1,'Missing/duplicate prefix')
            prefix=matches[0]
            require(prefix['source_ids']==ids[:n],'Incorrect prefix source membership')
            missing=[sid for sid in ids[:n] if not rows[sid]['content_usability']['provisional_content_usable']]
            require(prefix['missing_source_ids']==missing,'Missing-source status mismatch')
            require(prefix['status']==('blocked_missing_source' if missing else 'ready'),'Prefix status mismatch')
            expected=[{'source_id':sid,'exact_version_sha256':rows[sid]['raw_response_sha256'],'text_sha256':rows[sid]['extracted_text_sha256']} for sid in ids[:n] if sid not in missing]
            require(prefix['source_bindings']==expected,'Prefix hash bindings mismatch')
        if not missing:actual_ready.append(h['history_id'])
    require(delivery['model_pilot_history_ids']==actual_ready[:2],'Pilot selection not first two ready histories')
    request_path=folder/'request_manifest.json';request_manifest=json.loads(request_path.read_text())
    require(request_manifest['capture_manifest_sha256']==digest(cap_path),'Request capture parent mismatch')
    require(request_manifest['delivery_manifest_sha256']==digest(delivery_path),'Request delivery parent mismatch')
    require(request_manifest['builder_sha256']==digest(ROOT/'scripts/prepare_source_stream_v09.py'),'Request builder changed')
    require(request_manifest['ready_requests']==len(request_manifest['requests'])==8,'Expected eight requests')
    require(request_manifest['blocked_prefixes']==[],'Unexpected blocked request')
    require(datetime.fromisoformat(request_manifest['created_at_utc'])>=datetime.fromisoformat(delivery['frozen_at_utc']),'Requests precede delivery freeze')
    prefix_rows={p['prefix_id']:p for p in delivery['prefixes']}
    require({r['prefix_id'] for r in request_manifest['requests']}==set(prefix_rows),'Request coverage mismatch')
    requests_checked=0
    for receipt in request_manifest['requests']:
        prefix=prefix_rows[receipt['prefix_id']]
        require(receipt['source_bindings']==prefix['source_bindings'],'Request receipt bindings mismatch')
        if args.require_external:
            # Destination is fixed by the executed v0.9 batch and outside ROOT.
            path=ROOT.parent/'tmp/source_stream_v09/working/requests'/receipt['request_file_name']
            data=path.read_bytes();request=json.loads(data)
            require(digest(path)==receipt['request_sha256'] and len(data)==receipt['request_bytes'],'Request bytes mismatch')
            expected_sources=[]
            for binding in prefix['source_bindings']:
                expected_sources.append({**binding,'text':Path(rows[binding['source_id']]['local_text_path']).read_text()})
            expected_request={'schema_version':'source_annotation_request_v0.8','history_id':prefix['history_id'],
                'prefix_id':prefix['prefix_id'],'delivery_batch_index':prefix['delivery_batch_index'],
                'capture_manifest_sha256':digest(cap_path),'delivery_manifest_sha256':digest(delivery_path),'sources':expected_sources}
            require(request==expected_request,'Source-only request contract mismatch')
            requests_checked+=1
    print(json.dumps({'status':'passed','attempts':8,'histories':4,
        'ready_prefixes':sum(p['status']=='ready' for p in delivery['prefixes']),
        'external_files_hash_checked':external,
        'external_requests_hash_and_contract_checked':requests_checked,
        'external_check_scope':'raw/header/text exact bytes' if args.require_external else 'metadata only; external bytes not available from checkpoint hashes'},indent=2))


if __name__=='__main__':main()
