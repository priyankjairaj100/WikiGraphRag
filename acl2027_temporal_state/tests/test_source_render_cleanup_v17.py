"""One targeted authored-only consumer cleanup regression; no native calls."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
spec=importlib.util.spec_from_file_location('independent_cleanup_v17',ROOT/'scripts/render_source_blocks_lo_v17.py')
lo=importlib.util.module_from_spec(spec);spec.loader.exec_module(lo)


class ConfirmedCleanupTest(unittest.TestCase):
    def test_exited_leader_requires_confirmed_cleanup_and_blocks_next_worker_on_failure(self):
        process=SimpleNamespace(pid=424242,poll=lambda:0,wait=lambda **kwargs:0,returncode=0)
        anchor={'telemetry_ok':True,'authored':True}
        for state,expected in [('S',False),('Z',True)]:
            with self.subTest(member_state=state):
                group={'telemetry_ok':True,'members':[{'state':state}],
                       'group_members_observed':True,'rss_bytes':4096 if state=='S' else 0}
                with patch.object(lo.os,'killpg') as signal, \
                     patch.object(lo,'owned_group_rss',return_value=group), \
                     patch.object(lo.time,'monotonic',side_effect=[0,10,20,30]), \
                     patch.object(lo.time,'sleep'):
                    cleanup=lo.stop_owned_group(process,anchor)
                self.assertEqual(cleanup['cleanup_confirmed'],expected)
                self.assertTrue(all(call.args[0]==process.pid for call in signal.call_args_list))
                if not expected:
                    self.assertIn(unittest.mock.call(process.pid,lo.signal.SIGKILL),signal.call_args_list)
                    self.assertEqual(cleanup['status'],'owned_group_persists_after_kill')
                else:
                    self.assertEqual(cleanup['status'],'terminal_only_group_observed')
        with patch.object(lo.os,'killpg',side_effect=ProcessLookupError):
            self.assertTrue(lo.stop_owned_group(process,anchor)['cleanup_confirmed'])

        snapshot={'telemetry_ok':True,'memory_current_bytes':1000,
                  'memory_max_bytes':lo.guard.KERNEL_MAX_BYTES,'pressure_proxy_bytes':100,
                  'events':{'oom':0,'oom_kill':0}}
        lo.RUN_OOM_BASELINE=None;lo.RUN_CLEANUP_FAILURE=None
        failed={'cleanup_confirmed':False,'status':'owned_group_persists_after_kill','samples':[]}
        with tempfile.TemporaryDirectory(dir='/dev/shm') as directory:
            folder=Path(directory)
            with patch.object(lo.subprocess,'Popen',return_value=process) as popen, \
                 patch.object(lo.guard,'read_snapshot',return_value=snapshot), \
                 patch.object(lo,'bind_owned_group',return_value=anchor), \
                 patch.object(lo,'stop_owned_group',return_value=failed):
                first=lo.run_command(['authored-never-executes'],folder,folder,timeout=1,env={})
                second=lo.run_command(['authored-never-executes'],folder,folder,timeout=1,env={})
                remaining=lo.render_block({'block_id':'remaining','html':b'','fragment':b''},folder/'unused',folder,8)
            self.assertEqual(popen.call_count,1)
            self.assertEqual(first['status'],'stopped_at_unconfirmed_owned_group_cleanup')
            self.assertEqual(second['status'],'blocked_after_unconfirmed_owned_group_cleanup')
            self.assertFalse(second['process_started'])
            self.assertEqual(remaining['status'],'blocked_after_unconfirmed_owned_group_cleanup')
            self.assertFalse((folder/'unused').exists())
        lo.RUN_CLEANUP_FAILURE=None


if __name__=='__main__':unittest.main()
