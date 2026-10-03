"""Question drift and unsupported evidence must not become reference labels."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from validate_source_reference_v08 import validate
from validate_source_annotation_v08 import digest

def raw(x):return (json.dumps(x,ensure_ascii=False,indent=2)+'\n').encode()
class SourceReferenceValidation(unittest.TestCase):
    def setUp(self):
        spec=importlib.util.spec_from_file_location('annotation_test_fixture',ROOT/'tests/test_source_annotation_v08.py')
        mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
        f=mod.SourceAnnotationValidation();f.setUp()
        self.request=f.request;self.capture=f.capture;self.delivery=f.delivery
        self.plan={'questions_by_history':{'h1':[{'question_id':'q1','question':'Which place?'},{'question_id':'q2','question':'Actual opening day?'}]}}
        self.output={'schema_version':'source_reference_v0.8','history_id':'h1','prefix_id':'h1_p1','request_sha256':digest(raw(self.request)),'eligible_source_ids':['s1'],'reference_kind':'unversioned_model_source_only_not_human_gold','questions':[{'question_id':'q1','question':'Which place?','status':'supported','answer':'A café','rationale':'Named in source.','evidence':deepcopy(f.output['claims'][0]['evidence'])},{'question_id':'q2','question':'Actual opening day?','status':'not_established','answer':'Not established','rationale':'A plan does not prove execution.','evidence':[]}]}
    def run_validation(self):return validate(raw(self.request),raw(self.output),raw(self.plan),raw(self.capture),raw(self.delivery))
    def test_supported_and_unestablished_distinguished(self):
        d=self.run_validation();self.assertEqual(d['status_counts']['not_established'],1);self.assertFalse(d['model_accuracy_evaluated'])
    def test_question_drift_rejected(self):
        self.output['questions'][0]['question']='Which future place?'
        with self.assertRaisesRegex(ValueError,'question selection'):self.run_validation()
    def test_supported_without_evidence_rejected(self):
        self.output['questions'][1]['status']='supported'
        with self.assertRaisesRegex(ValueError,'lacks evidence'):self.run_validation()
    def test_future_evidence_rejected(self):
        self.output['questions'][0]['evidence'][0]['source_id']='future'
        with self.assertRaisesRegex(ValueError,'future/ineligible'):self.run_validation()
    def test_wrong_span_rejected(self):
        self.output['questions'][0]['evidence'][0]['quote']='airport'
        with self.assertRaisesRegex(ValueError,'evidence mismatch'):self.run_validation()
if __name__=='__main__':unittest.main()
