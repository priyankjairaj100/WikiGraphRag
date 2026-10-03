#!/usr/bin/env python3
"""Portable v14 metadata crosslink audit; optional external replay, never inference.

Default mode reads only project metadata. It does not independently certify raw
source semantics, tokenization, citations, or grading. Collector author audit.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
import json
import sys
from verify_paired_study_v13 import Audit as MetadataAudit, digest, canonical_hash, load, PARAMETERS

ROOT=Path(__file__).resolve().parents[1]
CONDITIONS=('ordinary','paired','parent','closure')
MODEL_SHA='8814232b85594dcd46c50e5b8b29324a7efe9e746edbe8a3d1df3d3fce7aad39'
MODEL_REV='e87f176479d0855a907a41277aca2f8ee7a09523'
RUNTIME_SHA='677ee4aa7ac9a70d0d664474f589ba2f35ee2716ea0244d076ab103c3e5304c7'


def tokens_match(selection,prompt):
    return (selection['input_tokens']==prompt['input_tokens'] and
            selection['rendered_prompt_sha256']==prompt['rendered_prompt_sha256'] and
            selection['token_ids_sha256']==prompt['input_token_ids_sha256'])


def before_predictions(frozen,receipt,preflight):
    return (frozen['completion_calls_at_freeze']==preflight['completion_calls_at_freeze']==0 and
            frozen['created_unix_seconds']<=receipt['started_unix_seconds']<=preflight['created_unix_seconds'] and
            (receipt.get('finished_unix_seconds') is None or preflight['created_unix_seconds']<=receipt['finished_unix_seconds']))


def flags(command):
    wanted=['--host','-c','-ngl','--parallel','-t','-tb','--chat-template-kwargs','-ctk','-ctv','-fa']
    return {flag:command[command.index(flag)+1] for flag in wanted}


class Audit(MetadataAudit):
    def __init__(self,root):
        super().__init__(root);self.frozen={};self.retrieval={};self.request_records={};self.notes=[];self.external_bindings={};self.support_rows={};self.semantic_disagreements=[]
        layout=(self.root/'results/structured_retrieval_layout_v14.json').exists()
        self.active_retrieval='results/structured_retrieval_layout_v14.json' if layout else 'results/structured_retrieval_v14.json'
        self.active_requests='data/structured_reader_v14/request_manifest_layout.json' if layout else 'data/structured_reader_v14/request_manifest.json'
        self.active_contexts='data/structured_reader_v14/context_review_layout_manifest.json' if layout else 'data/structured_reader_v14/context_review_manifest.json'
        self.active_retriever='scripts/retrieve_structured_layout_v14.py' if layout else 'scripts/retrieve_structured_v14.py'
        self.layout_revision=layout
        protocol_path=self.root/'data/structured_reader_v14/main_execution_protocol.json'
        if protocol_path.exists():
            active=load(protocol_path)['active_artifacts']
            self.active_retrieval=active['retrieval'];self.active_requests=active['request_manifest'];self.active_contexts=active['context_review_manifest']
            self.layout_revision=self.active_retrieval.endswith('structured_retrieval_layout_v14.json')
            self.active_retriever='scripts/retrieve_structured_layout_v14.py' if self.layout_revision else 'scripts/retrieve_structured_v14.py'

    def optional(self,path,stage):
        if not self.path(path).exists():self.pending.append(stage);return None
        return self.read(path)

    def mixed_mapping(self,mapping,label):
        for path,expected in mapping.items():
            try:relative=self.path(path)
            except ValueError:
                if not any(marker in path for marker in ['/tmp/structured_reader_v14/','/tmp/paired_reader_v13/']):raise
                if path in self.external_bindings and self.external_bindings[path]!=expected:raise ValueError('Conflicting external hash bindings.')
                self.external_bindings[path]=expected
                continue
            self.binding(relative,expected,label+':'+str(relative.relative_to(self.root)))

    def preparation(self):
        protocol=self.read('data/structured_reader_v14/protocol.json')
        self.mapping(protocol['question_and_reference_files'],'protocol:prior_frozen_input')
        self.check('protocol:bounded_development_only',protocol['development_only'] is True and protocol['question_count']==8 and protocol['histories']==2 and protocol['reference_changes_allowed'] is False and protocol['comparison_plan']['calls']==32)
        registry=self.read('configs/reader_candidates_v14.json')
        chosen=[x for x in registry['candidates'] if x['status']=='selected_before_predictions']
        self.check('selected_immutable_artifact',len(chosen)==1 and chosen[0]['artifact_sha256']==MODEL_SHA and chosen[0]['artifact_revision']==MODEL_REV and chosen[0]['artifact_bytes']==3143656608)
        questions=self.read('data/paired_reader_v13/questions.json')['questions']
        self.questions={x['question_id']:x for x in questions}
        self.check('question_denominator',len(self.questions)==len(questions)==8 and len({x['history_id'] for x in questions})==2)
        controls=self.read('data/structured_reader_v14/controls_contract.json')
        self.mapping({'data/structured_reader_v14/'+p:h for p,h in controls['input_files'].items()},'controls:authored_input')
        refs=self.read('data/structured_reader_v14/controls_references.json')
        self.control_refs={x['request_id']:x for x in refs}
        self.check('controls:frozen_gate',controls['request_count']==8 and controls['gate']=={'minimum_reference_complete_grounded':7,'denominator':8,'maximum_critical_sign_unit_year_sample_errors_among_answered_claims':0,'any_native_output_budget_failure_counts_as_incorrect':True})
        review=self.read('results/reader_control_reference_review_v14.json');self.mapping(review['input_sha256'],'controls:reference_review')
        self.check('controls:preoutput_reference_review',review['controls_reviewed']==review['controls_approved']==8 and review['required_facts_reviewed']==sum(len(x['required_facts']) for x in refs)==19 and review['decision']=='approved_for_frozen_control_execution' and review['provenance']['control_model_outputs_inspected'] is False)
        self.control_review=review
        corpus=self.read('data/structured_reader_v14/corpus_manifest.json')
        self.binding('scripts/build_structured_corpus_v14.py',corpus['builder_sha256'],'corpus:builder')
        self.binding(corpus['source_corpus_manifest_path'],corpus['source_corpus_manifest_sha256'],'corpus:prior_manifest')
        pages=corpus['pages'];attachments=[x for p in pages for x in p['attachments']]
        counts=Counter(x['status'] for x in attachments)
        self.check('corpus:exact_counts',len(pages)==corpus['pdf_pages']==550 and corpus['seed_chunks']==2306 and sum(len(p['blocks']) for p in pages)==corpus['primitive_blocks'] and dict(counts)==corpus['attachment_counts'] and sum(len(p['table_regions']) for p in pages)==corpus['table_regions'])
        self.check('corpus:declared_source_only',corpus['source_only'] is True and corpus['questions_references_or_predictions_read'] is False)
        structural=self.read('results/structural_audit_v14.json');self.mapping(structural['input_hashes'],'structure:review_input')
        self.check('structure:raw_corpus_crosslink',structural['external_structured_corpus']['sha256']==corpus['external_structured_corpus_sha256'])
        self.check('structure:gate_and_review_limit',structural['gate_recommendation']['accepted_false_header_or_unit_stop_triggered'] is False and structural['provenance']['independent_human_gold'] is False)
        retrieval=self.read(self.active_retrieval)
        for path,key in [(self.active_retriever,'script_sha256'),('scripts/retrieve_paired_v13.py','lexical_helper_sha256'),('data/structured_reader_v14/corpus_manifest.json','structured_manifest_sha256'),('data/paired_reader_v13/questions.json','questions_sha256'),('scripts/native_structured_reader_v14.py','token_counter_sha256'),('configs/structured_reader_main_v14.json','token_counter_config_sha256')]:self.binding(path,retrieval[key],'retrieval:'+key)
        self.retrieval={(x['question_id'],x['condition']):x for x in retrieval['records']}
        expected={(q,c) for q in self.questions for c in CONDITIONS}
        self.check('retrieval:complete_32_cells',len(self.retrieval)==len(retrieval['records'])==retrieval['context_count']==32 and set(self.retrieval)==expected and retrieval['question_count']==8 and retrieval['conditions']==list(CONDITIONS))
        self.check('retrieval:fixed_budgets_and_no_labels',retrieval['budgets']=={'evidence_words':960,'full_native_input_tokens':3500,'output_reserve_tokens':256,'context_tokens':4096} and retrieval['algorithm']['model_answers_or_reference_labels_read'] is False and retrieval['algorithm']['novelty_claim'] is False)
        main_config=self.read('configs/structured_reader_main_v14.json')
        for (qid,condition),row in self.retrieval.items():
            tag=qid+'__'+condition;cost=row['native_tokens'];identity=cost['tokenizer_identity']
            self.check('retrieval:budget:'+tag,0<cost['input_tokens']<=3500 and cost['input_tokens']+256<=4096 and row['evidence_word_count']<=960 and row['status']=='ready')
            self.check('retrieval:tokenizer_identity:'+tag,identity['model_sha256']==MODEL_SHA and identity['model_revision']==MODEL_REV and identity['runtime_files_sha256']==RUNTIME_SHA and identity['chat_template_kwargs']=={'enable_thinking':False} and identity['config_sha256_canonical']==canonical_hash(main_config) and identity['prompt_builder_sha256']==retrieval['token_counter_sha256'])
            self.check('retrieval:question_history_binding:'+tag,row['history_id']==self.questions[qid]['history_id'] and row['query_sha256']==__import__('hashlib').sha256(self.questions[qid]['question'].encode()).hexdigest() and all(s['history_id']==row['history_id'] for s in row['source_spans']))
            costs=row['cost_evaluations']
            self.check('retrieval:selected_cost_represented:'+tag,any(x['context_sha256']==row['context_sha256'] and x['input_tokens']==cost['input_tokens'] and x['rendered_prompt_sha256']==cost['rendered_prompt_sha256'] and x['token_ids_sha256']==cost['token_ids_sha256'] and x['fits'] for x in costs))
        requests=self.optional(self.active_requests,'requests:active_layout_manifest_pending')
        if requests is None:return
        self.request_records={x['request_id']:x for x in requests['records']}
        self.binding('scripts/prepare_structured_study_v14.py',requests['builder_sha256'],'requests:builder')
        self.binding('data/paired_reader_v13/questions.json',requests['questions_sha256'],'requests:questions')
        self.check('requests:exact_context_dataset',requests['reference_labels_loaded'] is False and requests['contexts_sha256']==retrieval['external_contexts_sha256'] and len(self.request_records)==len(requests['records'])==32)
        desired=[]
        for i,q in enumerate(questions):
            for c in CONDITIONS[i%4:]+CONDITIONS[:i%4]:desired.append(q['question_id']+'__'+c)
        self.check('requests:frozen_rotating_order',list(self.request_records)==desired)
        for rid,row in self.request_records.items():
            source=self.retrieval[(row['question_id'],row['condition'])]
            self.check('requests:retrieval_context:'+rid,row['context_sha256']==source['context_sha256'] and row['history_id']==source['history_id'] and rid==row['question_id']+'__'+row['condition'])
        masked=self.optional(self.active_contexts,'contexts:active_layout_manifest_pending')
        if masked is None:return
        self.check('context_review:masked_32',len(masked['records'])==32 and masked['native_outputs_in_packet'] is False and masked['condition_labels_masked'] is True and {(x['question_id'],x['condition']) for x in masked['records']}==expected)
        for row in masked['records']:self.check('context_review:context_crosslink:'+row['case_id'],row['context_sha256']==self.retrieval[(row['question_id'],row['condition'])]['context_sha256'])
        self.request_manifest=requests;self.structural=structural
        if self.layout_revision:
            layout_audit=self.optional('results/structured_retrieval_layout_audit_v14.json','layout:source_audit_pending')
            if layout_audit:
                self.mixed_mapping(layout_audit['input_sha256'],'layout:source_audit_input')
                self.check('layout:source_audit_passed_without_answers',layout_audit['status']=='passed_exact_source_and_recorded_cost_checks' and layout_audit['native_answers_or_qa_references_read'] is False and layout_audit['model_calls']==0 and all(x['passed'] for x in layout_audit['checks']))
                self.check('layout:external_dataset_hashes',self.external_bindings[corpus['external_structured_corpus_path']]==corpus['external_structured_corpus_sha256'] and self.external_bindings[retrieval['external_contexts_path']]==retrieval['external_contexts_sha256'])

    def batch(self,name,count):
        directory=self.root/'data/native_structured_reader_v14'/f'{name}_attempt01'
        if not (directory/'frozen_inputs.json').exists():self.pending.append(name+':not_frozen');return
        f=self.read(directory/'frozen_inputs.json');self.frozen[name]=f;c=f['config']
        self.mapping(f['source_hashes'],name+':source')
        self.check(name+':config_matches',c==self.read(f'configs/structured_reader_{name}_v14.json'))
        self.check(name+':frozen_budget',f['completion_calls_at_freeze']==0 and f['parameters']==PARAMETERS and c['expected_requests']==count and c['maximum_input_tokens']==3500 and c['maximum_generated_tokens']==256 and c['maximum_context_tokens']==4096)
        self.check(name+':unique_order_no_reference_input',len(f['execution_order'])==len(set(f['execution_order']))==count and [x['request_id'] for x in f['request_projection']]==f['execution_order'] and f['reference_review_supplied_to_model'] is False)
        self.check(name+':selected_model',c['model']['sha256']==MODEL_SHA and c['model']['revision']==MODEL_REV and c['model']['bytes']==3143656608 and f['asset_binding']['selected_model']==c['model'] and f['asset_binding']['reader_config_sha256_canonical']==canonical_hash(c))
        backend=self.read('configs/model_backend_v09.json')
        self.check(name+':runtime_guard_binding',f['asset_binding']['runtime_files_sha256']==canonical_hash(backend['runtime_files'])==RUNTIME_SHA and f['asset_binding']['config_sha256']==digest(self.path('configs/model_backend_v09.json')))
        legacy=self.read('configs/paired_reader_controls_v13.json')
        self.check(name+':same_system_prose_native_nonthinking',c['system_prompt']==legacy['system_prompt'].removesuffix(' /no_think'))
        if name=='controls':
            self.binding('results/reader_control_reference_review_v14.json',f['reference_review_sha256'],'controls:review_frozen')
            reviewtime=datetime.fromisoformat(self.control_review['reviewed_at_utc'].replace('Z','+00:00')).timestamp()
            self.check('controls:review_before_freeze',reviewtime<=f['created_unix_seconds'])
        else:
            self.check('main:exact_frozen_requests',f['execution_order']==list(self.request_records) and f['request_sha256']==self.request_manifest['requests_sha256'])
        receipt=self.optional(f['receipt_path'],name+':not_started')
        if receipt is None:return
        self.binding(directory/'frozen_inputs.json',receipt['frozen_inputs_sha256'],name+':receipt_freeze')
        if (directory/'preflight_freeze.json').exists():
            pre=self.read(directory/'preflight_freeze.json');rows=self.read(directory/'rendered_prompts.json')
            self.binding(directory/'rendered_prompts.json',pre['projected_prompts_sha256'],name+':preflight_prompt_projection')
            self.check(name+':preflight_chronology',before_predictions(f,receipt,pre) and pre['frozen_inputs_sha256']==receipt['frozen_inputs_sha256'])
            self.check(name+':preflight_exact_order',[r['request_id'] for r in rows]==f['execution_order'])
            self.check(name+':preflight_external_hashes',receipt['external_artifacts']['rendered_prompts.json']['sha256']==pre['rendered_prompts_sha256'] and receipt['external_artifacts']['preflight.props.json']['sha256']==pre['props_sha256'])
            if name=='main':
                for row in rows:
                    qid,cond=row['request_id'].rsplit('__',1)
                    self.check('main:retrieval_exact_native_tokens:'+row['request_id'],tokens_match(self.retrieval[(qid,cond)]['native_tokens'],row['prompt']))
        else:rows=[];self.pending.append(name+':preflight_not_finished')
        if receipt['status']!='completed':
            self.pending.append(name+':'+receipt['status']);self.batch_results.append({'batch':name,'status':receipt['status'],'validated_responses':receipt['validated_responses']})
            self.check(name+':not_failed',receipt['status']=='started');return
        self.mapping(receipt['project_artifacts'],name+':artifact')
        self.check(name+':metadata_inventory',set(receipt['project_artifacts'])=={str(p.relative_to(self.root)) for p in directory.rglob('*') if p.is_file()})
        self.check(name+':completed_counts',all(receipt[k]==count for k in ['completion_calls_started','raw_responses_returned','validated_responses','completion_processes']) and receipt['preflight_processes']==1 and receipt['all_inputs_preflight_passed'])
        counts=[];outputs=[];traces=[];budget_failures=0
        for index in range(-1,count):
            prefix='preflight' if index<0 else f'process_{index:02d}';trace=self.read(directory/f'{prefix}.execution.json');traces.append(trace);self.process_ids.append(trace['process_id']);cmd=trace['command']
            self.check(name+':'+prefix+':guarded_process',trace['process_exit_code']==0 and not trace.get('watchdog_stop_reason') and trace['address_space_limit_bytes']==5905580032 and trace['cpu_seconds_limit']==3600 and trace['elapsed_seconds']<=1050 and trace['cgroup_resident_observed_peak_bytes']<=7516192768 and flags(cmd)=={'--host':'127.0.0.1','-c':'4096','-ngl':'0','--parallel':'1','-t':'4','-tb':'4','--chat-template-kwargs':'{"enable_thinking":false}','-ctk':'q8_0','-ctv':'q8_0','-fa':'on'} and '--no-context-shift' in cmd and '--no-warmup' in cmd)
            self.check(name+':'+prefix+':asset_identity',trace['model_sha256']==MODEL_SHA and trace['model_repo_revision']==MODEL_REV and trace['runtime_files_sha256']==RUNTIME_SHA and trace['selected_model']==c['model'] and trace['reader_config_sha256_canonical']==canonical_hash(c))
            self.check(name+':'+prefix+':external_trace_hash',receipt['external_artifacts'][prefix+'.execution.json']['sha256']==digest(directory/(prefix+'.execution.json')))
            if index<0:continue
            prompt=rows[index]['prompt'];request=self.read(directory/f'{prefix}.request.json');response=self.read(directory/f'{prefix}.response_projection.json');rid=rows[index]['request_id']
            self.check(name+':'+prefix+':parameters',{k:request[k] for k in PARAMETERS}==PARAMETERS and request['prompt_tokens']==prompt['input_tokens'] and request['prompt_token_ids_sha256']==prompt['input_token_ids_sha256'])
            self.check(name+':'+prefix+':full_cold_input',response['request_id']==rid and 0<prompt['input_tokens']<=3500 and prompt['input_tokens']+256<=4096 and response['tokens_evaluated']==prompt['input_tokens'] and response['truncated'] is False and response['timings']['cache_n']==0 and response['timings']['prompt_n']==prompt['input_tokens'])
            self.check(name+':'+prefix+':bounded_output',0<response['tokens_predicted']<=256 and response['timings']['predicted_n']==response['tokens_predicted'] and response['stop'] is True and response['stop_type'] in ['eos','word','limit'] and response['output_budget_failure']==(response['stop_type']=='limit'))
            self.check(name+':'+prefix+':metadata_only',not any(k in response for k in ['content','tokens','prompt']) and not any(k in prompt for k in ['rendered_prompt','input_token_ids']))
            self.native_rows[(name,rid)]={'content_sha256':response['content_sha256'],'native_response_file_sha256':receipt['external_artifacts'][prefix+'.response.json']['sha256'],'projection_sha256':digest(directory/f'{prefix}.response_projection.json'),'native_output_budget_failure':response['output_budget_failure'],'input_tokens':prompt['input_tokens'],'input_token_ids_sha256':prompt['input_token_ids_sha256'],'output_tokens':response['tokens_predicted']}
            counts.append(prompt['input_tokens']);outputs.append(response['tokens_predicted']);budget_failures+=int(response['output_budget_failure'])
        self.check(name+':output_budget_failure_count',receipt['output_budget_failures']==budget_failures)
        self.batch_results.append({'batch':name,'status':'completed','validated_responses':count,'output_budget_failures':budget_failures,'fresh_processes':len(traces),'input_token_range':[min(counts),max(counts)],'output_token_range':[min(outputs),max(outputs)],'peak_cgroup_resident_bytes':max(t['cgroup_resident_observed_peak_bytes'] for t in traces),'started_unix_seconds':receipt['started_unix_seconds'],'finished_unix_seconds':receipt['finished_unix_seconds'],'receipt_sha256':digest(self.path(f['receipt_path']))})

    def later_stages(self):
        gate=self.optional('results/reader_controls_v14.json','controls:grading_pending')
        if gate:
            self.gate(gate)
        main=self.optional('data/structured_reader_v14/main_execution_protocol.json','main:execution_protocol_pending')
        if main:
            bindings=main.get('input_sha256',main.get('input_hashes',{}));self.mapping(bindings,'main:preexecution')
            required=['results/reader_controls_v14.json','results/structural_audit_v14.json',self.active_retrieval,self.active_requests,self.active_contexts,'configs/structured_reader_main_v14.json']
            if self.layout_revision:required.append('results/structured_retrieval_layout_audit_v14.json')
            required.append('results/prepared_request_token_binding_v14.json')
            self.check('main:prerequisite_hashes_frozen',all(p in bindings for p in required))
            self.check('main:preexecution_zero_predictions_and_gates',main['completion_calls_beforefreeze']==0 and main['gates']['reader_passed'] is True and main['gates']['structural_context_only_gate_passed'] is True and main['gates']['prepared_native_token_hashes_match_all32'] is True)
            if 'main' in self.frozen:self.check('main:protocol_before_native_freeze',main['created_unix_seconds']<=self.frozen['main']['created_unix_seconds'])
            if 'main' in self.frozen:self.binding('data/structured_reader_v14/main_execution_protocol.json',self.frozen['main']['protocol_sha256'],'main:protocol_bound_by_native_freeze')
            token_check=self.read('results/prepared_request_token_binding_v14.json')
            self.check('prepared_requests:zero_completion_chronology',token_check['completion_calls']==0 and token_check['status']=='passed' and token_check['created_unix_seconds']<=token_check['finished_unix_seconds']<=main['created_unix_seconds'])
            self.check('prepared_requests:exact_requests_and_inputs',token_check['request_sha256']==self.request_manifest['requests_sha256'] and token_check['retrieval_sha256']==digest(self.path(self.active_retrieval)) and token_check['config_sha256']==digest(self.path('configs/structured_reader_main_v14.json')))
            self.check('prepared_requests:exact_order',[x['request_id'] for x in token_check['records']]==list(self.request_records))
            for row in token_check['records']:
                qid,condition=row['request_id'].rsplit('__',1)
                self.check('prepared_requests:exact_native_tokens:'+row['request_id'],tokens_match(self.retrieval[(qid,condition)]['native_tokens'],row))
        support=self.optional('results/structured_context_support_v14.json','contexts:support_grading_pending')
        if support:self.context_support(support)
        self.grading_clarification()
        for domain in ['plug','opera']:self.grading(domain)
        summary=self.optional('results/structured_study_summary_v14.json','summary:pending')
        if summary:self.summary_check(summary)

    def gate(self,gate):
        self.mapping(gate['input_sha256'],'controls:grade_input')
        rows=gate['controls'];critical=sum(len(x['critical_errors']) for x in rows)
        self.check('controls:gate_denominator',len(rows)==gate['denominator']==8 and {x['request_id'] for x in rows}==set(self.control_refs))
        self.check('controls:gate_arithmetic',gate['controls_passed']==sum(x['reference_complete_grounded'] for x in rows) and len(gate['critical_errors'])==gate['critical_error_count']==critical and gate['gate_passed']==(gate['controls_passed']>=7 and critical==0))
        self.check('controls:fact_count',sum(len(x['required_facts_correct']) for x in rows)==gate['required_facts_correct'] and gate['required_facts_denominator']==19)
        finished=next(x['finished_unix_seconds'] for x in self.batch_results if x['batch']=='controls' and x['status']=='completed')
        graded=datetime.fromisoformat(gate['graded_at_utc'].replace('Z','+00:00')).timestamp()
        self.check('controls:graded_after_completed',gate['native_completion_finished_unix_seconds']==finished and graded>=finished)
        if 'main' in self.frozen:self.check('main:gate_before_freeze',graded<=self.frozen['main']['created_unix_seconds'])
        if 'main' in self.frozen:self.check('main:control_gate_passed',gate['gate_passed'] is True)
        if 'native_receipt_sha256' in gate:self.binding('results/native_structured_controls_v14_receipt.json',gate['native_receipt_sha256'],'controls:grade_receipt')
        for row in gate['controls']:
            native=self.native_rows.get(('controls',row['request_id']))
            if native:self.check('controls:answer_crosslink:'+row['request_id'],row['content_sha256']==native['content_sha256'] and row['response_file_sha256']==native['native_response_file_sha256'] and row['native_output_budget_failure']==native['native_output_budget_failure'])

    def context_support(self,support):
        bindings=support.get('input_sha256',support.get('input_hashes',{}));self.mapping(bindings,'contexts:support_input')
        rows=support.get('cases',support.get('records',[]));manifest=self.optional(self.active_contexts,'contexts:active_layout_manifest_pending')
        if manifest is None:return
        indexed={x['case_id']:x for x in manifest['records']}
        self.check('contexts:support_packet_binding',support['input_packet']['sha256']==manifest['packet_sha256'])
        self.mixed_mapping({support['input_packet']['path']:support['input_packet']['sha256'],support['delegated_subreview']['path']:support['delegated_subreview']['sha256']},'contexts:external_packet')
        self.binding(support['initial_audit']['path'],support['initial_audit']['sha256'],'contexts:preserved_initial_audit')
        references={x['question_id']:x for domain in ['plug','opera'] for x in self.read(f'data/paired_reader_v13/references_{domain}.json')['references']}
        self.check('contexts:unchanged_embedded_references',support['embedded_reference_canonical_json_sha256']=={q:canonical_hash(r) for q,r in references.items()})
        self.check('contexts:recorded_support_counts',dict(Counter(x['support_status'] for x in rows))==support['summary']['support_counts'] and support['summary']['contexts']==32)
        self.check('contexts:source_support_distinct_from_answers',support['provenance']['native_v14_answers_read'] is False and support['provenance']['condition_mapping_read'] is False and support['provenance']['independent_human_gold'] is False)
        self.check('contexts:support_32_masked_cases',len(rows)==32 and {x['case_id'] for x in rows}==set(indexed))
        for row in rows:
            if 'context_sha256' in row:self.check('contexts:support_hash:'+row['case_id'],row['context_sha256']==indexed[row['case_id']]['context_sha256'])
            facts=row['required_fact_support'];expected={f['fact_id'] for f in references[row['question_id']]['required_facts']}
            self.check('contexts:required_facts:'+row['case_id'],len(facts)==len(expected) and {f['fact_id'] for f in facts}==expected)
            mapped=indexed[row['case_id']];self.support_rows[(mapped['question_id'],mapped['condition'])]=row

    def grading_clarification(self):
        path='results/grading_schema_clarification_v14.json'
        clarification=self.optional(path,'grading:clarification_pending')
        if clarification is None:return
        self.binding(path,'6ac331ef71e6cca02f94ea3b6de4cf7ca5692ff1308598014660ce4faddb6e3e','grading:preserved_clarification')
        self.binding('data/paired_reader_v13/scoring_contract.json',clarification['scoring_contract_sha256'],'grading:clarification_unchanged_contract')
        self.check('grading:clarification_preserves_inputs',clarification['reference_edits'] is False and clarification['raw_output_edits'] is False and clarification['main_native_settings_changed'] is False and set(clarification['applies_to'])=={'plug','opera'})
        recorded=datetime.fromisoformat(clarification['recorded_at_utc']).timestamp()
        main=next((b for b in self.batch_results if b['batch']=='main' and b['status']=='completed'),None)
        if main:self.check('grading:clarification_recorded_during_inference',main['started_unix_seconds']<recorded<main['finished_unix_seconds'])
        self.notes.append({'kind':'grading_schema_clarification','path':path,'recorded_at_utc':clarification['recorded_at_utc'],'timing':'During main inference; not a pre-generation freeze.','meaning':'The legacy supported_correct fact label tracks frozen-reference semantic correctness, while actual received-context support remains a separate citation/unsupported-assertion axis.','declaration_not_independently_certified':'Root reports no main answers inspected before clarification; this metadata audit cannot independently prove that statement.'})

    def grading(self,domain):
        mp=f'data/structured_reader_v14/grading_{domain}_manifest.json';gp=f'results/grades_structured_{domain}_v14.json'
        manifest=self.optional(mp,domain+':grading_manifest_pending');grades=self.optional(gp,domain+':grades_pending')
        if manifest is None or grades is None:return
        semantics=grades.get('schema_clarification',grades.get('status_semantics'))
        self.check(domain+':fact_label_clarification_disclosed',isinstance(semantics,str) and 'supported_correct' in semantics and 'reference' in semantics and 'context' in semantics)
        self.binding(f'data/paired_reader_v13/references_{domain}.json',manifest['reference_sha256'],domain+':unchanged_reference')
        self.binding('data/paired_reader_v13/scoring_contract.json',manifest['scoring_contract_sha256'],domain+':unchanged_contract')
        self.binding('scripts/prepare_structured_study_v14.py',manifest['script_sha256'],domain+':grading_builder')
        references={x['question_id']:x for x in self.read(f'data/paired_reader_v13/references_{domain}.json')['references']}
        cases={x['case_id']:x for x in grades['cases']};records=manifest['records']
        self.check(domain+':grade_packet_binding',grades['grading_packet_sha256']==manifest['packet_sha256'])
        self.check(domain+':all_four_conditions_per_reference',{(x['question_id'],x['condition']) for x in records}=={(q,c) for q in references for c in CONDITIONS})
        self.check(domain+':exact_16_masked_cases',len(records)==len(cases)==len(grades['cases'])==16 and set(cases)=={x['case_id'] for x in records} and manifest['condition_labels_masked_in_packet'] is True and manifest['statistical_blindness_claimed'] is False)
        for row in records:
            case=cases[row['case_id']];native=self.native_rows.get(('main',row['request_id']))
            self.check(domain+':request_id_scope:'+row['case_id'],row['request_id']==row['question_id']+'__'+row['condition'])
            if native:
                self.check(domain+':native_answer:'+row['case_id'],row['content_sha256']==native['content_sha256']==case['raw_answer_sha256'] and row['native_response_file_sha256']==native['native_response_file_sha256'] and row['response_projection_sha256']==native['projection_sha256'] and case['native_output_budget_failure']==native['native_output_budget_failure'])
            self.check(domain+':received_context:'+row['case_id'],case['question_id']==row['question_id'] and case['received_context_sha256']==row['context_sha256']==self.retrieval[(row['question_id'],row['condition'])]['context_sha256'])
            facts=case['required_fact_assessments'];expected={x['fact_id'] for x in references[row['question_id']]['required_facts']}
            self.check(domain+':complete_fact_denominator:'+row['case_id'],len(facts)==len(expected) and {x['fact_id'] for x in facts}==expected)
            self.check(domain+':explicit_axes:'+row['case_id'],all(type(case[k]) is bool for k in ['reference_content_correct','scope_correct','citation_grounded','format_ok','appropriate_abstention','native_output_budget_failure']))
            self.graded_cases[row['request_id']]=case

    def summary_check(self,summary):
        self.mapping(summary['input_sha256'],'summary:input')
        review=self.read('results/summary_implementation_review_v14.json');self.mapping(review['input_sha256'],'summary:implementation_review')
        self.binding('scripts/summarize_structured_study_v14.py',review['reviewed_aggregation_sha256'],'summary:reviewed_source')
        axes=('reference_content_correct','scope_correct','citation_grounded','format_ok','appropriate_abstention','native_output_budget_failure')
        rows=summary['records'];indexed={(r['question_id'],r['condition']):r for r in rows}
        self.check('summary:exact_32_denominator',len(rows)==len(indexed)==summary['native_response_count']==32 and summary['question_count']==8 and summary['histories']==2 and summary['conditions']==list(CONDITIONS) and set(indexed)==set(self.retrieval))
        for (qid,condition),r in indexed.items():
            rid=qid+'__'+condition;grade=self.graded_cases[rid];native=self.native_rows[('main',rid)];ctx=self.retrieval[(qid,condition)]
            self.check('summary:case_identity:'+rid,r['request_id']==rid and r['case_id']==grade['case_id'] and r['history_id']==self.questions[qid]['history_id'] and r['content_sha256']==grade['raw_answer_sha256']==native['content_sha256'] and r['context_sha256']==grade['received_context_sha256']==ctx['context_sha256'])
            self.check('summary:unchanged_axes_and_budget_failure:'+rid,all(r[a]==grade[a] for a in axes) and r['joint_content_scope_citation']==all(grade[a] for a in axes[:3]) and r['native_output_budget_failure']==native['native_output_budget_failure'])
            self.check('summary:context_support:'+rid,r['context_support']==self.support_rows[(qid,condition)]['support_status'])
            self.check('summary:native_usage:'+rid,all(r[k]==native[k] for k in ['input_tokens','input_token_ids_sha256','output_tokens']) and r['evidence_words']==ctx['evidence_word_count'] and r['dropped_seed_count']==len(ctx['dropped_seeds']) and r['both_versions_represented']==ctx['both_versions_represented'])
            self.check('summary:fact_denominator:'+rid,r['required_fact_count']==len(grade['required_fact_assessments']) and r['fact_status_counts']==dict(Counter(x['status'] for x in grade['required_fact_assessments'])) and r['unsupported_assertion_count']==len(grade['unsupported_assertions']))
        aggregate={r['condition']:r for r in summary['aggregation']}
        self.check('summary:four_condition_rows',len(summary['aggregation'])==len(aggregate)==4 and set(aggregate)==set(CONDITIONS))
        for condition,value in aggregate.items():
            cells=[r for r in rows if r['condition']==condition];complete=[r for r in cells if r['context_support']=='complete']
            self.check('summary:aggregate_axes:'+condition,value['questions']==len(cells)==8 and all(value[a]==sum(r[a] for r in cells) for a in axes+('joint_content_scope_citation',)))
            self.check('summary:aggregate_context_support:'+condition,value['context_support_counts']==dict(Counter(r['context_support'] for r in cells)) and value['complete_context_cases']==len(complete) and value['complete_context_joint_passes']==sum(r['joint_content_scope_citation'] for r in complete))
            self.check('summary:aggregate_usage:'+condition,value['unsupported_assertions']==sum(r['unsupported_assertion_count'] for r in cells) and value['dropped_seeds']==sum(r['dropped_seed_count'] for r in cells) and value['input_token_range']==[min(r['input_tokens'] for r in cells),max(r['input_tokens'] for r in cells)] and value['evidence_word_range']==[min(r['evidence_words'] for r in cells),max(r['evidence_words'] for r in cells)])
        old_summary=self.read('results/paired_study_summary_v13.json')
        old={(r['question_id'],r['condition']):r for r in old_summary['records'] if r['batch']=='layout'}
        contrasts={(r['question_id'],r['condition']):r for r in summary['same_context_reader_contrasts']}
        expected={(q,c) for q in self.questions for c in ['ordinary','paired']}
        self.check('summary:16_same_context_reader_contrasts',len(contrasts)==len(summary['same_context_reader_contrasts'])==16 and set(contrasts)==expected)
        for key,r in contrasts.items():
            previous=old[key];current=indexed[key]
            self.check('summary:same_source_bytes:'+current['request_id'],r['identical_context_sha256']==previous['context_sha256']==current['context_sha256'])
            self.check('summary:same_context_axes:'+current['request_id'],all(r[a]=={'v13':previous[a],'v14':current[a]} for a in axes[:3]+('joint_content_scope_citation',)))
        token_groups={}
        for r in rows:token_groups.setdefault(r['input_token_ids_sha256'],[]).append(r)
        expected_duplicates={frozenset(r['request_id'] for r in cells):len({r['content_sha256'] for r in cells})==1 for cells in token_groups.values() if len(cells)>1}
        duplicates={frozenset(r['request_ids']):r['identical_content'] for r in summary['duplicate_input_groups']}
        self.check('summary:duplicate_input_groups',len(duplicates)==len(summary['duplicate_input_groups']) and duplicates==expected_duplicates and summary['unique_native_input_token_sequences']==len(token_groups))
        self.semantic_disagreements=[r['request_id'] for r in rows if r['joint_content_scope_citation'] and r['context_support']!='complete']
        self.check('summary:semantic_disagreements_exposed',summary['joint_pass_with_incomplete_context_review']==self.semantic_disagreements)
        main=next(b for b in self.batch_results if b['batch']=='main' and b['status']=='completed')
        self.check('summary:actual_wall_time',summary['native_main_wall_seconds']==main['finished_unix_seconds']-main['started_unix_seconds'])

    def run(self):
        self.preparation()
        self.batch('controls',8);self.batch('main',32)
        self.later_stages()
        self.check('all_observed_native_process_ids_unique',len(self.process_ids)==len(set(self.process_ids)))
        self.check('graded_cases_not_overcounted',len(self.graded_cases)<=32)
        if all(self.path(f'results/grades_structured_{d}_v14.json').exists() and self.path(f'data/structured_reader_v14/grading_{d}_manifest.json').exists() for d in ['plug','opera']):
            self.check('exact_final_32_graded_requests',len(self.graded_cases)==32 and set(self.graded_cases)==set(self.request_records))
        failed=[x for x in self.checks if not x['passed']]
        return {'schema_version':'structured_execution_audit_v14','status':'failed' if failed else ('pending' if self.pending else 'passed'),'checked_at_utc':datetime.now(timezone.utc).isoformat(),'auditor_sha256':digest(Path(__file__)),'auditor_helper_sha256':digest(Path(__file__).with_name('verify_paired_study_v13.py')),'mode':'portable_metadata_only','model_calls':0,'network_calls':0,'provenance':'Authored by the native collector author; this is not independent implementation review, human gold or semantic recertification.','limits':['Portable metadata verifies recorded hashes/counts/crosslinks, not external raw text, native token arrays, template application or answer semantics.','Source-only claims are declared lineage plus frozen code/hash bindings; the audit does not prove code had no unrecorded inputs.','Metadata source-only outputs without timestamps derive pre-prediction ordering from their explicit binding in the main native freeze; filesystem modification times are not treated as experimental timestamps.','Native per-call start times and raw props are external; optional external verification checks those retained artifacts.'],'inputs_sha256':self.inputs,'check_count':len(self.checks),'checks':self.checks,'failed_checks':failed,'pending_stages':self.pending,'batches':self.batch_results,'graded_case_count':len(self.graded_cases),'external_hash_assertions_not_read_in_portable_mode':self.external_bindings,'active_retrieval':self.active_retrieval,'layout_revision_active':self.layout_revision,'joint_pass_context_review_disagreements':self.semantic_disagreements,'recorded_protocol_notes':self.notes}


def external_checks(audit):
    """Read retained artifacts, then replay the frozen collector's verify only."""
    from native_structured_reader_v14 import verify,build_prompt
    from hashlib import sha256
    results={}
    for path,expected in audit.external_bindings.items():
        if digest(path)!=expected:raise ValueError('External source-audit input changed: '+path)
    results['source_audit_external_hashes_verified']=len(audit.external_bindings)
    for batch in audit.batch_results:
        if batch['status']!='completed':continue
        name=batch['batch'];f=audit.frozen[name];folder=Path(f['external_path'])
        results[name]=verify(folder)
        properties=load(folder/'preflight.props.json')
        template_sha=sha256(properties['chat_template'].encode()).hexdigest()
        if name=='main':
            retrieval=load(audit.path(audit.active_retrieval));ctxpath=Path(retrieval['external_contexts_path'])
            if digest(ctxpath)!=retrieval['external_contexts_sha256']:raise ValueError('External selected contexts changed.')
            contexts={(x['question_id'],x['condition']):x for x in [json.loads(line) for line in ctxpath.read_text().splitlines() if line.strip()]}
            requests=load(folder/'request.json')
            if len(contexts)!=32 or len(requests)!=32:raise ValueError('External context/request coverage changed.')
            for item in requests:
                qid,condition=item['request_id'].rsplit('__',1);source=contexts[(qid,condition)]
                expected=build_prompt(audit.questions[qid]['question'],source['context'])
                if item['prompt']!=expected:raise ValueError('Frozen request no longer equals shared builder/context.')
                selected=audit.retrieval[(qid,condition)]
                if selected['native_tokens']['tokenizer_identity']['chat_template_sha256']!=template_sha:raise ValueError('Selected-cost template differs from executed template.')
                if sha256(source['context'].encode()).hexdigest()!=selected['context_sha256']:raise ValueError('Selected context hash differs.')
            results[name]['selected_context_request_template_crosslinks']=32
        results[name]['native_chat_template_sha256']=template_sha
    return results


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,default=ROOT);p.add_argument('--external',action='store_true');p.add_argument('--output',type=Path);a=p.parse_args()
    audit=Audit(a.root);report=audit.run()
    if a.external:
        if a.root.resolve()!=ROOT:raise ValueError('External mode requires canonical checkout; portable mode supports relocation.')
        report['mode']='portable_plus_existing_external_artifacts'
        report['external_verification']=external_checks(audit)
    if a.output:
        with a.output.open('x') as f:json.dump(report,f,indent=2);f.write('\n')
    print(json.dumps({'status':report['status'],'checks':report['check_count'],'failed':report['failed_checks'],'pending':report['pending_stages'],'batches':report['batches']},indent=2))
    return int(report['status']=='failed')


if __name__=='__main__':sys.exit(main())
