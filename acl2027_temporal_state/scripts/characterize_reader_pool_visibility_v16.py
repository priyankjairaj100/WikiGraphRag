#!/usr/bin/env python3
"""Post-selection characterization of hidden subtrees; never changes the pool."""
from __future__ import annotations
import argparse
from collections import Counter
import gc
import importlib.util
import io
import json
import re
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('visibility_failure_parent_v16',ROOT/'scripts/analyze_reader_pool_v16.py')
analysis=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(analysis)
pool=analysis.pool; views=analysis.views
IX_NAMESPACES={'http://www.xbrl.org/2013/inlineXBRL','http://www.xbrl.org/2008/inlineXBRL'}
LAYOUT_TAGS={views.XH+name for name in ('table','thead','tbody','tfoot','tr','td','th','col','colgroup','div','span','p','br')}
LAYOUT_ATTRIBUTES={'style','class','id','width','height','colspan','rowspan','align','valign','border','cellpadding','cellspacing','bgcolor'}
LABEL_ATTRIBUTES={'alt','title','aria-label','aria-labelledby','aria-describedby','aria-description','abbr','axis','summary','headers','scope','label','name','value','content','data-label','data-title'}
MEDIA_TAGS={views.XH+name for name in ('img','picture','object','embed','canvas','video','audio','iframe')}
CATEGORIES=('strictly_empty_layout','non_whitespace_text','numeric_facts','images_or_embedded_media','non_whitespace_alt_text','non_whitespace_label_attributes','other_nonempty_attributes','non_layout_elements')


def meaningful(value):
    return any(not char.isspace() for char in value)


def subtree_text(node):
    """Include descendant tails, never the cue root's outside-sibling tail."""
    parts=[node.text or '']
    for child in node:
        if isinstance(child.tag,str): parts.append(subtree_text(child))
        parts.append(child.tail or '')
    return ''.join(parts)


def local_tag(tag):
    if tag.startswith('{'):
        namespace,local=tag[1:].split('}',1)
        return namespace,local
    return '',tag


def characterize_subtree(node):
    labels=Counter();other_attrs=Counter();nonlayout=Counter()
    numeric=0;images=0;alt=0;css_hooks=0;comments=0
    for descendant in node.iter():
        if not isinstance(descendant.tag,str):
            comments+=1
            continue
        namespace,local=local_tag(descendant.tag)
        numeric+=namespace in IX_NAMESPACES and local in {'nonFraction','fraction'}
        images+=descendant.tag in MEDIA_TAGS or (namespace=='http://www.w3.org/2000/svg' and local=='svg')
        if descendant.tag not in LAYOUT_TAGS:
            nonlayout['inline_xbrl' if namespace in IX_NAMESPACES else 'image_or_media' if descendant.tag in MEDIA_TAGS else 'other']+=1
        for attr,value in descendant.attrib.items():
            # Namespaced attributes are conservatively not benign HTML layout attributes.
            attr_key=attr if not attr.startswith('{') else 'namespaced_attribute'
            if not meaningful(value):continue
            if attr_key=='style' and re.search(r'(?:\bcontent\s*:|url\s*\()',value,re.I):
                other_attrs['inline_style_generated_content_or_url']+=1
            if attr_key in LABEL_ATTRIBUTES:
                labels[attr_key]+=1
            elif attr_key not in LAYOUT_ATTRIBUTES:
                other_attrs[attr_key]+=1
            if attr_key=='alt':alt+=1
            if attr_key in {'id','class'}:css_hooks+=1
    text=subtree_text(node)
    nonspace=meaningful(text)
    strict=not(nonspace or numeric or images or alt or labels or other_attrs or nonlayout)
    return {'strictly_empty_layout':strict,'non_whitespace_text':nonspace,
            'numeric_facts':numeric>0,'numeric_fact_occurrences':numeric,
            'images_or_embedded_media':images>0,'image_or_media_elements':images,
            'non_whitespace_alt_text':alt>0,'alt_attribute_count':alt,
            'non_whitespace_label_attributes':bool(labels),'label_attribute_counts_by_name':dict(labels),
            'other_nonempty_attributes':bool(other_attrs),'other_nonempty_attribute_counts_by_name':dict(other_attrs),
            'non_layout_elements':bool(nonlayout),'non_layout_element_counts_by_category':dict(nonlayout),
            'id_or_class_attribute_count':css_hooks,'comment_or_processing_instruction_count':comments,
            'unicode_whitespace_only_text':bool(text) and not nonspace,
            'no_text_nodes_with_characters':not text,
            'computed_css_evaluated':False,'rendering_verified':False}


def cue_nodes(table_node,paths,nodes):
    located={}
    for descendant in table_node.iter():
        if not isinstance(descendant.tag,str):continue
        for cue in views.visibility(descendant,paths)['cues']:
            located.setdefault(cue['dom_path'],set()).update(cue['kinds'])
    return [(path,nodes[path],sorted(kinds)) for path,kinds in sorted(located.items())]


