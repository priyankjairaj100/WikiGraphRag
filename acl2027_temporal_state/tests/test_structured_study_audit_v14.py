"""Audit safeguards: matching lengths alone cannot certify identical native input."""
from pathlib import Path
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import verify_structured_study_v14 as audit


class StructuredAuditTests(unittest.TestCase):
    def test_exact_native_cost_requires_both_hashes_and_count(self):
        chosen={'input_tokens':77,'rendered_prompt_sha256':'a'*64,'token_ids_sha256':'b'*64}
        prompt={'input_tokens':77,'rendered_prompt_sha256':'a'*64,'input_token_ids_sha256':'b'*64}
        self.assertTrue(audit.tokens_match(chosen,prompt))
        for changed in [prompt|{'input_tokens':78},prompt|{'rendered_prompt_sha256':'c'*64},prompt|{'input_token_ids_sha256':'d'*64}]:
            self.assertFalse(audit.tokens_match(chosen,changed))

    def test_chronology_requires_zero_predictions_and_correct_partial_order(self):
        frozen={'completion_calls_at_freeze':0,'created_unix_seconds':10}
        started={'started_unix_seconds':11}
        preflight={'completion_calls_at_freeze':0,'created_unix_seconds':12}
        self.assertTrue(audit.before_predictions(frozen,started,preflight))
        self.assertTrue(audit.before_predictions(frozen,started|{'finished_unix_seconds':13},preflight))
        for f,r,p in [(frozen,started,preflight|{'completion_calls_at_freeze':1}),
                      (frozen,started|{'finished_unix_seconds':11},preflight),
                      (frozen|{'created_unix_seconds':12},started,preflight),
                      (frozen,started,preflight|{'created_unix_seconds':10})]:
            self.assertFalse(audit.before_predictions(f,r,p))

    def test_portable_rebase_does_not_read_external_raw_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            checker=audit.Audit(tmp)
            self.assertEqual(checker.path('/old/acl2027_temporal_state/results/a.json'),Path(tmp)/'results/a.json')
            for path in ['/outside/raw_answer.json','../raw.json']:
                with self.assertRaises(ValueError):checker.path(path)


if __name__=='__main__':unittest.main()
