#!/usr/bin/env python3
"""Private mechanical admission worksheet. No rendering or semantic admission."""
from pathlib import Path
from datetime import datetime, timezone
import collections, hashlib, json, re, sys
ROOT=Path('/dev/shm/wikigraph_v15/WikiGraphRag/acl2027_temporal_state')
EXT=Path('/dev/shm/wikigraph_v15/external')
OUT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'src'))
from temporal_state import typed_reader_v15_1 as br
from lxml import etree
XH='{http://www.w3.org/1999/xhtml}'
IX='{http://www.xbrl.org/2013/inlineXBRL}'
sha=lambda b:hashlib.sha256(b).hexdigest()
def bind(p):
 p=Path(p);b=p.read_bytes();return {'filename':p.name,'path':str(p),'bytes':len(b),'sha256':sha(b)}
def write(p,d):
 if Path(p).exists():
  assert json.loads(Path(p).read_text())==json.loads(json.dumps(d))
  return
 with Path(p).open('x') as f:json.dump(d,f,indent=2,ensure_ascii=False,sort_keys=True);f.write('\n')
def anchor(b,n):return {'byte_start':n.start,'byte_stop':n.stop,'span_sha256':sha(b[n.start:n.stop])}
def opening(b,n):return {'dom_path':n.path,'byte_start':n.start,'byte_stop':n.opening_stop,'span_sha256':sha(b[n.start:n.opening_stop])}
def qname(b,n):return re.match(rb'<([^\s/>]+)',b[n.start:n.opening_stop]).group(1)
def props(n):
 result=[]
 for p in n.attrs.get('style','').split(';'):
  if ':' in p:
   k,v=p.split(':',1);result.append((k.strip().lower(),v.strip()))
 return result
def owntext(n):
 # Independently traverse Expat parts using the frozen author packet text rule.
 def walk(x):
  a=[]
  for p in x.parts:
   if isinstance(p,str):a.append(p);continue
   if p.tag==XH+'br':a.append('\n');continue
   if p.tag in {XH+'table',XH+'script',XH+'style'}:continue
   sep=p.tag in {XH+t for t in ('p','div','h1','h2','h3','h4','h5','h6')}
   if sep:a.append('\n')
   a.extend(walk(p))
   if sep:a.append('\n')
  return a
 return re.sub('[ \t\r\n]+',' ',''.join(walk(n))).strip(' \t\r\n')
def nearest(n,tags):return next((x for x in n.ancestors() if x.tag in tags),None)
def independent_paths(root):
 out={}
 def rec(n,path):
  out[path]=n;counts=collections.Counter()
  for c in n:
   if not isinstance(c.tag,str):continue
   counts[c.tag]+=1;rec(c,path+'/'+c.tag+'['+str(counts[c.tag])+']')
 rec(root,'/'+root.tag+'[1]');return out
