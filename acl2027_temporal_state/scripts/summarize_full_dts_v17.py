"""Summarize preserved outcomes; inspect telemetry metadata, never fact values."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

def binding(path):
 p=Path(path);return {'path':str(p),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}

def summarize(result_path,audit_path,output_path):
 result=json.loads(Path(result_path).read_text());audit=json.loads(Path(audit_path).read_text())
 if not audit['pass'] or audit['result']['sha256']!=binding(result_path)['sha256']:raise ValueError('integrity_audit_required')
 groups={};sources=[];technical_cases=[]
 for record in result['records']:
  status=record['validation_status'];group=groups.setdefault(status,{'sources':[],'counts':Counter(),'label_counts':Counter()})
  group['sources'].append(record['source_file']);group['counts'].update(record.get('counts',{}));group['label_counts'].update(record.get('label_counts',{}))
  complete=record.get('counts',{}).get('observed_nonfraction')==record['expected_nonfraction']
  row={k:record.get(k) for k in ['source_file','source_sha256','technical_status','validation_status','returncode','session_run_return','loaded_models','expected_nonfraction','counts','label_counts','unresolved_concepts','dependency_pin_failures','log_parse_error','worker_cleanup_confirmed','captured_closure_complete','unavailable_dependency_count','log_severity_code_counts']}
  row.update(fact_inventory_complete=complete,external_outputs=record.get('external_outputs',[]));sources.append(row)
  if record['technical_status']!='completed':
   samples=[json.loads(line) for line in Path(record['external_memory_telemetry']['path']).read_text().splitlines()]
   failures=[{'phase':s.get('phase'),'elapsed_seconds':s.get('elapsed_seconds'),'error_code':s.get('process_group_RSS_snapshot',{}).get('error_code')} for s in samples if s.get('process_group_RSS_snapshot',{}).get('telemetry_ok') is False]
   technical_cases.append({**row,'RSS_failure_samples':failures,'status_retained_unchanged':True})
 records=result['records'];labels=Counter()
 for r in records:labels.update(r.get('label_counts',{}))
 summary={'schema_version':'full_dts_terminal_summary_v17','script':binding(__file__),'result':binding(result_path),'integrity_audit':binding(audit_path),'source_denominator':result['source_denominator'],'numeric_occurrence_denominator':result['expected_nonfraction_denominator'],'technical_status_counts':result['technical_status_counts'],'validation_status_counts':result['validation_status_counts'],'observed_inventory_source_count':sum(s['fact_inventory_complete'] for s in sources),'observed_all_source_counts':result['totals'],'observed_all_source_label_counts':dict(labels),'unresolved_numeric_concept_occurrences':sum(r.get('unresolved_concepts',0) for r in records),'discrepancy_locator_count':sum(len(r.get('discrepancies',[])) for r in records),'groups_by_preserved_validation_status':groups,'sources':sources,'technical_cases_with_completed_artifacts':technical_cases,'resource_summary':{'sum_worker_elapsed_seconds':round(sum(r.get('elapsed_seconds',0) for r in records),3),'max_sampled_owned_group_RSS_bytes':max(r.get('peak_RSS_bytes',0) for r in records),'max_sampled_proxy_bytes':max(r.get('peak_pressure_proxy_bytes',0) for r in records),'max_sampled_actual_cgroup_bytes':max(r.get('peak_cgroup_bytes',0) for r in records),'all_owned_group_cleanup_confirmed':all(r.get('worker_cleanup_confirmed') is True for r in records),'run_baseline_events':result['run_baseline_memory_snapshot']['events'],'final_events':records[-1]['last_memory_snapshot']['events'],'two_RSS_telemetry_failures_retained':True,'old_total_cgroup_soft_limit_not_enforced':True},'natural_QA_predictions':0,'interpretation':'Observed record-level comparisons are reported for all12 complete saved inventories, including two retained guard failures and one incomplete DTS. They do not convert those source outcomes to successful validation, nor establish SEC original-byte identity/compliance, financial truth or QA gains.'}
 with Path(output_path).open('x') as stream:json.dump(summary,stream,indent=2,sort_keys=True);stream.write('\n')
 print(json.dumps({'summary':binding(output_path),'inventory_sources':summary['observed_inventory_source_count'],'groups':{k:{'sources':len(v['sources']),'occurrences':v['counts'].get('observed_nonfraction',0),'common_normalized':v['counts'].get('common_normalized',0),'exact_agreements':v['counts'].get('exact_agreements',0)} for k,v in groups.items()}}))

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--result',required=True);p.add_argument('--audit',required=True);p.add_argument('--output',required=True);a=p.parse_args();summarize(a.result,a.audit,a.output)
