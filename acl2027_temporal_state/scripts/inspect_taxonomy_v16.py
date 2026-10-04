#!/usr/bin/env python3
"""Bounded metadata inspection/capture for a fixed Inline XBRL source cohort.

Source values and response bodies stay outside the repository. This is not a
DTS validator or financial QA evaluator. A captured extension is only a candidate
until its relation to the exact source bytes has independently been established.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request
from lxml import etree

NS = {'link':'http://www.xbrl.org/2003/linkbase', 'xlink':'http://www.w3.org/1999/xlink', 'xs':'http://www.w3.org/2001/XMLSchema', 'ix':'http://www.xbrl.org/2013/inlineXBRL'}

def sha(b): return hashlib.sha256(b).hexdigest()
def utc(): return datetime.now(timezone.utc).isoformat()
def parser(): return etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=True)
def read_json(p): return json.loads(Path(p).read_text())
def write_json(p, j): Path(p).write_text(json.dumps(j, indent=2, sort_keys=True)+'\n')

class Capture:
    def __init__(self, directory, budget):
        self.directory = Path(directory); self.directory.mkdir(parents=True, exist_ok=False)
        self.budget = budget; self.bytes = 0; self.records = []
    def get(self, url, kind):
        if urllib.parse.urlsplit(url).hostname not in {'www.sec.gov','xbrl.sec.gov','xbrl.fasb.org','arelle.readthedocs.io'}:
            raise ValueError('URL outside explicit metadata hosts')
        r={'url':url,'kind':kind,'started_utc':utc(),'attempt':1}
        body=b''; begin=time.monotonic()
        try:
            req=urllib.request.Request(url,headers={'User-Agent':'WikiGraphRag academic source metadata study (bounded requests)','Accept-Encoding':'identity'})
            try: response=urllib.request.urlopen(req, timeout=self.budget['timeout_seconds'])
            except urllib.error.HTTPError as e: response=e
            with response:
                r.update({'http_status':response.status,'final_url':response.geturl(),'content_type':response.headers.get('Content-Type'),'content_length_header':response.headers.get('Content-Length')})
                cap=min(self.budget['per_response_bytes'],self.budget['total_response_bytes']-self.bytes)
                body=response.read(cap+1)
                if len(body)>cap:
                    body=body[:cap];r['truncated']=True;r['status']='response_limit_exceeded'
                else:r['truncated']=False;r['status']='captured' if response.status==200 else 'http_error'
        except Exception as e:
            r['status']='transport_error';r['error_class']=type(e).__name__;r['error_message']=str(e)
        self.bytes+=len(body)
        name=f'{len(self.records)+1:03d}_{kind}.body'
        (self.directory/name).write_bytes(body)
        r.update({'response_bytes':len(body),'response_sha256':sha(body),'external_file':str(self.directory/name),'finished_utc':utc(),'elapsed_seconds':round(time.monotonic()-begin,3)})
        self.records.append(r)
        write_json(self.directory/'capture_receipt.json',{'records':self.records,'total_response_bytes':self.bytes})
        return r,body

def source_inventory(path):
    b=path.read_bytes();tree=etree.fromstring(b,parser());refs=tree.xpath('//link:schemaRef',namespaces=NS)
    concepts=Counter();occurrences=Counter()
    for node in tree.xpath('//ix:nonFraction',namespaces=NS):
        n=node.get('name','');prefix,_,local=n.partition(':');ns=node.nsmap.get(prefix) if ':' in n else node.nsmap.get(None)
        key='{'+str(ns)+'}'+(local if ':' in n else n);concepts[key]+=1;occurrences[str(ns)]+=1
    return {'source_file':path.name,'source_sha256':sha(b),'source_bytes':len(b),'schema_refs':[{'href':n.get('{'+NS['xlink']+'}href'),'xml_base':n.get('{http://www.w3.org/XML/1998/namespace}base'),'locator':tree.getroottree().getpath(n)} for n in refs],'declared_namespaces':{str(k):v for k,v in tree.nsmap.items()},'nonfraction_occurrences':sum(occurrences.values()),'unique_nonfraction_concepts':len(concepts),'concept_occurrences_by_namespace':dict(sorted(occurrences.items())),'inline_label_resources':len(tree.xpath('//link:label',namespaces=NS))}

def schema_inventory(body):
    root=etree.fromstring(body,parser())
    if root.tag!='{'+NS['xs']+'}schema': raise ValueError('response is not an XML Schema document')
    return {'target_namespace':root.get('targetNamespace'),'imports':[{'namespace':n.get('namespace'),'schema_location':n.get('schemaLocation')} for n in root.xpath('//xs:import',namespaces=NS)],'includes':[n.get('schemaLocation') for n in root.xpath('//xs:include',namespaces=NS)],'linkbase_refs':[{'href':n.get('{'+NS['xlink']+'}href'),'role':n.get('{'+NS['xlink']+'}role')} for n in root.xpath('//link:linkbaseRef',namespaces=NS)],'embedded_label_resources':len(root.xpath('//link:label',namespaces=NS)),'embedded_label_arcs':len(root.xpath('//link:labelArc',namespaces=NS)),'declared_elements':len(root.xpath('/xs:schema/xs:element',namespaces=NS))}

def run(args):
    protocol=read_json(args.protocol);identity=read_json(args.identity)
    fixed={Path(r['source_path']).name:r for r in identity['records']}
    if {r['source_file'] for r in protocol['records']}!=set(fixed):raise ValueError('protocol population differs from fixed source population')
    if Path(args.output).exists():raise FileExistsError(args.output)
    captures=Capture(args.external_output,protocol['budget']);records=[]
    for configured in protocol['records']:
        inv=source_inventory(Path(args.sources)/configured['source_file']);expected=fixed[inv['source_file']]
        if inv['source_sha256']!=expected['source_sha256'] or inv['source_bytes']!=expected['bytes']:raise ValueError('source integrity mismatch')
        inv['candidate']=configured;inv['source_identity_verified']=False
        index,_=captures.get(configured['index_url'],'filing_index');inv['index_capture']=index
        schema_caps=[]
        for ref in inv['schema_refs']:
            href=ref['href'];url=urllib.parse.urljoin(configured['candidate_directory'],href)
            capture,body=captures.get(url,'extension_schema');item={'href':href,'capture':capture}
            if capture['status']=='captured':
                try:item['schema_metadata']=schema_inventory(body);item['schema_parse_status']='parsed'
                except Exception as e:item.update({'schema_parse_status':'failed','error_class':type(e).__name__})
            schema_caps.append(item)
        inv['schema_captures']=schema_caps;records.append(inv)
        print(json.dumps({'source_file':inv['source_file'],'index_status':index['status'],'schema_statuses':[c['capture']['status'] for c in schema_caps]}),flush=True)
    summary={'source_objects':len(records),'nonfraction_occurrences':sum(r['nonfraction_occurrences'] for r in records),'relative_schema_references':sum(not urllib.parse.urlsplit(s['href']).scheme for r in records for s in r['schema_refs']),'sources_with_inline_label_resources':sum(r['inline_label_resources']>0 for r in records),'extension_schemas_captured':sum(s.get('schema_parse_status')=='parsed' for r in records for s in r['schema_captures']),'source_identity_verified':0,'full_dts_validated':0,'natural_QA_predictions':0,'total_response_bytes':captures.bytes,'http_status_counts':dict(Counter(str(r.get('http_status')) for r in captures.records))}
    out={'schema_version':'taxonomy_feasibility_v16','created_utc':utc(),'code_sha256':sha(Path(__file__).read_bytes()),'input_bindings':{str(args.protocol):sha(Path(args.protocol).read_bytes()),str(args.identity):sha(Path(args.identity).read_bytes())},'records':records,'summary':summary,'limitations':['Candidate accession inferred from official index metadata, not byte-identical source recapture.','No recursive taxonomy discovery, Arelle model loading, XBRL validation or label-aware reader evaluation performed.','HTTP success, schema parse and embedded labels are resource checks only. All raw response bodies are external.']}
    write_json(args.output,out)
    print(json.dumps(summary),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--protocol',required=True);p.add_argument('--identity',required=True);p.add_argument('--sources',required=True);p.add_argument('--external-output',required=True);p.add_argument('--output',required=True);run(p.parse_args())
