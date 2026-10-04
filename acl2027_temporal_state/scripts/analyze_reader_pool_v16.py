#!/usr/bin/env python3
"""Post-selection descriptive predicate audit. No new pool, questions or answers."""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import gc
import importlib.util
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('pool_failure_replay_v16',ROOT/'scripts/select_reader_pool_v16.py')
pool=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(pool)
views=pool.views
COUNT_REASONS={'numeric_occurrence_count_outside_4_32','fewer_than_4_usable_duration_facts'}


def histogram(values):
    return [{'value':value,'table_count':count} for value,count in sorted(Counter(values).items())]


def hiding_summary(node, paths, fact_paths, locator_nodes):
    """Count recognized markup cues, not browser visibility or financial values."""
    cues={}
    for item in node.iter():
        if not isinstance(item.tag,str):continue
        for cue in views.visibility(item,paths)['cues']:
            cues.setdefault(cue['dom_path'],set()).update(cue['kinds'])
    by_kind=Counter(); by_position=Counter(); by_tag=Counter()
    lookup=locator_nodes
    table_path=paths[node]
    for path,kinds in cues.items():
        by_kind.update(kinds)
        by_position['descendant' if path.startswith(table_path+'/') else 'table_or_ancestor']+=1
        tag=lookup[path].tag
        category=('ix_nonFraction' if tag==views.IX+'nonFraction' else
                  'ix_other' if tag.startswith(views.IX) else
                  'xhtml_table' if tag==views.TABLE else
                  'xhtml_row' if tag==views.ROW else
                  'xhtml_cell' if tag in views.CELLS else
                  'xhtml_div_span' if tag in (views.XH+'div',views.XH+'span') else 'other')
        by_tag[category]+=1
    hidden_fact_paths={fp for fp in fact_paths if any(fp==cp or fp.startswith(cp+'/') for cp in cues)}
    return {'has_cue':bool(cues),'cue_kinds':sorted(by_kind),
            'unique_cue_node_count':len(cues),'cue_node_counts_by_kind':dict(by_kind),
            'cue_node_counts_by_position':dict(by_position),'cue_node_counts_by_tag_category':dict(by_tag),
            'numeric_occurrences_under_recognized_cues':len(hidden_fact_paths)}


def summarize(rows):
    revenue_leaf=[r for r in rows if 'has_child_table' not in r['rejection_reasons']
                  and 'missing_revenue_row_label' not in r['rejection_reasons']]
    single={reason:[r for r in revenue_leaf if r['rejection_reasons']==[reason]] for reason in pool.REASONS}
    count_joint=[r for r in revenue_leaf if r['rejection_reasons'] and set(r['rejection_reasons']).issubset(COUNT_REASONS)]
    patterns=Counter(tuple(r['rejection_reasons']) for r in revenue_leaf)
    cue_tables=Counter(); cue_nodes=Counter(); cue_positions=Counter(); cue_tags=Counter()
    for r in revenue_leaf:
        cue_tables.update(r['hiding']['cue_kinds'])
        cue_nodes.update(r['hiding']['cue_node_counts_by_kind'])
        cue_positions.update(r['hiding']['cue_node_counts_by_position'])
        cue_tags.update(r['hiding']['cue_node_counts_by_tag_category'])
    joints=Counter((r['fact_count'],r['usable_duration_count'],r['resolved_duration_period_count'],tuple(r['rejection_reasons'])) for r in count_joint)
    return {
        'all_table_count':len(rows),'revenue_row_leaf_table_count':len(revenue_leaf),
        'frozen_eligible_table_count':sum(r['eligible'] for r in rows),
        'single_failed_predicate_counts_with_every_other_rule_satisfied':{k:len(v) for k,v in single.items()},
        'single_predicate_scope':'Revenue-row leaf tables only; child-table and missing-label exceptions therefore have zero counts by definition.',
        'revenue_leaf_rejection_patterns':[{'reasons':list(k),'table_count':n} for k,n in sorted(patterns.items())],
        'numeric_occurrence_distribution_all_revenue_leaf':histogram(r['fact_count'] for r in revenue_leaf),
        'usable_duration_occurrence_distribution_all_revenue_leaf':histogram(r['usable_duration_count'] for r in revenue_leaf),
        'distinct_resolved_duration_period_distribution_all_revenue_leaf':histogram(r['resolved_duration_period_count'] for r in revenue_leaf),
        'size_only_failure_required_max_occurrences':histogram(r['fact_count'] for r in single['numeric_occurrence_count_outside_4_32'] if r['fact_count']>32),
        'size_only_failure_required_min_occurrences_at_most':histogram(r['fact_count'] for r in single['numeric_occurrence_count_outside_4_32'] if r['fact_count']<4),
        'usable_only_failure_required_min_usable_duration_at_most':histogram(r['usable_duration_count'] for r in single['fewer_than_4_usable_duration_facts']),
        'period_only_failure_required_min_distinct_periods_at_most':histogram(r['resolved_duration_period_count'] for r in single['fewer_than_2_resolved_duration_periods']),
        'text_only_failure_required_max_dom_characters':histogram(r['dom_text_characters'] for r in single['complete_dom_text_exceeds_12000_characters']),
        'joint_count_bound_only_failures':[{'numeric_occurrences':a,'usable_duration_occurrences':b,'resolved_duration_periods':c,
                                         'failed_count_predicates':list(reasons),'table_count':n} for (a,b,c,reasons),n in sorted(joints.items())],
        'hiding_cues_revenue_leaf':{
            'tables_with_any_cue':sum(r['hiding']['has_cue'] for r in revenue_leaf),
            'tables_with_each_cue_kind':dict(cue_tables),
            'cue_node_counts_by_kind_sum_over_tables':dict(cue_nodes),
            'cue_node_counts_by_position_sum_over_tables':dict(cue_positions),
            'cue_node_counts_by_tag_category_sum_over_tables':dict(cue_tags),
            'numeric_occurrences_under_recognized_cues_sum_over_tables':sum(r['hiding']['numeric_occurrences_under_recognized_cues'] for r in revenue_leaf),
            'visibility_only_failures_with_zero_numeric_occurrences_under_cues':sum(r['hiding']['numeric_occurrences_under_recognized_cues']==0 for r in single['recognized_hiding_cue']),
            'visibility_only_failures_with_some_numeric_occurrences_under_cues':sum(r['hiding']['numeric_occurrences_under_recognized_cues']>0 for r in single['recognized_hiding_cue']),
            'visibility_only_failure_hidden_numeric_occurrence_distribution':histogram(r['hiding']['numeric_occurrences_under_recognized_cues'] for r in single['recognized_hiding_cue']),
            'limitation':'Recognized markup cues only. Cue nodes can be counted in several table subtrees; no computed CSS, rendered visibility or semantic materiality is inferred.'},
        'relaxation_interpretation':'Descriptive required bounds, not proposed amendments. No tables selected with relaxed rules.'}


