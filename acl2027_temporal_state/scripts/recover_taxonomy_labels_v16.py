#!/usr/bin/env python3
"""Recover explicitly linked label resources within the shared 25 MiB cap.

The first-phase captures stay immutable. Labels are source resources, not QA
answers; public output is counts, URLs, and hashes, never the label strings.
"""
import argparse
from collections import Counter
from pathlib import Path
import urllib.parse
from lxml import etree
from inspect_taxonomy_v16 import Capture, NS, parser, read_json, write_json, sha, utc

XL='{'+NS['xlink']+'}'

def inventory_labels(root, extension_url, extension_elements):
    linked=set();label_count=0;arc_count=0
    roles=Counter();languages=Counter()
    for link in root.xpath('//link:labelLink',namespaces=NS):
        locs={n.get(XL+'label'):n.get(XL+'href') for n in link.findall('{'+NS['link']+'}loc')}
        labels={}
        for n in link.findall('{'+NS['link']+'}label'):
            label_count+=1; roles[n.get(XL+'role','')]+=1;languages[n.get('{http://www.w3.org/XML/1998/namespace}lang','')]+=1
            labels[n.get(XL+'label')]=n
        for arc in link.findall('{'+NS['link']+'}labelArc'):
            arc_count+=1;href=locs.get(arc.get(XL+'from'))
            if href and arc.get(XL+'to') in labels:
                absolute=urllib.parse.urljoin(extension_url,href);base,frag=urllib.parse.urldefrag(absolute)
                if base==extension_url and frag in extension_elements:linked.add(extension_elements[frag])
    return {'label_resources':label_count,'label_arcs':arc_count,'label_roles':dict(roles),'languages':dict(languages),'extension_concepts_with_linked_label':sorted(linked)}

def run(a):
    first=read_json(a.feasibility);first_protocol=read_json(a.first_protocol)
    remaining=first_protocol['budget']['total_response_bytes']-first['summary']['total_response_bytes']
    plan=[]
    for r in first['records']:
        for s in r['schema_captures']:
            if s.get('schema_parse_status')!='parsed':continue
            for ref in s['schema_metadata']['linkbase_refs']:
                if ref['role']=='http://www.xbrl.org/2003/role/labelLinkbaseRef':plan.append({'source_file':r['source_file'],'url':urllib.parse.urljoin(s['capture']['url'],ref['href'])})
    protocol={'schema_version':'taxonomy_labels_protocol_v16','frozen_at_utc':utc(),'phase1_sha256':sha(Path(a.feasibility).read_bytes()),'code_bindings':{str(Path(__file__)):sha(Path(__file__).read_bytes()),'inspect_taxonomy_v16.py':sha(Path(__file__).with_name('inspect_taxonomy_v16.py').read_bytes())},'remaining_response_byte_limit':remaining,'requests':plan,'scope':'all explicit labelLinkbaseRef resources plus embedded labels; no recursive DTS download or validation'}
    if Path(a.protocol).exists() or Path(a.output).exists():raise FileExistsError('No overwrite of prior attempt')
    write_json(a.protocol,protocol)
    caps=Capture(a.external_output,{**first_protocol['budget'],'total_response_bytes':remaining});records=[]
    for r in first['records']:
        d={'source_file':r['source_file'],'source_sha256':r['source_sha256'],'source_identity_verified':False,'schema_results':[]}
        for s in r['schema_captures']:
            if s.get('schema_parse_status')!='parsed':continue
            b=Path(s['capture']['external_file']).read_bytes()
            if sha(b)!=s['capture']['response_sha256']:raise ValueError('Extension input hash mismatch')
            root=etree.fromstring(b,parser());ns=root.get('targetNamespace');ext_url=s['capture']['url']
            ids={n.get('id'):'{'+ns+'}'+n.get('name') for n in root.findall('{'+NS['xs']+'}element') if n.get('id') and n.get('name')}
            analyses=[inventory_labels(root,ext_url,ids)];receipts=[]
            for p in [p for p in plan if p['source_file']==r['source_file']]:
                c,b=caps.get(p['url'],'label_linkbase');receipts.append(c)
                if c['status']=='captured':
                    try:analyses.append(inventory_labels(etree.fromstring(b,parser()),ext_url,ids))
                    except Exception as e:c['xml_parse_error_class']=type(e).__name__
            linked={n for x in analyses for n in x['extension_concepts_with_linked_label']}
            source=etree.parse(str(Path(a.sources)/r['source_file']),parser());numeric=Counter()
            for n in source.xpath('//ix:nonFraction',namespaces=NS):
                lexical=n.get('name','');pref,_,local=lexical.partition(':')
                if n.nsmap.get(pref)==ns:numeric['{'+ns+'}'+local]+=1
            item={'extension_schema_sha256':s['capture']['response_sha256'],'extension_target_namespace':ns,'label_captures':receipts,'label_resources':sum(x['label_resources'] for x in analyses),'label_arcs':sum(x['label_arcs'] for x in analyses),'extension_declared_concepts':len(ids),'extension_concepts_with_linked_label':len(linked),'numeric_extension_unique_concepts':len(numeric),'numeric_extension_occurrences':sum(numeric.values()),'numeric_extension_unique_concepts_with_label':sum(n in linked for n in numeric),'numeric_extension_occurrences_with_label':sum(v for n,v in numeric.items() if n in linked),'mapping_limit':'Syntactic ID/locator/labelArc graph only; no XBRL relationship priority, prohibition, language/role selection, dimensional validation or source-identity certification.'}
            d['schema_results'].append(item)
        records.append(d);print(r['source_file'],flush=True)
    items=[s for r in records for s in r['schema_results']]
    out={'schema_version':'taxonomy_labels_feasibility_v16','created_utc':utc(),'protocol_sha256':sha(Path(a.protocol).read_bytes()),'records':records,'summary':{'sources':len(records),'label_linkbases_captured':sum(c['status']=='captured' for c in caps.records),'label_resources':sum(s['label_resources'] for s in items),'extension_numeric_occurrences':sum(s['numeric_extension_occurrences'] for s in items),'extension_numeric_occurrences_with_syntactic_label':sum(s['numeric_extension_occurrences_with_label'] for s in items),'total_phase1_and_phase2_response_bytes':first['summary']['total_response_bytes']+caps.bytes,'full_dts_validated':0,'natural_QA_predictions':0}}
    write_json(a.output,out);print(out['summary'],flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser()
    for name in ['feasibility','first-protocol','sources','external-output','protocol','output']:p.add_argument('--'+name,required=True)
    run(p.parse_args())
