#!/usr/bin/env python3
"""Bounded taxonomy closure acquisition and offline Arelle validation.

No filing or numeric values are written to the public results. Each phase has
immutable inputs/outputs; all source-bearing worker outputs remain external.
"""
from __future__ import annotations
import argparse
from collections import Counter, deque
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import resource
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from lxml import etree
from cgroup_guard_v16_2 import COMPONENTS, WORKER_RESERVE_BYTES, read_snapshot, policy_reason
from process_group_rss_v16 import bind_group, read_group_rss

IX='{http://www.xbrl.org/2013/inlineXBRL}nonFraction'
XSD='http://www.w3.org/2001/XMLSchema'
LINK='http://www.xbrl.org/2003/linkbase'
XLINK='{http://www.w3.org/1999/xlink}'
HOSTS={'www.sec.gov','xbrl.sec.gov','xbrl.fasb.org','www.xbrl.org','xbrl.org','www.w3.org'}

def utc():return datetime.now(timezone.utc).isoformat()
def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
def jread(p):return json.loads(Path(p).read_text())
def jwrite(p,obj,exclusive=False):
 with Path(p).open('x' if exclusive else 'w') as f:json.dump(obj,f,indent=2,sort_keys=True);f.write('\n')
def binding(p):return {'path':str(p),'bytes':Path(p).stat().st_size,'sha256':sha(p)}
def verify(b):
 if Path(b['path']).stat().st_size!=b['bytes'] or sha(b['path'])!=b['sha256']:raise ValueError('bound file changed: '+b['path'])
def check_space(p,min_bytes):
 if shutil.disk_usage(p).free<min_bytes:raise RuntimeError('shared_memory_reserve')
def canonical_decimal(value):
 if isinstance(value,(float,bool)):raise ValueError('inexact_or_nonnumeric_type')
 d=Decimal(value)
 if not d.is_finite():raise ValueError('nonfinite')
 s=format(d,'f')
 if '.' in s:s=s.rstrip('0').rstrip('.')
 return '0' if d==0 else s

def safe_url(url):
 url=urllib.parse.urldefrag(url)[0];u=urllib.parse.urlsplit(url)
 if u.scheme not in {'http','https'} or u.hostname not in HOSTS or u.username or u.password or u.port or u.query:raise ValueError('unsafe_dependency_url')
 if any(ord(c)<32 for c in url) or '\\' in url:raise ValueError('unsafe_dependency_url')
 parts=u.path.split('/')
 for p in parts:
  decoded=urllib.parse.unquote(p)
  if decoded in {'.','..'} or '/' in decoded or '\\' in decoded or '\x00' in decoded:raise ValueError('unsafe_dependency_path')
 return url

def cache_path(root,url):
 url=safe_url(url);u=urllib.parse.urlsplit(url)
 encode=lambda p:re.sub(r'[:^]',lambda m:'^'+str(ord(m.group())).zfill(3),p)
 path=Path(root)/u.scheme/encode(u.netloc)
 for part in u.path.lstrip('/').split('/'):path=path/encode(part)
 if u.path.endswith('/'):path=path/'!~DirectoryIndex~!'
 if not path.resolve().is_relative_to(Path(root).resolve()):raise ValueError('cache_escape')
 return path

def xml_edges(path,url):
 parser=etree.XMLParser(resolve_entities=False,load_dtd=False,no_network=True)
 tree=etree.parse(str(path),parser,base_url=url)
 if tree.docinfo.doctype:raise ValueError('dependency_DTD_refused')
 edges=[]
 for n in tree.iter():
  if not isinstance(n.tag,str):continue
  ref=None
  if n.tag in {'{'+XSD+'}import','{'+XSD+'}include','{'+XSD+'}redefine'}:ref=n.get('schemaLocation')
  elif n.tag in {'{'+LINK+'}'+x for x in ['schemaRef','linkbaseRef','roleRef','arcroleRef','loc']}:ref=n.get(XLINK+'href')
  if ref:
   resolved=urllib.parse.urldefrag(urllib.parse.urljoin(n.base or url,ref))[0]
   if resolved!=url:edges.append({'kind':n.tag,'lexical_uri':ref,'resolved_uri':resolved})
 return sorted({(e['kind'],e['lexical_uri'],e['resolved_uri']) for e in edges})

class SafeRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,req,fp,code,msg,headers,newurl):
  safe_url(newurl)
  return super().redirect_request(req,fp,code,msg,headers,newurl)