def summarize(candidates):
    counts=Counter();labels=Counter();other_attrs=Counter();nonlayout=Counter();kinds=Counter()
    candidate_categories=Counter();unique=set();numeric=media=css_hooks=0
    whitespace=no_text=0
    for candidate in candidates:
        seen_categories=set()
        for cue in candidate['cues']:
            stats=cue['characterization'];unique.add((candidate['source_sha256'],cue['path']))
            counts.update(name for name in CATEGORIES if stats[name])
            seen_categories.update(name for name in CATEGORIES if stats[name])
            labels.update(stats['label_attribute_counts_by_name']);other_attrs.update(stats['other_nonempty_attribute_counts_by_name'])
            nonlayout.update(stats['non_layout_element_counts_by_category']);kinds.update(cue['kinds'])
            numeric+=stats['numeric_fact_occurrences'];media+=stats['image_or_media_elements'];css_hooks+=stats['id_or_class_attribute_count']
            whitespace+=stats['unicode_whitespace_only_text'];no_text+=stats['no_text_nodes_with_characters']
        candidate_categories.update(seen_categories)
    return {'hiding_only_candidate_count':len(candidates),
            'candidates_whose_every_recognized_cue_subtree_is_strictly_empty_layout':sum(bool(c['cues']) and all(x['characterization']['strictly_empty_layout'] for x in c['cues']) for c in candidates),
            'candidates_with_at_least_one_nonempty_or_nonlayout_cue_subtree':sum(any(not x['characterization']['strictly_empty_layout'] for x in c['cues']) for c in candidates),
            'cue_subtree_occurrences_sum_over_candidates':sum(len(c['cues']) for c in candidates),
            'distinct_source_cue_nodes':len(unique),
            'cue_subtree_counts_by_overlapping_category':{k:counts[k] for k in CATEGORIES},
            'candidate_counts_with_at_least_one_subtree_in_category':{k:candidate_categories[k] for k in CATEGORIES},
            'cue_subtree_counts_by_recognized_hiding_kind':dict(kinds),
            'numeric_fact_occurrences_sum_over_cue_subtrees':numeric,'image_or_media_elements_sum_over_cue_subtrees':media,
            'label_attribute_counts_by_name_sum_over_cue_subtrees':dict(labels),
            'other_nonempty_attribute_counts_by_name_sum_over_cue_subtrees':dict(other_attrs),
            'nonlayout_element_counts_by_category_sum_over_cue_subtrees':dict(nonlayout),
            'id_or_class_attribute_count_sum_over_cue_subtrees':css_hooks,
            'cue_subtrees_with_unicode_whitespace_only_text':whitespace,
            'cue_subtrees_without_text_characters':no_text,
            'counting_note':'Subtree categories overlap. An ancestor cue subtree can recur across candidates or contain a descendant cue; totals are labeled accordingly.',
            'interpretation':'Strictly empty is a bounded DOM/attribute characterization, not verified visual harmlessness, semantic sufficiency, source admission or a modified selection rule.'}


