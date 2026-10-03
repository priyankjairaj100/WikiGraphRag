#!/usr/bin/env python3
"""Freeze v0.9 author-defined current-document prefixes before annotation.

Full captured text is retained; this does not construct historical availability.
"""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
def digest(path): return sha256(path.read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--plan',type=Path,default=ROOT/'data/source_stream_v09/capture_selection_plan.json')
    p.add_argument('--capture',type=Path,default=ROOT/'data/source_stream_v09/capture_manifest.json')
    p.add_argument('--review',type=Path,default=ROOT/'data/source_stream_v09/content_review.json')
    p.add_argument('--delivery',type=Path,default=ROOT/'data/source_stream_v09/delivery_manifest.json')
    args=p.parse_args()
    if args.delivery.exists():raise ValueError('Frozen delivery already exists; no overwrite.')
    plan=json.loads(args.plan.read_text());cap=json.loads(args.capture.read_text());review=json.loads(args.review.read_text())
    if cap['status']!='all_selected_sources_attempted' or cap['attempted_sources']!=plan['expected_sources']:
        raise ValueError('Every selected source must have a completed attempt.')
    if cap['selection_plan_sha256']!=digest(args.plan) or review['capture_manifest_sha256']!=digest(args.capture):
        raise ValueError('Parent hash mismatch.')
    rows={s['source_id']:s for s in cap['sources']}
    reviews={s['source_id']:s for s in review['sources']}
    if len(rows)!=len(cap['sources']) or set(rows)!=set(reviews):raise ValueError('Duplicate or missing source.')
    result={'schema_version':'source_delivery_manifest_v0.9',
        'status':'frozen_current_version_controlled_document_stream',
        'frozen_at_utc':datetime.now(timezone.utc).isoformat(),
        'capture_manifest_sha256':digest(args.capture),'capture_selection_plan_sha256':digest(args.plan),
        'content_review_sha256':digest(args.review),'builder_sha256':digest(Path(__file__)),
        'historical_availability_claim':False,'annotation_or_reference_created_before_freeze':False,
        'scope':'Four preselected development histories; all current-version text intact. Author-defined delivery order by displayed publication dates; blocked sources not substituted.',
        'histories':[],'prefixes':[],'model_pilot_history_ids':[]}
    for history in plan['histories']:
        sources=history['sources']
        if len(sources)!=2 or [s['reported_publication_date'] for s in sources]!=sorted(s['reported_publication_date'] for s in sources):
            raise ValueError('Predeclared chronological pair invalid.')
        cumulative=[];batches=[]
        for batch_index,source in enumerate(sources,1):
            cumulative.append(source['source_id']);missing=[];bindings=[]
            for sid in cumulative:
                row=rows[sid]
                if not row['content_usability']['provisional_content_usable']:
                    missing.append(sid);continue
                if digest(Path(row['local_raw_path']))!=row['raw_response_sha256'] or digest(Path(row['local_text_path']))!=row['extracted_text_sha256']:
                    raise ValueError('Frozen source hash mismatch: '+sid)
                if reviews[sid]['text_sha256']!=row['extracted_text_sha256']:raise ValueError('Review text mismatch: '+sid)
                bindings.append({'source_id':sid,'exact_version_sha256':row['exact_version_sha256'],'text_sha256':row['extracted_text_sha256']})
            result['prefixes'].append({'history_id':history['history_id'],
                'prefix_id':history['history_id']+'_p'+str(batch_index),'delivery_batch_index':batch_index,
                'source_ids':list(cumulative),'source_bindings':bindings,'missing_source_ids':missing,
                'status':'blocked_missing_source' if missing else 'ready'})
            batches.append({'delivery_batch_index':batch_index,'source_ids':[source['source_id']],
                'order_basis':history['source_order_basis'],'version_dependencies':[],
                'historical_first_public_availability':None})
        result['histories'].append({'history_id':history['history_id'],'delivery_batches':batches,
            'current_version_caveat':'Current captured documents, not certified historical originals. Site chrome and updates remain intact; review notes bind their observed scope.',
            'event_times_in_text':'Source-only annotation pending; not inferred from delivery date.'})
        if not missing and len(result['model_pilot_history_ids'])<2:
            result['model_pilot_history_ids'].append(history['history_id'])
    result['model_pilot_selection_rule']=plan['model_pilot_selection_rule']
    args.delivery.parent.mkdir(parents=True,exist_ok=True)
    args.delivery.write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps({'ready_prefixes':sum(x['status']=='ready' for x in result['prefixes']),
        'model_pilot_history_ids':result['model_pilot_history_ids'],'delivery_sha256':digest(args.delivery)}))


if __name__=='__main__':main()
