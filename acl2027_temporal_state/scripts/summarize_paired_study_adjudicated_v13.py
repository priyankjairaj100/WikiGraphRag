#!/usr/bin/env python3
"""Apply the explicit v13 rounding adjudication; preserve raw grades and totals."""
from __future__ import annotations
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import summarize_paired_study_v13 as original

ROOT=Path(__file__).resolve().parents[1]
AXES=original.AXES


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def read(path):return json.loads(Path(path).read_text())
def require(condition,message):
    if not condition:raise ValueError(message)


def aggregate(records,batches):
    result=[]
    for batch in batches:
        for condition in ['ordinary','paired']:
            rows=[r for r in records if r['batch']==batch and r['condition']==condition]
            require(len(rows)==8,'Every batch/condition must retain all eight questions.')
            complete=[r for r in rows if r['context_support']=='complete']
            result.append({'batch':batch,'condition':condition,'questions':8,
                **{axis:sum(r[axis] for r in rows) for axis in (*AXES,'joint_content_scope_citation')},
                'context_support_counts':dict(sorted(Counter(r['context_support'] for r in rows).items())),
                'complete_context_cases':len(complete),
                'complete_context_joint_passes':sum(r['joint_content_scope_citation'] for r in complete)})
    return result


def contrasts(records,batches,questions):
    paired=[];layout=[]
    for batch in batches:
        for question in questions:
            cells={r['condition']:r for r in records if r['batch']==batch and r['question_id']==question['question_id']}
            require(set(cells)=={'ordinary','paired'},'Paired contrast cell missing.')
            paired.append({'batch':batch,'question_id':question['question_id'],
                **{axis:{'ordinary':cells['ordinary'][axis],'paired':cells['paired'][axis]} for axis in AXES[:4]},
                'joint_difference_paired_minus_ordinary':int(cells['paired']['joint_content_scope_citation'])-int(cells['ordinary']['joint_content_scope_citation'])})
    for condition in ['ordinary','paired']:
        for question in questions:
            cells={r['batch']:r for r in records if r['condition']==condition and r['question_id']==question['question_id']}
            require(set(cells)=={'main','layout'},'Layout contrast cell missing.')
            layout.append({'condition':condition,'question_id':question['question_id'],
                **{axis:{'main':cells['main'][axis],'layout':cells['layout'][axis]} for axis in AXES[:4]},
                'joint_difference_layout_minus_main':int(cells['layout']['joint_content_scope_citation'])-int(cells['main']['joint_content_scope_citation'])})
    return paired,layout


