#!/usr/bin/env python3
"""Verify distributed source-stream metadata; optional exact text check is separate."""
import argparse,hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def digest(b):return hashlib.sha256(b).hexdigest()
def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,default=ROOT/'results/source_stream_replay_v08.json');a=p.parse_args()
    d=json.loads((ROOT/'data/source_stream_v08/distribution_manifest.json').read_bytes())
    for r in d['files']+d['bindings']:
        path=Path(r['path']);assert not path.is_absolute() and '..' not in path.parts
        assert digest((ROOT/path).read_bytes())==r['sha256'],r['path']
    capture=json.loads((ROOT/'data/source_stream_v08/capture_manifest.json').read_bytes());sources={s['source_id']:s for s in capture['sources']};assert len(sources)==39
    delivery=json.loads((ROOT/'data/source_stream_v08/delivery_manifest.json').read_bytes());assert delivery['capture_manifest_sha256']==digest((ROOT/'data/source_stream_v08/capture_manifest.json').read_bytes())
    request=json.loads((ROOT/'data/source_stream_v08/request_manifest.json').read_bytes());requests={r['prefix_id']:r for r in request['requests']};prefixes={p['prefix_id']:p for p in delivery['prefixes']}
    assert len(requests)==6 and len(prefixes)==8
    spans=0;annotation_claims=0;reference_questions=0
    def walk(v,allowed):
        nonlocal spans
        if isinstance(v,list):
            for x in v:walk(x,allowed)
        elif isinstance(v,dict):
            assert 'quote' not in v and 'text' not in v,'full text in projection'
            if 'quote_sha256' in v:
                assert v['source_id'] in allowed and type(v['start']) is int and type(v['end']) is int and 0<=v['start']<v['end']
                assert v['quote_characters']==v['end']-v['start'] and len(v['quote_sha256'])==64;spans+=1
            for x in v.values():walk(x,allowed)
    for row in d['files']:
        obj=json.loads((ROOT/row['path']).read_bytes());pid=obj['prefix_id'];pr=prefixes[pid];req=requests[pid]
        assert pr['status']=='ready' and obj['source_request_sha256']==req['request_sha256']
        assert obj['content']['eligible_source_ids']==pr['source_ids']
        for b in pr['source_bindings']:
            s=sources[b['source_id']];assert s['content_usability']['provisional_content_usable']
            assert b['exact_version_sha256']==s['exact_version_sha256'] and b['text_sha256']==s['extracted_text_sha256']
        walk(obj['content'],set(pr['source_ids']))
        if row['stage']=='adjudicated':
            if row['kind']=='annotation':annotation_claims+=len(obj['content']['claims'])
            else:reference_questions+=len(obj['content']['questions'])
    out={'schema_version':'source_stream_replay_v0.8','status':'passed_distributed_metadata_integrity','source_urls_attempted':39,'usable_captures':sum(s['content_usability']['provisional_content_usable'] for s in sources.values()),'selected_histories':4,'ready_histories':len({p['history_id'] for p in prefixes.values() if p['status']=='ready'}),'ready_prefixes':len(requests),'blocked_prefixes':sum(p['status']!='ready' for p in prefixes.values()),'projections_checked':len(d['files']),'evidence_hash_entries_checked':spans,'adjudicated_annotation_claims':annotation_claims,'reference_judgments':reference_questions,'source_text_revalidated':False,'semantic_correctness_certified':False,'model_predictions_evaluated':0,'new_model_calls':0,'distribution_manifest_sha256':digest((ROOT/'data/source_stream_v08/distribution_manifest.json').read_bytes())}
    a.output.write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out))
if __name__=='__main__':main()
