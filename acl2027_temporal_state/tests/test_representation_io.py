from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from temporal_state.models import Source
from temporal_state.bounded import TemporalClaim, EvidenceSpan
from temporal_state.pilot_io import load_sources, load_prefix
from temporal_state.representation_io import load_migration, route_question, exact_projection

ROOT=Path(__file__).resolve().parents[1]


class MigrationIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.base=ROOT/'data/natural_pilot'
        self.new=ROOT/'data/natural_pilot_v03'
        self.sources=load_sources(self.base/'manifest.json')
        self.prefix=load_prefix(self.base/'early_prefix_input.json',self.sources)
        self.raw=self.base/'annotations/pass_b.json'
        self.prompt=self.new/'migration_prompt.txt'
        self.data=json.loads((self.new/'pass_b.json').read_text())

    def load(self,data):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'migration.json'
            path.write_text(json.dumps(data))
            return load_migration(path,self.prefix,self.raw,self.prompt)

    def test_all_raw_assertions_have_traceable_derived_records(self):
        result=self.load(self.data)
        raw=json.loads(self.raw.read_text())
        self.assertEqual({c.raw_assertion_id for c in result.claims},
                         {a['assertion_id'] for a in raw['assertions']})

    def test_changed_input_digest_rejected(self):
        self.data['prefix_input_sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'digest'):self.load(self.data)

    def test_changed_prompt_digest_rejected(self):
        self.data['prompt_sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'digest'):self.load(self.data)

    def test_disappearing_raw_assertion_rejected(self):
        self.data['claims']=self.data['claims'][1:]
        with self.assertRaisesRegex(ValueError,'disappeared'):self.load(self.data)

    def test_corrupted_claim_span_rejected(self):
        span=self.data['claims'][0]['evidence_spans'][0]
        span['quote']='X'+span['quote'][1:]
        with self.assertRaises(ValueError):self.load(self.data)

    def test_future_alias_source_rejected(self):
        self.data['aliases'][0]['source_id']='sbux_2024_10k_certification'
        with self.assertRaisesRegex(ValueError,'outside'):self.load(self.data)

    def test_cross_history_context_rejected(self):
        self.data['claims'][0]['context_source_ids']=['nike_8k_20240919']
        with self.assertRaisesRegex(ValueError,'history'):self.load(self.data)

    def test_projection_cannot_turn_planned_departure_into_positive_service(self):
        result=self.load(self.data)
        assertions,excluded=exact_projection(result.claims)
        negative_ids={c.claim_id for c in result.claims if c.polarity=='negative'}
        self.assertTrue(negative_ids)
        self.assertEqual(set(excluded),negative_ids)
        self.assertFalse(negative_ids & {a.assertion_id for a in assertions})


class AliasRoutingTests(unittest.TestCase):
    def setUp(self):
        self.sources=[Source('s','Orion Incorporated. Orion.', '2024-01-01'),
                      Source('f','Orion is also Cygnus.', '2024-02-01')]
        self.claim=TemporalClaim('c','s','Orion Incorporated','chief executive officer','A',
                                evidence_spans=(EvidenceSpan(0,5,'Orion'),))
        self.alias={'subject':'Orion Incorporated','alias':'Orion','source_id':'s',
                    'evidence_spans':[{'start':20,'end':25,'quote':'Orion'}]}

    def test_alias_adds_source_supported_route_without_editing_question(self):
        query='Who is the CEO of Orion?'
        self.assertEqual(route_question(query,[self.claim],[self.alias],self.sources,'2024-01-02',use_aliases=False),())
        self.assertEqual(route_question(query,[self.claim],[self.alias],self.sources,'2024-01-02'),(self.claim.key,))

    def test_future_alias_cannot_route_earlier_question(self):
        alias={**self.alias,'source_id':'f','alias':'Nebula'}
        self.assertEqual(route_question('Who is Nebula CEO?',[self.claim],[alias],self.sources,'2024-01-02'),())

    def test_collision_keeps_both_predicted_subjects(self):
        other=TemporalClaim('d','s','Cygnus','chief executive officer','B',
                            evidence_spans=(EvidenceSpan(0,5,'Orion'),))
        alias={**self.alias,'subject':'Cygnus'}
        keys=route_question('Who is Orion CEO?',[self.claim,other],[self.alias,alias],self.sources,'2024-01-02')
        self.assertEqual(set(keys),{self.claim.key,other.key})

    def test_future_context_claim_does_not_create_key(self):
        other=TemporalClaim('d','s','Cygnus','chief executive officer','B',
                            evidence_spans=(EvidenceSpan(0,5,'Orion'),),context_source_ids=('f',))
        self.assertEqual(route_question('Who is Cygnus CEO?',[other],[],self.sources,'2024-01-02'),())


if __name__=='__main__':unittest.main()
