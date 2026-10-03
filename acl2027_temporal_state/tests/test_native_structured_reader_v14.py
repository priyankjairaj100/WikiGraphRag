"""Offline safeguards for configurable assets and exact native token accounting."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import native_structured_reader_v14 as reader


def cfg():
    return reader.load(ROOT/'configs/structured_reader_controls_v14.json')


def prompt(n=3):
    text='PRIVATE SOURCE<|assistant|>';ids=list(range(n))
    return {'rendered_prompt':text,'input_token_ids':ids,'input_tokens':n,
            'rendered_prompt_sha256':hashlib.sha256(text.encode()).hexdigest(),
            'input_token_ids_sha256':reader.base.canonical_hash(ids)}


def response(p,c):
    settings={k:v for k,v in reader.PARAMETERS.items() if k not in ('temperature','cache_prompt','return_tokens')}
    settings.update(generation_prompt='',lora=[],backend_sampling=False,temperature=0.0)
    return {'prompt':p['rendered_prompt'],'model':c['model']['path'],
            'truncated':False,'tokens_evaluated':p['input_tokens'],'tokens_predicted':2,
            'timings':{'cache_n':0,'prompt_n':p['input_tokens'],'predicted_n':2},
            'stop':True,'stop_type':'eos','content':'PRIVATE ANSWER [doc p.1]',
            'tokens':[1,2],'generation_settings':settings}


class StructuredReaderTests(unittest.TestCase):
    def test_strict_fixed_budget_and_revision(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'config.json';c=cfg();p.write_text(json.dumps(c));self.assertEqual(reader.config(p),c)
            variants=[c|{'maximum_input_tokens':3501},c|{'expected_requests':9},c|{'maximum_generated_tokens':257},
                c|{'model':c['model']|{'revision':'main'}},c|{'runtime':c['runtime']|{'base_config_sha256':'0'*64}}]
            for x in variants:
                p.write_text(json.dumps(x))
                with self.assertRaises(ValueError):reader.config(p)

    def test_only_selected_model_asset_not_deleted_legacy_weight_required(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'model.gguf';p.write_bytes(b'pinned asset');c=cfg()
            c['model'].update(path=str(p),bytes=p.stat().st_size,sha256=reader.base.digest(p))
            backend=reader.ConfiguredBackend(c)
            with patch.object(reader.fetch,'inspect_runtime',return_value=[{'runtime':'pinned'}]) as inspect:
                binding=backend.verify_assets(p.parent)
                self.assertEqual(binding['selected_model'],c['model']);inspect.assert_called_once()
                p.write_bytes(b'changed')
                with self.assertRaises(ValueError):backend.verify_assets(p.parent)

    def test_identical_legacy_user_format_and_template_native_nonthinking(self):
        old=reader.load(ROOT/'configs/paired_reader_controls_v13.json')['system_prompt']
        self.assertEqual(reader.DEFAULT_SYSTEM_PROMPT,old.removesuffix(' /no_think'))
        self.assertEqual(reader.build_prompt('Q',''), 'Question:\nQ\n\nSource excerpts:\n\n\nAnswer the question using the supplied excerpts and cite source pages.')
        reader.validate_prompt(prompt(),cfg()) # no old <think> suffix requirement

    def test_counter_uses_full_system_and_user_messages_and_reports_overflow(self):
        c=cfg();binding={'runtime_files_sha256':'a'*64}
        counter=reader.TokenizerBridge(18141,c,{'chat_template':'native'},binding)
        with patch.object(reader.base,'render_and_tokenize',return_value=prompt(3501)) as count:
            result=counter.count_prompt('Question','Evidence')
            count.assert_called_once_with(18141,[{'role':'system','content':c['system_prompt']},{'role':'user','content':reader.build_prompt('Question','Evidence')}])
            self.assertEqual(result['input_tokens'],3501);self.assertFalse(result['fits_input_budget'])
            self.assertEqual(result['tokenizer_identity']['chat_template_kwargs'],{'enable_thinking':False})
            with self.assertRaises(ValueError):counter.count_prompt('Q','C',c|{'port':9999})

    def test_no_truncation_or_token_count_forgery(self):
        with self.assertRaises(ValueError):reader.validate_prompt(prompt(3501),cfg())
        with self.assertRaises(ValueError):reader.validate_prompt(prompt()|{'input_tokens':2},cfg())
        with self.assertRaises(ValueError):reader.validate_prompt(prompt()|{'input_token_ids_sha256':'bad'},cfg())

    def test_response_requires_cold_cache_full_input_selected_model(self):
        c=cfg();p=prompt();r=response(p,c);assets=Path(c['model']['path']).parent;b=reader.ConfiguredBackend(c)
        reader.validate_response(p,r,assets,b)
        for x in [r|{'model':'old.gguf'},r|{'truncated':True},r|{'tokens_evaluated':2},r|{'timings':r['timings']|{'cache_n':1}}]:
            with self.assertRaises(ValueError):reader.validate_response(p,x,assets,b)

    def test_output_limit_preserved_and_public_projection_has_no_quotes(self):
        c=cfg();p=prompt();r=response(p,c);r.update(stop_type='limit',tokens_predicted=256);r['timings']['predicted_n']=256
        reader.validate_response(p,r,Path(c['model']['path']).parent,reader.ConfiguredBackend(c))
        projected=reader.project_response('control',r);self.assertTrue(projected['output_budget_failure'])
        self.assertNotIn('PRIVATE',json.dumps([projected,reader.project_prompt(p)]))
        with self.assertRaises(ValueError):reader.external_path(ROOT.parent/'raw.json')

    def test_controls_requests_have_no_reference_fields_and_exact_prompt_builder(self):
        d=ROOT/'data/structured_reader_v14';inputs=reader.load(d/'controls_inputs.json');refs=reader.load(d/'controls_references.json')
        requests=reader.request_items(d/'controls_requests.json',8)
        self.assertEqual(len(refs),8)
        for x,q,r in zip(inputs,requests,refs):
            self.assertEqual(q,{'request_id':x['request_id'],'prompt':reader.build_prompt(x['question'],x['context'])})
            self.assertEqual(r['request_id'],q['request_id'])
            for f in r['required_facts']:
                self.assertIn(f"[{f['support']['document_id']} p.{f['support']['page']}]",x['context'])


if __name__=='__main__':unittest.main()
