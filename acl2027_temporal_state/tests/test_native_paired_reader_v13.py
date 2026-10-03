"""Offline budget, identity and source-copy checks; no backend is launched."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
spec=importlib.util.spec_from_file_location('native_paired_reader_v13',ROOT/'scripts/native_paired_reader_v13.py')
reader=importlib.util.module_from_spec(spec);spec.loader.exec_module(reader)


def prompt(n=3):
    text='PRIVATE SOURCE<think>\n\n</think>\n\n';ids=list(range(n))
    return {'rendered_prompt':text,'input_token_ids':ids,'input_tokens':n,
            'rendered_prompt_sha256':hashlib.sha256(text.encode()).hexdigest(),
            'input_token_ids_sha256':reader.base.canonical_hash(ids)}


def cfg():
    return {'schema_version':'native_paired_reader_config_v0.13','batch_id':'controls',
            'expected_requests':2,'maximum_context_tokens':4096,'maximum_input_tokens':3500,
            'maximum_generated_tokens':256,'port':18093,'per_request_timeout_seconds':900,
            'system_prompt':'Read the supplied evidence and cite [doc_id p.N]. /no_think'}


def response(p,assets):
    settings={k:v for k,v in reader.PARAMETERS.items() if k not in ('temperature','cache_prompt','return_tokens')}
    settings.update(generation_prompt='',lora=[],backend_sampling=False,temperature=0.0)
    return {'prompt':p['rendered_prompt'],'model':str(assets/reader.backend.config()['model_file']),
            'truncated':False,'tokens_evaluated':p['input_tokens'],'tokens_predicted':2,
            'timings':{'cache_n':0,'prompt_n':p['input_tokens'],'predicted_n':2},
            'stop':True,'stop_type':'eos','content':'PRIVATE ANSWER [doc_a p.1]',
            'tokens':[1,2],'generation_settings':settings}


class NativePairedReaderTests(unittest.TestCase):
    def test_strict_configuration_rejects_changed_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'config.json';c=cfg();path.write_text(json.dumps(c))
            self.assertEqual(reader.config(path),c)
            for key,value in [('maximum_generated_tokens',257),('expected_requests',18),('maximum_input_tokens',3841)]:
                changed=c|{key:value};path.write_text(json.dumps(changed))
                with self.assertRaises(ValueError):reader.config(path)

    def test_request_contract_rejects_labels_duplicates_and_wrong_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'requests.json';items=[{'request_id':'a','prompt':'Question A'},{'request_id':'b','prompt':'Question B'}]
            path.write_text(json.dumps(items));self.assertEqual(reader.request_items(path,2),items)
            variants=[items+[{'request_id':'c','prompt':'Question C'}], [items[0],items[0]],
                      [items[0]|{'reference_answer':'leak'},items[1]]]
            for variant in variants:
                path.write_text(json.dumps(variant))
                with self.assertRaises(ValueError):reader.request_items(path,2)

    def test_no_context_truncation_or_forged_token_count(self):
        reader.validate_prompt(prompt(),cfg())
        with self.assertRaises(ValueError):reader.validate_prompt(prompt(3501),cfg())
        with self.assertRaises(ValueError):reader.validate_prompt(prompt()|{'input_tokens':2},cfg())
        with self.assertRaises(ValueError):reader.validate_prompt(prompt()|{'input_token_ids_sha256':'bad'},cfg())

    def test_cold_response_identity_and_full_input_required(self):
        p=prompt();assets=Path('/external/assets');r=response(p,assets)
        reader.validate_response(p,r,assets)
        for variant in [r|{'truncated':True},r|{'tokens_evaluated':2},r|{'model':'wrong'},
                        r|{'timings':{'cache_n':1,'prompt_n':3,'predicted_n':2}}]:
            with self.assertRaises(ValueError):reader.validate_response(p,variant,assets)

    def test_output_limit_retained_as_failure_without_repair(self):
        p=prompt();assets=Path('/external/assets');r=response(p,assets)
        r.update(stop_type='limit',tokens_predicted=256)
        r['timings']['predicted_n']=256
        reader.validate_response(p,r,assets)
        projection=reader.project_response('a',r)
        self.assertTrue(projection['output_budget_failure'])
        self.assertEqual(projection['content_sha256'],hashlib.sha256(r['content'].encode()).hexdigest())
        # Native returned tokens may omit a final stop token; both counts remain distinct.
        self.assertEqual(projection['returned_generated_token_count'],2)
        self.assertEqual(projection['tokens_predicted'],256)

    def test_public_projections_withhold_source_answer_and_token_arrays(self):
        p=prompt();r=response(p,Path('/external/assets'))
        projected={'prompt':reader.project_prompt(p),'response':reader.project_response('a',r)}
        text=json.dumps(projected)
        self.assertNotIn('PRIVATE',text)
        self.assertNotIn('rendered_prompt"',text)
        self.assertNotIn('input_token_ids"',text)
        self.assertNotIn('"content":',text)
        with self.assertRaises(ValueError):reader.external_path(ROOT/'tmp/fulltext')


if __name__=='__main__':unittest.main()
