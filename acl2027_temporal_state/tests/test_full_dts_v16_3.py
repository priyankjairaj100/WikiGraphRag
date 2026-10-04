"""Authored controls for safe capture, exact comparison and process guards."""
import importlib.util
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import contextlib
import io
from decimal import localcontext

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
SCRIPT=Path(__file__).resolve().parents[1]/'scripts/run_full_dts_v16_3.py'
spec=importlib.util.spec_from_file_location('full_dts_v16_3',SCRIPT);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)

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

class NamespaceRssIntegrationControls(unittest.TestCase):
 def test_missing_RSS_telemetry_never_becomes_zero_success(self):
  self.assertEqual(mod.rss_reading_reason({'telemetry_ok':False},False),'process_RSS_telemetry_unavailable')
  self.assertEqual(mod.rss_reading_reason({'telemetry_ok':False},True),'process_RSS_telemetry_unavailable')
 def test_absent_group_requires_fresh_confirmed_parent_exit(self):
  reading={'telemetry_ok':True,'group_members_observed':False,'rss_bytes':0,'members':[]}
  self.assertEqual(mod.rss_reading_reason(reading,False),'live_process_group_unobserved')
  self.assertIsNone(mod.rss_reading_reason(reading,True))
 def test_live_zero_RSS_fails_and_positive_reading_passes(self):
  reading={'telemetry_ok':True,'group_members_observed':True,'rss_bytes':0,'members':[{'state':'S'}]}
  self.assertEqual(mod.rss_reading_reason(reading,False),'live_process_RSS_not_positive')
  reading['rss_bytes']=1024;self.assertIsNone(mod.rss_reading_reason(reading,False))
 def test_descendants_after_parent_exit_require_cleanup(self):
  reading={'telemetry_ok':True,'group_members_observed':True,'rss_bytes':1024,'members':[{'state':'S'}]}
  self.assertEqual(mod.rss_reading_reason(reading,True),'worker_exit_with_live_descendants')
  reading['members']=[{'state':'Z'}];reading['rss_bytes']=0
  self.assertIsNone(mod.rss_reading_reason(reading,True))

class OwnedGroupLoopControls(unittest.TestCase):
 def run_fixture(self, source_count, cleanup):
  with tempfile.TemporaryDirectory(dir='/dev/shm') as directory:
   root=Path(directory);protocol=root/'protocol.json';graph=root/'graph.json';output=root/'output.json'
   sources=[{'source_file':'authored_'+str(i),'source':{'sha256':'fixture_'+str(i)},'expected_nonfraction':0} for i in range(source_count)]
   p={'external_root':str(root),'validation_root':str(root/'validation'),'acquisition_protocol':{'sha256':'authored_acquisition'},'arelle_runtime':'authored_unused_runtime','sources':sources,'limits':{'min_shm_free_bytes':0,'source_wall_seconds':300,'source_RSS_bytes':2*1024**3},'resource_policy':{'fixture':True},'original_attempt':{},'prior_attempts':[]}
   mod.jwrite(protocol,p);mod.jwrite(graph,{'protocol_sha256':'authored_acquisition','records':[],'source_closures':[{'captured_closure_complete':True,'unavailable_or_unparsed_URLs':[]} for _ in sources]})
   mod.jwrite(root/'graph_freeze.json',{'protocol':{},'graph':mod.binding(graph)})
   memory={'telemetry_ok':True,'memory_current_bytes':100,'memory_max_bytes':8*1024**3,'pressure_proxy_bytes':100,'components':{k:1 for k in mod.COMPONENTS},'events':{'oom':0,'oom_kill':0}}
   live={'telemetry_ok':True,'group_members_observed':True,'rss_bytes':1024,'members':[{'state':'S'}]}
   class ExitedParent:
    pid=24
    returncode=0
    def poll(self):return 0
    def wait(self,timeout=None):return 0
   with patch.object(mod,'preflight'),patch.object(mod,'verify'),patch.object(mod,'read_snapshot',return_value=memory),patch.object(mod,'bind_group',return_value={'telemetry_ok':True}),patch.object(mod,'read_group_rss',side_effect=[live,cleanup]),patch.object(mod.subprocess,'Popen',return_value=ExitedParent()) as spawn,patch.object(mod.os,'killpg') as signal_group,contextlib.redirect_stdout(io.StringIO()):
    mod.validate(str(protocol),str(graph),str(output))
    result=mod.jread(output);return result,spawn.call_count,signal_group.call_args_list
 def test_unconfirmed_cleanup_prevents_next_Popen_and_retains_denominator(self):
  result,launches,signals=self.run_fixture(2,{'telemetry_ok':False})
  self.assertEqual(launches,1);self.assertEqual(result['source_denominator'],2)
  self.assertEqual(result['records'][0]['technical_status'],'worker_cleanup_unconfirmed')
  self.assertEqual(result['records'][1]['technical_status'],'prelaunch_previous_worker_cleanup_unconfirmed')
  self.assertFalse(result['records'][0]['worker_cleanup_confirmed']);self.assertEqual(len(signals),1)
 def test_exited_parent_live_descendant_is_signalled_and_cleanup_rechecked(self):
  empty={'telemetry_ok':True,'group_members_observed':False,'rss_bytes':0,'members':[]}
  result,launches,signals=self.run_fixture(1,empty)
  self.assertEqual(launches,1);self.assertEqual(signals[0].args,(24,mod.signal.SIGKILL))
  self.assertEqual(result['records'][0]['technical_status'],'worker_exit_with_live_descendants')
  self.assertTrue(result['records'][0]['worker_cleanup_confirmed'])
  self.assertEqual(result['records'][0]['validation_status'],'not_executed_to_completion')

if __name__=='__main__':unittest.main()
