#!/usr/bin/env python3
"""Index, freeze, then retrieve without opening annotations or answers."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from temporal_state.evidence_retrieval_v24 import Block,POLICIES,build_index,canonical,digest,retrieve
ROOT=Path(__file__).resolve().parents[1]
PROTOCOL=ROOT/'data/task_probe_v24/protocol.json'
FREEZE=ROOT/'data/task_probe_v24/retrieval_freeze.json'
RESULT=ROOT/'results/retrieval_probe_v24.json'
CODE=['src/temporal_state/evidence_retrieval_v24.py','scripts/run_retrieval_probe_v24.py','src/temporal_state/typed_reader_v15_1.py']

def read(path): return json.loads(path.read_text())
def write_new(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    raw=(json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode('utf-8')
    with path.open('xb') as f:
        if f.write(raw)!=len(raw): raise IOError('short write')
    if path.read_bytes()!=raw: raise IOError('write verification failed')
def now(): return datetime.now(timezone.utc).isoformat()
def hashes(): return {p:digest((ROOT/p).read_bytes()) for p in CODE}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--phase',choices=['index','freeze','run'],required=True)
    ap.add_argument('--source-dir',type=Path,required=True);ap.add_argument('--external-dir',type=Path,required=True)
    ap.add_argument('--freeze-path',type=Path,default=FREEZE);ap.add_argument('--result-path',type=Path,default=RESULT)
    a=ap.parse_args(); external=a.external_dir.resolve(); source_dir=a.source_dir.resolve();freeze_path=a.freeze_path.resolve();result_path=a.result_path.resolve()
    if external==ROOT or ROOT in external.parents or source_dir==ROOT or ROOT in source_dir.parents:
        raise ValueError('source/index/rendered captures must be outside repository')
    protocol=read(PROTOCOL);p_hash=digest(PROTOCOL.read_bytes())
    if p_hash!='214d0be8ba347c59a77a0a5b3c1f983d822ce9998999c7aae0ecfda654f21ae2': raise ValueError('protocol changed')
    manifest=external/'index_manifest.json'
    if a.phase=='index':
        records=[];start=time.perf_counter()
        for meta in protocol['sources']:
            raw=(source_dir/meta['external_filename']).read_bytes()
            if digest(raw)!=meta['expected_sha256'] or len(raw)!=meta['expected_bytes']: raise ValueError('source mismatch')
            t=time.perf_counter();blocks,info=build_index(raw,meta['external_filename'],meta['actual_document_fiscal_year_values'][0])
            dest=external/(meta['external_filename']+'.index.json')
            write_new(dest,[b.__dict__ for b in blocks]);info.update(index_filename=dest.name,index_file_sha256=digest(dest.read_bytes()),seconds=time.perf_counter()-t)
            records.append(info);print(json.dumps({k:info[k] for k in ['source_version','visible_blocks','typed_cards','seconds']}),flush=True)
        write_new(manifest,{'schema':'retrieval_index_v24','created_at_utc':now(),'protocol_sha256':p_hash,'code_sha256':hashes(),'sources':records,'total_seconds':time.perf_counter()-start})
    elif a.phase=='freeze':
        idx=read(manifest)
        if idx['code_sha256']!=hashes():raise ValueError('index implementation differs')
        write_new(freeze_path,{'schema':'retrieval_freeze_v24','created_at_utc':now(),'protocol_sha256':p_hash,'code_sha256':hashes(),'index_manifest_sha256':digest(manifest.read_bytes()),'sources':idx['sources'],'policies':list(POLICIES),'budgets_words':[1024,4096,16384],'bm25':{'k1':1.2,'b':0.75,'idf':'log(1 + (N-df+0.5)/(df+0.5))','query_tf':'binary','ranking_ties':'source filename, byte start, block kind'},'decomposition':'question plus punctuation/and/whether/while clauses with >=2 content tokens plus content-token query; equal reciprocal-rank fusion k=60','structural':'whole table; if too large row+leading 5-row date/unit/textual/explicit-th headers; 2 preceding short blocks; 2 neighboring text blocks; 2 post-table blocks; direct internal 1-hop targets; all rendered headers charged','source_date_rule':'typed/structural/decomposition restrict to matching actual filing FY if question contains exactly one distinct fiscal YYYY and such source exists; otherwise both sources; lexical retains both','data_access':'only protocol questions, exact admitted source HTML, source parser; no reference/annotation artifacts','prior_attempt':'provenance/retrieval_v24_interrupted0/interruption_receipt.json','prior_attempt_sha256':digest((ROOT/'provenance/retrieval_v24_interrupted0/interruption_receipt.json').read_bytes()),'amendment_reason':'Independent blind authored-fixture review fixed markup visibility, namespace-specific header visibility, whole-table one-hop closure, semantic headers, precision-aware exact-binding duplicates; no prior output contents inspected','completed_retrieval_rosters_before_freeze':0,'python':platform.python_version()})
        print('Frozen before retrieval outcomes',digest(freeze_path.read_bytes()))
    else:
        freeze=read(freeze_path)
        if freeze['protocol_sha256']!=p_hash or freeze['code_sha256']!=hashes() or freeze['index_manifest_sha256']!=digest(manifest.read_bytes()):raise ValueError('frozen input changed')
        indices={}
        for record in freeze['sources']:
            path=external/record['index_filename']
            if digest(path.read_bytes())!=record['index_file_sha256']:raise ValueError('index changed')
            indices[record['source_version']]=[Block(**b) for b in read(path)]
        results=[];catalog={};start=time.perf_counter()
        for item in protocol['items']:
            blocks=[b for filename in item['source_files'] for b in indices[filename]]
            for policy in freeze['policies']:
                for budget in freeze['budgets_words']:
                    t=time.perf_counter();result,rendered=retrieve(blocks,item['question'],budget,policy)
                    result.update(item_id=item['item_id'],history_id=item['history_id'],seconds=time.perf_counter()-t)
                    for block in result.pop('selected_blocks'):catalog[block['block_id']]=block
                    decisions=result.pop('decisions');result['decisions_counts']={k:sum(x['decision']==k for x in decisions) for k in sorted(set(x['decision'] for x in decisions))}
                    out=external/'packs'/f"{item['item_id']}_{policy}_{budget}.txt"
                    out.parent.mkdir(parents=True,exist_ok=True)
                    with out.open('x') as f:f.write(rendered)
                    write_new(out.with_suffix('.decisions.json'),decisions)
                    results.append(result)
            print('retrieved',item['item_id'],flush=True)
        write_new(result_path,{'schema':'retrieval_probe_v24','created_at_utc':now(),'protocol_sha256':p_hash,'retrieval_freeze_sha256':digest(freeze_path.read_bytes()),'index_manifest_sha256':digest(manifest.read_bytes()),'code_sha256':hashes(),'python':platform.python_version(),'total_retrieval_seconds':time.perf_counter()-start,'source_universe':'each item only its two protocol-admitted filings','source_index_access':'all 12 source files parsed once; indexing cost and bytes in freeze, not hidden by rendered budgets','interpretation':'automatic conventional baseline evidence retrieval only; no reference comparison, semantic coverage decision, model predictions, or held-out results','block_catalog':catalog,'records':results})
        print('Wrote',result_path,'records',len(results),'catalog',len(catalog))
if __name__=='__main__':main()