RISK={'display','visibility','position','z-index','opacity','overflow','overflow-x','overflow-y','clip','clip-path','transform','direction','writing-mode','order','flex','grid','content','top','bottom','left','right'}
PROTOCOL=ROOT/'data/reader_binding_v17/source_render_natural_protocol_v17.json'
RESULT=ROOT/'results/source_render_natural_execution_v17.json'
INDEX=EXT/'source_render_natural_v17/attempt01/review_index.json'
bindings={str(p):bind(p) for p in [PROTOCOL,RESULT,INDEX,ROOT/'docs/source_render_admission_contract_v17.txt',ROOT/'data/reader_binding_v17/source_render_admission_review_plan.json']}
assert bindings[str(PROTOCOL)]['sha256']=='a030efb9dcc9d144eee72595c714dfa64eeb05bccdde0b7fa43b5357f266f4c7'
assert bindings[str(RESULT)]['sha256']=='14b97956be21d0e91a2d6f299bc2f13ab4f08c104caf2e0bc164dabd1ed38a54'
assert bindings[str(INDEX)]['sha256']=='85172ba6d24100df49647b913c324107d36712495f14ded487c04ca44eca54bc'
protocol=json.loads(PROTOCOL.read_text());result=json.loads(RESULT.read_text());index=json.loads(INDEX.read_text())
sources=[];public=[]
for si in range(6):
 ps=protocol['sources'][si];rs=result['sources'][si];ix=index['sources'][si]
 assert ix['source_index']==si and ps['source_sha256']==rs['source_sha256']==ix['source_sha256']
 source_path=EXT/'fresh_sources'/ps['external_filename'];source=source_path.read_bytes()
 assert len(source)==ps['source_bytes'] and sha(source)==ps['source_sha256']
 nodes=br._parse(source);lookup={n.path:n for n in nodes}
 independent=independent_paths(etree.fromstring(source,etree.XMLParser(resolve_entities=False,no_network=True,load_dtd=False)))
 assert set(lookup)==set(independent)
 assert all(n.tag==independent[p].tag and n.attrs==dict(independent[p].attrib) for p,n in lookup.items())
 heads=[n for n in nodes if n.tag==XH+'head'];styles=[n for n in nodes if n.tag==XH+'style']
 headstyles=[n for n in styles if any(a in heads for a in n.ancestors())]
 links=[n for n in nodes if n.tag==XH+'link'];scripts=[n for n in nodes if n.tag==XH+'script']
 stylelinks=[n for n in links if 'stylesheet' in n.attrs.get('rel','').lower().split()]
 # The six actual sources have no sheet rules, script-driven DOM changes, or CSS links.
 deps={'all_style_elements':len(styles),'head_style_elements':len(headstyles),'out_of_head_style_elements':len(styles)-len(headstyles),'stylesheet_links':len(stylelinks),'all_link_elements':len(links),'script_elements':len(scripts),'base_elements':sum(n.tag==XH+'base' for n in nodes),'processing_instruction_count':source.count(b'<?')-(1 if source.lstrip().startswith(b'<?xml') else 0)}
 assert deps['all_style_elements']==deps['stylesheet_links']==deps['script_elements']==deps['base_elements']==deps['processing_instruction_count']==0
 authorfile=EXT/'reader_pool_attempt02'/(Path(ps['external_filename']).stem+'.author.jsonl')
 ab=next(x for x in protocol['author_packet_bindings'] if x['filename']==authorfile.name)
 assert bind(authorfile)['sha256']==ab['sha256'] and authorfile.stat().st_size==ab['bytes']
 authors=[json.loads(line) for line in authorfile.read_text().splitlines()]
 tables={a['dom_path']:a for a in authors};paragraphs={p['dom_path']:p for a in authors for p in a['neighbor_paragraphs']}
 facts={n.path:idx for idx,n in enumerate(n for n in nodes if n.tag==IX+'nonFraction')}
 assert [b['block_id'] for b in ps['blocks']]==[b['block_id'] for b in rs['blocks']]==[b['block_id'] for b in ix['blocks']]
 srec={'source_index':si,'source_path':ps['source_path'],'source_identity':bind(source_path),'author_packet_identity':bind(authorfile),'source_css_dependency_inventory':deps,'complete_lxml_expat_element_locator_attribute_comparisons':len(nodes),'blocks':[]}
 for bi,(p,r,i) in enumerate(zip(ps['blocks'],rs['blocks'],ix['blocks'])):
  n=lookup[p['dom_path']];aa=anchor(source,n)
  assert aa==p['anchor']==r['anchor']==i['anchor'] and n.path==r['dom_path']==i['dom_path']
  artifacts={k:dict(v) for k,v in i['artifacts'].items()}
  for v in [*artifacts.values(),*i['pngs']]:
   b=Path(v['path']).read_bytes();assert len(b)==v['bytes'] and sha(b)==v['sha256']
  receiptpath=Path(i['receipt_path']);assert json.loads(receiptpath.read_text())==r
  fragment=Path(artifacts['source-fragment.xml']['path']).read_bytes();assert fragment==source[n.start:n.stop]
  ancestors=list(reversed(list(n.ancestors())))
  expected=(source[ancestors[0].start:ancestors[0].opening_stop]+b'<head>'+b''.join(source[x.start:x.stop] for x in headstyles)+b'</head>'+b''.join(source[x.start:x.opening_stop] for x in ancestors[1:])+fragment+b''.join(b'</'+qname(source,x)+b'>' for x in reversed(ancestors)))
  assembly=Path(artifacts['source-assembly.html']['path']).read_bytes();assert expected==assembly and sha(assembly)==r['source_assembly_sha256']
  render=Path(artifacts['source-block.html']['path']).read_bytes()
  assert render==assembly.replace(b'<head>',b'<head><meta http-equiv="Content-Type" content="text/html; charset=utf-8"/>',1) and sha(render)==r['render_html_sha256']
  assert r['ancestor_openings']==[opening(source,x) for x in ancestors]
  assert not r['head_stylesheets'] and not r['external_stylesheet_links_not_loaded'] and not r['out_of_head_style_blocks_not_added']
  css=[];risk=[];hidden=[];fontfamilies=set();allprops=collections.defaultdict(set)
  for x in [*ancestors,*n.walk()]:
   decl=props(x)
   for k,v in decl:allprops[k].add(v)
   if x.attrs.get('style'):
    cr={'dom_path':x.path,'tag':x.tag,'location':'ancestor_wrapper' if x in ancestors else 'block','style':x.attrs['style'],'declarations':decl,'opening':opening(source,x),'source_text':x.text() if x not in ancestors else None}
    css.append(cr)
    if any(k in RISK for k,v in decl):
     rr={k:v for k,v in cr.items() if k!='source_text'};rr['risk_declarations']=[(k,v) for k,v in decl if k in RISK];rr['subtree_text']=x.text();rr['subtree_numeric_occurrences']=sum(y.tag==IX+'nonFraction' for y in x.walk());risk.append(rr)
   for k,v in decl:
    if k=='font-family':fontfamilies.add(v)
   if any(k=='display' and v.lower()=='none' for k,v in decl):
    h={'dom_path':x.path,'non_whitespace_text_characters':len(x.text().strip()),'numeric_occurrences':sum(y.tag==IX+'nonFraction' for y in x.walk()),'id_or_class_hooks':any('id' in y.attrs or 'class' in y.attrs for y in x.walk()),'inside_block':n.start<=x.start and x.stop<=n.stop};hidden.append(h)
  checks={'source_identity':True,'protocol_result_index_block_identity':True,'all_render_artifact_hashes':True,'source_fragment_exact_bytes':True,'ancestor_openings_exact_bytes_and_order':True,'head_styles_preserved':True,'assembly_exact_source_fragment_wrappers_and_balancing_closures':True,'render_input_only_generated_utf8_meta_added':True,'no_source_css_stylesheet_script_or_base_dependencies_omitted':True,'table_author_row_cell_span_text_matching':None,'numeric_occurrences_map_uniquely_to_source_row_cell':None,'paragraph_author_text_matching':None}
  rec={'source_index':si,'block_index':bi,'block_id':p['block_id'],'role':p['role'],'dom_path':n.path,'anchor':aa,'candidate_table_ordinals':p['candidate_table_ordinals'],'artifacts':artifacts,'receipt':bind(receiptpath),'raster_bindings':i['pngs'],'source_text':owntext(n),'ancestor_wrappers':[{'opening':opening(source,x),'source_opening':source[x.start:x.opening_stop].decode()} for x in ancestors],'source_css':css,'css_risks':risk,'hidden_descendants':hidden,'all_inline_property_values':{k:sorted(v) for k,v in sorted(allprops.items())},'font_families_requested':sorted(fontfamilies),'font_identity_verified':False,'css_dependency_conclusion':'No source style rules, external CSS, scripts, base URL or processing instruction dependencies exist; exact inline CSS and wrappers retained. Font substitution and fragment geometry require raster review.','checks':checks,'rows':[],'numeric_occurrence_mappings':[],'visual_review_performed_by_this_worksheet':False,'semantic_attachment_reviewed':False}
  if p['role']=='selected_table':
   a=tables[n.path];assert a['anchor']==aa
   rows=[x for x in n.walk() if x.tag==XH+'tr' and nearest(x,{XH+'table'}) is n];assert len(rows)==len(a['rows'])
   cellmap={}
   for ri,(row,ar) in enumerate(zip(rows,a['rows'])):
    assert anchor(source,row)==ar['anchor'] and row.path==n.path+ar['path_from_table']
    cells=[x for x in row.walk() if x.tag in {XH+'td',XH+'th'} and nearest(x,{XH+'tr'}) is row and nearest(x,{XH+'table'}) is n];assert len(cells)==len(ar['cells'])
    rr={'row_index_zero_based':ri,'dom_path':row.path,'anchor':anchor(source,row),'source_text':owntext(row),'cells':[]}
    for ci,(cell,ac) in enumerate(zip(cells,ar['cells'])):
     assert anchor(source,cell)==ac['anchor'] and cell.path==row.path+ac['path_from_row'] and owntext(cell)==ac['text']
     assert cell.attrs.get('rowspan')==ac['rowspan']['declared'] and cell.attrs.get('colspan')==ac['colspan']['declared']
     cr={'cell_index_zero_based':ci,'dom_path':cell.path,'anchor':anchor(source,cell),'tag':cell.tag,'source_text':owntext(cell),'raw_descendant_text':cell.text(),'source_opening':source[cell.start:cell.opening_stop].decode(),'rowspan':ac['rowspan'],'colspan':ac['colspan'],'source_attributes':cell.attrs,'source_numeric_occurrences':[]}
     rr['cells'].append(cr);cellmap[cell.path]=(ri,ci,cr)
    assert '\t'.join(c['source_text'] for c in rr['cells'])==ar['text']
    rec['rows'].append(rr)
   selectedfacts=[x for x in n.walk() if x.tag==IX+'nonFraction' and nearest(x,{XH+'table'}) is n]
   for x in selectedfacts:
    cell=nearest(x,{XH+'td',XH+'th'});assert cell is not None and cell.path in cellmap
    ri,ci,cr=cellmap[cell.path]
    f={'source_occurrence_ordinal_zero_based':facts[x.path],'dom_path':x.path,'anchor':anchor(source,x),'row_index_zero_based':ri,'cell_index_zero_based':ci,'cell_dom_path':cell.path,'raw_source_lexical_text':x.text(),'source_attributes':x.attrs,'source_opening':source[x.start:x.opening_stop].decode(),'numeric_normalization_used':False}
    rec['numeric_occurrence_mappings'].append(f);cr['source_numeric_occurrences'].append(f)
   assert len(selectedfacts)==len(rec['numeric_occurrence_mappings'])
   checks['table_author_row_cell_span_text_matching']=True;checks['numeric_occurrences_map_uniquely_to_source_row_cell']=True
  else:
   ap=paragraphs[n.path];assert ap['anchor']==aa and ap['text']==owntext(n);checks['paragraph_author_text_matching']=True
  fn=f'source_{si:02d}.block_{bi:02d}.{p["block_id"]}.json';write(OUT/fn,rec)
  lines=[f'Source {si:02d} block {bi:02d} {p["block_id"]}',f'Exact source locator: {n.path}','Mechanical source worksheet; raster geometry and admission remain pending.','',f'SOURCE TEXT: {owntext(n)}','', 'CSS/WRAPPER RISKS:']
  for rr in risk:lines.append(f'{rr["location"]} {rr["dom_path"]}: {rr["risk_declarations"]}; numeric occurrences={rr["subtree_numeric_occurrences"]}')
  lines+=['','SOURCE ROW/CELL ORDER (zero-based; all blank/hidden cells retained):']
  for row in rec['rows']:
   lines.append(f'ROW {row["row_index_zero_based"]}:')
   for c in row['cells']:
    lines.append(f'  CELL {c["cell_index_zero_based"]} rowspan={c["rowspan"]} colspan={c["colspan"]} style={c["source_attributes"].get("style","")} TEXT={c["source_text"]!r}')
    for f in c['source_numeric_occurrences']:lines.append(f'    NONFRACTION source ordinal={f["source_occurrence_ordinal_zero_based"]} lexical={f["raw_source_lexical_text"]!r} locator={f["dom_path"]}')
  (OUT/fn.replace('.json','.txt')).write_text('\n'.join(lines)+'\n')
  counts={'rows':len(rec['rows']),'cells':sum(len(x['cells']) for x in rec['rows']),'numeric_occurrences':len(rec['numeric_occurrence_mappings']),'inline_style_nodes':len(css),'risk_style_nodes':len(risk),'hidden_descendants':len(hidden),'hidden_nonempty_descendants':sum(x['non_whitespace_text_characters']>0 or x['numeric_occurrences']>0 for x in hidden),'positioned_nodes':sum(any(k=='position' for k,v in rr['risk_declarations']) for rr in risk),'pages':r['pages']}
  pub={'source_index':si,'block_index':bi,'block_id':p['block_id'],'role':p['role'],'dom_path':n.path,'anchor':aa,'candidate_table_ordinals':p['candidate_table_ordinals'],'checks':checks,'counts':counts,'css_risk_categories':sorted({k for rr in risk for k,v in rr['risk_declarations']}),'private_worksheet':bind(OUT/fn),'private_readable_worksheet':bind(OUT/fn.replace('.json','.txt')),'visual_review_performed':False,'admission_decision_made':False}
  public.append(pub);srec['blocks'].append(pub)
 write(OUT/f'source_{si:02d}.inventory.json',srec);sources.append(srec)
