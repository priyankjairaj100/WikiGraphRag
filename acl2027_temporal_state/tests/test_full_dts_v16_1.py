"""Authored controls for safe capture, exact comparison and process guards."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from decimal import localcontext

SCRIPT=Path(__file__).resolve().parents[1]/'scripts/run_full_dts_v16_1.py'
spec=importlib.util.spec_from_file_location('full_dts_v16_1',SCRIPT);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)

class FullDtsAmendedControls(unittest.TestCase):
 def test_safe_standard_url(self):
  self.assertEqual(mod.safe_url('https://xbrl.fasb.org/us-gaap/2024/elts/a.xsd#abc'),'https://xbrl.fasb.org/us-gaap/2024/elts/a.xsd')
 def test_reject_external_or_credential_URLs(self):
  for url in ['file:///etc/passwd','http://localhost/x','https://evil.example/x','https://user@www.xbrl.org/x','https://www.xbrl.org:444/x','https://www.xbrl.org/x?q=1']:
   with self.subTest(url=url),self.assertRaises(ValueError):mod.safe_url(url)
 def test_reject_traversal_and_encoded_separator(self):
  for suffix in ['../secret','%2e%2e/secret','a%2fb.xsd','a%5cb.xsd','a\\b.xsd','a%00b.xsd']:
   with self.subTest(suffix=suffix),self.assertRaises(ValueError):mod.safe_url('https://www.xbrl.org/'+suffix)
 def test_cache_matches_Arelle_linux_encoding(self):
  p=mod.cache_path('/tmp/example','https://www.xbrl.org/a:b^c.xsd')
  self.assertEqual(str(p),'/tmp/example/https/www.xbrl.org/a^058b^094c.xsd')
 def test_exact_decimal_not_ambient_precision(self):
  with localcontext() as ctx:
   ctx.prec=3;self.assertEqual(mod.canonical_decimal('123456789012345678901234567890.00'),'123456789012345678901234567890')
  self.assertEqual(mod.canonical_decimal('-0.000'),'0')
 def test_nonfinite_refused(self):
  with self.assertRaises(ValueError):mod.canonical_decimal('Infinity')
 def test_xml_base_edges_and_dedup(self):
  b=b'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" xml:base="sub/"><xs:import schemaLocation="a.xsd"/><xs:import schemaLocation="a.xsd"/></xs:schema>'
  with tempfile.TemporaryDirectory(dir='/dev/shm') as d:
   p=Path(d)/'a.xsd';p.write_bytes(b);rows=mod.xml_edges(p,'https://www.xbrl.org/root.xsd')
   self.assertEqual(len(rows),1);self.assertEqual(rows[0][2],'https://www.xbrl.org/sub/a.xsd')
 def test_DTD_refused(self):
  with tempfile.TemporaryDirectory(dir='/dev/shm') as d:
   p=Path(d)/'a.xml';p.write_text('<!DOCTYPE a [<!ENTITY b "x">]><a/>')
   with self.assertRaises(ValueError):mod.xml_edges(p,'https://www.xbrl.org/a.xml')
 def test_hash_change_fails(self):
  with tempfile.TemporaryDirectory(dir='/dev/shm') as d:
   p=Path(d)/'a';p.write_text('a');b=mod.binding(p);mod.verify(b);p.write_text('b')
   with self.assertRaises(ValueError):mod.verify(b)
 def test_process_resource_guards(self):
  limits={'source_wall_seconds':300,'source_RSS_bytes':200,'cgroup_total_bytes':725,'min_shm_free_bytes':256}
  self.assertIsNone(mod.resource_reason(300,200,725,256,limits))
  for args,want in [((301,0,0,256),'timeout'),((0,201,0,256),'memory_limit'),((0,0,726,256),'cgroup_memory_limit'),((0,0,None,256),'cgroup_monitor_unavailable'),((0,0,0,255),'shared_memory_reserve')]:
   self.assertEqual(mod.resource_reason(*args,limits),want)
 def test_dom_path_counts_expanded_names(self):
  root=mod.etree.fromstring(b'<r xmlns:x="urn:x"><x:n/><n/><x:n/></r>');paths=mod.dom_paths(root)
  self.assertEqual(paths[root[2]],'/r[1]/{urn:x}n[2]')

class FullDtsReportingControls(unittest.TestCase):
 def good(self):
  return {'session_run_return':True,'loaded_models':1,'counts':{'observed_nonfraction':3,'model_errors':0},'log_severity_code_counts':{},'log_parse_error':None,'dependency_pin_failures':[]}
 def test_complete_clean_requires_every_gate(self):
  self.assertEqual(mod.classify_result(self.good(),True,3)['validation_status'],'completed_without_errors')
  for key,value in [('session_run_return',False),('loaded_models',0),('log_parse_error','JSONDecodeError'),('dependency_pin_failures',[{'path':'x','reason':'unbound'}])]:
   d=self.good();d[key]=value
   with self.subTest(key=key):self.assertNotEqual(mod.classify_result(d,True,3)['validation_status'],'completed_without_errors')
 def test_model_errors_preclude_clean(self):
  d=self.good();d['counts']['model_errors']=1
  self.assertEqual(mod.classify_result(d,True,3)['validation_status'],'completed_with_errors')
 def test_partial_occurrence_inventory_precludes_clean(self):
  self.assertEqual(mod.classify_result(self.good(),True,4)['technical_status'],'fact_inventory_incomplete')
 def test_incomplete_closure_separate_from_validation_errors(self):
  self.assertEqual(mod.classify_result(self.good(),False,3)['validation_status'],'incomplete_DTS')
  d=self.good();d['log_severity_code_counts']={'error:xbrl.example':1}
  self.assertEqual(mod.classify_result(d,True,3)['validation_status'],'completed_with_errors')
 def test_technical_kill_never_becomes_clean(self):
  self.assertEqual(mod.classify_result(self.good(),True,3,'timeout')['validation_status'],'not_executed_to_completion')
 def test_unreadable_logs_are_not_empty_success(self):
  for text in ['', 'not json', '{}', '{"log":{}}', '{"log":[{}]}']:
   rows,error=mod.parse_logs(text);self.assertIsNotNone(error);self.assertEqual(rows,[])
  self.assertEqual(mod.parse_logs('{"log":[]}'),([],None))
 def fixture(self,sha='abc',loc='/x[1]'):
  return {'record_type':'fact_pair','fact_ordinal':1,'typed':{'anchor':{'dom_path':loc},'source_sha256':sha,'numeric_status':'normalized','normalized_value':'1'}}
 def test_expected_source_SHA_must_match(self):
  with self.assertRaisesRegex(ValueError,'source_sha'):mod.validate_expected_records([self.fixture()], 'def',1)
 def test_duplicate_v15_locator_rejected(self):
  with self.assertRaisesRegex(ValueError,'duplicate'):mod.validate_expected_records([self.fixture(),self.fixture()],'abc',2)
 def test_expected_count_checked(self):
  with self.assertRaisesRegex(ValueError,'count_mismatch'):mod.validate_expected_records([self.fixture()],'abc',2)
 def test_expected_valid_record_retained(self):
  self.assertEqual(mod.validate_expected_records([self.fixture()],'abc',1)['/x[1]']['value'],'1')
 def test_float_not_exact_decimal(self):
  with self.assertRaises(ValueError):mod.canonical_decimal(0.1)

if __name__=='__main__':unittest.main()
