#!/usr/bin/env python3
"""Validate source-only reference artifacts; does not certify answer correctness."""
import argparse,json
from pathlib import Path
from validate_source_annotation_v08 import digest,strict_json,require,schema_check,_validate_request,_validate_manifest_bindings
ROOT=Path(__file__).resolve().parents[1]
def validate(request_raw,reference_raw,question_plan_raw,capture_raw,delivery_raw):
    r=strict_json(request_raw);d=strict_json(reference_raw);p=strict_json(question_plan_raw)
    _validate_request(r);_validate_manifest_bindings(r,capture_raw,delivery_raw)
    schema_check(d,strict_json((ROOT/'configs/source_reference_v08_schema.json').read_bytes()))
    require(d['request_sha256']==digest(request_raw),'reference request mismatch')
    for k in ['history_id','prefix_id']:require(d[k]==r[k],f'{k} mismatch')
    require(d['eligible_source_ids']==[s['source_id'] for s in r['sources']],'reference source scope mismatch')
    expected=p['questions_by_history'][r['history_id']]
    require(len(d['questions'])==len(expected)==2,'requires exactly two fixed questions')
    require([{'question_id':q['question_id'],'question':q['question']} for q in d['questions']]==expected,'question selection/text changed')
    sources={s['source_id']:s['text'] for s in r['sources']};n=0
    for q in d['questions']:
        require(q['status'] not in {'supported','conflict'} or bool(q['evidence']),'supported/conflict answer lacks evidence')
        for e in q['evidence']:
            require(e['source_id'] in sources,'future/ineligible evidence')
            text=sources[e['source_id']]
            require(0<=e['start']<e['end']<=len(text),'invalid evidence offsets')
            require(text[e['start']:e['end']]==e['quote'],'evidence mismatch');n+=1
    return {'schema_version':'source_reference_validation_v0.8','status':'structurally_valid_not_semantically_adjudicated','request_sha256':digest(request_raw),'reference_sha256':digest(reference_raw),'question_plan_sha256':digest(question_plan_raw),'history_id':r['history_id'],'prefix_id':r['prefix_id'],'questions':2,'evidence_spans':n,'status_counts':{k:sum(q['status']==k for q in d['questions']) for k in ['supported','conflict','not_established']},'human_gold':False,'model_accuracy_evaluated':False}
def main():
    p=argparse.ArgumentParser();p.add_argument('--request',type=Path,required=True);p.add_argument('--reference',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--question-plan',type=Path,default=ROOT/'data/source_stream_v08/reference_question_plan.json');p.add_argument('--capture-manifest',type=Path,default=ROOT/'data/source_stream_v08/capture_manifest.json');p.add_argument('--delivery-manifest',type=Path,default=ROOT/'data/source_stream_v08/delivery_manifest.json');a=p.parse_args()
    out=validate(a.request.read_bytes(),a.reference.read_bytes(),a.question_plan.read_bytes(),a.capture_manifest.read_bytes(),a.delivery_manifest.read_bytes());a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out))
if __name__=='__main__':main()
