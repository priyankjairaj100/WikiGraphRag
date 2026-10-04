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
import render_source_blocks_lo_v16_1 as lo
from tests.test_source_render_lo_pipeline_v16_1 import html


class SourceRenderAdversarialTests(unittest.TestCase):
    def setUp(self):
        fixture=lo.load_module(ROOT/'tests/test_source_render_v16.py','independent_authored_render_fixture')
        self.raw=fixture.FIXTURE;self.selection=fixture.selection()
        self.block=lo.source_render.prepare_blocks(self.raw,self.selection)[0]

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
        code=['scripts/render_source_blocks_lo_v16_1.py','scripts/probe_source_render_lo_v16.py',
              'scripts/render_source_blocks_v16.py','src/temporal_state/typed_reader_v15_1.py']
        protocol={'schema_version':lo.SCHEMA,'max_pages':8,'code_bindings':{p:lo.source_render.digest(ROOT/p) for p in code},
                  'input_bindings':{},'runtime_bindings':{str(lo.WRAPPER):lo.source_render.digest(lo.WRAPPER)},'sources':[source]}
        with tempfile.TemporaryDirectory(dir='/dev/shm') as directory:
            p=Path(directory);sources=p/'sources';sources.mkdir();(sources/'source.html').write_bytes(self.raw)
            (p/'protocol.json').write_text(json.dumps(protocol))
            with patch.object(lo,'memory_bytes',return_value=lo.MEMORY_CAP),patch.object(lo.subprocess,'Popen') as popen:
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
        with patch.object(lo.os,'killpg') as killpg:
            lo.stop_group(process)
        self.assertTrue(killpg.called,'owned children can survive an exited process-group leader')

    def test_memory_and_output_cap_refuse_before_subprocess(self):
        with tempfile.TemporaryDirectory(dir='/dev/shm') as directory:
            p=Path(directory)
            with patch.object(lo,'memory_bytes',return_value=lo.MEMORY_CAP),patch.object(lo.subprocess,'Popen') as popen:
                result=lo.run_command(['not-executed'],p,p,timeout=1,env={})
            self.assertEqual(result['status'],'blocked_before_launch_memory_cap');popen.assert_not_called()
            with patch.object(lo,'memory_bytes',return_value=0),patch.object(lo,'byte_size',return_value=lo.MAX_BYTES),patch.object(lo.subprocess,'Popen') as popen:
                result=lo.run_command(['not-executed'],p,p,timeout=1,env={})
            self.assertEqual(result['status'],'blocked_before_launch_output_cap');popen.assert_not_called()

if __name__=='__main__':unittest.main()