def fetch(url,dest,limit,timeout):
 started=utc();r={'url':url,'started_utc':started,'attempt':1};dest.parent.mkdir(parents=True,exist_ok=True)
 try:
  safe_url(url);req=urllib.request.Request(url,headers={'User-Agent':'WikiGraphRag bounded academic XBRL metadata study','Accept-Encoding':'identity'})
  opener=urllib.request.build_opener(SafeRedirect())
  try:response=opener.open(req,timeout=timeout)
  except urllib.error.HTTPError as e:response=e
  with response, dest.open('xb') as out:
   r.update({'http_status':response.status,'final_url':response.geturl(),'content_type':response.headers.get('Content-Type')})
   remaining=limit
   while remaining:
    chunk=response.read(min(65536,remaining))
    if not chunk:break
    out.write(chunk);remaining-=len(chunk)
   declared=response.headers.get('Content-Length')
   exhausted=remaining==0 and (not declared or not declared.isdigit() or int(declared)>limit)
   r['status']='response_limit' if exhausted else ('captured' if response.status==200 else 'http_error')
   r['over_limit_probe_bytes']=0
 except Exception as e:
  r.update({'status':'transport_error','error_class':type(e).__name__})
  if not dest.exists():dest.touch()
 r.update(binding(dest));r['finished_utc']=utc();return r

def load_plugin(directory):
 name='wikigraph_pinned_sec_transform_v16'
 spec=importlib.util.spec_from_file_location(name,Path(directory)/'__init__.py',submodule_search_locations=[str(directory)])
 module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module);return module

def dependency_modules():
 out=[];seen=set()
 for name,module in sorted(sys.modules.items()):
  f=getattr(module,'__file__',None)
  if f and Path(f).is_file() and ('site-packages' in f or 'arelle_runtime' in f or 'edgar_transform_metadata' in f):
   p=str(Path(f).resolve())
   if p not in seen:out.append({**binding(p),'first_module':name});seen.add(p)
 return out

def controls(plugin_directory):
 m=load_plugin(plugin_directory);cases={'zero':'0','No':'0','twenty one':'21','two thousand four':'2004','one hundred twenty-three':'123'};rows=[]
 for lexical,wanted in cases.items():
  got=m.numwordsen(lexical);rows.append({'lexical':lexical,'expected':wanted,'observed':got,'pass':got==wanted})
 try:m.numwordsen('not a number');invalid=False
 except Exception:invalid=True
 return {'authored_transform_cases':rows,'invalid_words_rejected':invalid,'pass':all(r['pass'] for r in rows) and invalid,'no_natural_source_values_used':True,'modules':dependency_modules()}

def preflight(protocol):
 for b in protocol['bindings']:verify(b)
 for s in protocol['sources']:verify(s['source']);verify(s['v15_records'])
 for b in protocol['runtime_files']:verify(b)
 for b in protocol['control_modules']:verify(b)
 check_space(protocol['external_root_parent'],protocol['limits']['min_shm_free_bytes'])

def seed_resources(protocol):
 first=jread(protocol['feasibility']);labels=jread(protocol['labels']);rows={}
 for r in first['records']:
  for s in r['schema_captures']:
   c=s['capture'];rows[c['url']]={'path':c['external_file'],'sha256':c['response_sha256'],'bytes':c['response_bytes'],'origin':'extension_capture'}
 for r in labels['records']:
  for s in r['schema_results']:
   for c in s['label_captures']:rows[c['url']]={'path':c['external_file'],'sha256':c['response_sha256'],'bytes':c['response_bytes'],'origin':'label_capture'}
 return rows

