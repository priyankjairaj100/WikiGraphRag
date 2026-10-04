"""Focused offline audit; mocks native I/O and never loads a model or corpus."""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import native_binding_reader_v16 as native
from tests.test_native_binding_reader_v16 import prompt


def response(config, rendered, count=3):
    settings={k:v for k,v in native.PARAMETERS.items() if k not in ('temperature','cache_prompt','return_tokens')}
    settings.update(generation_prompt='',lora=[],backend_sampling=False,temperature=0.0)
    return {'prompt':rendered['rendered_prompt'],'model':config['model']['path'],
            'truncated':False,'tokens_evaluated':rendered['input_tokens'],'tokens_predicted':count,
            'timings':{'cache_n':0,'prompt_n':rendered['input_tokens'],'predicted_n':count},
            'stop':True,'stop_type':'eos','content':'{}','tokens':list(range(count)),
            'generation_settings':settings}


class NativeReaderAdversarialTests(unittest.TestCase):
    def setUp(self):
        self.c=native.config(ROOT/'configs/binding_reader_v16.json')
        self.controls=native.load(ROOT/'data/reader_binding_v16/interface_controls_v16.json')
        self.items=native.authored_requests(self.controls)

    def test_generated_token_accounting_rejects_every_missing_token(self):
        rendered=prompt();valid=response(self.c,rendered)
        native.validate_response(rendered,valid,self.c)
        for tokens in ([],[0],[0,1],[0,1,2,3]):
            changed=deepcopy(valid);changed['tokens']=tokens
            with self.subTest(tokens=tokens),self.assertRaises(ValueError):
                native.validate_response(rendered,changed,self.c)

    def test_cold_native_accounting_and_identity_changes_fail(self):
        rendered=prompt();valid=response(self.c,rendered)
        variants=[]
        for key,value in [('truncated',True),('tokens_evaluated',rendered['input_tokens']-1),
                          ('model','wrong model'),('prompt','changed prompt'),('tokens_predicted',769)]:
            changed=deepcopy(valid);changed[key]=value;variants.append(changed)
        changed=deepcopy(valid);changed['timings']['cache_n']=1;variants.append(changed)
        changed=deepcopy(valid);changed['generation_settings']['seed']=1;variants.append(changed)
        for changed in variants:
            with self.assertRaises(ValueError):native.validate_response(rendered,changed,self.c)

    def test_no_model_symlink_is_followed_before_validation(self):
        with tempfile.TemporaryDirectory(dir='/dev/shm') as directory:
            target=Path(directory)/'target';target.write_bytes(b'authored model')
            link=Path(directory)/'model';link.symlink_to(target)
            config=deepcopy(self.c);config['model']['path']=str(link)
            with patch.object(native,'model_digest') as model_hash,self.assertRaisesRegex(ValueError,'symlink'):
                native.verify_assets(config)
            model_hash.assert_not_called()

    def test_private_output_cannot_cross_into_repository_through_symlink(self):
        with tempfile.TemporaryDirectory(dir='/dev/shm') as directory:
            link=Path(directory)/'repo_link';link.symlink_to(ROOT,target_is_directory=True)
            with self.assertRaisesRegex(ValueError,'source_records_must_be_external'):
                native.external(link/'private_records')

    def test_authored_eight_and_natural_thirty_six_limits_are_strict(self):
        self.assertEqual(len(self.items),8)
        too_many=deepcopy(self.controls);too_many['requests'].append(deepcopy(too_many['requests'][0]))
        with self.assertRaises(ValueError):native.authored_requests(too_many)
        natural=[]
        for index in range(18):
            for arm in ('B1','B2'):
                item=deepcopy(self.items[0]);item.pop('fixture_id');item.update(case_id='authored_case_'+str(index),arm=arm,request_id='authored_'+str(index)+'_'+arm);natural.append(item)
        self.assertEqual(len(native.natural_requests(natural)),36)
        extra=deepcopy(natural[-2:])
        for item in extra:item['case_id']='extra';item['request_id']='extra_'+item['arm']
        with self.assertRaisesRegex(ValueError,'natural_request_limit'):native.natural_requests(natural+extra)
        changed=deepcopy(natural[:2]);changed[1]['pack']['facts'][0]['value']='999'
        with self.assertRaisesRegex(ValueError,'unmatched_arm_evidence'):native.natural_requests(changed)

    def test_authored_reference_sentinel_is_not_in_messages_or_completion_parameters(self):
        controls=deepcopy(self.controls);controls['references']['authored_only_secret']='PRIVATE_REFERENCE_SENTINEL'
        items=native.authored_requests(controls)
        self.assertNotIn('PRIVATE_REFERENCE_SENTINEL',json.dumps([native.request_messages(i) for i in items]))
        body=native.PARAMETERS|{'prompt':prompt()['input_token_ids']}
        self.assertNotIn('PRIVATE_REFERENCE_SENTINEL',json.dumps(body))
        illegal=deepcopy(items[0]);illegal['reference']='PRIVATE_REFERENCE_SENTINEL'
        with self.assertRaisesRegex(ValueError,'request_shape'):native.request_messages(illegal)

    def test_natural_admission_rejects_stale_authored_reader_recipe(self):
        gate={'status':'completed','authored_controls':True,'authored_gate_passed':True,
              'completion_calls_started':8,'completion_responses':8,
              'assessments':[{'authored_control_correct':True} for _ in range(8)],
              'model_sha256':self.c['model']['sha256'],'config_sha256':native.base.canonical_hash(self.c),
              'reader_recipe_sha256':'0'*64}
        with tempfile.TemporaryDirectory(dir='/dev/shm') as directory:
            p=Path(directory);native.write(p/'gate.json',gate)
            with patch.object(native,'config',return_value=self.c),patch.object(native,'freeze_batch') as freeze, \
                 self.assertRaisesRegex(ValueError,'gate_reader_code_changed'):
                native.freeze_requests(p/'c.json',p/'requests.json',p/'run',p/'receipt.json',p/'gate.json',p/'protocol.json','a'*64)
            freeze.assert_not_called()

    def test_prelaunch_guard_failure_retains_zero_calls_and_full_denominator(self):
        items=deepcopy(self.items);messages=[native.request_messages(i) for i in items]
        with tempfile.TemporaryDirectory(dir='/dev/shm') as directory:
            p=Path(directory)
            receipt={'status':'frozen','completion_calls_started':0,'completion_responses':0,
                     'natural_QA_predictions':0,'expected_completions':8,'assessments':[],
                     'request_states':[{'request_id':i['request_id'],'status':'pending','call_started':False,'response_received':False} for i in items]}
            frozen={'config':self.c,'maximum_completions':8,'public_receipt_path':str(p/'public_receipt.json'),'kind':'authored'}
            with patch.object(native,'check_frozen',return_value=(frozen,items,messages,receipt)), \
                 patch.object(native,'server',side_effect=ValueError('active_memory_limit')), \
                 patch.object(native.base,'post') as post, self.assertRaises(ValueError):
                native.tokenize(p)
            public=native.load(p/'public_receipt.json')
            self.assertEqual(public['status'],'tokenization_failed')
            self.assertEqual(public['completion_calls_started'],0);post.assert_not_called()
            self.assertEqual(len(public['request_states']),8)
            self.assertTrue(all(s['status']=='not_attempted' and not s['call_started'] for s in public['request_states']))

    def test_transport_failure_keeps_all_eight_request_states_without_private_error_text(self):
        items=deepcopy(self.items);messages=[native.request_messages(i) for i in items]
        rendered=prompt();template='authored template'
        with tempfile.TemporaryDirectory(dir='/dev/shm') as directory:
            p=Path(directory);rows=[{'request_id':i['request_id'],'prompt':rendered} for i in items]
            native.write(p/'native_prompts.json',rows)
            stamp={'native_prompts_sha256':native.base.digest(p/'native_prompts.json'),'requests':8,
                   'template_sha256':hashlib.sha256(template.encode()).hexdigest()}
            native.write(p/'tokenization_freeze.json',stamp);native.write(p/'references.json',{})
            receipt={'status':'tokenized','completion_calls_started':0,'completion_responses':0,
                     'natural_QA_predictions':0,'expected_completions':8,'assessments':[],
                     'tokenization_freeze_sha256':native.base.digest(p/'tokenization_freeze.json'),
                     'request_states':[{'request_id':i['request_id'],'status':'pending','call_started':False,'response_received':False} for i in items]}
            frozen={'config':self.c,'maximum_completions':8,'public_receipt_path':str(p/'public_receipt.json'),'kind':'authored'}
            @contextmanager
            def fake_server(config,folder,trace):
                folder.mkdir();trace.update(process_exit_code=0,completion_requests=0)
                yield {'chat_template':template}
            with patch.object(native,'check_frozen',return_value=(frozen,items,messages,receipt)), \
                 patch.object(native,'server',side_effect=fake_server), \
                 patch.object(native.base,'render_and_tokenize',return_value=rendered), \
                 patch.object(native.base,'post',side_effect=RuntimeError('PRIVATE_ERROR_SENTINEL')) as post, \
                 self.assertRaises(RuntimeError):
                native.execute(p)
            public=native.load(p/'public_receipt.json')
            self.assertEqual(post.call_count,1);self.assertEqual(public['completion_calls_started'],1)
            self.assertEqual(public['completion_responses'],0);self.assertEqual(public['expected_completions'],8)
            self.assertEqual(len(public['request_states']),8)
            self.assertEqual([s['request_id'] for s in public['request_states']],[i['request_id'] for i in items])
            self.assertEqual(public['request_states'][0]['status'],'failed')
            self.assertTrue(public['request_states'][0]['call_started'])
            self.assertTrue(all(s['status']=='not_attempted' and not s['call_started'] for s in public['request_states'][1:]))
            self.assertNotIn('PRIVATE_ERROR_SENTINEL',json.dumps(public))
            self.assertIn('PRIVATE_ERROR_SENTINEL',(p/'failure.json').read_text())
            self.assertFalse(public['authored_gate_passed'])

if __name__=='__main__':unittest.main()
