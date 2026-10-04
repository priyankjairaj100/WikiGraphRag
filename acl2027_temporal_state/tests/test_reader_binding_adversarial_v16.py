"""Independent authored-only interface audit; never loads filings or QA labels."""
from copy import deepcopy
import json
import unittest
from temporal_state.reader_binding_v16 import (
    ASPECTS, BindingError, EvidencePack, render_proposal, score_direct_quantities,
    validate_proposal, validate_direct_output, lexical_baseline,
)
from tests.test_reader_binding_v16 import authored_fact, payload, proposal


def direct(pack, handle='f01', value='12', action='answered'):
    claim = proposal(pack, (handle,))['hypotheses'][0]['claims'][0]
    return {'action': action, 'unknown': False,
            'clarify_aspects': ['source'] if action == 'clarify' else [],
            'quantities': [{'value': value, **claim}]}


class BindingAdversarialTests(unittest.TestCase):
    def test_joint_equal_duplicate_handles_emit_one_scoped_quantity(self):
        pack = EvidencePack(payload(authored_fact(), authored_fact('f02')))
        result = render_proposal(pack, proposal(pack, ('f01', 'f02'), joint=True))
        self.assertEqual(result['action'], 'answered')
        self.assertEqual(len(result['quantities']), 1)
        self.assertEqual(result['quantities'][0]['occurrence_handles'], ['f01', 'f02'])

    def test_alias_only_hypothesis_difference_is_not_ambiguity(self):
        pack = EvidencePack(payload(authored_fact(), authored_fact('f02')))
        proposed = proposal(pack)
        proposed['hypotheses'].append(proposal(pack, ('f01','f02'), joint=True)['hypotheses'][0])
        result = render_proposal(pack, proposed)
        self.assertEqual(result['action'], 'answered')
        self.assertEqual(len(result['quantities']), 1)
        self.assertEqual(result['clarify_aspects'], [])

    def test_joint_duplicate_citations_are_unioned_without_dropping_provenance(self):
        pack = EvidencePack(payload(authored_fact(), authored_fact('f02')))
        result = render_proposal(pack, proposal(pack, ('f01','f02'), joint=True))
        self.assertEqual(len(result['quantities']), 1)
        for index, aspect in enumerate(ASPECTS):
            self.assertEqual(set(result['quantities'][0]['binding_witnesses'][aspect]),
                             {'w01'+str(index), 'w02'+str(index)})
        self.assertEqual(len(result['hypotheses'][0]['claims']), 2)

    def test_equivalent_separate_hypotheses_union_citation_provenance(self):
        pack=EvidencePack(payload(authored_fact(),authored_fact('f02')))
        result=render_proposal(pack,proposal(pack,('f01','f02')))
        self.assertEqual(result['action'],'answered');self.assertEqual(len(result['quantities']),1)
        for index,aspect in enumerate(ASPECTS):
            self.assertEqual(set(result['quantities'][0]['binding_witnesses'][aspect]),
                             {'w01'+str(index),'w02'+str(index)})

    def test_joint_claim_order_is_not_ambiguity(self):
        pack = EvidencePack(payload(authored_fact(), authored_fact('f02',year='2023',value='17')))
        proposed = proposal(pack, ('f01','f02'), joint=True)
        proposed['hypotheses'].append(proposal(pack, ('f02','f01'), joint=True)['hypotheses'][0])
        result = render_proposal(pack, proposed)
        self.assertEqual(result['action'],'answered')
        self.assertEqual(len(result['quantities']),2)

    def test_unselected_same_scope_conflict_is_not_value_matched_away(self):
        pack = EvidencePack(payload(authored_fact(value='12'),authored_fact('f02',value='13')))
        result=score_direct_quantities(pack,direct(pack,value='13'))
        self.assertEqual(result['action'],'answered')
        self.assertFalse(result['checks'][0]['support_complete'])
        self.assertFalse(result['checks'][0]['value_matches'])
        self.assertEqual(result['checks'][0]['stated_value'],'13')
        self.assertEqual(result['checks'][0]['candidate_handles'],['f01','f02'])

    def test_each_withheld_binding_cannot_be_reconstructed_from_visible_block(self):
        for aspect in ASPECTS:
            raw=payload();raw['facts'][0]['bindings'][aspect]=None
            raw['blocks']=[{'handle':'b01','text':'Authored narrative mentioning Alpha 2024 USD Report A whole group revenue 12.'}]
            pack=EvidencePack(raw)
            result=render_proposal(pack,proposal(pack))
            with self.subTest(aspect=aspect):
                self.assertEqual(result['action'],'insufficient')
                self.assertEqual(result['quantities'],[])
                self.assertIn('unavailable_binding',result['reason_codes'])

    def test_missing_citation_cannot_be_recovered_from_binding(self):
        pack=EvidencePack(payload())
        for aspect in ASPECTS:
            output=direct(pack);output['quantities'][0]['binding_witnesses'][aspect]=[]
            check=score_direct_quantities(pack,output)['checks'][0]
            with self.subTest(aspect=aspect):
                self.assertFalse(check['support_complete'])
                self.assertFalse(check['value_matches'])
                self.assertIn('uncited_binding',check['reason_codes'])

    def test_wrong_fact_same_aspect_witness_is_unavailable(self):
        pack=EvidencePack(payload(authored_fact(),authored_fact('f02',year='2023')))
        p=proposal(pack);p['hypotheses'][0]['claims'][0]['binding_witnesses']['period']=['w022']
        self.assertEqual(render_proposal(pack,p)['action'],'invalid_output')

    def test_unresolved_possible_duplicate_blocks_every_aspect(self):
        for aspect in ASPECTS:
            second=authored_fact('f02',value='99');second[0]['bindings'][aspect]=None
            pack=EvidencePack(payload(authored_fact(),second))
            with self.subTest(aspect=aspect):
                result=render_proposal(pack,proposal(pack))
                self.assertEqual(result['action'],'insufficient')
                self.assertIn('unresolved_possible_match',result['reason_codes'])

    def test_known_mismatch_excludes_candidate_with_other_missing_aspect(self):
        for aspect in ('concept','period','unit','population','source'):
            second=authored_fact('f02',value='99',entity='Other issuer');second[0]['bindings'][aspect]=None
            pack=EvidencePack(payload(authored_fact(),second))
            with self.subTest(aspect=aspect):
                result=render_proposal(pack,proposal(pack))
                self.assertEqual(result['action'],'answered')
                self.assertEqual(result['quantities'][0]['occurrence_handles'],['f01'])

    def test_null_seed_and_null_duplicate_are_never_numeric_zero(self):
        for items in ((authored_fact(value=None),),(authored_fact(value='0'),authored_fact('f02',value=None))):
            pack=EvidencePack(payload(*items));result=render_proposal(pack,proposal(pack))
            self.assertEqual(result['action'],'insufficient');self.assertEqual(result['quantities'],[])

    def test_namespace_scope_difference_survives_equal_numeric_value(self):
        pack=EvidencePack(payload(authored_fact(),authored_fact('f02',concept='{urn:other}Revenue')))
        result=render_proposal(pack,proposal(pack,('f01','f02')))
        self.assertEqual(result['action'],'clarify');self.assertEqual(result['clarify_aspects'],['concept'])

    def test_source_digest_difference_survives_same_source_label(self):
        second=authored_fact('f02',source='b')
        second[0]['bindings']['source']['value']['version']='Report A'
        second[1][-1]['text']='Report A'
        pack=EvidencePack(payload(authored_fact(),second))
        result=render_proposal(pack,proposal(pack,('f01','f02')))
        self.assertEqual(result['action'],'clarify');self.assertEqual(result['clarify_aspects'],['source'])

    def test_same_scope_conflict_requests_no_misleading_aspect_clarification(self):
        pack=EvidencePack(payload(authored_fact(),authored_fact('f02',value='14')))
        result=render_proposal(pack,proposal(pack,('f01','f02')))
        self.assertEqual(result['action'],'insufficient');self.assertEqual(result['clarify_aspects'],[])

    def test_compact_projection_is_detached_and_exact(self):
        raw=payload(authored_fact(),authored_fact('f02',year='2023'))
        pack=EvidencePack(raw);compact=pack.prompt_view()
        self.assertEqual(EvidencePack.from_prompt_view(compact).as_json(),pack.as_json())
        compact['facts'][0][1]='999'
        self.assertEqual(pack.snapshot()['facts'][0]['value'],'12')

    def test_unused_compact_text_cannot_smuggle_content(self):
        compact=EvidencePack(payload()).prompt_view()
        compact['texts'].append(['t999','UNACQUIRED_PRIVATE_SOURCE_TEXT'])
        with self.assertRaises(BindingError):EvidencePack.from_prompt_view(compact)

    def test_unused_compact_value_cannot_smuggle_metadata(self):
        compact=EvidencePack(payload()).prompt_view()
        compact['values'].append(['v999',['source',{'sha256':'f'*64,'version':'Unacquired source'}]])
        with self.assertRaises(BindingError):EvidencePack.from_prompt_view(compact)

    def test_unused_compact_binding_cannot_be_silently_ignored(self):
        compact=EvidencePack(payload()).prompt_view()
        compact['bindings'].append(['b999',['v999',['unknown']]])
        with self.assertRaises(BindingError):EvidencePack.from_prompt_view(compact)

    def test_changed_compact_binding_aspect_is_rejected(self):
        compact=EvidencePack(payload()).prompt_view()
        compact['values'][0][1][0]='period'
        with self.assertRaises(BindingError):EvidencePack.from_prompt_view(compact)

    def test_duplicate_json_keys_rejected_at_nested_claim_depth(self):
        pack=EvidencePack(payload());p=proposal(pack)
        text=json.dumps(p).replace('"fact_handle": "f01"','"fact_handle": "f01", "fact_handle": "f02"')
        self.assertEqual(render_proposal(pack,text)['action'],'invalid_output')

    def test_extra_hidden_reference_fields_in_proposal_and_direct_are_rejected(self):
        pack=EvidencePack(payload());p=proposal(pack);p['hidden_reference']='Answer=12'
        self.assertEqual(render_proposal(pack,p)['action'],'invalid_output')
        d=direct(pack);d['full_document']='Private unavailable source'
        self.assertEqual(score_direct_quantities(pack,d)['action'],'invalid_output')

    def test_direct_wrong_value_never_repaired_even_when_equal_scope_duplicate_exists(self):
        pack=EvidencePack(payload(authored_fact(),authored_fact('f02')))
        d=direct(pack,value='123');result=score_direct_quantities(pack,d)
        self.assertEqual(validate_direct_output(pack,d)['quantities'][0]['value'],'123')
        self.assertFalse(result['checks'][0]['value_matches'])
        self.assertEqual(result['checks'][0]['stated_value'],'123')
        self.assertTrue(result['checks'][0]['support_complete'])

    def test_direct_refusal_does_not_exempt_material_wrong_claim(self):
        pack=EvidencePack(payload())
        for action in ('clarify','insufficient'):
            result=score_direct_quantities(pack,direct(pack,value='-99',action=action))
            self.assertEqual(result['action'],action);self.assertEqual(len(result['checks']),1)
            self.assertFalse(result['checks'][0]['value_matches'])

    def test_exact_large_quantity_is_not_float_rounded(self):
        value='9'*180+'.00000000000000000001'
        pack=EvidencePack(payload(authored_fact(value=value)))
        result=score_direct_quantities(pack,direct(pack,value=value))
        self.assertTrue(result['checks'][0]['value_matches']);self.assertEqual(result['checks'][0]['stated_value'],value)

    def test_boolean_unknown_is_not_replaced_with_truthy_string(self):
        pack=EvidencePack(payload());p=proposal(pack);p['unknown']='false'
        self.assertEqual(render_proposal(pack,p)['action'],'invalid_output')
        d=direct(pack);d['unknown']=0
        self.assertEqual(score_direct_quantities(pack,d)['action'],'invalid_output')

    def test_unpaired_unicode_surrogate_fails_as_controlled_input_error(self):
        raw=payload();raw['blocks']=[{'handle':'b01','text':'\ud800'}]
        with self.assertRaises(BindingError):EvidencePack(raw)

    def test_json_escaped_surrogate_cannot_create_non_utf8_pack(self):
        raw=payload();raw['blocks']=[{'handle':'b01','text':'\ud800'}]
        serialized=json.dumps(raw,ensure_ascii=True)
        with self.assertRaises(BindingError):EvidencePack(serialized)

    def test_deep_python_object_fails_as_controlled_input_error(self):
        value=[]
        for _ in range(1500):value=[value]
        with self.assertRaises(BindingError):EvidencePack(value)

    def test_unknown_question_tokens_do_not_trigger_hidden_lookup(self):
        pack=EvidencePack(payload())
        out=lexical_baseline(pack,'Alpha adjusted revenue for 2024 in USD')
        self.assertNotEqual(render_proposal(pack,out)['action'],'answered')

if __name__ == '__main__':unittest.main()