def prepare(protocol_path,output_graph):
 p=jread(protocol_path);preflight(p);root=Path(p['external_root']);root.mkdir(exist_ok=False)
 cache=root/'cache';cache.mkdir();responses=root/'responses';responses.mkdir();staging=root/'staged';staging.mkdir()
 seeds=seed_resources(p);queue=deque(seeds);seen=set();records=[];edges=[];network_bytes=0
 builtin=Path(p['arelle_runtime'])/'arelle/resources/cache'
 for source in p['sources']:
  target=staging/source['source_file']/source['candidate_filename'];target.parent.mkdir()
  os.link(source['source']['path'],target);verify({**source['source'],'path':str(target)})
 while queue:
  url=queue.popleft()
  if url in seen:continue
  seen.add(url)
  if len(seen)>p['limits']['max_unique_resources']:
   records.append({'url':url,'status':'resource_count_limit'});continue
  try:
   safe_url(url);dest=cache_path(cache,url)
   if url in seeds:
    b=seeds[url];verify(b);r={'url':url,'status':'reused','origin':b['origin'],**binding(b['path'])}
   else:
    built=cache_path(builtin,url)
    if built.is_file():r={'url':url,'status':'reused','origin':'pinned_Arelle_builtin',**binding(built)}
    else:
     check_space(root,p['limits']['min_shm_free_bytes']+p['limits']['per_response_bytes'])
     remaining=p['limits']['additional_network_bytes']-network_bytes
     if remaining<=1:r={'url':url,'status':'network_byte_limit'}
     else:
      r=fetch(url,responses/(str(len(records)).zfill(4)+'.body'),min(p['limits']['per_response_bytes'],remaining-1),p['limits']['network_timeout_seconds']);network_bytes+=r['bytes']+r.get('over_limit_probe_bytes',0)
   if r['status'] in {'captured','reused'}:
    dest.parent.mkdir(parents=True,exist_ok=True)
    if not dest.exists():os.link(r['path'],dest)
    elif sha(dest)!=r['sha256']:raise ValueError('cache_content_conflict')
    r['cache_path']=str(dest)
    try:
     outgoing=xml_edges(r['path'],url);r['xml_edges']=len(outgoing)
     for kind,lexical,target in outgoing:
      edges.append({'from':url,'kind':kind,'lexical_uri':lexical,'to':target})
     for target in sorted({e[2] for e in outgoing}):
      if target not in seen:queue.append(target)
    except Exception as e:r['discovery_status']='xml_parse_failure';r['discovery_error_class']=type(e).__name__
  except Exception as e:r={'url':url,'status':'refused_or_local_failure','error_class':type(e).__name__,'reason':str(e) if str(e) in {'shared_memory_reserve','unsafe_dependency_url','unsafe_dependency_path','cache_escape'} else None}
  records.append(r)
  jwrite(root/'capture_progress.json',{'records':records,'edges':edges,'network_bytes':network_bytes})
  print(json.dumps({'resource':len(records),'status':r['status'],'network_bytes':network_bytes,'queue_remaining':len(queue)}),flush=True)
 # Stage each local extension/linkbase file recovered from its candidate directory.
 for source in p['sources']:
  directory=source['candidate_directory'];local=staging/source['source_file']
  for r in records:
   if r.get('cache_path') and r['url'].startswith(directory):
    tail=r['url'][len(directory):]
    if '/' not in tail and tail:
     dest=local/tail
     if not dest.exists():os.link(r['cache_path'],dest)
 # Store closure status per source, including each refused edge.
 byurl={r['url']:r for r in records};children={}
 for e in edges:children.setdefault(e['from'],set()).add(e['to'])
 closures=[]
 for s in p['sources']:
  todo=list(s['extension_urls']);visited=set()
  while todo:
   url=todo.pop()
   if url in visited:continue
   visited.add(url);todo.extend(children.get(url,set()))
  bad=[u for u in sorted(visited) if byurl.get(u,{}).get('status') not in {'captured','reused'} or byurl.get(u,{}).get('discovery_status')=='xml_parse_failure']
  closures.append({'source_file':s['source_file'],'reachable_resource_count':len(visited),'unavailable_or_unparsed_URLs':bad,'captured_closure_complete':not bad})
 graph={'schema_version':'full_dts_dependency_graph_v16','frozen_at_utc':utc(),'protocol_sha256':sha(protocol_path),'records':records,'edges':edges,'source_closures':closures,'additional_network_bytes':network_bytes,'status_counts':dict(Counter(r['status'] for r in records)),'network_phase_finished_before_validation':True}
 jwrite(output_graph,graph,exclusive=True);jwrite(root/'graph_freeze.json',{'graph':binding(output_graph),'protocol':binding(protocol_path)},exclusive=True)
 print(json.dumps({'graph_frozen':output_graph,'sha256':sha(output_graph),'resources':len(records),'additional_network_bytes':network_bytes,'complete_source_closures':sum(c['captured_closure_complete'] for c in closures)}),flush=True)

