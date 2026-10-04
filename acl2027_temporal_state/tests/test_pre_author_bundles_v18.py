"""Authored-only v18 shape rebuild and exact-parent comparison controls."""
from copy import deepcopy
import hashlib
import inspect
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import build_pre_author_bundles_v17 as old
import build_pre_author_bundles_v18 as new
from test_pre_author_bundles_v17 import SHA, anchor, block, fact


def pair(blank=True, status='normalized'):
    blocks=[block()]
    if blank:
        blocks.extend({'block_id':'paragraph_'+str(i),'kind':'paragraph',
                       'dom_path':'/authored/p['+str(i)+']','anchor':anchor(101+i,102+i),
                       'text':text,'rows':[]} for i,text in enumerate(('', '\u00a0')))
    selected=old.collect_facts([fact(status=status)],blocks,SHA)
    plan={'additional_dependencies':[],'unresolved':['authored_unreviewed']}
    def make(module):
        registry,pack,gates=module.candidate_registry('p00t1','s00',SHA,blocks,selected)
        return {'author_staging':module.author_projection('p00t1','s00',SHA,blocks),
                'question_free_registry':registry,'interface_shape_candidate':pack,
                'gates':gates,'question_or_reference':None,'source_only_dependency_plan':deepcopy(plan)}
    parent,current=make(old),make(new)
    record={'numeric_occurrences':len(selected),'interface_candidate_gates':deepcopy(parent['gates'])}
    return current,parent,deepcopy(parent['author_staging']),record


