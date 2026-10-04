"""Independent authored-only LO pipeline checks; never launches LO or a browser."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import render_source_blocks_lo_v16_2 as lo
import run_source_render_controls_v16_2 as controls
from tests.test_source_render_lo_pipeline_v16_1 import html


class SourceRenderAdversarialTests(unittest.TestCase):
    def setUp(self):
        lo.RUN_OOM_BASELINE=None
        fixture=lo.load_module(ROOT/'tests/test_source_render_v16.py','independent_authored_render_fixture')
        self.raw=fixture.FIXTURE;self.selection=fixture.selection()
        self.block=lo.source_render.prepare_blocks(self.raw,self.selection)[0]

    def good_snapshot(self, *, current=1000, pressure=100, oom=0):
        return {'telemetry_ok':True,'memory_current_bytes':current,
                'memory_max_bytes':lo.guard.KERNEL_MAX_BYTES,'pressure_proxy_bytes':pressure,
                'events':{'oom':oom,'oom_kill':0}}

    def monitored_fake(self, snapshots, *, leader_finished, rss=0):
        process=SimpleNamespace(pid=424242,poll=lambda:0 if leader_finished else None,returncode=0)
        with tempfile.TemporaryDirectory(dir='/dev/shm') as directory:
            folder=Path(directory)
            with patch.object(lo.guard,'read_snapshot',side_effect=snapshots), \
                 patch.object(lo.subprocess,'Popen',return_value=process), \
                 patch.object(lo,'owned_group_rss',return_value=rss), \
                 patch.object(lo,'stop_owned_group') as stop:
                result=lo.run_command(['authored-never-executes'],folder,folder,timeout=1,env={})
            stop.assert_called_once_with(process)
            return result

    def test_monitor_oom_delta_stops_owned_group(self):
        good=self.good_snapshot();bad=self.good_snapshot(oom=1)
        result=self.monitored_fake([good,bad,bad],leader_finished=False)
        self.assertEqual(result['status'],'stopped_at_new_cgroup_oom_event')
        self.assertEqual(len(result['telemetry_samples']),3)

    def test_final_oom_delta_cannot_look_successful_and_final_peak_is_counted(self):
        good=self.good_snapshot();bad=self.good_snapshot(current=2000,pressure=200,oom=1)
        result=self.monitored_fake([good,bad],leader_finished=True)
        self.assertEqual(result['status'],'failed_final_new_cgroup_oom_event')
        self.assertEqual(result['peak_sampled_actual_total_bytes'],2000)
        self.assertEqual(result['peak_sampled_pressure_proxy_bytes'],200)

    def test_summed_owned_group_rss_limit_stops(self):
        good=self.good_snapshot()
        result=self.monitored_fake([good,good,good],leader_finished=False,rss=lo.LO_GROUP_RSS_LIMIT_BYTES)
        self.assertEqual(result['status'],'stopped_at_group_rss_limit')

    def test_string_resource_functions_and_meta_refresh_refused(self):
        for function in ('image-set','-webkit-image-set','image','src','attr'):
            with self.subTest(function=function):
                self.assertEqual(lo.resource_guard(html('<p>authored</p>',
                    'p{background:'+function+'("https://invalid.example/resource")}'))['status'],'refused')
        self.assertEqual(lo.resource_guard(html('<meta http-equiv="refresh" content="0;url=https://invalid.example"/>'))['status'],'refused')
        self.assertEqual(lo.resource_guard(html('<p style="color:rgb(1,2,3);width:calc(100% - 1px)">authored</p>'))['status'],'passive_resource_profile_passed')

    def test_source_assembly_unchanged_and_generated_charset_separate(self):
        with tempfile.TemporaryDirectory(dir='/dev/shm') as directory:
            attempt=Path(directory);folder=attempt/'block'
            with patch.object(lo,'run_command',return_value={'status':'blocked_before_launch_memory_cap','process_started':False}):
                result=lo.render_block(self.block,folder,attempt,8)
            self.assertEqual((folder/'source-fragment.xml').read_bytes(),self.block['fragment'])
            self.assertEqual((folder/'source-assembly.html').read_bytes(),self.block['html'])
            rendered=(folder/'source-block.html').read_bytes()
            insertion=b'<meta http-equiv="Content-Type" content="text/html; charset=utf-8"/>'
            self.assertEqual(rendered.replace(insertion,b'',1),self.block['html'])
            self.assertEqual(result['source_assembly_sha256'],lo.source_render.sha(self.block['html']))
            self.assertEqual(result['render_html_sha256'],lo.source_render.sha(rendered))
            self.assertEqual(result['admission_status'],'not_admitted_pending_full_visual_and_css_review')

    def test_page_cap_plus_one_is_explicit_failure_without_raster_launch(self):
        calls=[]
        def converter(command,logs,attempt,**kwargs):
            calls.append(command)
            target=Path(command[command.index('--outdir')+1])/'source-block.pdf'
            target.write_bytes(b'authored stub; not a real PDF')
            return {'status':'process_finished'}
        class PDF:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def __len__(self):return 9
            def __iter__(self):return iter([SimpleNamespace(get_text=lambda:'authored') for _ in range(9)])
        with tempfile.TemporaryDirectory(dir='/dev/shm') as directory:
            attempt=Path(directory)
            with patch.object(lo,'run_command',side_effect=converter),patch.dict(sys.modules,{'fitz':SimpleNamespace(open=lambda _:PDF())}):
                result=lo.render_block(self.block,attempt/'block',attempt,8)
            self.assertEqual(result['status'],'page_cap_exceeded_partial_pdf_retained')
            self.assertEqual(result['pages'],9);self.assertEqual(len(calls),1)
            exported=calls[0][calls[0].index('--convert-to')+1]
            self.assertEqual(json.loads(exported.split(':',2)[2])['PageRange']['value'],'1-9')
            self.assertTrue((attempt/'block/rendered/source-block.pdf').exists())

    def test_preparation_failure_retains_every_requested_block(self):
        selection=deepcopy(self.selection)
        selection['blocks'].append(deepcopy(selection['blocks'][0]));selection['blocks'][1]['block_id']='second_authored_block'
        source=dict(selection,source_path='authored/source.html',external_filename='source.html')
        code=['scripts/render_source_blocks_lo_v16_2.py','scripts/cgroup_guard_v16_2.py','scripts/probe_source_render_lo_v16.py',
              'scripts/render_source_blocks_v16.py','src/temporal_state/typed_reader_v15_1.py']
        protocol={'schema_version':lo.SCHEMA,'max_pages':8,'code_bindings':{p:lo.source_render.digest(ROOT/p) for p in code},
                  'input_bindings':{},'runtime_bindings':{str(lo.WRAPPER):lo.source_render.digest(lo.WRAPPER)},'sources':[source]}
        with tempfile.TemporaryDirectory(dir='/dev/shm') as directory:
            p=Path(directory);sources=p/'sources';sources.mkdir();(sources/'source.html').write_bytes(self.raw)
            (p/'protocol.json').write_text(json.dumps(protocol))
            with patch.object(lo.guard,'read_snapshot',return_value={'telemetry_ok':False}),patch.object(lo.subprocess,'Popen') as popen:
                result=lo.execute(p/'protocol.json',sources,p/'attempt',p/'public.json')
            popen.assert_not_called()
            self.assertEqual(result['requested_blocks'],2);self.assertEqual(result['rendered_blocks'],0)
            self.assertEqual(result['retained_failed_or_unrenderable_blocks'],2)
            self.assertEqual(len(result['sources'][0]['blocks']),2)
            self.assertTrue(all(b['status']=='source_preparation_failed' for b in result['sources'][0]['blocks']))
            self.assertNotIn('Authored source table',json.dumps(result))
            self.assertFalse(result['reference_admission_performed'])

    def test_output_guards_refuse_repository_symlink_and_existing_directory(self):
        with tempfile.TemporaryDirectory(dir='/dev/shm') as directory:
            p=Path(directory);link=p/'git';link.symlink_to(ROOT,target_is_directory=True)
            with self.assertRaisesRegex(ValueError,'external_output_inside_git'):
                lo.execute(p/'missing',p,link/'private',p/'public.json')
            existing=p/'existing';existing.mkdir()
            with self.assertRaisesRegex(ValueError,'invalid_or_existing_output'):
                lo.execute(p/'missing',p,existing,p/'public.json')

    def test_owned_group_cleanup_runs_even_after_leader_exits(self):
        process=SimpleNamespace(pid=424242,poll=lambda:0,wait=lambda **kwargs:0)
        with patch.object(lo.os,'killpg',side_effect=[None,ProcessLookupError]) as killpg:
            lo.stop_owned_group(process)
        self.assertTrue(killpg.called,'owned children can survive an exited process-group leader')

    def test_memory_and_output_cap_refuse_before_subprocess(self):
        with tempfile.TemporaryDirectory(dir='/dev/shm') as directory:
            p=Path(directory)
            with patch.object(lo.guard,'read_snapshot',return_value={'telemetry_ok':False}),patch.object(lo.subprocess,'Popen') as popen:
                result=lo.run_command(['not-executed'],p,p,timeout=1,env={})
            self.assertEqual(result['status'],'blocked_before_launch_cgroup_telemetry_unavailable');popen.assert_not_called()
            lo.RUN_OOM_BASELINE=None
            with patch.object(lo.guard,'read_snapshot',return_value=self.good_snapshot()),patch.object(lo,'byte_size',return_value=lo.MAX_BYTES),patch.object(lo.subprocess,'Popen') as popen:
                result=lo.run_command(['not-executed'],p,p,timeout=1,env={})
            self.assertEqual(result['status'],'blocked_before_launch_output_cap');popen.assert_not_called()

    def authored_control_protocol(self, folder):
        source=folder/'authored.xml';source.write_bytes(self.raw)
        control={'filename':source.name,'bytes':len(self.raw),'sha256':lo.source_render.sha(self.raw),
                 'max_pages':8,'expected_status':'rendered_pending_full_visual_and_css_review'}
        protocol={'code_bindings':{},'input_bindings':{},'runtime_bindings':{},
                  'controls':[dict(control,name='first'),dict(control,name='second')]}
        path=folder/'protocol.json';path.write_text(json.dumps(protocol));return path

    def test_authored_runner_unexpected_render_error_preserves_denominator_and_private_error(self):
        with tempfile.TemporaryDirectory(dir='/dev/shm') as directory:
            p=Path(directory);protocol=self.authored_control_protocol(p)
            with patch.object(lo.guard,'read_snapshot',return_value=self.good_snapshot()), \
                 patch.object(lo,'render_block',side_effect=[RuntimeError('private-authored-sentinel'),
                     {'status':'rendered_pending_full_visual_and_css_review'}]):
                result=controls.execute(protocol,p,p/'attempt',p/'public.json')
            self.assertEqual([c['control_name'] for c in result['controls']],['first','second'])
            self.assertFalse(result['controls'][0]['control_passed'])
            self.assertNotIn('private-authored-sentinel',(p/'public.json').read_text())
            self.assertIn('failure',result['status'])

    def test_authored_runner_unexpected_baseline_error_preserves_denominator_before_launch(self):
        with tempfile.TemporaryDirectory(dir='/dev/shm') as directory:
            p=Path(directory);protocol=self.authored_control_protocol(p)
            with patch.object(lo.guard,'read_snapshot',side_effect=RuntimeError('private-authored-sentinel')), \
                 patch.object(lo.subprocess,'Popen') as popen:
                result=controls.execute(protocol,p,p/'attempt',p/'public.json')
            popen.assert_not_called()
            self.assertEqual([c['control_name'] for c in result['controls']],['first','second'])
            self.assertFalse(any(c['control_passed'] for c in result['controls']))
            self.assertNotIn('private-authored-sentinel',(p/'public.json').read_text())

    def test_authored_runner_public_output_cannot_mix_with_private_attempt(self):
        with tempfile.TemporaryDirectory(dir='/dev/shm') as directory:
            p=Path(directory);protocol=self.authored_control_protocol(p)
            with self.assertRaises(ValueError):
                controls.execute(protocol,p,p/'attempt',p/'attempt/public.json')

if __name__=='__main__':unittest.main()