def dom_paths(root):
 paths={root:'/'+root.tag+'[1]'}
 for n in etree._Element.iter(root):
  counts=Counter()
  for child in etree._Element.iterchildren(n):
   if isinstance(child.tag,str):counts[child.tag]+=1;paths[child]=paths[n]+'/'+child.tag+'['+str(counts[child.tag])+']'
 return paths

def cgroup_bytes():
 try:return int(Path('/sys/fs/cgroup/memory.current').read_text())
 except OSError:return None

def rss_reading_reason(reading,parent_exited):
 if not reading.get('telemetry_ok'):return 'process_RSS_telemetry_unavailable'
 if not reading.get('group_members_observed') and not parent_exited:return 'live_process_group_unobserved'
 if reading.get('rss_bytes',0)<=0 and not parent_exited:return 'live_process_RSS_not_positive'
 if parent_exited and any(m['state'] not in {'Z','X','x'} for m in reading.get('members',[])):return 'worker_exit_with_live_descendants'
 return None

def resource_reason(elapsed,rss,cg,free,limits):
 if elapsed>limits['source_wall_seconds']:return 'timeout'
 if rss>limits['source_RSS_bytes']:return 'memory_limit'
 if cg is None:return 'cgroup_monitor_unavailable'
 if cg>limits['cgroup_total_bytes']:return 'cgroup_memory_limit'
 if free<limits['min_shm_free_bytes']:return 'shared_memory_reserve'
 return None

def validate_expected_records(records,source_sha,expected_count):
 out={}
 for r in records:
  if r.get('record_type')!='fact_pair':continue
  t=r['typed'];path=t['anchor']['dom_path']
  if t['source_sha256']!=source_sha:raise ValueError('v15_source_sha_mismatch')
  if path in out:raise ValueError('duplicate_v15_locator')
  out[path]={'ordinal':r['fact_ordinal'],'numeric_status':t['numeric_status'],'value':t['normalized_value'],'source_sha256':t['source_sha256']}
 if len(out)!=expected_count:raise ValueError('v15_occurrence_count_mismatch')
 return out

def parse_logs(logs):
 try:
  decoded=json.loads(logs)
  rows=decoded['log'] if isinstance(decoded,dict) else decoded
  if not isinstance(rows,list) or any(not isinstance(r,dict) or 'level' not in r or 'code' not in r for r in rows):raise ValueError('invalid_log_shape')
  return rows,None
 except Exception as e:return [],type(e).__name__

def classify_result(details,closure_complete,expected,technical='completed'):
 errorcount=sum(v for k,v in details.get('log_severity_code_counts',{}).items() if k.split(':')[0].upper() in {'ERROR','CRITICAL','FATAL'})
 dep_errors=sum(v for k,v in details.get('log_severity_code_counts',{}).items() if any(t in k.lower() for t in ['filenotloadable','importerror','ioerror','missingfile','offline_network']))
 if details.get('log_parse_error'):technical='log_parse_failure'
 elif details.get('loaded_models',0)!=1:technical='processor_load_failure'
 elif details.get('counts',{}).get('observed_nonfraction',0)!=expected:technical='fact_inventory_incomplete'
 elif details.get('dependency_pin_failures'):technical='dependency_pin_failure'
 elif details.get('session_run_return') is not True:technical='processor_reported_failure'
 if technical!='completed':status='not_executed_to_completion'
 elif not closure_complete or dep_errors:status='incomplete_DTS'
 elif errorcount or details.get('counts',{}).get('model_errors',0):status='completed_with_errors'
 else:status='completed_without_errors'
 return {'technical_status':technical,'validation_status':status,'validation_error_count':errorcount,'dependency_error_count':dep_errors}

