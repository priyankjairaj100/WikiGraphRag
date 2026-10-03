"""Synthetic contract tests: no network, model calls, or research labels."""
from copy import deepcopy
import hashlib
import math
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import larger_scorer_v09 as scorer


class LargerScorerContracts(unittest.TestCase):
    def fixture(self):
        text='SYNTHETIC ONLY';tokens=[11,22,33]
        prompt={'rendered_prompt':text,'rendered_prompt_sha256':hashlib.sha256(text.encode()).hexdigest(),
                'input_token_ids':tokens,'input_token_ids_sha256':scorer.base.canonical_hash(tokens),
                'input_tokens':3,'label_tokens':{'Yes':[9454],'No':[2753]}}
        request=scorer.payload(tokens)
        response={'prompt':text,'truncated':False,'tokens_evaluated':3,'tokens_predicted':1,
                  'generation_settings':{'generation_prompt':''},
                  'timings':{'cache_n':0,'prompt_n':3,'prompt_ms':1},
                  'completion_probabilities':[{'top_logprobs':[{'id':9454,'logprob':-0.5},{'id':2753,'logprob':-1.5}]}]}
        return prompt,request,response

    def test_full_and_distributed_projection_keep_identical_metrics(self):
        p,q,r=self.fixture()
        full=scorer.extract(p,q,r)
        projected=scorer.extract(scorer.projected_prompt(p),scorer.projected_request(q),scorer.projected_response(r,p))
        self.assertEqual(full,projected)
        self.assertEqual(full['logodds'],1)
        self.assertEqual(full['decision'],'Yes')

    def test_projection_excludes_recoverable_full_sources(self):
        p,q,r=self.fixture()
        self.assertNotIn('rendered_prompt',scorer.projected_prompt(p))
        self.assertNotIn('input_token_ids',scorer.projected_prompt(p))
        self.assertNotIn('prompt',scorer.projected_request(q))
        self.assertNotIn('prompt',scorer.projected_response(r,p))

    def test_projection_rejects_mismatched_echoed_prompt(self):
        p,_,r=self.fixture();r['prompt']='DIFFERENT'
        with self.assertRaisesRegex(ValueError,'response prompt'):
            scorer.projected_response(r,p)

    def test_projection_rejects_unexpected_generation_text(self):
        p,_,r=self.fixture();r['generation_settings']['generation_prompt']='UNEXPECTED'
        with self.assertRaisesRegex(ValueError,'generation prompt'):
            scorer.projected_response(r,p)

    def test_changed_projected_token_hash_rejected(self):
        p,q,r=self.fixture();p=scorer.projected_prompt(p);q=scorer.projected_request(q)
        q['prompt_token_ids_sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'request differs'):
            scorer.extract(p,q,r)

    def test_hidden_sampling_policy_changes_rejected(self):
        for key,value in [('cache_prompt',True),('temperature',0),('n_predict',2),('logit_bias',[[9454,100]]),('n_probs',2)]:
            p,q,r=self.fixture();q[key]=value
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,'request differs'):
                scorer.extract(p,q,r)

    def test_cold_token_accounting_rejects_prefix_reuse(self):
        p,q,r=self.fixture();r['timings'].update(cache_n=1,prompt_n=2)
        with self.assertRaisesRegex(ValueError,'reused prompt'):
            scorer.extract(p,q,r)

    def test_truncation_or_wrong_generation_length_rejected(self):
        for key,value in [('truncated',True),('tokens_evaluated',2),('tokens_predicted',2)]:
            p,q,r=self.fixture();r[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):
                scorer.extract(p,q,r)

    def test_missing_duplicate_or_invalid_probabilities_rejected(self):
        for case in ('missing','duplicate','nan','positive','excess_mass'):
            p,q,r=self.fixture();top=r['completion_probabilities'][0]['top_logprobs']
            if case=='missing':top.pop()
            elif case=='duplicate':top.append(deepcopy(top[0]))
            elif case=='nan':top[0]['logprob']=float('nan')
            elif case=='positive':top[0]['logprob']=0.5
            else:
                for x in top:x['logprob']=-0.01
            with self.subTest(case=case),self.assertRaises((ValueError,KeyError)):
                scorer.extract(p,q,r)

    def test_exact_zero_abstains(self):
        p,q,r=self.fixture()
        for x in r['completion_probabilities'][0]['top_logprobs']:x['logprob']=-1
        self.assertEqual(scorer.extract(p,q,r)['decision'],'abstain')

    def test_mass_tolerance_preserves_unmodified_log_probabilities(self):
        p,q,r=self.fixture();tolerance=scorer.config()['probability_mass_absolute_tolerance']
        value=math.log((1+tolerance/2)/2)
        for x in r['completion_probabilities'][0]['top_logprobs']:x['logprob']=value
        actual=scorer.extract(p,q,r)
        self.assertEqual(actual['yes_logprob'],value)
        self.assertEqual(actual['no_logprob'],value)
        self.assertGreater(actual['returned_top_mass'],1)
        self.assertEqual(actual['returned_top_mass'],actual['literal_label_mass'])

    def test_mass_above_declared_tolerance_rejected(self):
        p,q,r=self.fixture();tolerance=scorer.config()['probability_mass_absolute_tolerance']
        for x in r['completion_probabilities'][0]['top_logprobs']:
            x['logprob']=math.log((1+2*tolerance)/2)
        with self.assertRaisesRegex(ValueError,'probability mass'):
            scorer.extract(p,q,r)

    def test_prompt_must_leave_generation_slot(self):
        p,q,r=self.fixture();p=scorer.projected_prompt(p);q=scorer.projected_request(q)
        p['input_tokens']=4096;q['prompt_tokens']=4096
        with self.assertRaisesRegex(ValueError,'cap mismatch'):
            scorer.extract(p,q,r)


if __name__=='__main__':
    unittest.main()
