"""Authored-only pre-author registry and projection controls."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import build_pre_author_bundles_v17 as m

SHA='a'*64
def anchor(start,stop):return {'byte_start':start,'byte_stop':stop,'span_sha256':'b'*64}
def cell(text,start,stop):
    return {'text':text,'anchor':anchor(start,stop),'rowspan':{'declared':None,'parsed':1,'status':'absent_default_one'},
            'colspan':{'declared':None,'parsed':1,'status':'absent_default_one'}}
def block():
    return {'block_id':'table_1','kind':'table','dom_path':'/authored/table[1]','anchor':anchor(0,100),
            'text':'Revenue 12 Other 4','rows':[{'text':'Revenue 12','anchor':anchor(10,50),
              'cells':[cell('Revenue',10,20),cell('12',20,40)]}]}
def fact(ordinal=0,start=25,stop=30,status='normalized'):
    return {'fact_ordinal':ordinal,'source_sha256':SHA,'anchor':anchor(start,stop),
      'numeric_status':status,'binding_status':'reported_aspects_resolved','lexical_text':'12',
      'normalized_value':'12','reported_aspects':{'concept':'{http://example.test}Revenue',
       'entity':{'scheme':'authored','identifier':'entity'},'period':{'kind':'duration','lexemes':{'startDate':'2099-01-01','endDate':'2099-12-31'}},
       'unit':{'shape':'simple_product','measures':['{http://www.xbrl.org/2003/iso4217}USD'],'numerator_measures':[],'denominator_measures':[]},'dimensions':[]},
      'resolved_aspects':{'concept':True,'entity':True,'period':True,'unit':True},'issues':[]}

class BundleControls(unittest.TestCase):
 def test_author_projection_omits_typed_and_legacy_card(self):
    b=block();b.update(typed={'normalized_value':'999999'},source_card={'internal_fiscal_year_values':['2099']},gold_query='SECRET')
    x=m.author_projection('p00t1','s00',SHA,[b]);s=json.dumps(x)
    for forbidden in ['999999','internal_fiscal','gold_query','SECRET','normalized_value']:self.assertNotIn(forbidden,s)
    self.assertIsNone(x['identity_card']);self.assertFalse(x['author_release_allowed'])
 def test_nested_extra_keys_cannot_leak(self):
    b=block();b['anchor']['context_id']='SECRET';b['rows'][0]['answer']='SECRET'
    b['rows'][0]['cells'][0]['rowspan']['gold']='SECRET';b['rows'][0]['cells'][0]['typed']='SECRET'
    self.assertNotIn('SECRET',json.dumps(m.author_projection('p','s',SHA,[b])))
 def test_original_visible_quantity_retained(self):
    x=m.author_projection('p','s',SHA,[block()]);self.assertEqual(x['blocks'][0]['text'],'Revenue 12 Other 4')
 def test_projection_has_no_shared_mutable_rows(self):
    b=block();x=m.author_projection('p','s',SHA,[b]);x['blocks'][0]['rows'][0]['cells'][0]['rowspan']['parsed']=8
    self.assertEqual(b['rows'][0]['cells'][0]['rowspan']['parsed'],1)
 def test_nil_and_unsupported_facts_retained(self):
    fs=[fact(),fact(1,55,60,'nil'),fact(2,70,75,'unsupported')]
    chosen=m.collect_facts(fs,[block()],SHA);r,p,g=m.candidate_registry('p','s',SHA,[block()],chosen)
    self.assertEqual(len(r['facts']),3);self.assertEqual([f['value'] for f in p['facts']],['12',None,None])
 def test_overlapping_blocks_dedupe_same_occurrence_only(self):
    b=block();b2=deepcopy(b);b2['block_id']='overlap'
    chosen=m.collect_facts([fact(),fact(1,55,60)],[b,b2],SHA)
    self.assertEqual(len(chosen),2);self.assertEqual(chosen[0]['block_ids'],['table_1','overlap'])
 def test_conflicting_ordinal_rejected(self):
    f=fact();other=deepcopy(f);other['normalized_value']='13'
    with self.assertRaisesRegex(ValueError,'conflicting_duplicate'):m.collect_facts([f,other],[block()],SHA)
 def test_wrong_source_rejected(self):
    with self.assertRaisesRegex(ValueError,'wrong_fact_source'):m.collect_facts([fact()],[block()],'c'*64)
 def test_outside_fact_not_acquired(self):
    self.assertEqual(m.collect_facts([fact(0,101,109)],[block()],SHA),[])
 def test_cross_boundary_fact_not_acquired(self):
    self.assertEqual(m.collect_facts([fact(0,95,105)],[block()],SHA),[])
 def test_row_label_has_no_numeric_cell_text(self):
    c=m.collect_facts([fact()],[block()],SHA)
    self.assertEqual(m.labels_for(c[0]['fact'],[block()],c),['Revenue'])
 def test_blank_row_label_not_replaced_by_total(self):
    b=block();b['rows'][0]['cells'][0]['text']=''
    c=m.collect_facts([fact()],[b],SHA);self.assertEqual(m.labels_for(c[0]['fact'],[b],c),[])
 def test_population_null_even_empty_dimensions(self):
    c=m.collect_facts([fact()],[block()],SHA);r,p,g=m.candidate_registry('p','s',SHA,[block()],c)
    self.assertIsNone(p['facts'][0]['bindings']['population']);self.assertIsNone(r['facts'][0]['population_binding'])
    self.assertTrue(g['interface_shape_valid']);self.assertFalse(g['population_bindings_complete'])
 def test_declared_metadata_not_called_visible(self):
    c=m.collect_facts([fact()],[block()],SHA);r,p,g=m.candidate_registry('p','s',SHA,[block()],c)
    self.assertFalse(r['facts'][0]['visible_period_unit_scope_verified'])
    self.assertTrue(all(w['text'].startswith('Declared source metadata') for w in p['witnesses']))
 def test_unresolved_aspect_stays_null(self):
    f=fact();f['resolved_aspects']['period']=False;c=m.collect_facts([f],[block()],SHA)
    _,p,_=m.candidate_registry('p','s',SHA,[block()],c);self.assertIsNone(p['facts'][0]['bindings']['period'])
 def test_65_facts_retained_without_interface_truncation(self):
    fs=[fact(i,25,30) for i in range(65)];c=m.collect_facts(fs,[block()],SHA)
    r,p,g=m.candidate_registry('p','s',SHA,[block()],c)
    self.assertEqual(len(r['facts']),65);self.assertIsNone(p);self.assertFalse(g['interface_shape_valid'])
 def test_whole_heading_row_kept(self):
    b=block();b['rows'][0]['path_from_table']='/tr[1]'
    r=m.block_from_view({'rows':b['rows'],'dom_path':'/table[1]'},'table_row','caption',0)
    self.assertEqual(r['text'],'Revenue 12');self.assertEqual(len(r['rows']),1)
 def test_whole_paragraph_kept(self):
    v={'text':'Long original statement.','dom_path':'/p[1]','anchor':anchor(0,100)}
    self.assertEqual(m.block_from_view(v,'paragraph','p1')['text'],v['text'])
 def test_table_dependency_always_rejects_numeric_occurrence(self):
    for kind in ('table','table_row'):
      with self.subTest(kind=kind):
        with self.assertRaisesRegex(ValueError,'dependency_would_import'):
          m.check_dependency(kind,block(),[fact()])
 def test_paragraph_dependency_retains_numeric_occurrence(self):
    m.check_dependency('paragraph',block(),[fact()])
    self.assertEqual(len(m.collect_facts([fact()],[block()],SHA)),1)
 def test_no_overwrite(self):
    with tempfile.TemporaryDirectory() as d:
      p=Path(d)/'x.json';m.write_new(p,{'a':1})
      with self.assertRaises(FileExistsError):m.write_new(p,{'a':2})
 def test_empty_protocol_bindings_rejected_before_source(self):
    with tempfile.TemporaryDirectory() as d:
      p=Path(d);(p/'protocol.json').write_text(json.dumps({'schema_version':'preauthor_bundle_materialization_protocol_v17'}))
      with self.assertRaisesRegex(ValueError,'binding_population'):m.build(p/'protocol.json',p,p,p,p/'out',p/'result')
      self.assertFalse((p/'out').exists())

if __name__=='__main__':unittest.main()
