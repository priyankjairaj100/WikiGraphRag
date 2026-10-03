#!/usr/bin/env python3
"""Four source-only retrieval representations with exact reader-token costing.

The callable adapter must tokenize the full actual reader prompt. There is no
word-to-token approximation, answer-based selection, inference, or query expansion.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re

import retrieve_paired_v13 as lexical

ROOT=Path(__file__).resolve().parents[1]
CONDITIONS=('ordinary','paired','parent','closure')
MAX_WORDS=960
MAX_INPUT_TOKENS=3500
MAX_OUTPUT_TOKENS=256
CONTEXT_TOKENS=4096


def sha(data:bytes)->str:return hashlib.sha256(data).hexdigest()


def overlap(a:dict,b:dict)->bool:
    return a['document_id']==b['document_id'] and a['pdf_page']==b['pdf_page'] and a['char_start']<b['char_stop'] and b['char_start']<a['char_stop']


def seed_span(seed:dict)->dict:
    return {k:seed[k] for k in ('document_id','history_id','version_order','pdf_page','char_start','char_stop')} | {'span_id':seed['chunk_id'],'role':'retrieval_seed'}


def merge_spans(spans:list[dict])->list[dict]:
    """Union overlapping source intervals without filling unselected gaps."""
    ordered=sorted(spans,key=lambda s:(s['version_order'],s['document_id'],s['pdf_page'],s['char_start'],s['char_stop']))
    merged=[]
    for span in ordered:
        if merged and merged[-1]['document_id']==span['document_id'] and merged[-1]['pdf_page']==span['pdf_page'] and span['char_start']<=merged[-1]['char_stop']:
            merged[-1]['char_stop']=max(merged[-1]['char_stop'],span['char_stop'])
            merged[-1]['member_span_ids']=sorted(set(merged[-1]['member_span_ids']+[span['span_id']]))
        else:
            merged.append({k:span[k] for k in ('document_id','history_id','version_order','pdf_page','char_start','char_stop')} |
                          {'member_span_ids':[span['span_id']]})
    return merged


def source_index(corpus:dict)->tuple[dict,dict,list[dict]]:
    pages={(p['document_id'],p['pdf_page']):p for p in corpus['pages']}
    spans={s['span_id']:s for p in corpus['pages'] for field in ('blocks','table_regions','target_spans') for s in p[field]}
    links=[a for p in corpus['pages'] for a in p['attachments']]
    return pages,spans,links


def bundle(seed:dict,condition:str,spans:dict,links:list[dict])->dict:
    anchor=seed_span(seed)
    if condition in ('ordinary','paired'):
        return {'seed':seed,'spans':[anchor],'accepted_attachment_ids':[],'unresolved_attachment_ids':[]}
    if condition=='parent':
        return {'seed':seed,'spans':[spans[sid] for sid in seed['parent_span_ids']],
                'accepted_attachment_ids':[],'unresolved_attachment_ids':[]}
    if condition!='closure':raise ValueError('unknown representation')
    selected={anchor['span_id']:anchor};accepted=set();unresolved=set()
    # Transitive source-context closure, not a claim-level truth/correction graph.
    # Each link is followed once; only parser-accepted, exact source targets enter.
    changed=True
    while changed:
        changed=False
        for link in links:
            if not any(overlap(span,link['content_span']) for span in selected.values()):continue
            if link['status']!='accepted' or link['target_span'] is None:
                unresolved.add(link['attachment_id']);continue
            accepted.add(link['attachment_id']);target=link['target_span']
            if target['span_id'] not in selected:
                selected[target['span_id']]=target;changed=True
    return {'seed':seed,'spans':list(selected.values()),'accepted_attachment_ids':sorted(accepted),
            'unresolved_attachment_ids':sorted(unresolved)}


def render(bundles:list[dict],condition:str,pages:dict)->tuple[str,list[dict],int]:
    ordered=sorted(bundles,key=lambda b:(b['seed']['version_order'],b['seed']['document_id'],b['seed']['pdf_page'],b['seed']['chunk_index']))
    if condition in ('ordinary','paired'):
        # Match frozen v13 layout contexts exactly when all fixed seeds fit.
        pieces=[];records=[];words=0
        for b in ordered:
            c=b['seed'];text=c['native_text'];words+=len(text.split())
            pieces.append(f"[{c['document_id']} p.{c['pdf_page']} | chunk={c['chunk_id']} | version={c['version_order']}]\n{text}")
            records.append({**seed_span(c),'text_sha256':sha(text.encode()),'word_count':len(text.split())})
        return '\n\n'.join(pieces),records,words
    union=merge_spans([span for b in ordered for span in b['spans']]);pieces=[];records=[];words=0
    for span in union:
        page=pages[(span['document_id'],span['pdf_page'])]
        text=page['text'][span['char_start']:span['char_stop']]
        # Preserve all bytes in the source slice, including first-line indentation.
        count=len(text.split());words+=count
        label=f"{span['document_id']}:p{span['pdf_page']:03}:{span['char_start']}-{span['char_stop']}"
        pieces.append(f"[{span['document_id']} p.{span['pdf_page']} | span={label} | version={span['version_order']}]\n{text}")
        records.append({**span,'text_sha256':sha(text.encode()),'word_count':count,'source_page_sha256':page['source_page_sha256']})
    return '\n\n'.join(pieces),records,words


def token_receipt(counter,question:str,context:str)->dict:
    raw=counter(question,context)
    allowed=('input_tokens','rendered_prompt_sha256','token_ids_sha256','tokenizer_identity')
    if not isinstance(raw,dict) or any(k not in raw for k in allowed):raise ValueError('invalid token-counter contract')
    receipt={k:raw[k] for k in allowed}
    if type(receipt['input_tokens']) is not int or receipt['input_tokens']<1:raise ValueError('invalid native token count')
    if any(not isinstance(receipt[k],str) or not re.fullmatch('[0-9a-f]{64}',receipt[k]) for k in ('rendered_prompt_sha256','token_ids_sha256')):
        raise ValueError('token counter must supply exact rendered/token hashes')
    if not isinstance(receipt['tokenizer_identity'],dict) or not receipt['tokenizer_identity']:raise ValueError('missing pinned tokenizer identity')
    return receipt


def pack(candidates:list[dict],condition:str,pages:dict,question:str,counter)->dict:
    evaluations=[]
    def assess(items,reason):
        context,source_spans,words=render(items,condition,pages)
        receipt=token_receipt(counter,question,context)
        fits=words<=MAX_WORDS and receipt['input_tokens']<=MAX_INPUT_TOKENS and receipt['input_tokens']+MAX_OUTPUT_TOKENS<=CONTEXT_TOKENS
        evaluations.append({'evaluation':reason,'seed_ids':[b['seed']['chunk_id'] for b in items],
                            'evidence_words':words,'context_sha256':sha(context.encode()),'fits':fits,**receipt})
        return fits,context,source_spans,words,receipt
    all_result=assess(candidates,'all_fixed_shortlist_bundles')
    if all_result[0]:selected=candidates;dropped=[];final=all_result
    else:
        selected=[];dropped=[]
        # One fixed score-ordered pass over six seeds. No backfill, seed splitting
        # or answer-informed repair. Every rejected whole bundle is reported.
        for candidate in candidates:
            proposed=selected+[candidate];result=assess(proposed,'greedy_whole_bundle_addition')
            if result[0]:selected=proposed
            else:dropped.append({'chunk_id':candidate['seed']['chunk_id'],'reason':'whole_bundle_exceeds_word_or_full_native_token_budget',
                                 'candidate_evidence_words':result[3],'candidate_full_input_tokens':result[4]['input_tokens']})
        final=assess(selected,'final_selected_bundles')
        if not final[0]:raise ValueError('even final evidence-free prompt is outside budget')
    identities=[json.dumps(e['tokenizer_identity'],sort_keys=True) for e in evaluations]
    if len(set(identities))!=1:raise ValueError('tokenizer identity changed during packing')
    return {'selected':selected,'dropped':dropped,'context':final[1],'source_spans':final[2],
            'evidence_word_count':final[3],'native_tokens':final[4],'cost_evaluations':evaluations,
            'status':'ready' if selected else 'empty_evidence_after_budget_packing'}


def load_adapter(path:Path,config_path:Path):
    spec=importlib.util.spec_from_file_location('frozen_v14_token_counter',path)
    if spec is None or spec.loader is None:raise ValueError('invalid token-counter module')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    if not callable(getattr(module,'count_prompt',None)):raise ValueError('adapter must define count_prompt(question, context, config)')
    config=json.loads(config_path.read_text())
    return lambda question,context:module.count_prompt(question,context,config)


def retrieve(manifest_path:Path,questions_path:Path,adapter_path:Path,adapter_config:Path,external_output:Path,metadata_output:Path,*,counter=None)->dict:
    if external_output.resolve().is_relative_to(ROOT.parent):raise ValueError('full source contexts must remain external')
    if external_output.exists() or metadata_output.exists():raise FileExistsError('new retrieval output paths required')
    manifest=json.loads(manifest_path.read_text());source=Path(manifest['external_structured_corpus_path'])
    if sha(source.read_bytes())!=manifest['external_structured_corpus_sha256']:raise ValueError('structured corpus hash mismatch')
    corpus=json.loads(source.read_text());pages,spans,links=source_index(corpus)
    for page in pages.values():
        if sha(page['text'].encode())!=page['source_page_sha256']:raise ValueError('source page hash mismatch')
    questions=lexical.questions_only(questions_path)
    counter=counter if counter is not None else load_adapter(adapter_path,adapter_config)
    if not callable(counter):raise ValueError('a callable exact full-prompt counter is required')
    contexts=[];records=[];identity=None
    for question in questions:
        pool=[seed for seed in corpus['seeds'] if seed['history_id']==question['history_id']]
        ranked=lexical.rank_bm25(pool,question['question']);score_by_id={c['chunk_id']:score for score,c in ranked}
        for condition in CONDITIONS:
            shortlist=lexical.select(ranked,'ordinary' if condition=='ordinary' else 'paired')
            shortlist=sorted(shortlist,key=lambda pair:(-pair[0],pair[1]['chunk_id']))
            candidates=[bundle(seed,condition,spans,links) for _,seed in shortlist]
            result=pack(candidates,condition,pages,question['question'],counter)
            current_identity=result['native_tokens']['tokenizer_identity']
            if identity is None:identity=current_identity
            elif identity!=current_identity:raise ValueError('tokenizer identity changed between questions/conditions')
            selected=result['selected'];selected_ids=[b['seed']['chunk_id'] for b in selected]
            documents=sorted(set(b['seed']['document_id'] for b in selected))
            row={'question_id':question['question_id'],'history_id':question['history_id'],'condition':condition,
                 'query_sha256':sha(question['question'].encode()),'context_sha256':sha(result['context'].encode()),
                 'status':result['status'],'fixed_shortlist_seed_ids':[c['chunk_id'] for _,c in shortlist],
                 'selected_seed_ids':selected_ids,'dropped_seeds':result['dropped'],'selected_document_ids':documents,
                 'both_versions_represented':len(documents)==2,'evidence_word_count':result['evidence_word_count'],
                 'context_words_including_headers':len(result['context'].split()),'native_tokens':result['native_tokens'],
                 'source_spans':result['source_spans'],'seed_scores':{c['chunk_id']:score_by_id[c['chunk_id']] for _,c in shortlist},
                 'accepted_attachment_ids':sorted(set(a for b in selected for a in b['accepted_attachment_ids'])),
                 'unresolved_attachment_ids':sorted(set(a for b in candidates for a in b['unresolved_attachment_ids'])),
                 'cost_evaluations':result['cost_evaluations'],
                 'baseline_context_unchanged_from_full_shortlist':condition in ('ordinary','paired') and not result['dropped']}
            records.append(row);contexts.append({**row,'question':question['question'],'context':result['context']})
    external_output.parent.mkdir(parents=True,exist_ok=True)
    with external_output.open('x') as stream:
        for row in contexts:stream.write(json.dumps(row,sort_keys=True,ensure_ascii=False)+'\n')
    report={'schema_version':'structured_retrieval_v0.14','script_sha256':sha(Path(__file__).read_bytes()),
            'lexical_helper_sha256':sha((ROOT/'scripts/retrieve_paired_v13.py').read_bytes()),
            'structured_manifest_sha256':sha(manifest_path.read_bytes()),'questions_sha256':sha(questions_path.read_bytes()),
            'token_counter_sha256':sha(adapter_path.read_bytes()),'token_counter_config_sha256':sha(adapter_config.read_bytes()),
            'external_contexts_path':str(external_output),'external_contexts_sha256':sha(external_output.read_bytes()),
            'question_count':len(questions),'context_count':len(contexts),'conditions':list(CONDITIONS),
            'budgets':{'evidence_words':MAX_WORDS,'full_native_input_tokens':MAX_INPUT_TOKENS,'output_reserve_tokens':MAX_OUTPUT_TOKENS,'context_tokens':CONTEXT_TOKENS},
            'algorithm':{'seed_retrieval':'Unchanged history-local v13 BM25; no query expansion',
                         'ordinary':'history-wide fixed top6 seed spans, native layout',
                         'paired':'fixed top3 per document, native layout',
                         'parent':'same paired shortlist expanded to source paragraph/table parents',
                         'closure':'same paired shortlist plus transitive parser-accepted exact context attachments',
                         'packing':'Try all fixed bundles, otherwise fixed score-order greedy whole-bundle acceptance; no backfill or truncation',
                         'source_order':'version,document,page,source offset; baseline chunk order preserved',
                         'word_charging':'Every displayed source word, including structural spans; overlapping structural spans unioned, no free headers',
                         'novelty_claim':False,'model_answers_or_reference_labels_read':False},'records':records}
    metadata_output.parent.mkdir(parents=True,exist_ok=True)
    with metadata_output.open('x') as stream:json.dump(report,stream,indent=2,ensure_ascii=False);stream.write('\n')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--questions',type=Path,required=True)
    parser.add_argument('--token-counter',type=Path,required=True)
    parser.add_argument('--token-counter-config',type=Path,required=True)
    parser.add_argument('--contexts',type=Path,required=True)
    parser.add_argument('--metadata',type=Path,required=True)
    args=parser.parse_args();result=retrieve(args.manifest,args.questions,args.token_counter,args.token_counter_config,args.contexts,args.metadata)
    print(json.dumps({key:result[key] for key in ('question_count','context_count','external_contexts_sha256')}))


if __name__=='__main__':main()