def execute(protocol_path,result_path,source_dir,typed_dir,view_dir,output):
    if output.exists() or output.is_symlink():raise pool.PoolError('refusing_to_overwrite_failure_analysis')
    protocol=json.loads(protocol_path.read_text()); original=json.loads(result_path.read_text())
    if original['protocol_sha256']!=pool.digest(protocol_path) or original['status']!='completed':
        raise pool.PoolError('completed_frozen_pool_parent_required')
    errors=pool.preflight(protocol,source_dir,typed_dir,view_dir)
    if errors:raise pool.PoolError('frozen_input_preflight_failed')
    result={'schema_version':'reader_pool_failure_analysis_v16','started_at_utc':pool.utc(),
            'analysis_status':'post_selection_descriptive_not_preregistered',
            'code_sha256':pool.digest(Path(__file__)),
            'input_bindings':{str(protocol_path.resolve().relative_to(ROOT)):pool.digest(protocol_path),
                              str(result_path.resolve().relative_to(ROOT)):pool.digest(result_path)},
            'source_denominator':12,'natural_questions_authored':0,'natural_QA_predictions':0,'model_calls':0,
            'new_pool_selection_performed':False,'frozen_rule_changed':False,
            'source_text_or_financial_values_released':False,'records':[]}
    original_by={r['source_path']:r for r in original['records']}
    all_rows=[];checks=0
    for item in protocol['sources']:
        source=pool.confined(source_dir,item['external_filename']).read_bytes()
        records=pool.load_view_records(pool.confined(view_dir,item['views_filename']))
        typed={f['fact_ordinal']:f for f in views.load_typed(pool.confined(typed_dir,item['typed_filename']))}
        root,paths,spans=views.parse_bound(source)
        nodes={path:node for node,path in paths.items()}
        tables=[r for r in records if r['record_type']=='table']
        rows=[]
        for table in tables:
            node=nodes[table['dom_path']]
            if views.anchor(node,paths,spans,source)!=table['anchor']:raise pool.PoolError('replayed_table_anchor_mismatch')
            facts=pool.joined_facts(table,typed)
            cues=hiding_summary(node,paths,[f['anchor']['dom_path'] for f in facts],nodes)
            row=pool.assess_table(table,facts,views.own_text(node),descendant_hiding=cues['has_cue'])
            row.update(table_ordinal=table['table_ordinal'],hiding=cues)
            rows.append(row);checks+=1
        prior=original_by[item['source_path']]
        if len(rows)!=prior['table_denominator']:raise pool.PoolError('replayed_table_denominator_mismatch')
        if sum(r['eligible'] for r in rows)!=prior['eligible_tables']:raise pool.PoolError('replayed_eligible_count_mismatch')
        if dict(Counter(reason for r in rows for reason in r['rejection_reasons']))!=prior['rejection_counts']:raise pool.PoolError('replayed_rejection_counts_mismatch')
        if [r['table_ordinal'] for r in pool.choose_tables(rows)]!=[r['table_ordinal'] for r in prior['selected_table_summaries']]:raise pool.PoolError('frozen_selection_replay_mismatch')
        checks+=4
        result['records'].append({'source_path':item['source_path'],'source_sha256':item['expected_sha256'],
                                  'frozen_predicate_replay':'exact_counts_and_selected_ordinals_match',**summarize(rows)})
        all_rows.extend(rows)
        del source,records,typed,root,paths,spans,nodes,tables
        gc.collect()
    result['aggregate']=summarize(all_rows)
    result['replay_checks_passed']=checks
    result['finished_at_utc']=pool.utc();result['status']='completed'
    pool.new_json(output,result)
    return result


def main():
    p=argparse.ArgumentParser()
    for key in ('protocol','pool-result','sources','typed-records','table-views','output'):
        p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args();r=execute(a.protocol,a.pool_result,a.sources,a.typed_records,a.table_views,a.output)
    print(json.dumps({'status':r['status'],'all_tables':r['aggregate']['all_table_count'],
                      'revenue_row_leaf_tables':r['aggregate']['revenue_row_leaf_table_count'],
                      'frozen_eligible_tables':r['aggregate']['frozen_eligible_table_count'],
                      'replay_checks_passed':r['replay_checks_passed'],'new_selection':False}))

if __name__=='__main__':main()
