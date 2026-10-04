"""Post-run metadata/hash audit; source bodies and fact values are not parsed."""
import argparse
import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone

def sha(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as stream:
  for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
 return h.hexdigest()

def binding(path):
 p=Path(path);return {'path':str(p),'bytes':p.stat().st_size,'sha256':sha(p)}

def audit(protocol_path,graph_path,result_path,output_path):
 protocol=json.loads(Path(protocol_path).read_text());graph=json.loads(Path(graph_path).read_text());result=json.loads(Path(result_path).read_text())
 checks=[]
 def check(kind,passed,subject=None):checks.append({'kind':kind,'pass':bool(passed),'subject':subject})
 def verify(kind,b):
  p=Path(b['path']);check(kind,p.is_file() and p.stat().st_size==b['bytes'] and sha(p)==b['sha256'],str(p))
 check('protocol_binding',sha(protocol_path)==result['protocol_sha256'])
 check('graph_binding',sha(graph_path)==result['dependency_graph_sha256'])
 check('all_source_order',[r['source_file'] for r in result['records']]==[s['source_file'] for s in protocol['sources']])
 check('all_source_denominator',result['source_denominator']==len(protocol['sources'])==12)
 check('all_occurrence_denominator',result['expected_nonfraction_denominator']==sum(s['expected_nonfraction'] for s in protocol['sources'])==23651)
 for source,record in zip(protocol['sources'],result['records']):
  verify('original_source_unchanged',source['source'])
  staged=Path(protocol['external_root'])/'staged'/source['source_file']/source['candidate_filename']
  verify('staged_source_unchanged',{**source['source'],'path':str(staged)})
  verify('v15_records_unchanged',source['v15_records'])
  check('result_source_binding',record['source_sha256']==source['source']['sha256'],source['source_file'])
  if record.get('technical_status')=='completed':
   check('completed_worker_gates',record.get('session_run_return') is True and record.get('loaded_models')==1 and record.get('counts',{}).get('observed_nonfraction')==source['expected_nonfraction'] and not record.get('log_parse_error') and not record.get('dependency_pin_failures') and record.get('worker_cleanup_confirmed') is True and 0<record.get('peak_RSS_bytes',0)<=protocol['limits']['source_RSS_bytes'],source['source_file'])
  for b in record.get('external_outputs',[]):verify('external_output_binding',b)
  for key in ['external_process_log','external_memory_telemetry']:
   if record.get(key):verify(key,record[key])
 for resource in graph['records']:
  if resource.get('cache_path'):verify('captured_cache_unchanged',{'path':resource['cache_path'],'bytes':resource['bytes'],'sha256':resource['sha256']})
 passed=sum(c['pass'] for c in checks)
 receipt={'schema_version':'full_dts_integrity_audit_v17','observed_at_utc':datetime.now(timezone.utc).isoformat(),'script':binding(__file__),'protocol':binding(protocol_path),'dependency_graph':binding(graph_path),'result':binding(result_path),'checks_passed':passed,'checks_total':len(checks),'pass':passed==len(checks),'checks':checks,'source_body_or_fact_values_parsed':False,'interpretation':'Hash preservation and metadata consistency only; not an independent validation of numeric semantics, SEC identity/compliance, financial truth or QA.'}
 with Path(output_path).open('x') as stream:json.dump(receipt,stream,indent=2,sort_keys=True);stream.write('\n')
 print(json.dumps({'pass':receipt['pass'],'checks_passed':passed,'checks_total':len(checks)}))
 return receipt['pass']

if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--protocol',required=True);parser.add_argument('--graph',required=True);parser.add_argument('--result',required=True);parser.add_argument('--output',required=True);args=parser.parse_args()
 raise SystemExit(0 if audit(args.protocol,args.graph,args.result,args.output) else 1)
