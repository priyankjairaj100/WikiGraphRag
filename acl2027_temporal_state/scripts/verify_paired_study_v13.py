#!/usr/bin/env python3
"""Portable metadata audit; optional existing-external-artifact verification only.

Never launches a model or accesses public network. Metadata mode cannot re-read
source text, native token arrays or raw native responses that are not distributed.
"""
from __future__ import annotations
import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sys

DEFAULT_ROOT = Path(__file__).resolve().parents[1]
BATCHES = [('controls', 2), ('main', 16), ('layout', 16)]
PARAMETERS = {'n_predict':256,'temperature':-1.0,'seed':7,'n_probs':0,
              'post_sampling_probs':False,'cache_prompt':False,'return_tokens':True,
              'stream':False,'repeat_penalty':1.0,'presence_penalty':0.0,
              'frequency_penalty':0.0,'logit_bias':[],'samplers':['temperature'],
              'grammar':'','stop':[],'ignore_eos':False}


def digest(path):
    with Path(path).open('rb') as stream:
        value=sha256()
        for block in iter(lambda:stream.read(1024*1024),b''):value.update(block)
    return value.hexdigest()


def canonical_hash(value):
    return sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def load(path):return json.loads(Path(path).read_text())


class Audit:
    def __init__(self,root):
        self.root=Path(root).resolve();self.checks=[];self.pending=[];self.inputs={}
        self.batch_results=[];self.native_rows={};self.process_ids=[];self.graded_cases={}

    def path(self,name):
        """Rebase only known historical project paths, never open other absolutes."""
        candidate=Path(name)
        if candidate.is_absolute():
            if candidate.is_relative_to(self.root):
                candidate=candidate.relative_to(self.root)
            else:
                marker='/acl2027_temporal_state/'
                if marker not in str(candidate):raise ValueError('Nonproject absolute path in metadata audit: '+str(candidate))
                candidate=Path(str(candidate).split(marker,1)[1])
        result=(self.root/candidate).resolve()
        if not result.is_relative_to(self.root):raise ValueError('Project-relative path escapes checkout.')
        return result

    def read(self,name):
        path=self.path(name);self.inputs[str(path.relative_to(self.root))]=digest(path)
        return load(path)

    def check(self,name,ok,details=None):
        result={'check':name,'passed':bool(ok)}
        if details is not None:result['details']=details
        self.checks.append(result)

    def binding(self,name,expected,label):
        path=self.path(name)
        actual=digest(path) if path.is_file() else None
        if actual is not None:self.inputs[str(path.relative_to(self.root))]=actual
        self.check(label,actual==expected)

    def mapping(self,mapping,label):
        for name,value in mapping.items():self.binding(name,value,label+':'+str(self.path(name).relative_to(self.root)))

    def batch(self,name,count):
        directory=self.root/'data/native_paired_reader_v13'/f'{name}_attempt01'
        frozen=self.read(str(directory/'frozen_inputs.json'))
        self.mapping(frozen['source_hashes'],name+':frozen_source')
        self.check(name+':frozen_call_and_parameter_budget',frozen['completion_calls_at_freeze']==0 and frozen['parameters']==PARAMETERS and frozen['config']['expected_requests']==count and frozen['config']['maximum_input_tokens']==3500 and frozen['config']['maximum_generated_tokens']==256 and frozen['config']['maximum_context_tokens']==4096)
        self.check(name+':frozen_order_and_no_reference_input',len(frozen['execution_order'])==count and len(set(frozen['execution_order']))==count and [x['request_id'] for x in frozen['request_projection']]==frozen['execution_order'] and frozen['reference_review_supplied_to_model'] is False)
        receipt_path=self.path(frozen['receipt_path'])
        if not receipt_path.exists():
            self.pending.append(name+':execution_not_started');return
        receipt=self.read(str(receipt_path))
        if receipt['status']!='completed':
            self.pending.append(name+':'+receipt['status'])
            self.batch_results.append({'batch':name,'status':receipt['status'],'validated_responses':receipt['validated_responses']})
            return
        self.binding(directory/'frozen_inputs.json',receipt['frozen_inputs_sha256'],name+':receipt_freeze_binding')
        self.mapping(receipt['project_artifacts'],name+':project_artifact')
        inventory={str(p.relative_to(self.root)) for p in directory.rglob('*') if p.is_file()}
        self.check(name+':exact_metadata_inventory',set(receipt['project_artifacts'])==inventory)
        self.check(name+':completed_counts',all(receipt[k]==count for k in ['completion_calls_started','raw_responses_returned','validated_responses','completion_processes']) and receipt['preflight_processes']==1 and receipt['all_inputs_preflight_passed'])
        preflight=self.read(str(directory/'preflight_freeze.json'))
        prompts=self.read(str(directory/'rendered_prompts.json'))
        self.binding(directory/'rendered_prompts.json',preflight['projected_prompts_sha256'],name+':preflight_projection_hash')
        self.check(name+':preflight_prior_to_completions',frozen['created_unix_seconds']<=receipt['started_unix_seconds']<=preflight['created_unix_seconds']<=receipt['finished_unix_seconds'] and preflight['completion_calls_at_freeze']==0 and preflight['frozen_inputs_sha256']==receipt['frozen_inputs_sha256'])
        self.check(name+':preflight_order',[x['request_id'] for x in prompts]==frozen['execution_order'])
        self.check(name+':external_preflight_hash_crosslinks',receipt['external_artifacts']['rendered_prompts.json']['sha256']==preflight['rendered_prompts_sha256'] and receipt['external_artifacts']['preflight.props.json']['sha256']==preflight['props_sha256'])
        backend=self.read('configs/model_backend_v09.json')
        self.check(name+':asset_metadata_binding',frozen['asset_binding']['config_sha256']==digest(self.root/'configs/model_backend_v09.json') and frozen['asset_binding']['assets']==backend['assets'] and frozen['asset_binding']['runtime_files_sha256']==canonical_hash(backend['runtime_files']))
        input_counts=[];output_counts=[];budget_count=0;traces=[]
        for index in range(-1,count):
            prefix='preflight' if index==-1 else f'process_{index:02d}'
            trace=self.read(str(directory/f'{prefix}.execution.json'));traces.append(trace)
            command=trace['command'];self.process_ids.append(trace['process_id'])
            controls={flag:command[command.index(flag)+1] for flag in ['--host','-c','-ngl','--parallel','-t','-tb']}
            self.check(name+':'+prefix+':fresh_guarded_process',trace['process_exit_code']==0 and not trace.get('watchdog_stop_reason') and trace['address_space_limit_bytes']==5905580032 and trace['cpu_seconds_limit']==3600 and trace['elapsed_seconds']<=1050 and trace['cgroup_resident_observed_peak_bytes']<=7*1024**3 and controls=={'--host':'127.0.0.1','-c':'4096','-ngl':'0','--parallel':'1','-t':'4','-tb':'4'} and '--no-context-shift' in command and '--no-warmup' in command)
            self.check(name+':'+prefix+':pinned_model_runtime',trace['model_key']=='large' and trace['model_sha256']==backend['model_sha256'] and trace['model_repo_revision']==backend['model_repo_revision'] and trace['config_sha256']==frozen['asset_binding']['config_sha256'] and trace['runtime_files_sha256']==frozen['asset_binding']['runtime_files_sha256'])
            self.check(name+':'+prefix+':external_trace_binding',receipt['external_artifacts'][f'{prefix}.execution.json']['sha256']==digest(directory/f'{prefix}.execution.json'))
            if index<0:continue
            prompt=prompts[index]['prompt'];request=self.read(str(directory/f'{prefix}.request.json'));response=self.read(str(directory/f'{prefix}.response_projection.json'))
            self.check(name+':'+prefix+':exact_request_parameters',{k:request[k] for k in PARAMETERS}==PARAMETERS and request['prompt_token_ids_sha256']==prompt['input_token_ids_sha256'] and request['prompt_tokens']==prompt['input_tokens'])
            self.check(name+':'+prefix+':complete_cold_input',response['request_id']==prompts[index]['request_id'] and 0<prompt['input_tokens']<=3500 and prompt['input_tokens']+256<=4096 and response['tokens_evaluated']==prompt['input_tokens'] and response['truncated'] is False and response['timings']['cache_n']==0 and response['timings']['prompt_n']==prompt['input_tokens'])
            self.check(name+':'+prefix+':bounded_output',0<response['tokens_predicted']<=256 and response['timings']['predicted_n']==response['tokens_predicted'] and response['stop'] is True and response['stop_type'] in ['eos','word','limit'] and response['output_budget_failure']==(response['stop_type']=='limit'))
            self.check(name+':'+prefix+':text_and_tokens_withheld',not any(key in response for key in ['content','tokens','prompt']) and not any(key in prompt for key in ['rendered_prompt','input_token_ids']))
            self.native_rows[(name,response['request_id'])]={'input_token_ids_sha256':prompt['input_token_ids_sha256'],'projection_path':str((directory/f'{prefix}.response_projection.json').relative_to(self.root)),'projection_sha256':digest(directory/f'{prefix}.response_projection.json'),'content_sha256':response['content_sha256'],'native_response_file_sha256':receipt['external_artifacts'][f'{prefix}.response.json']['sha256']}
            input_counts.append(prompt['input_tokens']);output_counts.append(response['tokens_predicted']);budget_count+=response['output_budget_failure']
        self.check(name+':budget_failure_count',receipt['output_budget_failures']==budget_count)
        duplicate_groups={}
        for (batch,rid),row in self.native_rows.items():
            if batch==name:duplicate_groups.setdefault(row['input_token_ids_sha256'],[]).append((rid,row['content_sha256']))
        duplicates=[{'request_ids':[rid for rid,_ in group],'identical_content':len({sha for _,sha in group})==1} for group in duplicate_groups.values() if len(group)>1]
        self.batch_results.append({'batch':name,'status':'completed','validated_responses':count,'output_budget_failures':budget_count,'input_token_range':[min(input_counts),max(input_counts)],'output_token_range':[min(output_counts),max(output_counts)],'fresh_processes':len(traces),'unique_input_token_sequences':len(duplicate_groups),'duplicate_input_groups':duplicates,'peak_cgroup_resident_bytes':max(x['cgroup_resident_observed_peak_bytes'] for x in traces),'receipt_sha256':digest(receipt_path),'started_unix_seconds':receipt['started_unix_seconds'],'finished_unix_seconds':receipt['finished_unix_seconds']})

    def study(self):
        for path in ['data/paired_reader_v13/main_execution_protocol.json','data/paired_reader_v13/layout_execution_protocol.json','results/paired_reference_review_v13.json']:
            value=self.read(path);self.mapping(value['input_sha256'],path+':input')
        questions=self.read('data/paired_reader_v13/questions.json')['questions'];ids=[x['question_id'] for x in questions]
        self.check('eight_unique_questions_two_histories',len(ids)==len(set(ids))==8 and len({x['history_id'] for x in questions})==2)
        review=self.read('results/paired_reference_review_v13.json')
        self.check('eight_reference_reviews',review['questions_reviewed']==8 and {x['question_id'] for x in review['assessments']}==set(ids))
        gate=self.read('results/reader_controls_v13.json')
        self.binding('results/native_controls_v13_receipt.json',gate['native_receipt_sha256'],'controls:scoring_receipt_binding')
        self.check('controls_gate_passed_before_main',gate['gate_passed'] and gate['controls_passed']==2 and len(gate['controls'])==2)
        for control in gate['controls']:
            native=self.native_rows.get(('controls',control['request_id']))
            if native:self.check('controls:content_binding:'+control['request_id'],native['content_sha256']==control['content_sha256'] and native['native_response_file_sha256']==control['response_file_sha256'])
        corpus=self.read('data/paired_reader_v13/corpus_manifest.json')
        docs=corpus['documents'];pages=[p for d in docs for p in d['pages']];chunks=[c for p in pages for c in p['chunks']]
        self.check('corpus_manifest_counts',len(docs)==corpus['source_pdf_count']==4 and len(pages)==corpus['pdf_page_count']==550 and len(chunks)==corpus['chunk_count']==2306 and len({c['chunk_id'] for c in chunks})==2306)
        chunkmap={c['chunk_id']:c for c in chunks}
        retrieval=self.read('results/paired_retrieval_v13.json');layout=self.read('results/layout_contexts_v13.json')
        self.binding('data/paired_reader_v13/corpus_manifest.json',retrieval['corpus_manifest_sha256'],'retrieval:corpus_binding')
        self.binding('data/paired_reader_v13/questions.json',retrieval['question_file_sha256'],'retrieval:questions_binding')
        self.check('retrieval_sixteen_contexts',retrieval['context_count']==len(retrieval['records'])==16 and retrieval['question_count']==8)
        self.check('layout_sixteen_contexts',layout['context_count']==len(layout['records'])==16 and layout['all_invariants_pass'])
        context_rows={}
        for batch,value in [('main',retrieval),('layout',layout)]:
            manifest=self.read('data/paired_reader_v13/'+('request_manifest.json' if batch=='main' else 'layout_request_manifest.json'))
            frozen=self.read('data/native_paired_reader_v13/'+batch+'_attempt01/frozen_inputs.json')
            self.check(batch+':request_context_freeze_binding',manifest['request_file_sha256']==frozen['request_sha256'] and manifest['contexts_file_sha256']==value['external_contexts_sha256'] and manifest['question_file_sha256']==digest(self.root/'data/paired_reader_v13/questions.json') and len(manifest['requests'])==16 and [r['request_id'] for r in manifest['requests']]==frozen['execution_order'])
            mapping={r['question_id']+'__'+r['condition']:r for r in value['records']};context_rows[batch]=mapping
            self.check(batch+':condition_coverage',set(mapping)=={q+'__'+condition for q in ids for condition in ['ordinary','paired']})
            for row in manifest['requests']:
                ctx=mapping[row['request_id']]
                self.check(batch+':context_binding:'+row['request_id'],row['context_sha256']==ctx['context_sha256'] and ctx['chunk_count']==len(ctx['selected'])==len(ctx['selected_chunk_ids'])==6 and ctx['evidence_word_count']<=960)
                self.check(batch+':selected_chunk_membership:'+row['request_id'],all(s['chunk_id'] in chunkmap and s['text_sha256']==chunkmap[s['chunk_id']]['text_sha256'] for s in ctx['selected']))
        for key,old in context_rows['main'].items():
            new=context_rows['layout'][key]
            self.check('layout:unchanged_selection:'+key,old['selected']==new['selected'] and old['selected_chunk_ids']==new['selected_chunk_ids'] and old['evidence_word_count']==new['evidence_word_count'])
        grade_packets=[];grade_results=[]
        for path in sorted((self.root/'data/paired_reader_v13').glob('grading_*_manifest.json')):
            packet=self.read(str(path));batch=packet['batch'];domain=packet['domain'];rows=packet['records'];grade_packets.append({'batch':batch,'domain':domain,'records':len(rows),'manifest_sha256':digest(path)})
            self.binding('data/paired_reader_v13/references_'+domain+'.json',packet['reference_sha256'],'grading:'+batch+':'+domain+':reference_binding')
            self.binding('data/paired_reader_v13/scoring_contract.json',packet['scoring_contract_sha256'],'grading:'+batch+':'+domain+':scoring_binding')
            self.check('grading:'+batch+':'+domain+':eight_unique_cells',len(rows)==8 and len({r['request_id'] for r in rows})==8 and len({r['case_id'] for r in rows})==8)
            for row in rows:
                native=self.native_rows.get((batch,row['request_id']))
                if native:self.check('grading:unchanged_native_answer:'+batch+':'+row['request_id'],all(row[k]==native[k] for k in ['native_response_file_sha256','content_sha256']) and row['response_projection_sha256']==native['projection_sha256'] and row['context_sha256']==context_rows[batch][row['request_id']]['context_sha256'])
                else:self.pending.append('grading:native_missing:'+batch+':'+row['request_id'])
            grade_path=self.root/f'results/grades_{batch}_{domain}_v13.json'
            if not grade_path.exists():
                self.pending.append('grading:result_not_present:'+batch+':'+domain)
                continue
            grades=self.read(str(grade_path));cases=grades['cases'];case_map={row['case_id']:row for row in rows}
            references=self.read('data/paired_reader_v13/references_'+domain+'.json')['references']
            reference_map={ref['question_id']:ref for ref in references}
            self.check('grades:'+batch+':'+domain+':eight_case_coverage',len(cases)==8 and {case['case_id'] for case in cases}==set(case_map))
            self.check('grades:'+batch+':'+domain+':packet_binding',grades['grading_packet_sha256']==packet['packet_sha256'])
            if 'scoring_contract_sha256_canonical_json' in grades:
                self.check('grades:'+batch+':'+domain+':scoring_canonical_binding',grades['scoring_contract_sha256_canonical_json']==canonical_hash(self.read('data/paired_reader_v13/scoring_contract.json')))
            elif 'scoring_contract_sha256' in grades:
                self.binding('data/paired_reader_v13/scoring_contract.json',grades['scoring_contract_sha256'],'grades:'+batch+':'+domain+':scoring_binding')
            for case in cases:
                record=case_map[case['case_id']];ref=reference_map[case['question_id']]
                self.graded_cases[(batch,record['request_id'])]=case
                self.check('grades:'+batch+':'+case['case_id']+':unchanged_answer_context',case['question_id']==record['question_id'] and case['raw_answer_sha256']==record['content_sha256'] and case['received_context_sha256']==record['context_sha256'])
                facts=case['required_fact_assessments']
                self.check('grades:'+batch+':'+case['case_id']+':required_fact_coverage',len(facts)==len(ref['required_facts']) and {fact['fact_id'] for fact in facts}=={fact['fact_id'] for fact in ref['required_facts']} and all(fact['status'] in ['supported_correct','missing','incorrect','ambiguous'] for fact in facts))
                axes=['reference_content_correct','scope_correct','citation_grounded','format_ok','appropriate_abstention','native_output_budget_failure']
                self.check('grades:'+batch+':'+case['case_id']+':separate_boolean_axes',all(type(case[key]) is bool for key in axes))
            grade_results.append({'batch':batch,'domain':domain,'case_count':len(cases),'result_sha256':digest(grade_path)})
        expected_packets={(batch,domain) for batch in ['main','layout'] for domain in ['plug','opera']}
        observed_packets=[(packet['batch'],packet['domain']) for packet in grade_packets]
        self.check('grading:only_expected_unique_packets',len(set(observed_packets))==len(observed_packets) and set(observed_packets)<=expected_packets)
        if set(observed_packets)!=expected_packets:self.pending.append('grading:four_domain_packets_not_all_present')
        self.check('fresh_process_ids_across_complete_batches',len(self.process_ids)==len(set(self.process_ids)))
        completed={r['batch']:r for r in self.batch_results if r['status']=='completed'}
        if 'main' in completed:
            main_start=completed['main']['started_unix_seconds']
            self.check('review_and_controls_scored_before_main',datetime.fromisoformat(review['frozen_at_utc']).timestamp()<main_start and datetime.fromisoformat(gate['evaluated_at_utc']).timestamp()<main_start)
        if 'main' in completed and 'layout' in completed:self.check('main_and_layout_process_batches_sequential',completed['main']['finished_unix_seconds']<completed['layout']['started_unix_seconds'])
        for result in self.batch_results:
            for group in result.get('duplicate_input_groups',[]):
                if not group['identical_content']:continue
                request_ids=group['request_ids']
                keys=[(result['batch'],rid) for rid in request_ids]
                if not all(key in self.graded_cases for key in keys):continue
                if len({rid.rsplit('__',1)[0] for rid in request_ids})!=1:continue
                cases=[self.graded_cases[key] for key in keys]
                axes=['reference_content_correct','scope_correct','citation_grounded','format_ok','appropriate_abstention','native_output_budget_failure']
                self.check('identical_answer_grading_consistency:'+result['batch']+':'+request_ids[0],all(all(case[axis]==cases[0][axis] for axis in axes) and {fact['fact_id']:fact['status'] for fact in case['required_fact_assessments']}=={fact['fact_id']:fact['status'] for fact in cases[0]['required_fact_assessments']} for case in cases))
        summaries=[]
        axes=['reference_content_correct','scope_correct','citation_grounded','format_ok','appropriate_abstention','native_output_budget_failure']
        for filename in ['results/paired_main_summary_v13.json','results/paired_study_initial_v13.json','results/paired_study_summary_v13.json']:
            if not (self.root/filename).exists():continue
            summary=self.read(filename);self.mapping(summary['input_sha256'],filename+':input')
            rows=summary['records'];batches=summary['batches'];overlay_changes={}
            if filename=='results/paired_study_summary_v13.json':
                overlay=self.read('results/reader_adjudication_overlay_v13.json')
                self.mapping(overlay['input_sha256'],'adjudication:bound_input')
                initial=self.read('results/paired_study_initial_v13.json')
                self.check('adjudication:single_explicit_case_only',len(overlay['changes'])==1 and overlay['changes'][0]['case_id']=='case_87126f917505' and overlay['changes'][0]['batch']=='layout' and overlay['changes'][0]['question_id']=='plug_v13_q2' and overlay['changes'][0]['field_changes']=={'reference_content_correct':{'old':True,'new':False}} and overlay['changes'][0]['fact_changes']==[{'fact_id':'amount','old':'supported_correct','new':'ambiguous'}] and overlay['reference_edits']==overlay['native_response_edits']==0)
                self.check('adjudication:summary_overlay_binding',summary['schema_version']=='paired_study_adjudicated_summary_v0.13' and summary['adjudication']['overlay_sha256']==digest(self.root/'results/reader_adjudication_overlay_v13.json') and summary['adjudication']['changes']==overlay['changes'] and summary['adjudication']['initial_summary_sha256']==digest(self.root/'results/paired_study_initial_v13.json'))
                sensitivity=summary['initial_grader_sensitivity']
                self.check('adjudication:initial_sensitivity_preserved',sensitivity['initial_summary_sha256']==digest(self.root/'results/paired_study_initial_v13.json') and sensitivity['aggregation']==initial['aggregation'] and sensitivity['all_32_cases_retained'] and sensitivity['joint_counts_unchanged'])
                overlay_changes={(change['batch'],change['case_id']):change for change in overlay['changes']}
                unchanged=[row for row in rows if (row['batch'],row['case_id']) not in overlay_changes]
                initial_unchanged=[row for row in initial['records'] if (row['batch'],row['case_id']) not in overlay_changes]
                self.check('adjudication:other_31_rows_unchanged',unchanged==initial_unchanged and len(unchanged)==31)
                for old,new,delta in zip(initial['aggregation'],summary['aggregation'],sensitivity['final_minus_initial']):
                    self.check('adjudication:explicit_count_sensitivity:'+new['batch']+':'+new['condition'],old['questions']==new['questions']==delta['questions']==8 and all(delta[axis]==new[axis]-old[axis] for axis in axes+['joint_content_scope_citation']) and delta['joint_content_scope_citation']==0)
            self.check(filename+':fixed_denominators',summary['histories']==2 and summary['questions']==8 and len(rows)==summary['native_response_count']==16*len(batches) and len({(row['batch'],row['request_id']) for row in rows})==len(rows))
            for row in rows:
                grade=deepcopy(self.graded_cases[(row['batch'],row['request_id'])])
                change=overlay_changes.get((row['batch'],row['case_id']))
                if change:
                    for field,values in change['field_changes'].items():
                        self.check('adjudication:original_axis:'+field,grade[field] is values['old'])
                        grade[field]=values['new']
                    for fact_change in change['fact_changes']:
                        facts=[fact for fact in grade['required_fact_assessments'] if fact['fact_id']==fact_change['fact_id']]
                        self.check('adjudication:original_fact:'+fact_change['fact_id'],len(facts)==1 and facts[0]['status']==fact_change['old'])
                        facts[0]['status']=fact_change['new']
                counts=dict(sorted(Counter(fact['status'] for fact in grade['required_fact_assessments']).items()))
                self.check(filename+':grade_join:'+row['batch']+':'+row['request_id'],all(row[axis]==grade[axis] for axis in axes) and row['content_sha256']==grade['raw_answer_sha256'] and row['context_sha256']==grade['received_context_sha256'] and row['joint_content_scope_citation']==all(grade[axis] for axis in axes[:3]) and row['fact_status_counts']==counts and row['required_fact_count']==sum(counts.values()))
            for aggregate in summary['aggregation']:
                group=[row for row in rows if row['batch']==aggregate['batch'] and row['condition']==aggregate['condition']]
                self.check(filename+':aggregate:'+aggregate['batch']+':'+aggregate['condition'],len(group)==aggregate['questions']==8 and all(aggregate[axis]==sum(row[axis] for row in group) for axis in axes+['joint_content_scope_citation']))
            summaries.append({'path':filename,'sha256':digest(self.root/filename),'native_response_count':len(rows)})
        return {'question_count':8,'history_count':2,'source_pdfs':4,'source_pdf_pages':550,'corpus_chunks':2306,'contexts_per_variant':16,'grading_packets':grade_packets,'grade_results':grade_results,'summaries':summaries}


