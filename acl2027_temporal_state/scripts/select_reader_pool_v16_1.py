#!/usr/bin/env python3
"""Explicit post-inspection visibility-only amendment to the frozen v16 pool."""
from __future__ import annotations
import argparse
from collections import Counter
from copy import deepcopy
import gc
import importlib.util
import io
import json
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('pool_parent_v16',ROOT/'scripts/select_reader_pool_v16.py')
base=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(base)
SPEC_VIS=importlib.util.spec_from_file_location('pool_empty_profile_v16',ROOT/'scripts/characterize_reader_pool_visibility_v16.py')
profile=importlib.util.module_from_spec(SPEC_VIS);SPEC_VIS.loader.exec_module(profile)
views=base.views
PARENT_PROTOCOL='data/reader_binding_v16/question_pool_protocol.json'
PARENT_RESULT='results/reader_pool_v16.json'
AMENDMENT_DOC='docs/reader_pool_visibility_amendment_v16_1.txt'
REVIEW='results/reader_pool_visibility_amendment_review_v16_1.json'
RULE=deepcopy(base.RULE)
RULE['recognized_hiding_cues_allowed']='only_strictly_empty_descendant_layout_subtrees_without_id_or_class_hooks'
RULE['recognized_hiding_amendment']={
    'descendants_only':True,'all_recognized_cue_subtrees_must_pass':True,
    'profile':'characterize_reader_pool_visibility_v16.characterize_subtree',
    'additional_id_class_policy':'Attribute presence, even empty, blocks permission.',
    'original_rows_cells_text_and_metadata_retained':True,'browser_visibility_claim':False}
CODE_PATHS=['scripts/select_reader_pool_v16_1.py','tests/test_reader_pool_amendment_v16_1.py',
            'scripts/select_reader_pool_v16.py','tests/test_reader_pool_v16.py','tests/test_reader_pool_adversarial_v16.py',
            'scripts/characterize_reader_pool_visibility_v16.py','tests/test_reader_pool_visibility_v16.py',
            'scripts/analyze_reader_pool_v16.py','scripts/build_table_views_v16.py',
            'src/temporal_state/typed_reader_v15_1.py','src/temporal_state/__init__.py']
INPUT_PATHS=[PARENT_PROTOCOL,PARENT_RESULT,'results/reader_pool_failure_analysis_v16.json',
             'results/reader_pool_visibility_characterization_v16.json',AMENDMENT_DOC,REVIEW]


def hidden_descendant_gate(table_node,paths,nodes):
    located=profile.cue_nodes(table_node,paths,nodes)
    reasons=Counter();categories=Counter();table_path=paths[table_node]
    strict_count=0
    for path,node,kinds in located:
        if not path.startswith(table_path+'/'):
            reasons['cue_on_table_or_ancestor']+=1
        characterization=profile.characterize_subtree(node)
        if characterization['strictly_empty_layout']:
            strict_count+=1
        else:
            reasons['subtree_not_strictly_empty_layout']+=1
        hooks=sum('id' in item.attrib or 'class' in item.attrib for item in node.iter() if isinstance(item.tag,str))
        if hooks:reasons['id_or_class_attribute_hook_present']+=1
        categories.update(name for name in profile.CATEGORIES if characterization[name])
    permitted=bool(located) and not reasons
    return {'recognized_cue_count':len(located),'strictly_empty_layout_subtree_count':strict_count,
            'decision':'permit_empty_descendant_layout' if permitted else 'no_recognized_cues' if not located else 'reject_nonempty_or_uncertain_cue',
            'blocking_reason_counts':dict(reasons),'subtree_category_counts':dict(categories),
            'computed_css_evaluated':False,'rendering_verified':False}


