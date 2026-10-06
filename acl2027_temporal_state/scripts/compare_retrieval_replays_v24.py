#!/usr/bin/env python3
"""Validate fixed-roster retrieval replay and compare actual pack bytes."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def read(path):return json.loads(path.read_text())
def key(row):return row['item_id'],row['policy'],row['budget_words']

def validate(result,packs,freeze,expected):
    rows={key(row):row for row in result['records']}
    if set(rows)!=expected or len(rows)!=len(result['records']):raise ValueError('incomplete or duplicate roster')
    if result['code_sha256']!=freeze['code_sha256']:raise ValueError('implementation mismatch')
    if result['index_manifest_sha256']!=freeze['index_manifest_sha256']:raise ValueError('index manifest mismatch')
    for k,row in rows.items():
        path=packs/f'{k[0]}_{k[1]}_{k[2]}.txt'
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=row['rendered_sha256']:raise ValueError('pack hash mismatch')
        if len(raw.decode('utf-8').split())!=row['rendered_words'] or row['rendered_words']>k[2]:raise ValueError('budget mismatch')
        ids=row['selected_block_ids']
        if len(ids)!=len(set(ids)) or any(b not in result['block_catalog'] for b in ids):raise ValueError('bad selected IDs')
    return rows

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--original',type=Path,default=ROOT/'results/retrieval_probe_v24.json')
    ap.add_argument('--replay',type=Path,default=ROOT/'results/retrieval_probe_seed0_v24.json')
    ap.add_argument('--freeze',type=Path,default=ROOT/'data/task_probe_v24/retrieval_freeze.json')
    ap.add_argument('--environment-freeze',type=Path,default=ROOT/'data/task_probe_v24/retrieval_seed0_environment_freeze.json')
    ap.add_argument('--protocol',type=Path,default=ROOT/'data/task_probe_v24/protocol.json')
    ap.add_argument('--original-pack-dir',type=Path,required=True)
    ap.add_argument('--replay-pack-dir',type=Path,required=True)
    ap.add_argument('--output',type=Path,default=ROOT/'results/retrieval_replay_comparison_v24.json')
    a=ap.parse_args();original=read(a.original);replay=read(a.replay);freeze=read(a.freeze);environment=read(a.environment_freeze);protocol=read(a.protocol)
    if sha(a.protocol)!=freeze['protocol_sha256']:raise ValueError('protocol mismatch')
    if environment['original_result_sha256']!=sha(a.original):raise ValueError('original changed after environment freeze')
    if any(r['retrieval_freeze_sha256']!=sha(a.freeze) for r in (original,replay)):raise ValueError('retrieval freeze mismatch')
    if environment['original_freeze_sha256']!=sha(a.freeze):raise ValueError('environment freeze mismatch')
    expected={(i['item_id'],p,b) for i in protocol['items'] for p in freeze['policies'] for b in freeze['budgets_words']}
    x=validate(original,a.original_pack_dir,freeze,expected);y=validate(replay,a.replay_pack_dir,freeze,expected)
    records=[]
    for k in sorted(expected):
        left,right=x[k],y[k]
        records.append({'item_id':k[0],'policy':k[1],'budget_words':k[2],
                        'selected_ids_same_order':left['selected_block_ids']==right['selected_block_ids'],
                        'selected_ids_same_set':set(left['selected_block_ids'])==set(right['selected_block_ids']),
                        'pack_hash_equal':left['rendered_sha256']==right['rendered_sha256'],
                        'source_intervals_equal':left['rendered_source_intervals']==right['rendered_source_intervals'],
                        'rendered_words_equal':left['rendered_words']==right['rendered_words'],
                        'original_pack_sha256':left['rendered_sha256'],'replay_pack_sha256':right['rendered_sha256'],
                        'original_only_block_ids':sorted(set(left['selected_block_ids'])-set(right['selected_block_ids'])),
                        'replay_only_block_ids':sorted(set(right['selected_block_ids'])-set(left['selected_block_ids']))})
    keys=['selected_ids_same_order','selected_ids_same_set','pack_hash_equal','source_intervals_equal','rendered_words_equal']
    receipt={'schema':'retrieval_replay_comparison_v24','created_at_utc':datetime.now(timezone.utc).isoformat(),
             'original_result_sha256':sha(a.original),'replay_result_sha256':sha(a.replay),'environment_freeze_sha256':sha(a.environment_freeze),
             'analysis_code_sha256':sha(Path(__file__)),'frozen_roster_cells':len(expected),'validated_original_packs':len(x),'validated_replay_packs':len(y),
             'matching_counts':{k:sum(r[k] for r in records) for k in keys},'differing_counts':{k:sum(not r[k] for r in records) for k in keys},
             'interpretation':'Exact reproducibility comparison only. Changed selections require separate semantic review; no QA or method superiority claim.',
             'records':records}
    with a.output.open('x') as out:json.dump(receipt,out,indent=2);out.write('\n')
    print(json.dumps({'matching':receipt['matching_counts'],'differing':receipt['differing_counts']}))
if __name__=='__main__':main()