write(OUT/'worksheet_index.json',{'schema_version':'private_css_binding_worksheet_index_v17','source_indices':list(range(6)),'input_bindings':bindings,'sources':sources,'scope':'Source identity, CSS dependency inventory and source row/cell/occurrence mappings only. No raster inspection or admission decisions.'})
write(OUT/'public_receipt_draft.json',{'schema_version':'source_render_css_binding_review_00_05_v17','created_at_utc':datetime.now(timezone.utc).isoformat(),'source_indices':list(range(6)),'input_bindings':bindings,'source_count':6,'block_count':len(public),'table_count':sum(x['role']=='selected_table' for x in public),'block_checks':public,'source_dependency_inventories':[{'source_index':s['source_index'],'source_path':s['source_path'],'source_sha256':s['source_identity']['sha256'],'counts':s['source_css_dependency_inventory'],'complete_lxml_expat_element_locator_attribute_comparisons':s['complete_lxml_expat_element_locator_attribute_comparisons']} for s in sources],'private_index':bind(OUT/'worksheet_index.json'),'worksheet_builder':bind(Path(__file__)),'visual_review_performed':False,'final_admission_decisions':0,'semantic_attachment_decisions':0,'financial_truth_claims':False,'normalized_numeric_values_used':False,'questions_authored':0,'model_calls':0})
print(json.dumps({'sources':6,'blocks':len(public),'tables':sum(x['role']=='selected_table' for x in public),'rows':sum(x['counts']['rows'] for x in public),'cells':sum(x['counts']['cells'] for x in public),'numeric_occurrences':sum(x['counts']['numeric_occurrences'] for x in public),'hidden_descendants':sum(x['counts']['hidden_descendants'] for x in public),'hidden_nonempty_descendants':sum(x['counts']['hidden_nonempty_descendants'] for x in public),'positioned_blocks':[(x['source_index'],x['block_id']) for x in public if x['counts']['positioned_nodes']],'index':str(OUT/'worksheet_index.json')}))