def worker(protocol_path,index):
 p=jread(protocol_path);s=p['sources'][index];root=Path(p['external_root']);out=Path(p['validation_root'])/s['source_file'];out.mkdir(parents=True,exist_ok=False)
 # Refuse all socket connects during offline validation, in addition to Arelle's option.
 def deny_network(*args,**kwargs):raise RuntimeError('offline_network_refused')
 socket.create_connection=deny_network;socket.socket.connect=deny_network;socket.socket.connect_ex=deny_network
 from arelle.api.Session import Session
 from arelle.RuntimeOptions import RuntimeOptions
 from arelle.XmlValidateConst import VALID
 staged=root/'staged'/s['source_file']/s['candidate_filename'];verify({**s['source'],'path':str(staged)})
 with Path(s['v15_records']['path']).open() as f:
  expected=validate_expected_records((json.loads(line) for line in f),s['source']['sha256'],s['expected_nonfraction'])
 options={**p['runtime_options'],'entrypointFile':str(staged),'cacheDirectory':str(root/'cache'),'plugins':p['sec_transform_directory']}
 counters=Counter();discrepancies=[];log_codes=Counter();labels=Counter();unresolved=0
 with Session() as session:
  ok=session.run(RuntimeOptions(**options));models=session.get_models();logs=session.get_logs('json');(out/'arelle_logs.json').write_text(logs)
  log_records,log_parse_error=parse_logs(logs)
  for r in log_records:
   if isinstance(r,dict):log_codes[str(r.get('level'))+':'+str(r.get('code'))]+=1
  with (out/'facts.jsonl').open('x') as f:
   visited=set()
   for model in models:
    doc=model.modelDocument
    if doc is None or getattr(doc,'xmlRootElement',None) is None:continue
    paths=dom_paths(doc.xmlRootElement)
    for fact in etree._Element.iter(doc.xmlRootElement,IX):
     path=paths[fact];visited.add(path);counters['observed_nonfraction']+=1
     concept=getattr(fact,'concept',None);validity=getattr(fact,'xValid',None);nil=getattr(fact,'isNil',False)
     value=None;state='invalid_or_unvalidated'
     if nil:state='nil'
     elif validity is not None and validity>=VALID:
      try:value=canonical_decimal(fact.xValue);state='normalized'
      except Exception:state='nonnumeric_or_nonfinite'
     counters['arelle_'+state]+=1
     label=None
     if concept is not None:
      try:label=concept.label(lang=('en-US','en'),fallbackToQname=False)
      except Exception:counters['label_exception']+=1
     else:unresolved+=1
     labels['with_effective_English_label' if label else 'without_effective_English_label']+=1
     old=expected.get(path)
     if old is None:discrepancies.append({'locator':path,'reason':'missing_v15_locator'});counters['missing_v15_locator']+=1
     elif old['numeric_status']=='normalized' and state=='normalized':
      counters['common_normalized']+=1
      if old['value']==value:counters['exact_agreements']+=1
      else:counters['exact_disagreements']+=1;discrepancies.append({'locator':path,'ordinal':old['ordinal'],'reason':'numeric_value_disagreement'})
     elif old['numeric_status']=='unsupported' and state=='normalized':counters['v15_unsupported_arelle_normalized']+=1
     elif old['numeric_status']=='nil' and state=='nil':counters['both_nil']+=1
     else:counters['other_status_comparison']+=1;discrepancies.append({'locator':path,'ordinal':old['ordinal'],'reason':'status_difference','v15_status':old['numeric_status'],'arelle_status':state})
     f.write(json.dumps({'source_sha256':s['source']['sha256'],'locator':path,'state':state,'xValid':validity,'canonical_value':value,'concept':str(getattr(fact,'qname',None)),'effective_English_label':label,'concept_period_type':getattr(concept,'periodType',None),'concept_type':str(getattr(concept,'typeQname',None)),'context_id':fact.get('contextRef'),'unit_id':fact.get('unitRef')})+'\n')
    counters['dimension_defaults']+=len(getattr(model,'qnameDimensionDefaults',{}));counters['model_errors']+=len(getattr(model,'errors',[]))
   for path in expected.keys()-visited:discrepancies.append({'locator':path,'reason':'missing_arelle_locator'});counters['missing_arelle_locator']+=1
  loaded_modules=dependency_modules();pins={str(Path(b['path']).resolve()):b['sha256'] for b in p['runtime_files']+p['control_modules']+p['bindings']}
  pin_failures=[{'path':b['path'],'reason':'unbound' if b['path'] not in pins else 'hash_changed'} for b in loaded_modules if pins.get(b['path'])!=b['sha256']]
  verify({**s['source'],'path':str(staged)})
  summary={'log_parse_error':log_parse_error,'dependency_pin_failures':pin_failures,'source_file':s['source_file'],'source_sha256':s['source']['sha256'],'session_run_return':ok,'loaded_models':len(models),'counts':dict(counters),'label_counts':dict(labels),'unresolved_concepts':unresolved,'log_severity_code_counts':dict(log_codes),'discrepancies':discrepancies,'external_outputs':[binding(out/'facts.jsonl'),binding(out/'arelle_logs.json')],'loaded_nonstdlib_modules':loaded_modules,'taxonomy_validation_scope':p['runtime_options'],'source_identity_verified':False}
 jwrite(out/'worker_summary.json',summary,exclusive=True)

