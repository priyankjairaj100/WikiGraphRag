#!/usr/bin/env python3
"""Audit retained dense retrieval; optionally one fresh native tokenizer, zero completions.

No ONNX graph is opened and no reviews or native answers are read. Final context
counts are recomputed only with explicit --tokenize and a new external session.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import re
import time
import numpy as np
import dense_retrieval_v14 as dense
import native_structured_reader_v14 as native

ROOT=Path(__file__).resolve().parents[1]
CONDITIONS=[r+'_'+c for r in ('bm25','dense','rrf') for c in ('paired','closure')]

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()

def textsha(value):return hashlib.sha256(value.encode()).hexdigest()
def read(path):return json.loads(Path(path).read_text())
def write_new(path,value):
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    with Path(path).open('x') as f:json.dump(value,f,indent=2);f.write('\n')

def native_guards(trace):
    command=trace['command']
    options={key:command[command.index(key)+1] for key in ['-c','-ngl','--parallel','-t','-tb','--chat-template-kwargs']}
    return (trace['process_exit_code']==0 and not trace.get('watchdog_stop_reason') and
            trace['address_space_limit_bytes']==5905580032 and trace['cpu_seconds_limit']==3600 and
            trace['elapsed_seconds']<=1050 and trace['cgroup_resident_observed_peak_bytes']<=7516192768 and
            options=={'-c':'4096','-ngl':'0','--parallel':'1','-t':'4','-tb':'4','--chat-template-kwargs':'{"enable_thinking":false}'} and
            '--no-context-shift' in command and '--no-warmup' in command)

def bm25(pool,question):
    terms=lambda text:re.findall(r'[^\W_]+',text.lower(),flags=re.UNICODE)
    bags=[Counter(terms(row['text'])) for row in pool];lengths=[sum(x.values()) for x in bags]
    avg=sum(lengths)/len(lengths);df=Counter(t for bag in bags for t in bag);query=sorted(set(terms(question)))
    rows=[]
    for seed,bag,length in zip(pool,bags,lengths):
        score=0.0
        for term in query:
            tf=bag[term]
            if tf:
                idf=math.log(1+(len(pool)-df[term]+0.5)/(df[term]+0.5))
                score+=idf*tf*2.2/(tf+1.2*(0.25+0.75*length/avg))
        rows.append((score,seed))
    return sorted(rows,key=lambda x:(-x[0],x[1]['chunk_id']))

class Audit:
    def __init__(self):self.inputs={};self.checks=[];self.contexts=[]
    def check(self,label,ok):
        self.checks.append({'check':label,'passed':bool(ok)})
        if not ok:raise ValueError(label)
    def bind(self,path,expected=None):
        path=Path(path);actual=sha(path)
        if expected is not None:self.check('hash:'+str(path),actual==expected)
        key=str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
        self.inputs[key]={'sha256':actual,'bytes':path.stat().st_size}
        return path
    def run(self):
        protocol=read(self.bind(ROOT/'data/dense_retrieval_v14/source_coverage_protocol.json'))
        for x in protocol['inputs'].values():
            p=self.bind(ROOT/x['path'],x['sha256']);self.check('protocol_bytes:'+x['path'],p.stat().st_size==x['bytes'])
        for x in protocol['external_bindings'].values():self.bind(x['path'],x['sha256'])
        self.check('source_only_48_context_protocol',protocol['natural_qa_generation_authorized'] is False and protocol['conditions']==CONDITIONS and protocol['context_count']==48)
        report=read(self.bind(ROOT/'results/dense_retrieval_v14.json'));packing=read(self.bind(ROOT/'results/dense_packing_runtime_v14.json'))
        configpath=ROOT/'configs/dense_retriever_candidate_v14.json';manifestpath=ROOT/'data/structured_reader_v14/corpus_manifest.json';questionpath=ROOT/'data/paired_reader_v13/questions.json'
        config=dense.load_config(configpath);manifest,seeds,pages,spans,links=dense.load_source(manifestpath);questions=dense.lexical.questions_only(questionpath)
        self.check('fixed_source_population',len(seeds)==2306 and len(questions)==8 and len(pages)==550)
        idir=Path(report['index_directory']);public=ROOT/'data/dense_retrieval_v14/index01'
        for name in ['index.json','input_freeze.json','attempt_started.json','attempt_finished.json']:
            self.bind(public/name,sha(idir/name));self.bind(idir/name)
        index,seedvectors,queryvectors=dense.load_index(idir,manifestpath,questionpath,configpath,seeds,questions)
        self.check('frozen_loader_checks_real_vectors',True)
        for kind in ['seed','query']:self.bind(idir/('seeds.npy' if kind=='seed' else 'queries.npy'),index[kind+'_vectors_sha256'])
        freeze=read(idir/'input_freeze.json');started=read(idir/'attempt_started.json');finished=read(idir/'attempt_finished.json')
        for x in freeze['inputs'].values():self.bind(x['path'],x['sha256'])
        gate=dense.execution_gate(Path(freeze['inputs']['execution_gate']['path']),configpath)
        self.check('gate_exact_freeze',gate==freeze['reader_completion_gate'] and gate['source_coverage_protocol_sha256']==sha(ROOT/'data/dense_retrieval_v14/source_coverage_protocol.json'))
        self.bind(ROOT/'scripts/check_dense_readiness_v14.py',gate['readiness_script_sha256'])
        main=read(self.bind(ROOT/'results/native_structured_main_v14_receipt.json',gate['native_verification']['receipt_sha256']))
        self.check('serialized_workload_chronology',protocol['frozen_unix_seconds']<gate['created_unix_seconds'] and main['finished_unix_seconds']<gate['created_unix_seconds']<=started['started_unix_seconds']<=freeze['created_unix_seconds']<=finished['finished_unix_seconds']<=packing['started_unix_seconds']<packing['finished_unix_seconds'])
        acquired=read(ROOT/'results/dense_assets_v14.json')
        for item in acquired['verified']:
            self.bind(Path(acquired['external_asset_directory'])/item['relative_path'],item['sha256'])
        self.check('verified_pinned_assets',not acquired['missing'] and acquired['model_executed'] is False and freeze['assets']==index['encoder_identity']['assets'])
        self.check('exact_runtime_config',index['encoder_identity']['pooling']=='CLS_first_token_then_L2' and index['encoder_identity']['dimension']==384 and index['encoder_identity']['providers']==['CPUExecutionProvider'])
        sites=list((idir.parent/'venv/lib').glob('python*/site-packages'))
        self.check('pinned_runtime_site_directory',len(sites)==1)
        normalize=lambda s:re.sub(r'[-_.]+','-',s).lower()
        venvcfg=dict(line.split(' = ',1) for line in self.bind(idir.parent/'venv/pyvenv.cfg').read_text().splitlines() if ' = ' in line)
        base_site=Path(venvcfg['home']).parent/'lib'/('python'+'.'.join(venvcfg['version'].split('.')[:2]))/'site-packages'
        search=[base_site,sites[0]] if venvcfg.get('include-system-site-packages')=='true' else sites
        installed={normalize(d.metadata['Name']):d.version for directory in search for d in importlib.metadata.distributions(path=[str(directory)])}
        self.check('installed_runtime_metadata_pins',all(installed.get(normalize(name))==version for name,version in index['encoder_identity']['runtime_distributions'].items()))
        fields={'script_sha256':ROOT/'scripts/dense_retrieval_v14.py','config_sha256':configpath,'structured_manifest_sha256':manifestpath,'questions_sha256':questionpath,'index_sha256':idir/'index.json','lexical_helper_sha256':ROOT/'scripts/retrieve_paired_v13.py','source_closure_helper_sha256':ROOT/'scripts/retrieve_structured_layout_v14.py','token_counter_sha256':ROOT/'scripts/native_structured_reader_v14.py','token_counter_config_sha256':ROOT/'configs/structured_reader_main_v14.json'}
        for key,p in fields.items():self.bind(p,report[key])
        self.check('packing_bindings',packing['status']=='complete' and packing['context_count']==48 and packing['metadata_sha256']==sha(ROOT/'results/dense_retrieval_v14.json') and packing['external_contexts_sha256']==report['external_contexts_sha256'] and packing['encoder_retriever_sha256']==report['script_sha256'] and packing['native_adapter_sha256']==report['token_counter_sha256'] and packing['reader_config_sha256']==report['token_counter_config_sha256'] and packing['wrapper_sha256']==sha(ROOT/'scripts/run_dense_retrieval_v14.py') and packing['native_completion_calls']==0 and packing['encoder_loaded'] is False and packing['inner_timings']==report['timings'])
        previous_tokenizer=idir.parent/'tokenizer01'
        original_trace=read(self.bind(previous_tokenizer/'execution.json'));original_freeze=read(self.bind(previous_tokenizer/'frozen_tokenizer.json'));original_props=read(self.bind(previous_tokenizer/'props.json'))
        self.check('original_tokenizer_actual_guards_and_freeze',native_guards(original_trace) and original_freeze['completion_calls_authorized']==0 and original_freeze['config_sha256']==report['token_counter_config_sha256'] and original_freeze['collector_sha256']==report['token_counter_sha256'] and packing['started_unix_seconds']<=original_freeze['created_unix_seconds']<packing['finished_unix_seconds'])
        template_sha=textsha(original_props['chat_template'])
        for row in report['records']:
            identity=row['native_tokens']['tokenizer_identity']
            self.check('original_tokenizer_identity:'+row['question_id']+'__'+row['condition'],identity['chat_template_sha256']==template_sha and identity['model_sha256']==original_trace['model_sha256'] and identity['model_revision']==original_trace['model_repo_revision'] and identity['runtime_files_sha256']==original_trace['runtime_files_sha256'] and identity['config_sha256_canonical']==original_trace['reader_config_sha256_canonical'] and identity['prompt_builder_sha256']==report['token_counter_sha256'])
        contextpath=self.bind(report['external_contexts_path'],report['external_contexts_sha256']);self.contexts=[json.loads(s) for s in contextpath.read_text().splitlines() if s.strip()]
        records={(r['question_id'],r['condition']):r for r in report['records']};contexts={(r['question_id'],r['condition']):r for r in self.contexts};qs={q['question_id']:q for q in questions}
        self.check('exact_grid_and_unique_rows',len(records)==len(report['records'])==len(contexts)==len(self.contexts)==48 and set(records)==set(contexts)=={(q,c) for q in qs for c in CONDITIONS})
        ranking={r['question_id']:r for r in report['rankings']};seedby={s['chunk_id']:s for s in seeds};rank_replayed=0;span_count=0;cost_count=0
        for qnumber,q in enumerate(questions):
            qid=q['question_id'];indices=[i for i,s in enumerate(seeds) if s['history_id']==q['history_id']];pool=[seeds[i] for i in indices]
            lexical=bm25(pool,q['question']);scores=seedvectors[indices]@queryvectors[qnumber]
            vectorrank=sorted(zip(map(float,scores),pool),key=lambda x:(-x[0],x[1]['chunk_id']))
            ranks=[{s['chunk_id']:n for n,(_,s) in enumerate(rows,1)} for rows in [lexical,vectorrank]]
            rr=sorted([(1/(60+ranks[0][s['chunk_id']])+1/(60+ranks[1][s['chunk_id']]),s) for s in pool],key=lambda x:(-x[0],x[1]['chunk_id']))
            expected={'bm25':lexical,'dense':vectorrank,'rrf':rr}
            self.check('full_history_ranking_count:'+qid,ranking[qid]['pool_chunk_count']==len(pool))
            for ranker,rows in expected.items():
                actual=ranking[qid]['rankings'][ranker]
                self.check('independent_ranking:'+qid+':'+ranker,len(actual)==len(pool) and [x['chunk_id'] for x in actual]==[s['chunk_id'] for _,s in rows] and all(x['rank']==i and abs(x['score']-score)<(2e-6 if ranker=='dense' else 1e-12) for i,(x,(score,_)) in enumerate(zip(actual,rows),1)))
                docs=sorted({s['document_id'] for s in pool});short=[]
                for doc in docs:short.extend([pair for pair in rows if pair[1]['document_id']==doc][:3])
                short=sorted(short,key=lambda x:(-x[0],x[1]['chunk_id']));shortids=[s['chunk_id'] for _,s in short]
                rank_replayed+=1
                for representation in ['paired','closure']:
                    key=qid,ranker+'_'+representation;r=records[key];raw=contexts[key];label=qid+'__'+key[1]
                    self.check('source_context_identity:'+label,{k:v for k,v in raw.items() if k not in ['question','context']}==r and raw['question']==q['question'] and raw['history_id']==q['history_id'] and textsha(raw['context'])==r['context_sha256'])
                    self.check('fixed_six_seed_shortlist:'+label,len(shortids)==6 and shortids==r['fixed_shortlist_seed_ids'])
                    candidates=[dense.structural.bundle(s,representation,spans,links) for _,s in short]
                    # Replay deterministic packing decisions against retained candidate costs;
                    # final prompt costs are independently recomputed below in a fresh session.
                    costs=defaultdict(list)
                    for e in r['cost_evaluations']:costs[e['context_sha256']].append(e)
                    def counter(question,context):
                        options=costs[textsha(context)];self.check('candidate_cost_context_binding:'+label,bool(options))
                        value=options[0]
                        self.check('candidate_cost_hash_agreement:'+label,all(all(v[k]==value[k] for k in ['input_tokens','rendered_prompt_sha256','token_ids_sha256','tokenizer_identity']) for v in options))
                        return {k:value[k] for k in ['input_tokens','rendered_prompt_sha256','token_ids_sha256','tokenizer_identity']}
                    packed=dense.structural.pack(candidates,representation,pages,q['question'],counter)
                    self.check('whole_bundle_packing_replay:'+label,packed['context']==raw['context'] and packed['source_spans']==r['source_spans'] and packed['dropped']==r['dropped_seeds'] and packed['cost_evaluations']==r['cost_evaluations'] and packed['native_tokens']==r['native_tokens'] and packed['evidence_word_count']==r['evidence_word_count'] and [b['seed']['chunk_id'] for b in packed['selected']]==r['selected_seed_ids'])
                    self.check('attachment_accounting:'+label,r['accepted_attachment_ids']==sorted({a for b in packed['selected'] for a in b['accepted_attachment_ids']}) and r['unresolved_attachment_ids']==sorted({a for b in candidates for a in b['unresolved_attachment_ids']}))
                    for s in r['source_spans']:
                        page=pages[(s['document_id'],s['pdf_page'])];text=page['text'][s['char_start']:s['char_stop']]
                        self.check('source_span_exact:'+label,0<=s['char_start']<s['char_stop']<=len(page['text']) and textsha(text)==s['text_sha256'] and len(text.split())==s['word_count'] and s['history_id']==q['history_id']);span_count+=1
                    self.check('actual_budget_metadata:'+label,r['evidence_word_count']==sum(s['word_count'] for s in r['source_spans'])<=960 and 0<r['native_tokens']['input_tokens']<=3500 and r['native_tokens']['input_tokens']+256<=4096)
                    cost_count+=len(r['cost_evaluations'])
        baseline=read(self.bind(ROOT/'results/structured_retrieval_layout_v14.json'));basepath=self.bind(baseline['external_contexts_path'],baseline['external_contexts_sha256']);old={(r['question_id'],r['condition']):r for r in map(json.loads,basepath.read_text().splitlines())};reuse=[]
        for qid in qs:
            for representation in ['paired','closure']:
                now,before=contexts[(qid,'bm25_'+representation)],old[(qid,representation)]
                self.check('exact_BM25_control:'+qid+':'+representation,now['question']==before['question'] and now['history_id']==before['history_id'] and now['context']==before['context'] and now['context_sha256']==before['context_sha256'] and now['native_tokens']==before['native_tokens'])
                reuse.append({'question_id':qid,'condition':'bm25_'+representation,'context_sha256':now['context_sha256'],'bytes_identical':True})
        self.report=report
        return {'schema_version':'dense_execution_audit_v14','status':'mechanical_checks_passed_native_recount_pending','auditor_sha256':sha(__file__),'created_unix_seconds':time.time(),'input_bindings':self.inputs,'check_count':len(self.checks),'checks':self.checks,'population':{'source_seeds':2306,'questions':8,'contexts':48,'full_history_rankings_replayed':rank_replayed,'selected_source_spans_checked':span_count,'retained_candidate_costs_replayed':cost_count,'exact_BM25_controls':16},'baseline_controls':reuse,'encoder_ledger':{'seed_count':index['seed_count'],'query_count':index['query_count'],'truncated_seeds':len(index['truncated_chunk_ids']),'truncated_queries':len(index['truncated_question_ids']),'saved_vector_hashes_verified':True,'new_encoder_forwards':0},'chronology':{'protocol_frozen':protocol['frozen_unix_seconds'],'main_completed':main['finished_unix_seconds'],'gate_created':gate['created_unix_seconds'],'encoder_started':started['started_unix_seconds'],'encoder_frozen':freeze['created_unix_seconds'],'encoder_finished':finished['finished_unix_seconds'],'native_packing_started':packing['started_unix_seconds'],'native_packing_finished':packing['finished_unix_seconds']},'provenance':{'collector_author_audit':True,'independent_human_review':False,'review_contents_inspected':False,'frozen_review_files_hashed_only':True,'native_answers_read':0,'network_calls':0,'onnx_sessions_opened':0,'native_completion_calls':0},'limits':['Ranking is independently recomputed from saved verified vectors; CLS hidden states and encoder forward execution are not rerun.','Source slicing and whole-bundle selection are replayed with the frozen structural helper and retained candidate costs; only48 final prompts are independently re-tokenized.','No context-support reviews or native answers are inspected, and no semantic source sufficiency, reader gain or held-out claim is certified.']}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--tokenize',action='store_true');p.add_argument('--tokenizer-dir',type=Path);p.add_argument('--output',type=Path);a=p.parse_args()
    if a.output and a.output.exists():raise FileExistsError('Preserve existing audit result')
    if a.tokenize and (a.tokenizer_dir is None or a.tokenizer_dir.exists()):raise ValueError('Token recount requires a fresh external directory')
    audit=Audit();report=audit.run()
    if a.tokenize:
        attempt=a.tokenizer_dir.with_suffix('.audit_attempt.json');finished=a.tokenizer_dir.with_suffix('.audit_finished.json')
        if attempt.exists() or finished.exists():raise FileExistsError('Preserve existing tokenizer-audit attempt')
        configpath=ROOT/'configs/structured_reader_main_v14.json';started=time.time()
        write_new(attempt,{'created_unix_seconds':started,'audit_script_sha256':sha(__file__),'input_bindings':audit.inputs,'completion_calls_authorized':0,'expected_final_contexts':48,'tokenizer_directory':str(a.tokenizer_dir)})
        rows=[]
        try:
            with native.tokenizer_session(configpath,a.tokenizer_dir) as bridge:
                for context in audit.contexts:
                    actual=bridge.count_prompt(context['question'],context['context']);expected=context['native_tokens']
                    match=all(actual[k]==expected[k] for k in ['input_tokens','token_ids_sha256','rendered_prompt_sha256','tokenizer_identity'])
                    row={'question_id':context['question_id'],'condition':context['condition'],'context_sha256':context['context_sha256'],**{k:actual[k] for k in ['input_tokens','token_ids_sha256','rendered_prompt_sha256','tokenizer_identity']},'matches_recorded_exactly':match}
                    rows.append(row);audit.check('fresh_final_native_tokens:'+context['question_id']+'__'+context['condition'],match and actual['fits_input_budget'] and actual['fits_context_with_output_reserve'])
            trace=read(a.tokenizer_dir/'execution.json');frozen=read(a.tokenizer_dir/'frozen_tokenizer.json')
            audit.check('fresh_tokenizer_guards',native_guards(trace) and frozen['completion_calls_authorized']==0)
            for path in a.tokenizer_dir.iterdir():
                if path.is_file():audit.bind(path)
            for path,spec in audit.inputs.items():
                resolved=Path(path) if Path(path).is_absolute() else ROOT/path
                audit.check('unchanged_after_recount:'+path,sha(resolved)==spec['sha256'])
            audit.check('audit_code_unchanged_after_recount',sha(__file__)==report['auditor_sha256'])
            report.update(status='passed',check_count=len(audit.checks),checks=audit.checks,input_bindings=audit.inputs,native_recount={'fresh_sessions':1,'completion_calls':0,'contexts':len(rows),'rows':rows,'execution':trace,'started_unix_seconds':started,'finished_unix_seconds':time.time(),'attempt_sha256':sha(attempt),'frozen_tokenizer_sha256':sha(a.tokenizer_dir/'frozen_tokenizer.json')})
            write_new(finished,{'status':'passed','finished_unix_seconds':time.time(),'contexts':len(rows),'completion_calls':0,'attempt_sha256':sha(attempt),'execution_sha256':sha(a.tokenizer_dir/'execution.json')})
        except Exception as error:
            write_new(finished,{'status':'failed','finished_unix_seconds':time.time(),'completed_contexts':len(rows),'error_type':type(error).__name__,'error':str(error),'completion_calls':0,'attempt_sha256':sha(attempt)});raise
    if a.output:write_new(a.output,report)
    print(json.dumps({'status':report['status'],'checks':report['check_count'],'population':report['population'],'native_recount_contexts':report.get('native_recount',{}).get('contexts',0),'output_sha256':sha(a.output) if a.output else None},indent=2))

if __name__=='__main__':main()