def assess_table(table,facts,full_text,gate):
    original=base.assess_table(table,facts,full_text,descendant_hiding=gate['recognized_cue_count']>0)
    amended=deepcopy(original)
    known_visibility_statuses={'no_recognized_hiding_cue','markup_hiding_cue'}
    statuses=[table['visibility']['status']]+[cell['visibility']['status'] for row in table['rows'] for cell in row['cells']]
    unknown_view_status=any(status not in known_visibility_statuses for status in statuses)
    if gate['decision']=='permit_empty_descendant_layout' and gate['recognized_cue_count']>0 and not unknown_view_status:
        amended['rejection_reasons']=[r for r in amended['rejection_reasons'] if r!='recognized_hiding_cue']
    amended['eligible']=not amended['rejection_reasons']
    amended['original_eligible']=original['eligible']
    amended['original_rejection_reasons']=original['rejection_reasons']
    amended['empty_descendant_layout_gate']=deepcopy(gate)
    amended['unrecognized_view_visibility_status']=unknown_view_status
    return amended


def authored_checks():
    suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'),pattern='test_reader_pool_amendment*v16_1.py')
    transcript=io.StringIO();result=unittest.TextTestRunner(stream=transcript,verbosity=1).run(suite)
    if not result.wasSuccessful() or result.skipped or result.testsRun<20:raise base.PoolError('authored_amendment_controls_failed_or_incomplete')
    return {'tests_run':result.testsRun,'errors':len(result.errors),'failures':len(result.failures),
            'skipped':len(result.skipped),'transcript_sha256':base.sha(transcript.getvalue().encode()),
            'scope':'Authored fixtures only; no natural source/query/reference predictions in controls.'}


def freeze(protocol_path):
    if protocol_path.exists() or protocol_path.is_symlink():raise base.PoolError('refusing_to_overwrite_amendment_protocol')
    parent=json.loads((ROOT/PARENT_PROTOCOL).read_text());outcome=json.loads((ROOT/PARENT_RESULT).read_text())
    if (outcome['protocol_sha256']!=base.digest(ROOT/PARENT_PROTOCOL) or outcome['status']!='completed'
        or outcome['source_denominator']!=12 or outcome['totals']['selected_tables']!=13
        or outcome['totals']['unfilled_table_slots_including_failed_sources']!=11):
        raise base.PoolError('original_13_of_24_outcome_required')
    review=json.loads((ROOT/REVIEW).read_text())
    if review.get('status')!='passed_before_amended_selection' or review.get('open_blocking_findings')!=0:
        raise base.PoolError('independent_amendment_review_not_passed')
    if any(base.digest(ROOT/p)!=sha for p,sha in review['input_bindings'].items()):
        raise base.PoolError('independent_amendment_review_bindings_stale')
    codes=list(CODE_PATHS)
    adversarial='tests/test_reader_pool_amendment_adversarial_v16_1.py'
    if (ROOT/adversarial).is_file():codes.append(adversarial)
    controls=authored_checks()
    protocol={'schema_version':'reader_question_pool_protocol_v16_1','frozen_at_utc':base.utc(),
              'parent_protocol':{'path':PARENT_PROTOCOL,'sha256':base.digest(ROOT/PARENT_PROTOCOL)},
              'parent_result':{'path':PARENT_RESULT,'sha256':base.digest(ROOT/PARENT_RESULT)},
              'amendment_scope':'Visibility-only, post-inspection engineering change; every other predicate and first-two/source order unchanged.',
              'post_inspection_basis':'Both separately preserved predicate and empty-subtree analyses; no natural questions/references/predictions exist.',
              'rule':RULE,'sources':parent['sources'],'source_denominator':12,'planned_table_slots':24,
              'external_output_limit_bytes':base.LIMIT,'private_metadata_reserve_bytes':base.RESERVE,
              'runtime_bindings':views.runtime_bindings(),
              'code_bindings':{p:base.digest(ROOT/p) for p in codes},
              'input_bindings':{p:base.digest(ROOT/p) for p in INPUT_PATHS},
              'pre_freeze_authored_tests':controls,
              'question_authoring_performed':False,'natural_QA_predictions':0,'model_calls':0,
              'rendering_verified':False,'complete_evidence_verified':False}
    base.new_json(protocol_path,protocol)
    return protocol


