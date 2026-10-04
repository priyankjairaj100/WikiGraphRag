"""Authored filesystem telemetry controls; no real process or cgroup writes."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

SCRIPT=Path(__file__).resolve().parents[1]/'scripts/cgroup_guard_v16_2.py'
spec=importlib.util.spec_from_file_location('guard_v16_2',SCRIPT)
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)

class PressureGuardControls(unittest.TestCase):
 def fixture(self, directory):
  p=Path(directory)
  (p/'memory.current').write_text(str(8*1024**3-1))
  (p/'memory.max').write_text(str(8*1024**3))
  (p/'memory.stat').write_text('\n'.join(k+' 10' for k in mod.COMPONENTS)+'\nfile 999999999\n')
  (p/'memory.events').write_text('oom 2\noom_kill 1\nmax 100\n')
  return p
 def good(self):
  return {'telemetry_ok':True,'memory_max_bytes':mod.KERNEL_MAX_BYTES,'pressure_proxy_bytes':100,'events':{'oom':2,'oom_kill':1}}
 def test_full_snapshot_records_every_component_and_actual_total(self):
  with tempfile.TemporaryDirectory() as d:
   s=mod.read_snapshot(self.fixture(d))
   self.assertTrue(s['telemetry_ok']);self.assertEqual(s['pressure_proxy_bytes'],90)
   self.assertEqual(set(s['components']),set(mod.COMPONENTS));self.assertEqual(s['memory_current_bytes'],8*1024**3-1)
 def test_missing_component_fails_closed(self):
  with tempfile.TemporaryDirectory() as d:
   p=self.fixture(d);(p/'memory.stat').write_text('anon 10\n')
   s=mod.read_snapshot(p);self.assertFalse(s['telemetry_ok']);self.assertEqual(mod.policy_reason(s),'cgroup_telemetry_unavailable')
 def test_missing_file_fails_closed(self):
  with tempfile.TemporaryDirectory() as d:
   p=self.fixture(d);(p/'memory.events').unlink()
   self.assertFalse(mod.read_snapshot(p)['telemetry_ok'])
 def test_missing_event_fails_closed(self):
  with tempfile.TemporaryDirectory() as d:
   p=self.fixture(d);(p/'memory.events').write_text('oom 0\n')
   self.assertFalse(mod.read_snapshot(p)['telemetry_ok'])
 def test_bad_negative_duplicate_or_unbounded_telemetry_fails(self):
  for name,body in [('memory.current','-1'),('memory.current','bad'),('memory.max','max'),('memory.stat','anon 1\nanon 2\n')]:
   with self.subTest(name=name,body=body),tempfile.TemporaryDirectory() as d:
    p=self.fixture(d);(p/name).write_text(body);self.assertFalse(mod.read_snapshot(p)['telemetry_ok'])
 def test_changed_kernel_limit_refused(self):
  s=self.good();s['memory_max_bytes']-=1
  self.assertEqual(mod.policy_reason(s),'unexpected_kernel_memory_max')
 def test_launch_reserve_is_strict_and_separate_from_runtime_threshold(self):
  s=self.good();s['pressure_proxy_bytes']=mod.PROXY_LIMIT_BYTES-mod.WORKER_RESERVE_BYTES
  self.assertEqual(mod.policy_reason(s,reserve_bytes=mod.WORKER_RESERVE_BYTES),'cgroup_pressure_proxy_limit')
  self.assertIsNone(mod.policy_reason(s))
  s['pressure_proxy_bytes']-=1;self.assertIsNone(mod.policy_reason(s,reserve_bytes=mod.WORKER_RESERVE_BYTES))
 def test_runtime_threshold_equality_stops(self):
  s=self.good();s['pressure_proxy_bytes']=mod.PROXY_LIMIT_BYTES
  self.assertEqual(mod.policy_reason(s),'cgroup_pressure_proxy_limit')
 def test_each_new_oom_event_stops(self):
  for key in mod.EVENTS:
   s=self.good();s['events'][key]+=1
   self.assertEqual(mod.policy_reason(s,self.good()),'new_cgroup_oom_event')
 def test_old_oom_events_do_not_fabricate_new_events(self):
  self.assertIsNone(mod.policy_reason(self.good(),self.good()))
 def test_counter_reset_and_unreadable_baseline_stop(self):
  s=self.good();s['events']['oom']-=1
  self.assertEqual(mod.policy_reason(s,self.good()),'cgroup_event_counter_reset')
  self.assertEqual(mod.policy_reason(self.good(),{}),'cgroup_telemetry_unavailable')
 def test_total_current_does_not_silently_reintroduce_original_guard(self):
  s=self.good();s['memory_current_bytes']=mod.KERNEL_MAX_BYTES-1
  self.assertIsNone(mod.policy_reason(s,reserve_bytes=mod.WORKER_RESERVE_BYTES))
 def test_negative_reserve_refused(self):
  self.assertEqual(mod.policy_reason(self.good(),reserve_bytes=-1),'invalid_worker_reserve')

if __name__=='__main__':unittest.main()