def execute(protocol_path,pool_result_path,failure_path,source_dir,typed_dir,view_dir,output):
    if output.exists() or output.is_symlink():raise pool.PoolError('refusing_to_overwrite_visibility_characterization')
    protocol=json.loads(protocol_path.read_text());prior=json.loads(pool_result_path.read_text());failure=json.loads(failure_path.read_text())
    if prior['protocol_sha256']!=pool.digest(protocol_path) or prior['status']!='completed' or failure['status']!='completed':
        raise pool.PoolError('completed_parent_receipts_required')
    relative_pool=str(pool_result_path.resolve().relative_to(ROOT))
    if failure['input_bindings'].get(relative_pool)!=pool.digest(pool_result_path):raise pool.PoolError('failure_analysis_parent_mismatch')
    if pool.preflight(protocol,source_dir,typed_dir,view_dir):raise pool.PoolError('frozen_pool_preflight_failed')
    suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'),pattern='test_reader_pool_visibility_v16.py')
    transcript=io.StringIO();checked=unittest.TextTestRunner(stream=transcript,verbosity=1).run(suite)
    if not checked.wasSuccessful() or checked.skipped or checked.testsRun<19:raise pool.PoolError('authored_characterization_checks_failed')
    inputs=[protocol_path,pool_result_path,failure_path]
    result={'schema_version':'reader_pool_visibility_characterization_v16','started_at_utc':pool.utc(),
            'analysis_status':'separately_versioned_post_selection_descriptive_characterization',
            'code_bindings':{str(p.relative_to(ROOT)):pool.digest(p) for p in [Path(__file__).resolve(),ROOT/'scripts/analyze_reader_pool_v16.py',ROOT/'tests/test_reader_pool_visibility_v16.py']},
            'input_bindings':{str(p.resolve().relative_to(ROOT)):pool.digest(p) for p in inputs},
            'source_denominator':12,'expected_hiding_only_candidate_count':29,
            'natural_questions_authored':0,'natural_QA_predictions':0,'model_calls':0,
            'new_pool_selection_performed':False,'frozen_rule_changed':False,
            'source_text_attribute_values_or_financial_values_released':False,
            'authored_test_receipt':{'tests_run':checked.testsRun,'errors':len(checked.errors),'failures':len(checked.failures),'skipped':len(checked.skipped),'transcript_sha256':pool.sha(transcript.getvalue().encode())},
            'definition':{'whitespace':'Python Unicode str.isspace, including NBSP; zero-width non-whitespace characters remain nonempty.',
                          'subtree_text':'All element text and descendant tails; excludes root tail outside cue subtree. Comments/processing instructions do not supply displayed text.',
                          'layout_tags':sorted(LAYOUT_TAGS),'benign_layout_attribute_names':sorted(LAYOUT_ATTRIBUTES),
                          'label_attribute_names':sorted(LABEL_ATTRIBUTES),
                          'other_attributes':'Any non-whitespace unrecognized or namespaced attribute, or inline style content/url construct, blocks strictly-empty status.',
                          'computed_CSS_generated_content_external_CSS_images_and_rendering':'Not evaluated; id/class hooks retained in counts.'},
            'records':[]}
    by_source={r['source_path']:r for r in failure['records']};all_candidates=[];replay_checks=0
    for item in protocol['sources']:
        source=pool.confined(source_dir,item['external_filename']).read_bytes()
        records=pool.load_view_records(pool.confined(view_dir,item['views_filename']))
        typed={f['fact_ordinal']:f for f in views.load_typed(pool.confined(typed_dir,item['typed_filename']))}
        root,paths,spans=views.parse_bound(source);nodes={path:node for node,path in paths.items()}
        tables=[r for r in records if r['record_type']=='table'];candidates=[];reason_counts=Counter();eligible=0
        for table in tables:
            node=nodes[table['dom_path']]
            if views.anchor(node,paths,spans,source)!=table['anchor']:raise pool.PoolError('source_table_anchor_mismatch')
            facts=pool.joined_facts(table,typed);located=cue_nodes(node,paths,nodes)
            assessment=pool.assess_table(table,facts,views.own_text(node),descendant_hiding=bool(located))
            reason_counts.update(assessment['rejection_reasons']);eligible+=assessment['eligible'];replay_checks+=1
            if assessment['rejection_reasons']==['recognized_hiding_cue']:
                candidates.append({'source_sha256':item['expected_sha256'],
                                   'cues':[{'path':path,'kinds':kinds,'characterization':characterize_subtree(cue)} for path,cue,kinds in located]})
        expected=by_source[item['source_path']]
        if len(tables)!=expected['all_table_count'] or eligible!=expected['frozen_eligible_table_count']:raise pool.PoolError('frozen_source_predicate_replay_mismatch')
        if len(candidates)!=expected['single_failed_predicate_counts_with_every_other_rule_satisfied']['recognized_hiding_cue']:raise pool.PoolError('hiding_only_population_replay_mismatch')
        if dict(reason_counts)!={k:v for k,v in next(r for r in prior['records'] if r['source_path']==item['source_path'])['rejection_counts'].items()}:raise pool.PoolError('all_predicate_reason_replay_mismatch')
        replay_checks+=3
        result['records'].append({'source_path':item['source_path'],'source_sha256':item['expected_sha256'],**summarize(candidates)})
        all_candidates.extend(candidates)
        del source,records,typed,root,paths,spans,nodes,tables
        gc.collect()
    if len(all_candidates)!=29:raise pool.PoolError('fixed_29_candidate_denominator_mismatch')
    result['aggregate']=summarize(all_candidates);result['predicate_replay_checks_passed']=replay_checks
    result['finished_at_utc']=pool.utc();result['status']='completed'
    pool.new_json(output,result)
    return result


def main():
    p=argparse.ArgumentParser()
    for key in ('protocol','pool-result','failure-analysis','sources','typed-records','table-views','output'):
        p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args();r=execute(a.protocol,a.pool_result,a.failure_analysis,a.sources,a.typed_records,a.table_views,a.output)
    print(json.dumps({'status':r['status'],'hiding_only_candidates':r['aggregate']['hiding_only_candidate_count'],
                      'only_empty_candidates':r['aggregate']['candidates_whose_every_recognized_cue_subtree_is_strictly_empty_layout'],
                      'cue_subtrees':r['aggregate']['cue_subtree_occurrences_sum_over_candidates'],
                      'predicate_replay_checks_passed':r['predicate_replay_checks_passed'],'new_selection':False}))

if __name__=='__main__':main()