def validate(protocol_path,graph_path,output):
 p=jread(protocol_path);preflight(p);root=Path(p['external_root']);frozen=jread(root/'graph_freeze.json');verify(frozen['protocol']);verify(frozen['graph'])
 if sha(graph_path)!=frozen['graph']['sha256']:raise ValueError('graph_not_frozen')
 graph=jread(graph_path)
 if graph['protocol_sha256']!=p['acquisition_protocol']['sha256']:raise ValueError('acquisition_protocol_mismatch')
 verify(p['acquisition_protocol'])
 for r in graph['records']:
  if r.get('cache_path'):verify({**r,'path':r['cache_path']})
 vroot=Path(p['validation_root']);vroot.mkdir(exist_ok=False)
 records=[];begin=utc();run_baseline=read_snapshot();cleanup_blocked=False
 for i,s in enumerate(p['sources']):
  snap=read_snapshot();reason=policy_reason(snap,run_baseline,WORKER_RESERVE_BYTES)
  if shutil.disk_usage(root).free<p['limits']['min_shm_free_bytes']:reason='shared_memory_reserve'
  if cleanup_blocked:reason='previous_worker_cleanup_unconfirmed'
  base={'source_file':s['source_file'],'source_sha256':s['source']['sha256'],'expected_nonfraction':s['expected_nonfraction'],'prelaunch_memory_snapshot':snap}
  if reason:
   records.append({**base,'technical_status':'prelaunch_'+reason,'validation_status':'not_executed_to_completion'})
   jwrite(vroot/'progress.json',{'records':records});continue
  start=time.monotonic();peakrss=0;peakcgroup=snap['memory_current_bytes'];peakproxy=snap['pressure_proxy_bytes'];peakcomponents=dict(snap['components']);reason=None;sample_count=0
  stdout=vroot/(s['source_file']+'.process.log');telemetry=vroot/(s['source_file']+'.memory.jsonl');env=os.environ.copy();env['PYTHONDONTWRITEBYTECODE']='1';env['PYTHONPATH']=p['arelle_runtime'];env['OPENBLAS_NUM_THREADS']='1';env['OMP_NUM_THREADS']='1'
  with stdout.open('xb') as log,telemetry.open('x') as memorylog:
   memorylog.write(json.dumps({'phase':'prelaunch','snapshot':snap})+'\n');memorylog.flush()
   process=subprocess.Popen([sys.executable,__file__,'worker','--protocol',protocol_path,'--index',str(i)],stdout=log,stderr=subprocess.STDOUT,env=env,start_new_session=True)
   group_anchor=bind_group(process.pid)
   while True:
    rss_snapshot=read_group_rss(process.pid,anchor=group_anchor);exited=process.poll() is not None;rss_reason=rss_reading_reason(rss_snapshot,exited);rss=rss_snapshot.get('rss_bytes')
    snap=read_snapshot();sample_count+=1
    if rss is not None:peakrss=max(peakrss,rss)
    if snap.get('telemetry_ok'):
     peakcgroup=max(peakcgroup,snap['memory_current_bytes']);peakproxy=max(peakproxy,snap['pressure_proxy_bytes'])
     for key in COMPONENTS:peakcomponents[key]=max(peakcomponents[key],snap['components'][key])
    memorylog.write(json.dumps({'phase':'postexit' if exited else 'monitor','elapsed_seconds':time.monotonic()-start,'worker_RSS_bytes':rss,'process_group_RSS_snapshot':rss_snapshot,'snapshot':snap})+'\n');memorylog.flush()
    reason=rss_reason or policy_reason(snap,run_baseline)
    if time.monotonic()-start>p['limits']['source_wall_seconds']:reason='timeout'
    elif rss is not None and rss>p['limits']['source_RSS_bytes']:reason='memory_limit'
    elif shutil.disk_usage(root).free<p['limits']['min_shm_free_bytes']:reason='shared_memory_reserve'
    if reason or exited:
     if not exited or any(m['state'] not in {'Z','X','x'} for m in rss_snapshot.get('members',[])):
      try:os.killpg(process.pid,signal.SIGKILL)
      except ProcessLookupError:pass
      try:process.wait(timeout=2)
      except subprocess.TimeoutExpired:pass
     cleanup_snapshot=read_group_rss(process.pid,anchor=group_anchor)
     cleanup_start=time.monotonic()
     while cleanup_snapshot.get('telemetry_ok') and any(m['state'] not in {'Z','X','x'} for m in cleanup_snapshot.get('members',[])) and time.monotonic()-cleanup_start<2:
      time.sleep(.05);cleanup_snapshot=read_group_rss(process.pid,anchor=group_anchor)
     cleanup_blocked=not cleanup_snapshot.get('telemetry_ok') or any(m['state'] not in {'Z','X','x'} for m in cleanup_snapshot.get('members',[]))
     if cleanup_blocked:reason='worker_cleanup_unconfirmed'
     memorylog.write(json.dumps({'phase':'cleanup','process_group_RSS_snapshot':cleanup_snapshot,'confirmed_no_live_owned_members':not cleanup_blocked})+'\n');memorylog.flush()
     break
    time.sleep(.1)
  closure=graph['source_closures'][i];r={**base,'watchdog_stop_reason':reason,'technical_status':reason or ('completed' if process.returncode==0 else 'processor_failure'),'returncode':process.returncode,'elapsed_seconds':round(time.monotonic()-start,3),'peak_RSS_bytes':peakrss,'RSS_monitor':'namespace-aware owned process group; summed memberRSS','worker_cleanup_confirmed':not cleanup_blocked,'peak_cgroup_bytes':peakcgroup,'peak_pressure_proxy_bytes':peakproxy,'peak_memory_components':peakcomponents,'last_memory_snapshot':snap,'memory_sample_count':sample_count,'captured_closure_complete':closure['captured_closure_complete'],'unavailable_dependency_count':len(closure['unavailable_or_unparsed_URLs']),'external_process_log':binding(stdout),'external_memory_telemetry':binding(telemetry)}
  summary=vroot/s['source_file']/'worker_summary.json'
  if summary.exists():
   details=jread(summary);r.update(details)
   r.update(classify_result(details,closure['captured_closure_complete'],s['expected_nonfraction'],r['technical_status']))
  else:r['validation_status']='not_executed_to_completion'
  records.append(r);jwrite(vroot/'progress.json',{'records':records});print(json.dumps({k:r[k] for k in ['source_file','technical_status','validation_status','elapsed_seconds','peak_RSS_bytes']}),flush=True)
 totals=Counter()
 for r in records:totals.update(r.get('counts',{}))
 result={'schema_version':'full_dts_execution_v16_3','started_utc':begin,'finished_utc':utc(),'protocol_sha256':sha(protocol_path),'acquisition_protocol_sha256':p['acquisition_protocol']['sha256'],'amendment':'Physical XML traversal and namespace-aware RSS monitoring; all earlier attempts preserved; source, numeric and Arelle options unchanged.','dependency_graph_sha256':sha(graph_path),'source_denominator':len(p['sources']),'expected_nonfraction_denominator':sum(s['expected_nonfraction'] for s in p['sources']),'records':records,'totals':dict(totals),'technical_status_counts':dict(Counter(r['technical_status'] for r in records)),'validation_status_counts':dict(Counter(r['validation_status'] for r in records)),'run_baseline_memory_snapshot':run_baseline,'resource_policy':p['resource_policy'],'original_attempt':p['original_attempt'],'prior_attempts':p['prior_attempts'],'natural_QA_predictions':0,'source_identity_verified':False,'SEC_filing_rule_compliance_evaluated':False,'interpretation':'Conventional Arelle comparison on fixed HF bytes against candidate declared taxonomy resources under pinned offline options. No financial truth, natural QA or method novelty claim. The old total-current soft limit does not govern this amended attempt.'}
 jwrite(output,result,exclusive=True);print(json.dumps({'finished':True,'technical_status_counts':result['technical_status_counts'],'validation_status_counts':result['validation_status_counts'],'totals':dict(totals)}),flush=True)

if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['prepare','validate','worker']);parser.add_argument('--protocol',required=True);parser.add_argument('--graph');parser.add_argument('--output');parser.add_argument('--index',type=int);args=parser.parse_args()
 if args.mode=='prepare':prepare(args.protocol,args.graph)
 elif args.mode=='validate':validate(args.protocol,args.graph,args.output)
 else:worker(args.protocol,args.index)
