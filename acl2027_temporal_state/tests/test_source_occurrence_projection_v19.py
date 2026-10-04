"""Owner controls use only invented source snippets and temporary metadata."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
import unittest
import tempfile
from unittest import mock
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('occurrence_owner', ROOT/'scripts/build_source_occurrence_projection_v19.py')
m = importlib.util.module_from_spec(spec); sys.modules[spec.name] = m; spec.loader.exec_module(m)


def fixture(fragment='<p>Prior <ix:nonFraction name="SECRET_CONCEPT" contextRef="SECRET_CONTEXT" unitRef="SECRET_UNIT" scale="7">01,200</ix:nonFraction> after</p>'):
    raw = ('<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><body>'+fragment+'</body></html>').encode()
    nodes=m.byte_reader._parse(raw)
    facts=[n for n in nodes if n.tag == m.byte_reader._tag(m.byte_reader.IX,'nonFraction')]
    body=[n for n in nodes if n.tag == m.byte_reader._tag(m.byte_reader.XHTML,'body')][0]
    block={'block_id':'paragraph_0','kind':'paragraph','dom_path':body.path,'anchor':m.anchor_at(raw,body.start,body.stop),'text':body.text(),'rows':[]}
    entries=[{'anchor':dict(m.anchor_at(raw,n.start,n.stop),dom_path=n.path,source_sha256=m.sha(raw)),
              'block_ids':['paragraph_0'], 'normalized_value':'SECRET_VALUE', 'numeric_status':'SECRET_STATUS',
              'binding_status':'SECRET_BINDING', 'source_fact_ordinal':i,'declared_aspects':{'concept':'SECRET_QNAME'},
              'visible_row_label_candidates':['SECRET_LABEL'], 'issues':['SECRET_ISSUE']} for i,n in enumerate(facts)]
    author={'schema_version':'source_only_author_staging_v17','bundle_id':'p00t0','source_id':'s00',
            'source_sha256':m.sha(raw),'author_release_allowed':False,'identity_card':None,
            'status':'pending_visible_identity_and_complete_bundle_admission','blocks':[block]}
    return raw,nodes,facts,{'author_staging':author,'question_free_registry':{'facts':entries}}


class ProjectionOwnerTests(unittest.TestCase):
    def bundle(self, private=None, raw=None):
        default_raw,_,_,default_private=fixture()
        private=private or default_private; raw=raw or default_raw
        return m.project_bundle(raw,private,{},deepcopy(private['author_staging']),None)

    def test_source_only_fields_no_hidden_sentinel(self):
        out=self.bundle()
        self.assertNotIn('SECRET_',repr(out))
        self.assertEqual(out['occurrences'][0]['source_text']['decoded_text'],'01,200')
        self.assertEqual(out['occurrences'][0]['visibility']['status'],'unverified')
        self.assertFalse(out['author_release_allowed'])

    def test_distinct_equal_and_nil_unsupported_entries_retained(self):
        raw,_,_,private=fixture('<p><ix:nonFraction>7</ix:nonFraction><ix:nonFraction>7</ix:nonFraction><ix:nonFraction xsi:nil="true"/><ix:nonFraction format="odd">dash</ix:nonFraction></p>')
        out=self.bundle(private,raw)
        self.assertEqual(len(out['occurrences']),4)
        self.assertEqual([o['source_text']['decoded_text'] for o in out['occurrences']],['7','7','','dash'])
        self.assertEqual(len({o['occurrence_handle'] for o in out['occurrences']}),4)
        self.assertNotEqual(out['occurrences'][0]['locator'],out['occurrences'][1]['locator'])
        self.assertNotIn('nil',repr(out))

    def test_repeated_physical_entry_preserved_without_value_dedup(self):
        raw,_,_,private=fixture();private['question_free_registry']['facts'] *= 2
        out=self.bundle(private,raw)
        self.assertEqual(len(out['occurrences']),2)
        self.assertNotEqual(out['occurrences'][0]['entry_sha256'],out['occurrences'][1]['entry_sha256'])

    def test_unresolved_anchor_entry_remains_without_trusted_text(self):
        raw,_,_,private=fixture();private['question_free_registry']['facts'][0]['anchor']['span_sha256']='0'*64
        out=self.bundle(private,raw); occurrence=out['occurrences'][0]
        self.assertEqual(occurrence['locator_status'],'unresolved')
        self.assertIsNone(occurrence['source_text']);self.assertIsNone(occurrence['locator'])

    def test_wrong_source_or_block_membership_remains_unresolved(self):
        for key,value in [('source_sha256','0'*64),('dom_path','/not-real')]:
            raw,_,_,private=fixture();private['question_free_registry']['facts'][0]['anchor'][key]=value
            self.assertEqual(self.bundle(private,raw)['occurrences'][0]['locator_status'],'unresolved')
        raw,_,_,private=fixture();private['question_free_registry']['facts'][0]['block_ids']=[]
        self.assertEqual(self.bundle(private,raw)['occurrences'][0]['unresolved_reason'],'frozen_block_membership_mismatch')

    def test_whole_context_exact_and_input_immutable(self):
        raw,_,_,private=fixture(); before=deepcopy(private)
        out=self.bundle(private,raw)
        self.assertEqual(out['whole_source_context'],private['author_staging']);self.assertEqual(before,private)
        out['whole_source_context']['blocks'][0]['text']='mutated'
        self.assertEqual(before,private)

    def test_whole_context_tamper_rejected(self):
        raw,_,_,private=fixture(); author=deepcopy(private['author_staging']);author['blocks'][0]['text']='changed'
        with self.assertRaises(m.ProjectionError):m.project_bundle(raw,private,{},author,None)

    def test_nested_allowlist_rejects_extra_even_nonforbidden_key(self):
        raw,_,_,private=fixture();private['author_staging']['blocks'][0]['accidental_helper']='SECRET'
        with self.assertRaises(m.ProjectionError):self.bundle(private,raw)
        with self.assertRaises(m.ProjectionError):m.forbid_helpers({'safe':[{'normalized_value':'x'}]})

    def test_anchor_must_be_whole_xml_element(self):
        for raw in (b'plain text',b'<a>5</a><a>6</a>',b'<!DOCTYPE a><a>1</a>'):
            with self.assertRaises(m.ProjectionError):m.extract_text_segments(raw,m.anchor_at(raw,0,len(raw)))

    def test_entry_digest_replay(self):
        entry=self.bundle()['occurrences'][0]; expected=entry.pop('entry_sha256')
        self.assertEqual(m.sha(m.encoded(entry)),expected)

    def test_visible_helper_word_not_censored(self):
        raw,_,_,private=fixture('<p><ix:nonFraction>normalized_value literal</ix:nonFraction></p>')
        self.assertEqual(self.bundle(private,raw)['occurrences'][0]['source_text']['decoded_text'],'normalized_value literal')

    def test_visibility_cue_does_not_certify_or_leak_attribute(self):
        raw,_,_,private=fixture('<p style="display:none;SECRET_CSS:one"><ix:nonFraction>7</ix:nonFraction></p>')
        out=self.bundle(private,raw);v=out['occurrences'][0]['visibility']
        self.assertEqual(v['recognized_cue_kinds'],['inline_display_none']);self.assertEqual(v['status'],'unverified')
        self.assertNotIn('SECRET_CSS',repr(out))

class OrchestrationOwnerTests(unittest.TestCase):
    def test_draft_refused_before_any_external_population_read(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'protocol.json';m.write_new(path,{'status':'draft_not_executable'})
            with mock.patch.object(m,'protocol_value',side_effect=AssertionError('must not read frozen recipe')):
                with self.assertRaisesRegex(m.ProjectionError,'protocol_not_exact'):
                    m.build(path)

    def test_protocol_mutation_refused_before_external_output(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'protocol.json';m.write_new(path,{'status':'frozen_after_independent_review','planned_bundles':20})
            with mock.patch.object(m,'protocol_value',return_value={'status':'frozen_after_independent_review','planned_bundles':21}):
                with self.assertRaisesRegex(m.ProjectionError,'protocol_not_exact'):
                    m.build(path)

    def test_external_cap_is_charged_exactly_and_existing_output_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(m,'MAX_EXTERNAL',5):
            self.assertEqual(m.write_packet(temp,'one.json',b'123',0),3)
            with self.assertRaisesRegex(m.ProjectionError,'external_output_limit'):
                m.write_packet(temp,'two.json',b'456',3)
            self.assertFalse((Path(temp)/'two.json').exists())
            with self.assertRaises(FileExistsError):m.write_packet(temp,'one.json',b'9',3)
            self.assertEqual((Path(temp)/'one.json').read_bytes(),b'123')
            with self.assertRaisesRegex(m.ProjectionError,'external_filename'):
                m.write_packet(temp,'../escape.json',b'4',3)

    def test_exact_file_binding_rejects_wrong_hash_bytes_and_symlink(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'input';path.write_bytes(b'one')
            binding={'bytes':3,'sha256':m.sha(b'one')}
            self.assertEqual(m.checked_file(path,binding),path)
            for bad in (dict(binding,bytes=2),dict(binding,sha256='0'*64)):
                with self.assertRaises(m.ProjectionError):m.checked_file(path,bad)
            link=Path(temp)/'alias';link.symlink_to(path)
            with self.assertRaises(m.ProjectionError):m.checked_file(link,binding)

    def test_minimal_identity_reference_accepts_only_expected_leaf_types(self):
        raw,_,_,private=fixture()
        ref={'source_handle':'s00','path':'/private/s00.identity_admitted.json','bytes':12,'sha256':'a'*64}
        out=m.project_bundle(raw,private,{},deepcopy(private['author_staging']),ref)
        self.assertEqual(out['separate_identity_card_reference'],ref)
        for bad in (dict(ref,extra_alias='HIDDEN'),dict(ref,bytes=True),dict(ref,source_handle='s01')):
            with self.assertRaises(m.ProjectionError):m.project_bundle(raw,private,{},deepcopy(private['author_staging']),bad)

    def test_span_primitive_leaves_reject_metadata_objects(self):
        raw,_,_,private=fixture()
        span={'declared':None,'parsed':1,'status':'absent_default_one'}
        private['author_staging']['blocks'][0]['rows']=[{'text':'row','cells':[{'text':'cell','rowspan':deepcopy(span),'colspan':deepcopy(span)}]}]
        for key in span:
            altered=deepcopy(private);altered['author_staging']['blocks'][0]['rows'][0]['cells'][0]['rowspan'][key]={'helper_alias':'HIDDEN'}
            with self.assertRaises(m.ProjectionError):m.project_bundle(raw,altered,{},deepcopy(altered['author_staging']),None)

def raster_fixture(*, absolute_admission=False):
    block={'block_id':'table_0','dom_path':'/html[1]/table[1]',
           'anchor':{'byte_start':10,'byte_stop':20,'span_sha256':'a'*64}}
    artifact=lambda suffix,letter:{'path':'/private/source_00/block_00/'+suffix,'bytes':10,'sha256':letter*64}
    pdf=artifact('rendered/source-block.pdf','b');png=artifact('raster/page-1.png','c')
    fragment=artifact('source-fragment.xml','a');assembly=artifact('source-assembly.html','d')
    render=dict(block,pages=1,pdf=pdf,pngs=[png],source_assembly_sha256='d'*64,role='SECRET_RENDER_HINT')
    admitted=dict(block,decision='admitted_as_qualified_inspection_view',raster_pages_available=1,
                  raster_page_indices_inspected=[0],unverified_inventory_roles=['SECRET_ROLE_HINT'])
    if absolute_admission:
        admitted['artifact_bindings']={'rendered/source-block.pdf':pdf,'source-fragment.xml':fragment,'source-assembly.html':assembly}
        admitted['raster_bindings']=[png]
    else:
        admitted['reviewed_artifacts']=[{'filename':a['path'].split('/block_00/')[1],'bytes':a['bytes'],'sha256':a['sha256']}
                                       for a in (pdf,png,fragment,assembly)]
    receipt=lambda item:{'sources':[{'source_sha256':'e'*64,'blocks':[item]}]}
    return block,receipt(admitted),receipt(render)


class RasterOwnerTests(unittest.TestCase):
    def test_final_contrast_lineage_is_distinct_from_telemetry_recovery(self):
        record={'original_receipt':'old','separate_recovery_receipt':None,'separate_contrast_receipt':'contrast'}
        self.assertEqual(m.lineage_admission_path(record),'contrast')
        self.assertEqual(m.lineage_admission_path({'original_receipt':'old','separate_recovery_receipt':'retry'}),'retry')
        self.assertEqual(m.lineage_admission_path({'admission_receipt':'dependency','separate_recovery_receipt':'retry'}),'retry')

    def project(self,block,admission,execution):
        return m.project_qualified_view('e'*64,block,'b000',admission,execution,
                                       {'path':'results/admit.json','sha256':'f'*64},
                                       {'path':'results/render.json','sha256':'1'*64})

    def test_both_admission_shapes_rebuild_same_safe_map(self):
        maps=[]
        for shape in (False,True):
            block,a,e=raster_fixture(absolute_admission=shape); maps.append(self.project(block,a,e))
            self.assertNotIn('SECRET_',repr(maps[-1]))
            self.assertEqual(maps[-1]['pages'][0]['page_index'],0)
        self.assertEqual(*maps)

    def test_wrong_source_anchor_page_or_receipt_does_not_advertise_view(self):
        for change in ('source','anchor','page','qualification'):
            block,a,e=raster_fixture()
            if change=='source':e['sources'][0]['source_sha256']='2'*64
            elif change=='anchor':e['sources'][0]['blocks'][0]['anchor']=dict(block['anchor'],byte_stop=21)
            elif change=='page':a['sources'][0]['blocks'][0]['raster_page_indices_inspected']=[]
            else:a['sources'][0]['blocks'][0]['decision']='rejected'
            with self.assertRaises(m.ProjectionError):self.project(block,a,e)

    def test_missing_or_tampered_raster_and_extra_artifact_key_rejected(self):
        for change in ('missing','hash','extra'):
            block,a,e=raster_fixture()
            if change=='missing':e['sources'][0]['blocks'][0]['pngs']=[]
            elif change=='hash':e['sources'][0]['blocks'][0]['pngs'][0]['sha256']='2'*64
            else:e['sources'][0]['blocks'][0]['pngs'][0]['hidden_alias']='SECRET'
            with self.assertRaises(m.ProjectionError):self.project(block,a,e)

if __name__=='__main__':unittest.main()