def run(root,external_root=None):
    audit=Audit(root)
    for name,count in BATCHES:audit.batch(name,count)
    study=audit.study()
    external=[]
    if external_root is not None:
        sys.path.insert(0,str(audit.root/'scripts'))
        import native_paired_reader_v13 as reader
        for name,count in BATCHES:
            row=reader.verify(Path(external_root)/f'{name}_attempt01');external.append({'batch':name,**row})
    failed=[x for x in audit.checks if not x['passed']]
    return {'schema_version':'paired_execution_audit_v0.13','audited_at_utc':datetime.now(timezone.utc).isoformat(),
            'status':'failed' if failed else ('pending' if audit.pending else 'passed'),
            'mode':'metadata_and_external' if external_root else 'portable_metadata_only',
            'reviewer_role':'collector-author postexecution provenance audit; separate from semantic graders',
            'independent_implementation_review':False,'model_calls':0,'new_network_calls':0,
            'verifier_sha256':digest(Path(__file__)),'inputs_sha256':audit.inputs,'checks':audit.checks,'checks_passed':len(audit.checks)-len(failed),
            'checks_failed':len(failed),'pending':audit.pending,'batches':audit.batch_results,
            'study':study,'external_native_verification':external,
            'limits':['Portable mode checks released hashes, counts and recorded provenance; it cannot independently inspect undistributed source text, full native token arrays or raw model responses.',
                      'Optional external verification reuses the frozen collector offline; neither mode certifies semantic correctness or human gold.',
                      'Scoring operationalization and layout follow-up were recorded during primary inference; their declared before-answer-inspection status is not independently attestable from file metadata.',
                      'Model-assisted graders and reference reviewers share project context; condition masking is not statistical blindness.',
                      'Cgroup resident peak is the memory watchdog measure; memory.current includes cache and child peak RSS is cumulative telemetry, not an independent per-call allocation measurement.']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root',type=Path,default=DEFAULT_ROOT)
    parser.add_argument('--external-root',type=Path)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--allow-incomplete',action='store_true')
    args=parser.parse_args();result=run(args.project_root,args.external_root)
    if args.output:
        if result['status']!='passed':raise SystemExit('Refusing to finalize audit while failed or pending.')
        with args.output.open('x') as stream:json.dump(result,stream,indent=2);stream.write('\n')
    print(json.dumps({k:v for k,v in result.items() if k in ['status','mode','checks_passed','checks_failed','pending','batches','study']}))
    if result['status']=='failed' or (result['status']=='pending' and not args.allow_incomplete):
        if result['status']=='failed':print(json.dumps([x for x in result['checks'] if not x['passed']]))
        raise SystemExit(1)


if __name__=='__main__':main()