def preflight(protocol,source_dir,typed_dir,view_dir):
    errors=[]
    if protocol.get('schema_version')!='reader_question_pool_protocol_v16_1' or protocol.get('rule')!=RULE:
        errors.append('amendment_rule_or_schema_mismatch')
    for group,required in [('code_bindings',CODE_PATHS),('input_bindings',INPUT_PATHS)]:
        if not set(required).issubset(protocol.get(group,{})):errors.append('required_'+group+'_missing')
        for name,sha in protocol.get(group,{}).items():
            path=(ROOT/name).resolve()
            if not path.is_relative_to(ROOT) or not path.is_file() or base.digest(path)!=sha:
                errors.append(group+'_mismatch:'+name)
    if protocol.get('parent_protocol')!={'path':PARENT_PROTOCOL,'sha256':base.digest(ROOT/PARENT_PROTOCOL)}:
        errors.append('parent_protocol_mismatch')
    if protocol.get('parent_result')!={'path':PARENT_RESULT,'sha256':base.digest(ROOT/PARENT_RESULT)}:
        errors.append('parent_result_mismatch')
    parent=json.loads((ROOT/PARENT_PROTOCOL).read_text())
    if protocol.get('sources')!=parent['sources'] or protocol.get('source_denominator')!=12 or protocol.get('planned_table_slots')!=24:
        errors.append('unchanged_population_or_order_mismatch')
    if protocol.get('external_output_limit_bytes')!=base.LIMIT or protocol.get('private_metadata_reserve_bytes')!=base.RESERVE:
        errors.append('unchanged_output_budget_mismatch')
    if protocol.get('runtime_bindings')!=views.runtime_bindings():errors.append('runtime_binding_mismatch')
    errors.extend(base.preflight(parent,source_dir,typed_dir,view_dir))
    return errors


