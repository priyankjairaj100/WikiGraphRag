#!/usr/bin/env python3
"""Replay resource integrity and metadata counts without printing source labels."""
import argparse
import hashlib
import json
from pathlib import Path
from lxml import etree, html
from inspect_taxonomy_v16 import parser, NS

def sha(b):return hashlib.sha256(b).hexdigest()
def run(a):
 first=json.loads(Path(a.feasibility).read_text());second=json.loads(Path(a.labels).read_text());checks=[];sizes=[];arcs=0;bad_arcroles=0
 for r in first['records']:
  body=(Path(a.sources)/r['source_file']).read_bytes();checks.append(sha(body)==r['source_sha256']);checks.append(len(body)==r['source_bytes'])
  for c in [r['index_capture']]+[s['capture'] for s in r['schema_captures']]:
   b=Path(c['external_file']).read_bytes();checks.append(sha(b)==c['response_sha256']);checks.append(len(b)==c['response_bytes'])
  t=html.fromstring(Path(r['index_capture']['external_file']).read_bytes());name=r['candidate']['candidate_filing_url'].rsplit('/',1)[-1];found=[]
  for row in t.xpath('//tr'):
   if any(x.rsplit('/',1)[-1]==name for x in row.xpath('.//a/@href')):
    cells=[' '.join(x.itertext()).strip() for x in row.xpath('./td')]
    if cells and cells[-1].isdigit():found.append(int(cells[-1]))
  checks.append(len(found)==1)
  sizes.append({'source_file':r['source_file'],'hf_bytes':r['source_bytes'],'candidate_SEC_index_bytes':found,'difference_if_one_listing':r['source_bytes']-found[0] if len(found)==1 else None})
  checks.append(all(s['schema_metadata']['target_namespace'] in r['declared_namespaces'].values() for s in r['schema_captures']))
  for s in r['schema_captures']:
   root=etree.fromstring(Path(s['capture']['external_file']).read_bytes(),parser())
   for n in root.xpath('//link:labelArc',namespaces=NS):
    arcs+=1;bad_arcroles+=n.get('{'+NS['xlink']+'}arcrole')!='http://www.xbrl.org/2003/arcrole/concept-label'
 for r in second['records']:
  for s in r['schema_results']:
   checks.append(s['numeric_extension_occurrences']==s['numeric_extension_occurrences_with_label'])
   for c in s['label_captures']:
    b=Path(c['external_file']).read_bytes();checks.append(sha(b)==c['response_sha256']);checks.append(len(b)==c['response_bytes'])
    root=etree.fromstring(b,parser())
    for n in root.xpath('//link:labelArc',namespaces=NS):
     arcs+=1;bad_arcroles+=n.get('{'+NS['xlink']+'}arcrole')!='http://www.xbrl.org/2003/arcrole/concept-label'
 checks.append(bad_arcroles==0)
 checks.append(second['summary']['total_phase1_and_phase2_response_bytes']<=25*1024*1024)
 out={'schema_version':'taxonomy_resource_audit_v16','checks':len(checks),'passed':sum(checks),'status':'pass' if all(checks) else 'fail','input_bindings':{a.feasibility:sha(Path(a.feasibility).read_bytes()),a.labels:sha(Path(a.labels).read_bytes())},'code_sha256':sha(Path(__file__).read_bytes()),'label_arcs_inspected':arcs,'non_concept_label_arcroles':bad_arcroles,'source_size_comparisons':sizes,'scope':'Source and capture hashes/sizes, index filename lookup, namespace compatibility, syntactic extension-label coverage, concept-label arcroles and download cap; no filing byte identity, effective label selection or DTS validation.'}
 if Path(a.output).exists():raise FileExistsError(a.output)
 Path(a.output).write_text(json.dumps(out,indent=2)+'\n');print(json.dumps({k:out[k] for k in ['checks','passed','status','label_arcs_inspected','non_concept_label_arcroles']}))
if __name__=='__main__':
 p=argparse.ArgumentParser()
 for n in ['feasibility','labels','sources','output']:p.add_argument('--'+n,required=True)
 run(p.parse_args())
