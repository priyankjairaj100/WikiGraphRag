"""Authored controls for identity reconciliation; no actual candidate inputs."""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import reconcile_source_identity_cards_v18 as m


def candidate():
    c={k:None for k in m.CARD_KEYS}
    c.update(schema_version='visible_source_identity_card_candidate_v18',
             status='candidate_pending_independent_reconciliation',independent_reconciliation_status='pending',
             card_admitted=False,author_release=False,source_id='s00',native_reader_calls=0,
             questions_authored=0,reference_answers_authored=0,registry_updates=0,
             physical_provenance={'source_id':'s00','frozen_source_sha256':'a'*64},
             essential_fields={},source_witnesses=[])
    for name in sorted(m.FIELDS):
        wid='s00_'+name;text=' \tAuthored '+name+'\u00a0'
        c['essential_fields'][name]={'literal':text.strip(),'normalization':m.NORMALIZATION,'witness_ids':[wid]}
        if name=='issuer_name':c['essential_fields'][name]['supporting_witness_ids']=[]
        c['source_witnesses'].append({'witness_id':wid,'semantic_role':name,'whole_visible_source_text':text})
    return c


class ReconciliationControls(unittest.TestCase):
    def test_exact_outer_whitespace_only_accepted(self):
        m.reconcile_fields(candidate())

    def test_partial_or_case_normalized_literal_rejected(self):
        for replacement in ('Authored','AUTHORED ISSUER_NAME'):
            c=candidate();c['essential_fields']['issuer_name']['literal']=replacement
            with self.assertRaisesRegex(m.ReconciliationError,'field_not_full_literal'):
                m.reconcile_fields(c)

    def test_optional_identity_or_hidden_fiscal_fields_rejected(self):
        c=candidate();c['essential_fields']['cik']={'literal':'123'}
        with self.assertRaisesRegex(m.ReconciliationError,'optional_or_missing_identity_field'):m.reconcile_fields(c)
        c=candidate();c['fiscal_year']='2099'
        with self.assertRaisesRegex(m.ReconciliationError,'candidate_extra_or_missing_fields'):m.reconcile_fields(c)

    def test_wrong_role_and_unused_witness_rejected(self):
        c=candidate();c['source_witnesses'][0]['semantic_role']='different'
        with self.assertRaisesRegex(m.ReconciliationError,'field_witness_role_mismatch'):m.reconcile_fields(c)
        c=candidate();c['source_witnesses'].append({'witness_id':'unused','semantic_role':'issuer_name','whole_visible_source_text':'extra'})
        with self.assertRaisesRegex(m.ReconciliationError,'unused_or_aliased_witness'):m.reconcile_fields(c)

    def test_admitted_copy_changes_only_allowed_metadata(self):
        c=candidate();before=deepcopy(c);ref={'path':'authored','bytes':1,'sha256':'a'*64}
        new=m.admitted_copy(c,ref)
        self.assertEqual(c,before);self.assertTrue(new['card_admitted']);self.assertFalse(new['author_release'])
        changed={k for k in set(new)|set(c) if new.get(k)!=c.get(k)}
        self.assertEqual(changed,m.CHANGED_KEYS)
        self.assertEqual(new['essential_fields'],c['essential_fields'])
        self.assertEqual(new['source_witnesses'],c['source_witnesses'])

    def test_xml_body_itertext_preserves_literal_internal_whitespace(self):
        raw=b'<html xmlns="http://www.w3.org/1999/xhtml"><head/><body> A <b>B</b>&#160; C </body></html>'
        self.assertEqual(m.body_text(raw),' A B\u00a0 C ')
        with self.assertRaisesRegex(m.ReconciliationError,'source_assembly_body_not_unique'):
            m.body_text(b'<html><head/></html>')

    def test_xml_entities_are_not_resolved(self):
        raw=b'<!DOCTYPE html [<!ENTITY authored SYSTEM "file:///must-not-read">]><html><body>&authored;</body></html>'
        self.assertEqual(m.body_text(raw),'&authored;')

    def test_recovery_source_uses_original_global_index(self):
        d={'sources':[{'source_index':0,'original_source_index':10,'blocks':[{'block_id':'b'}]}]}
        self.assertEqual(m.source_block(d,10,'b')[1]['block_id'],'b')
        with self.assertRaisesRegex(m.ReconciliationError,'source_index_not_unique'):m.source_block(d,0,'b')

    def test_bound_file_bytes_hash_and_symlink_are_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'fixture';p.write_bytes(b'authored')
            ref=m.binding(p);self.assertEqual(m.read_bound(ref),b'authored')
            with self.assertRaisesRegex(m.ReconciliationError,'artifact_hash_or_bytes_mismatch'):
                m.read_bound({**ref,'bytes':0})
            link=Path(directory)/'link';link.symlink_to(p)
            with self.assertRaisesRegex(m.ReconciliationError,'artifact_file_missing_or_symlink'):
                m.read_bound({**ref,'path':str(link)})


if __name__=='__main__':unittest.main()