def execute(protocol_path,source_dir,typed_dir,view_dir,external_dir,output):
    if output.exists() or output.is_symlink() or external_dir.exists() or external_dir.is_symlink():
        raise base.PoolError('refusing_to_overwrite_amended_attempt')
    if external_dir.resolve().is_relative_to(ROOT.parent.resolve()):raise base.PoolError('private_output_must_be_outside_repository')
    if output.resolve().is_relative_to(external_dir.resolve()):raise base.PoolError('public_output_must_be_outside_private_attempt')
    protocol=json.loads(protocol_path.read_text());errors=preflight(protocol,source_dir,typed_dir,view_dir)
    result={'schema_version':'reader_question_pool_result_v16_1','started_at_utc':base.utc(),
            'protocol_sha256':base.digest(protocol_path),'status':'preflight_failed' if errors else 'running',
            'preflight_errors':errors,'source_denominator':12,'planned_table_slots':24,
            'parent_protocol_sha256':base.digest(ROOT/PARENT_PROTOCOL),'parent_result_sha256':base.digest(ROOT/PARENT_RESULT),
            'amendment_scope':protocol['amendment_scope'],'post_inspection':True,
            'question_authoring_performed':False,'natural_QA_predictions':0,'model_calls':0,
            'rendering_verified':False,'complete_evidence_verified':False,'records':[]}
    if not errors:
        identities={r['source_path']:r for r in json.loads((ROOT/'data/fresh_source_audit_v14/source_identity_structure.json').read_text())['records']}
        original={r['source_path']:r for r in json.loads((ROOT/PARENT_RESULT).read_text())['records']}
        external_dir.mkdir(parents=True)
        base.new_json(external_dir/'attempt_started.json',{'started_at_utc':result['started_at_utc'],'protocol_sha256':result['protocol_sha256']})
        writer=base.Writer();totals=Counter();rejections=Counter();private_failures=[]
        for item in protocol['sources']:
            entry={'source_path':item['source_path'],'source_sha256':item['expected_sha256'],'status':'running','planned_table_slots':2};created=[]
            try:
                source=base.confined(source_dir,item['external_filename']).read_bytes()
                records=base.load_view_records(base.confined(view_dir,item['views_filename']));meta=records[0]
                if meta.get('record_type')!='source' or meta['source_path']!=item['source_path'] or meta['source_sha256']!=item['expected_sha256']:
                    raise base.PoolError('view_source_identity_mismatch')
                typed_list=views.load_typed(base.confined(typed_dir,item['typed_filename']));typed={f['fact_ordinal']:f for f in typed_list}
                if len(typed)!=len(typed_list) or len(typed)!=item['expected_nonfraction_count'] or any(f['source_sha256']!=item['expected_sha256'] for f in typed_list):
                    raise base.PoolError('typed_population_or_source_mismatch')
                root,paths,spans=views.parse_bound(source);nodes={path:node for node,path in paths.items()}
                tables=[r for r in records if r['record_type']=='table'];paras={r['paragraph_ordinal']:r for r in records if r['record_type']=='paragraph'}
                actual_tables=[n for n in paths if n.tag==views.TABLE]
                if len(tables)!=meta['table_count'] or len(tables)!=len(actual_tables) or [t['table_ordinal'] for t in tables]!=list(range(len(actual_tables))) or [t['dom_path'] for t in tables]!=[paths[n] for n in actual_tables]:
                    raise base.PoolError('table_population_or_order_mismatch')
                assessments=[];details={};gate_counts=Counter()
                for table in tables:
                    node=nodes[table['dom_path']]
                    if views.anchor(node,paths,spans,source)!=table['anchor']:raise base.PoolError('table_anchor_mismatch')
                    facts=base.joined_facts(table,typed);full_text=views.own_text(node)
                    gate=hidden_descendant_gate(node,paths,nodes);gate_counts.update([gate['decision']])
                    assessment=assess_table(table,facts,full_text,gate);assessment['table_ordinal']=table['table_ordinal']
                    assessments.append(assessment);details[table['table_ordinal']]=(table,facts,full_text,node)
                old=original[item['source_path']]
                if (sum(a['original_eligible'] for a in assessments)!=old['eligible_tables'] or
                    dict(Counter(r for a in assessments for r in a['original_rejection_reasons']))!=old['rejection_counts']):
                    raise base.PoolError('unchanged_original_predicate_replay_mismatch')
                selected=base.choose_tables(assessments);reasons=Counter(r for a in assessments for r in a['rejection_reasons'])
                identity=identities[item['source_path']]
                if identity['source_sha256']!=item['expected_sha256']:raise base.PoolError('identity_hash_mismatch')
                card=base.source_card(identity);stem=Path(item['external_filename']).stem
                author_path=external_dir/(stem+'.author.jsonl');review_path=external_dir/(stem+'.review.jsonl');created=[author_path,review_path]
                with author_path.open('xb') as author,review_path.open('xb') as review:
                    for a in selected:
                        table,facts,full_text,node=details[a['table_ordinal']]
                        neighbors=[paras[i] for i in table['paragraph_refs']['preceding']+table['paragraph_refs']['following']]
                        author_record=base.author_packet(table,full_text,neighbors,card)
                        writer.write(author,author_record)
                        ancestors=[]
                        for ancestor in views.ancestors_including(node.getparent()):
                            n=spans[paths[ancestor]];opening=source[n.start:n.opening_stop]
                            ancestors.append({'dom_path':paths[ancestor],'tag':ancestor.tag,'anchor':views.anchor(ancestor,paths,spans,source),
                                              'opening_byte_start':n.start,'opening_byte_stop':n.opening_stop,'opening_sha256':base.sha(opening),'opening_tag_utf8':opening.decode('utf-8')})
                        writer.write(review,{'record_type':'review_table_fact_join','source_sha256':item['expected_sha256'],'table':table,'facts':facts,'source_card':card,
                                             'ancestor_wrappers':ancestors,'selection_assessment':a,'author_packet_sha256':base.sha(base.encoded(author_record)),
                                             'rendering_verified':False,'semantic_attachment_verified':False})
                eligible=sum(a['eligible'] for a in assessments);old_ordinals={a['table_ordinal'] for a in old['selected_table_summaries']}
                entry.update(status='completed',table_denominator=len(tables),eligible_tables=eligible,selected_tables=len(selected),
                             unfilled_table_slots=2-len(selected),eligible_not_selected=max(0,eligible-2),
                             newly_eligible_by_visibility_amendment=sum(a['eligible'] and not a['original_eligible'] for a in assessments),
                             original_selected_tables_retained=sum(a['table_ordinal'] in old_ordinals for a in selected),
                             original_selected_tables_not_in_amended_first_two=sum(i not in {a['table_ordinal'] for a in selected} for i in old_ordinals),
                             gate_decision_counts=dict(gate_counts),rejection_counts=dict(reasons),
                             first_rejection_counts=dict(Counter(a['rejection_reasons'][0] for a in assessments if a['rejection_reasons'])),
                             selected_table_summaries=selected,original_predicate_replay='exact_counts_match')
                totals.update({k:entry[k] for k in ('table_denominator','eligible_tables','selected_tables','unfilled_table_slots','eligible_not_selected','newly_eligible_by_visibility_amendment','original_selected_tables_retained','original_selected_tables_not_in_amended_first_two')})
                rejections.update(reasons)
                del source,records,typed_list,typed,root,paths,spans,nodes,tables,paras,details
            except Exception as exc:
                entry.update(status='failed',failure_code=str(exc) if isinstance(exc,base.PoolError) else 'technical_exception',error_class=type(exc).__name__)
                private_failures.append({'source_path':item['source_path'],'error_class':type(exc).__name__,'message':str(exc)[:2048]})
            finally:
                entry['external_artifacts']=[{'filename':p.name,'bytes':p.stat().st_size,'sha256':base.digest(p),'complete':entry['status']=='completed'} for p in created if p.exists()]
                result['records'].append(entry);gc.collect()
        result['totals']=dict(totals)
        result['totals']['completed_sources']=sum(r['status']=='completed' for r in result['records'])
        result['totals']['sources_with_at_least_one_selected_table']=sum(r.get('selected_tables',0)>0 for r in result['records'])
        result['totals']['failed_source_table_slots']=2*(12-result['totals']['completed_sources'])
        result['totals']['unfilled_table_slots_including_failed_sources']=24-totals['selected_tables']
        result['rejection_counts']=dict(rejections)
        result['status']='completed' if all(r['status']=='completed' for r in result['records']) else 'failed_sources_retained'
        result['external_payload_bytes']=writer.bytes
        base.new_json(external_dir/'attempt_finished.json',{'finished_at_utc':base.utc(),'status':result['status'],'private_failures':private_failures})
        result['external_total_bytes']=sum(p.stat().st_size for p in external_dir.iterdir() if p.is_file());result['external_output_limit_bytes']=base.LIMIT
        if result['external_total_bytes']>base.LIMIT:result['status']='external_budget_violation'
    result['finished_at_utc']=base.utc();base.new_json(output,result)
    return result


def main():
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='command',required=True)
    f=sub.add_parser('freeze');f.add_argument('--protocol',type=Path,required=True)
    r=sub.add_parser('run')
    for key in ('protocol','sources','typed-records','table-views','external-output','output'):r.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args()
    if a.command=='freeze':
        freeze(a.protocol);print(json.dumps({'status':'frozen','protocol_sha256':base.digest(a.protocol)}))
    else:
        r=execute(a.protocol,a.sources,a.typed_records,a.table_views,a.external_output,a.output)
        print(json.dumps({k:r[k] for k in ('status','totals') if k in r},sort_keys=True))
        raise SystemExit(0 if r['status']=='completed' else 1)

if __name__=='__main__':main()