class ShapeRebuildControls(unittest.TestCase):
    def test_blank_only_failure_changes_to_shape_valid_without_other_change(self):
        current,parent,source_only,record=pair()
        self.assertIsNone(parent['interface_shape_candidate'])
        self.assertEqual(parent['gates']['reason'],'block_text')
        self.assertTrue(current['gates']['interface_shape_valid'])
        proof=new.compare_parent_bundle(current,parent,source_only,record)
        self.assertEqual(proof['blank_block_exposures'],2)
        self.assertFalse(proof['original_shape_valid'])
        self.assertEqual(proof['original_reason'],'block_text')
        self.assertTrue(proof['all_occurrences_retained'])

    def test_nonblank_parent_pack_replayed_exactly(self):
        current,parent,source_only,record=pair(False)
        proof=new.compare_parent_bundle(current,parent,source_only,record)
        self.assertTrue(proof['original_shape_valid'])
        pack=deepcopy(current['interface_shape_candidate']);pack['schema_version']='reader_evidence_pack_v16'
        self.assertTrue(new.exact_equal(pack,parent['interface_shape_candidate']))

    def test_blank_text_change_even_whitespace_to_whitespace_is_rejected(self):
        current,parent,source_only,record=pair()
        current['author_staging']['blocks'][1]['text']=' '
        with self.assertRaisesRegex(ValueError,'parent_author_staging_mismatch'):
            new.compare_parent_bundle(current,parent,source_only,record)

    def test_block_reorder_or_drop_is_rejected(self):
        for reorder in (True,False):
            current,parent,source_only,record=pair()
            if reorder:current['author_staging']['blocks'].reverse()
            else:current['author_staging']['blocks'].pop()
            with self.subTest(reorder=reorder),self.assertRaisesRegex(ValueError,'parent_author_staging_mismatch'):
                new.compare_parent_bundle(current,parent,source_only,record)

    def test_typed_registry_change_is_rejected(self):
        current,parent,source_only,record=pair()
        current['question_free_registry']['facts'][0]['normalized_value']='99'
        with self.assertRaisesRegex(ValueError,'parent_question_free_registry_mismatch'):
            new.compare_parent_bundle(current,parent,source_only,record)

    def test_dependency_plan_expansion_is_rejected(self):
        current,parent,source_only,record=pair()
        current['source_only_dependency_plan']['additional_dependencies'].append({'kind':'paragraph','ordinal':99})
        with self.assertRaisesRegex(ValueError,'parent_source_only_dependency_plan_mismatch'):
            new.compare_parent_bundle(current,parent,source_only,record)

    def test_export_must_match_even_when_combined_parent_does(self):
        current,parent,source_only,record=pair()
        source_only['blocks'][1]['text']=' '
        with self.assertRaisesRegex(ValueError,'unreleased_semantics_changed'):
            new.compare_parent_bundle(current,parent,source_only,record)

    def test_interface_block_handle_or_text_order_change_rejected(self):
        for key,value in (('handle','b999'),('text',' ')):
            current,parent,source_only,record=pair()
            current['interface_shape_candidate']['blocks'][1][key]=value
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,'block_text_order_changed'):
                new.compare_parent_bundle(current,parent,source_only,record)

    def test_fact_truncation_rejected(self):
        current,parent,source_only,record=pair()
        current['interface_shape_candidate']['facts']=[]
        with self.assertRaisesRegex(ValueError,'untruncated_registry_or_population_changed'):
            new.compare_parent_bundle(current,parent,source_only,record)

    def test_parent_count_cannot_disagree_with_registry(self):
        current,parent,source_only,record=pair()
        record['numeric_occurrences']=0
        with self.assertRaisesRegex(ValueError,'untruncated_registry_or_population_changed'):
            new.compare_parent_bundle(current,parent,source_only,record)

    def test_generated_value_or_label_drift_rejected_even_for_original_blank_failure(self):
        for key,value in (('value','99'),('labels',['Different'])):
            current,parent,source_only,record=pair()
            current['interface_shape_candidate']['facts'][0][key]=value
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,'candidate_value_or_label_differs_from_registry'):
                new.compare_parent_bundle(current,parent,source_only,record)

    def test_declared_binding_change_rejected_even_for_original_blank_failure(self):
        current,parent,source_only,record=pair()
        current['interface_shape_candidate']['facts'][0]['bindings']['entity']['value']['identifier']='different'
        with self.assertRaisesRegex(ValueError,'candidate_binding_differs_from_declared_registry'):
            new.compare_parent_bundle(current,parent,source_only,record)

    def test_declared_witness_wording_cannot_gain_visible_support_claim(self):
        current,parent,source_only,record=pair()
        current['interface_shape_candidate']['witnesses'][0]['text']='Visibly verified concept'
        with self.assertRaisesRegex(ValueError,'candidate_witness_text_or_order_changed'):
            new.compare_parent_bundle(current,parent,source_only,record)

    def test_original_failure_is_not_rewritten_as_success(self):
        current,parent,source_only,record=pair()
        parent['gates']['interface_shape_valid']=True;parent['gates']['reason']=None
        with self.assertRaisesRegex(ValueError,'original_v16_gate_or_pack_changed'):
            new.compare_parent_bundle(current,parent,source_only,record)

    def test_parent_public_gate_must_match_private_gate(self):
        current,parent,source_only,record=pair()
        record['interface_candidate_gates']['serialized_bytes']+=1
        with self.assertRaisesRegex(ValueError,'original_v16_gate_or_pack_changed'):
            new.compare_parent_bundle(current,parent,source_only,record)

    def test_nil_and_unsupported_are_retained_not_zeroed(self):
        for status in ('nil','unsupported'):
            current,parent,source_only,record=pair(status=status)
            proof=new.compare_parent_bundle(current,parent,source_only,record)
            self.assertTrue(proof['all_occurrences_retained'])
            self.assertIsNone(current['interface_shape_candidate']['facts'][0]['value'])
            self.assertEqual(current['question_free_registry']['facts'][0]['numeric_status'],status)

    def test_identity_card_or_author_release_cannot_be_added(self):
        for key,value in (('identity_card',{'name':'Authored issuer'}),('author_release_allowed',True)):
            current,parent,source_only,record=pair()
            current['author_staging'][key]=value
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,'parent_author_staging_mismatch'):
                new.compare_parent_bundle(current,parent,source_only,record)

    def test_changed_question_reference_field_rejected(self):
        current,parent,source_only,record=pair()
        current['question_or_reference']={'question':'Authored intrusion'}
        with self.assertRaisesRegex(ValueError,'parent_question_or_reference_mismatch'):
            new.compare_parent_bundle(current,parent,source_only,record)

    def test_new_private_field_rejected(self):
        current,parent,source_only,record=pair()
        current['extra']='unapproved'
        with self.assertRaisesRegex(ValueError,'private_bundle_shape_changed'):
            new.compare_parent_bundle(current,parent,source_only,record)

    def test_json_exact_comparison_distinguishes_bool_from_number(self):
        self.assertFalse(new.exact_equal({'flag':False},{'flag':0}))
        self.assertFalse(new.exact_equal({'text':'\u00a0'},{'text':' '}))

    def test_parent_artifact_hash_bytes_and_safe_basename_enforced(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);raw=b'{"authored":true}\n';(root/'parent.json').write_bytes(raw)
            artifact={'filename':'parent.json','bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
            self.assertEqual(new.read_bound_parent(root,artifact),{'authored':True})
            for changed,code in (({**artifact,'bytes':len(raw)+1},'parent_external_input_binding'),
                                 ({**artifact,'sha256':'0'*64},'parent_external_input_binding'),
                                 ({**artifact,'filename':'../parent.json'},'unsafe_parent_basename')):
                with self.subTest(code=code),self.assertRaisesRegex(ValueError,code):
                    new.read_bound_parent(root,changed)

    def test_parent_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);raw=b'{}';(root/'original.json').write_bytes(raw)
            (root/'link.json').symlink_to(root/'original.json')
            with self.assertRaisesRegex(ValueError,'parent_external_input_binding'):
                new.read_bound_parent(root,{'filename':'link.json','bytes':2,'sha256':hashlib.sha256(raw).hexdigest()})

    def test_draft_cannot_launch_or_create_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);protocol=root/'draft.json'
            protocol.write_text(json.dumps({'schema_version':'preauthor_bundle_materialization_protocol_v18',
                                            'status':'draft_not_executable'}))
            with self.assertRaisesRegex(ValueError,'protocol_not_frozen'):
                new.build(protocol,root,root,root,root,root,root/'out',root/'result.json')
            self.assertFalse((root/'out').exists());self.assertFalse((root/'result.json').exists())

    def test_missing_bindings_fail_before_source_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);protocol=root/'protocol.json'
            protocol.write_text(json.dumps({'schema_version':'preauthor_bundle_materialization_protocol_v18',
                                            'status':'frozen_after_independent_review'}))
            with self.assertRaisesRegex(ValueError,'binding_population'):
                new.build(protocol,root,root,root,root,root,root/'out',root/'result.json')
            self.assertFalse((root/'out').exists())

    def test_assembly_projection_and_registry_algorithms_unchanged(self):
        for name in ('digest','write_new','encoded','inside','block_from_view','clean_rows',
                     'author_projection','collect_facts','check_dependency','labels_for'):
            with self.subTest(function=name):
                self.assertEqual(inspect.getsource(getattr(old,name)),inspect.getsource(getattr(new,name)))
        self.assertEqual(inspect.getsource(new.candidate_registry).replace('reader_evidence_pack_v18','reader_evidence_pack_v16'),
                         inspect.getsource(old.candidate_registry))
        self.assertEqual(new.MAX_EXTERNAL,25_000_000)


if __name__=='__main__':unittest.main()