def summarize(initial_path,overlay_path):
    initial=read(initial_path);overlay=read(overlay_path)
    require(initial==original.summarize(['main','layout']),'Initial summary does not exactly replay original raw-grader aggregation.')
    require(initial['batches']==['main','layout'] and initial['native_response_count']==len(initial['records'])==32,'Both complete 16-cell batches required.')
    require(overlay['schema_version']=='reader_adjudication_overlay_v0.13' and overlay['reference_edits']==overlay['native_response_edits']==0,'Invalid preservation contract.')
    for name,expected in overlay['input_sha256'].items():require(sha(ROOT/name)==expected,'Adjudication input changed: '+name)
    require(len(overlay['changes'])==1,'Only the single explicitly authorized adjudication is supported.')
    change=overlay['changes'][0]
    require({k:change[k] for k in ['batch','domain','case_id','question_id']}=={'batch':'layout','domain':'plug','case_id':'case_87126f917505','question_id':'plug_v13_q2'},'Unexpected adjudicated case.')
    require(change['field_changes']=={'reference_content_correct':{'old':True,'new':False}} and change['fact_changes']==[{'fact_id':'amount','old':'supported_correct','new':'ambiguous'}],'Overlay changes exceed the explicit adjudication.')
    grades=read(ROOT/'results/grades_layout_plug_v13.json')
    matches=[c for c in grades['cases'] if c['case_id']==change['case_id']]
    require(len(matches)==1,'Adjudicated grader case missing or duplicated.')
    case=matches[0]
    final=deepcopy(initial);records=final['records']
    candidates=[r for r in records if r['batch']==change['batch'] and r['case_id']==change['case_id']]
    require(len(candidates)==1,'Adjudicated summary row missing or duplicated.')
    row=candidates[0]
    require(row['question_id']==change['question_id'] and row['content_sha256']==case['raw_answer_sha256'] and row['context_sha256']==case['received_context_sha256'],'Adjudication answer/context binding changed.')
    for field,values in change['field_changes'].items():
        require(row[field] is values['old'] and case[field] is values['old'],'Original grade differs from overlay old value.')
        row[field]=values['new']
    facts={fact['fact_id']:fact for fact in case['required_fact_assessments']}
    counts=Counter(row['fact_status_counts'])
    for fact_change in change['fact_changes']:
        require(facts[fact_change['fact_id']]['status']==fact_change['old'],'Original fact status differs from overlay.')
        require(counts[fact_change['old']]>0,'Missing original fact count.')
        counts[fact_change['old']]-=1;counts[fact_change['new']]+=1
    row['fact_status_counts']=dict(sorted((key,value) for key,value in counts.items() if value))
    require(sum(row['fact_status_counts'].values())==row['required_fact_count'],'Required fact denominator changed.')
    row['joint_content_scope_citation']=all(row[axis] for axis in AXES[:3])
    require(row['scope_correct'] is True and row['citation_grounded'] is False and row['joint_content_scope_citation'] is False,'Unchanged scope/citation/joint finding changed.')
    untouched_initial=[r for r in initial['records'] if not(r['batch']==change['batch'] and r['case_id']==change['case_id'])]
    untouched_final=[r for r in records if not(r['batch']==change['batch'] and r['case_id']==change['case_id'])]
    require(untouched_initial==untouched_final and len(untouched_final)==31,'Another row changed.')
    final['aggregation']=aggregate(records,final['batches'])
    questions=read(ROOT/'data/paired_reader_v13/questions.json')['questions']
    final['paired_contrasts'],final['layout_contrasts']=contrasts(records,final['batches'],questions)
    final['schema_version']='paired_study_adjudicated_summary_v0.13'
    additions={str(Path(initial_path).resolve().relative_to(ROOT)):sha(initial_path),
               str(Path(overlay_path).resolve().relative_to(ROOT)):sha(overlay_path),
               'scripts/summarize_paired_study_adjudicated_v13.py':sha(Path(__file__))}
    final['input_sha256']=dict(sorted((initial['input_sha256']|additions|overlay['input_sha256']).items()))
    final['adjudication']={'overlay_path':str(Path(overlay_path).resolve().relative_to(ROOT)),
        'overlay_sha256':sha(overlay_path),'changed_cases':1,'changed_fact_assessments':1,
        'changed_grade_axes':1,'changes':deepcopy(overlay['changes']),
        'unchanged_case_count':31,'native_response_edits':0,'reference_edits':0,
        'original_grades_modified':False,'initial_summary_sha256':sha(initial_path),
        'interpretation':'Apply the frozen explicit-rounding requirement after recorded model-assisted review; preserve the initial lenient interpretation as a sensitivity. No statistical blindness claimed.'}
    deltas=[]
    for old,new in zip(initial['aggregation'],final['aggregation']):
        require((old['batch'],old['condition'])==(new['batch'],new['condition']),'Aggregation order changed.')
        deltas.append({'batch':new['batch'],'condition':new['condition'],'questions':8,
            **{axis:new[axis]-old[axis] for axis in (*AXES,'joint_content_scope_citation')}})
    final['initial_grader_sensitivity']={'initial_summary_path':str(Path(initial_path).resolve().relative_to(ROOT)),
        'initial_summary_sha256':sha(initial_path),'label':'initial_lenient_implicit_rounding_grader_counts',
        'aggregation':deepcopy(initial['aggregation']),'final_minus_initial':deltas,
        'all_32_cases_retained':True,'joint_counts_unchanged':all(x['joint_content_scope_citation']==0 for x in deltas)}
    return final


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--initial',type=Path,default=ROOT/'results/paired_study_initial_v13.json')
    parser.add_argument('--overlay',type=Path,default=ROOT/'results/reader_adjudication_overlay_v13.json')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--check',action='store_true')
    args=parser.parse_args();result=summarize(args.initial,args.overlay);encoded=json.dumps(result,indent=2)+'\n'
    if args.check:require(args.output.read_text()==encoded,'Final summary does not replay exactly.')
    else:
        with args.output.open('x') as stream:stream.write(encoded)
    print(json.dumps({'status':'passed','native_response_count':32,'adjudicated_cases':1,
                      'aggregation':result['aggregation'],'output_sha256':sha(args.output)}))


if __name__=='__main__':main()
