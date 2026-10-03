#!/usr/bin/env python3
"""Publish compact annotation metadata and integrity receipts, not full source text.

Exact source requests and raw quote-bearing annotations are analysis-only. These
projections retain semantic cores and evidence offsets/hashes; they cannot replace
full-text revalidation or be presented as fully regenerated model outputs.
"""
import argparse,hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def digest(b):return hashlib.sha256(b).hexdigest()
def canonical(x):return (json.dumps(x,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode()
def write(p,d):p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(canonical(d))
def compact(value):
    if isinstance(value,list):return [compact(v) for v in value]
    if not isinstance(value,dict):return value
    if set(value)=={'source_id','start','end','quote'}:
        return {'source_id':value['source_id'],'start':value['start'],'end':value['end'],'quote_sha256':digest(value['quote'].encode()),'quote_characters':len(value['quote'])}
    result={}
    for k,v in value.items():
        if k in {'notes','description','rationale','interpretation','authority_basis','target_description'}:
            result[k+'_sha256']=digest(v.encode());continue
        result[k]=compact(v)
    return result

def main():
    p=argparse.ArgumentParser();p.add_argument('--working-root',type=Path,required=True)
    p.add_argument('--review',type=Path,default=ROOT/'results/source_semantic_review_v08.json');a=p.parse_args()
    review=json.loads(a.review.read_bytes());entries=[]
    for row in review['artifacts']:
        kind='annotations' if row['kind']=='annotation' else 'references';pid=row['prefix_id']
        raw=a.working_root/kind/(pid+'.raw.json')
        assert digest(raw.read_bytes())==row['raw_output_sha256']
        final=Path(row['final_selected_path'])
        if not final.is_absolute():final=a.working_root/final
        assert digest(final.read_bytes())==row['final_selected_sha256']
        req=a.working_root/'requests'/(pid+'.json');assert digest(req.read_bytes())==row['request_sha256']
        for stage,path in [('initial',raw),('adjudicated',final)]:
            obj=json.loads(path.read_bytes())
            proj={'schema_version':'source_annotation_projection_v0.8','kind':row['kind'],'stage':stage,'prefix_id':pid,'source_request_sha256':digest(req.read_bytes()),'full_response_sha256':digest(path.read_bytes()),'representation':'Compact semantic core and span hashes; exact source text/quotes and prose notes omitted. Not original annotation schema or executable decoder candidates.','content':compact(obj)}
            dest=ROOT/'data/source_stream_v08/distributed'/kind/(pid+'.'+stage+'.json');write(dest,proj)
            entries.append({'path':str(dest.relative_to(ROOT)),'sha256':digest(dest.read_bytes()),'bytes':dest.stat().st_size,'kind':row['kind'],'stage':stage,'prefix_id':pid,'raw_output_sha256':digest(raw.read_bytes()),'final_selected_sha256':digest(final.read_bytes())})
    assert len(entries)==24
    artifacts=['data/source_stream_v08/capture_manifest.json','data/source_stream_v08/annotation_batch_plan.json','data/source_stream_v08/delivery_manifest.json','data/source_stream_v08/request_manifest.json','data/source_stream_v08/generation_manifest.json','data/source_stream_v08/reference_question_plan.json','data/source_stream_v08/review_protocol.json','configs/source_annotation_v08_schema.json','configs/source_annotation_v08_prompt.txt','configs/source_reference_v08_schema.json','configs/source_reference_v08_prompt.txt','results/source_semantic_review_v08.json']
    manifest={'schema_version':'source_stream_distribution_v0.8','files':entries,'bindings':[{'path':x,'sha256':digest((ROOT/x).read_bytes())} for x in artifacts],'limitations':['Full HTTP bodies, source text, full request JSON and raw quote-bearing model responses are analysis-only and excluded from this checkpoint.','Default replay checks distributed metadata integrity, not source quote occurrence or semantic correctness.','A fresh public download can differ. Exact quote revalidation requires the matching original extracted text.','Initial and reviewed projections are distinct; they are model-assisted development artifacts, not human gold or a model-accuracy result.']}
    write(ROOT/'data/source_stream_v08/distribution_manifest.json',manifest)
    print(json.dumps({'distributed_projections':len(entries),'initial_raw_responses':12,'source_text_included':False}))
if __name__=='__main__':main()
