"""Independent invented fixtures only; never opens a natural filing or packet."""
import hashlib
import copy
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
spec = importlib.util.spec_from_file_location('occurrence_projection_under_review', ROOT / 'scripts/build_source_occurrence_projection_v19.py')
projection = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = projection
spec.loader.exec_module(projection)

PREFIX = b'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><body>'
SUFFIX = b'</body></html>'


def fixture(fragment, before=''):
    raw = fragment.encode('utf-8')
    prefix = PREFIX + before.encode('utf-8')
    source = prefix + raw + SUFFIX
    return source, {'byte_start': len(prefix), 'byte_stop': len(prefix) + len(raw),
                    'span_sha256': hashlib.sha256(raw).hexdigest()}


class SegmentAdversarialTests(unittest.TestCase):
    def extract(self, fragment, expected, before=''):
        source, anchor = fixture(fragment, before)
        result = projection.extract_text_segments(source, anchor)
        self.assertEqual(result['decoded_text'], expected)
        self.assertEqual(''.join(s['decoded_text'] for s in result['segments']), expected)
        previous = anchor['byte_start']
        for segment in result['segments']:
            self.assertEqual(set(segment), {'kind', 'anchor', 'raw_text', 'decoded_text'})
            self.assertIn(segment['kind'], ('text', 'cdata'))
            a = segment['anchor']
            self.assertGreaterEqual(a['byte_start'], previous)
            self.assertLessEqual(a['byte_stop'], anchor['byte_stop'])
            self.assertGreater(a['byte_stop'], a['byte_start'])
            raw = source[a['byte_start']:a['byte_stop']]
            self.assertEqual(raw.decode('utf-8'), segment['raw_text'])
            self.assertEqual(hashlib.sha256(raw).hexdigest(), a['span_sha256'])
            previous = a['byte_stop']
        return result, source, anchor

    def test_utf8_nested_tail_entities_quoted_gt_comment_pi_cdata(self):
        fragment = '<ix:nonFraction name="SECRET_CONCEPT>suffix" contextRef="SECRET_CONTEXT">12<span title="SECRET_ATTRIBUTE >">é &amp; &#xA3;</span><![CDATA[<literal>&raw]]><!--SECRET_COMMENT--> tail<?audit SECRET_PI?>&#13;\r\n</ix:nonFraction>'
        result, _, _ = self.extract(fragment, '12é & £<literal>&raw tail\r\n', before='<p>préface £</p>')
        serialized = repr(result)
        for hidden in ('SECRET_CONCEPT', 'SECRET_CONTEXT', 'SECRET_ATTRIBUTE', 'SECRET_COMMENT', 'SECRET_PI', '<![CDATA['):
            self.assertNotIn(hidden, serialized)
        self.assertIn('cdata', [s['kind'] for s in result['segments']])

    def test_raw_crlf_preserved_while_xml_decoding_is_explicit(self):
        result, _, _ = self.extract('<ix:nonFraction>A\r\nB\rC&#13;D</ix:nonFraction>', 'A\nB\nC\rD')
        self.assertEqual(''.join(s['raw_text'] for s in result['segments']), 'A\r\nB\rC&#13;D')

    def test_distinct_equal_segments_keep_distinct_byte_anchors(self):
        result, _, _ = self.extract('<ix:nonFraction>7<span>7</span>7</ix:nonFraction>', '777')
        self.assertEqual([s['raw_text'] for s in result['segments']], ['7', '7', '7'])
        self.assertEqual(len({s['anchor']['byte_start'] for s in result['segments']}), 3)

    def test_empty_nil_and_whitespace_do_not_infer_numeric_value(self):
        result, _, _ = self.extract('<ix:nonFraction xsi:nil="true" scale="SECRET_SCALE"/>', '')
        self.assertEqual(result['segments'], [])
        self.assertEqual(set(result), {'segments', 'decoded_text'})
        result, _, _ = self.extract('<ix:nonFraction xsi:nil="false"> \t\n</ix:nonFraction>', ' \t\n')
        self.assertNotIn('nil', repr(result))

    def test_text_remains_lexical_without_numeric_or_sign_normalization(self):
        self.extract('<ix:nonFraction sign="-" scale="9" format="SECRET_FORMAT"> (1,234.50) − 01.00 </ix:nonFraction>', ' (1,234.50) − 01.00 ')

    def test_cdata_entities_are_literal_and_not_decoded_twice(self):
        result, _, _ = self.extract('<ix:nonFraction><![CDATA[<&amp;&#13;>]]>&amp;amp;</ix:nonFraction>', '<&amp;&#13;>&amp;')
        self.assertEqual(result['segments'][0]['raw_text'], '<&amp;&#13;>')

    def test_unsupported_or_invalid_entity_forms_fail_closed(self):
        for value in ('&unknown;', '&#0;', '&#xD800;', '&#x110000;', '&amp', '&#xZZ;'):
            with self.subTest(value=value):
                source, anchor = fixture('<ix:nonFraction>' + value + '</ix:nonFraction>')
                with self.assertRaises(projection.ProjectionError):
                    projection.extract_text_segments(source, anchor)

    def test_tampered_or_nonintegral_byte_anchors_fail_closed(self):
        source, anchor = fixture('<ix:nonFraction>42</ix:nonFraction>')
        variants = [dict(anchor, span_sha256='0' * 64), dict(anchor, byte_start=-1),
                    dict(anchor, byte_stop=len(source) + 1), dict(anchor, byte_start=True),
                    dict(anchor, byte_start=float(anchor['byte_start']))]
        for altered in variants:
            with self.subTest(anchor=altered):
                with self.assertRaises(projection.ProjectionError):
                    projection.extract_text_segments(source, altered)


def bundle_fixture(body):
    source = PREFIX + body.encode('utf-8') + SUFFIX
    nodes = projection.byte_reader._parse(source)
    tags = lambda name: projection.byte_reader._tag(projection.byte_reader.XHTML, name)
    facts = [n for n in nodes if n.tag == projection.byte_reader._tag(projection.byte_reader.IX, 'nonFraction')]
    context = next(n for n in nodes if n.parent and n.parent.tag == tags('body'))
    block = {'block_id':'ctx0', 'kind':'table' if context.tag == tags('table') else 'paragraph',
             'dom_path':context.path, 'anchor':projection.anchor_at(source, context.start, context.stop),
             'text':context.text(), 'rows':[]}
    author = {'schema_version':'source_only_author_staging_v17', 'bundle_id':'p00t00', 'source_id':'s00',
              'source_sha256':projection.sha(source), 'author_release_allowed':False, 'identity_card':None,
              'status':'pending_visible_identity_and_complete_bundle_admission', 'blocks':[block]}
    entries = []
    for i, node in enumerate(facts):
        a = projection.anchor_at(source, node.start, node.stop)
        a.update(source_sha256=projection.sha(source), dom_path=node.path)
        entries.append({'handle':f'old{i}', 'source_fact_ordinal':i, 'anchor':a, 'block_ids':['ctx0'],
                        'normalized_value':'SECRET_NORMALIZED', 'numeric_status':'SECRET_STATUS',
                        'binding_status':'SECRET_BINDING', 'declared_aspects':{'SECRET_ASPECT':'SECRET_ASPECT_VALUE'},
                        'visible_row_label_candidates':['SECRET_ROW_HELPER'], 'lexical_text':'SECRET_TYPED_LEXICAL',
                        'issues':['SECRET_ISSUE'], 'arbitrary_helper_alias':'SECRET_ALIAS'})
    private = {'author_staging':author, 'question_free_registry':{'facts':entries}}
    return source, nodes, facts, private


def frozen_views(source, nodes):
    tag = lambda name: projection.byte_reader._tag(projection.byte_reader.XHTML, name)
    def ancestor(node, names):
        return next((n for n in node.ancestors() if n.tag in {tag(k) for k in names}), None)
    views = {}
    for table in [n for n in nodes if n.tag == tag('table')]:
        view = {'dom_path':table.path, 'anchor':projection.anchor_at(source,table.start,table.stop), 'rows':[], 'fact_links':[]}
        cells = {}
        for row in [n for n in nodes if n.tag == tag('tr') and ancestor(n,('table',)) is table]:
            rv = {'path_from_table':row.path[len(table.path):], 'anchor':projection.anchor_at(source,row.start,row.stop), 'cells':[]}
            for cell in [n for n in nodes if n.tag in (tag('td'),tag('th')) and ancestor(n,('table',)) is table and ancestor(n,('tr',)) is row]:
                cells[cell.path] = [len(view['rows']),len(rv['cells'])]
                rv['cells'].append({'path_from_row':cell.path[len(row.path):], 'anchor':projection.anchor_at(source,cell.start,cell.stop)})
            view['rows'].append(rv)
        for fact in [n for n in nodes if n.tag == projection.byte_reader._tag(projection.byte_reader.IX,'nonFraction') and ancestor(n,('table',)) is table]:
            cell = ancestor(fact,('td','th'))
            view['fact_links'].append({'path_from_table':fact.path[len(table.path):],
                                      'anchor':projection.anchor_at(source,fact.start,fact.stop),
                                      'row_cell_index':cells.get(cell.path) if cell else None})
        views[table.path] = view
    return views


class OccurrenceBoundaryAdversarialTests(unittest.TestCase):
    def test_nested_cell_uses_exact_nearest_table_link(self):
        source,nodes,facts,private = bundle_fixture('<table><tr><td>Outer<ix:nonFraction>7</ix:nonFraction><table><tr><td>Inner<ix:nonFraction>7</ix:nonFraction></td></tr></table></td></tr></table>')
        views = frozen_views(source,nodes)
        keys = list(views)
        blocks = private['author_staging']['blocks']
        bound = projection.bind_cell(facts[1],views,source,blocks)
        self.assertEqual(bound['status'],'bound_to_frozen_nearest_table_cell')
        self.assertEqual(bound['table']['dom_path'],keys[1])
        self.assertEqual(bound['cell']['dom_path'],facts[1].parent.path)
        missing = projection.bind_cell(facts[1],{keys[0]:views[keys[0]]},source,blocks)
        self.assertEqual(missing['status'],'unresolved')
        self.assertIsNone(missing['cell'])

    def test_duplicate_or_corrupted_frozen_cell_link_stays_unresolved(self):
        source,nodes,facts,private = bundle_fixture('<table><tr><td><ix:nonFraction>7</ix:nonFraction></td><td><ix:nonFraction>9</ix:nonFraction></td></tr></table>')
        views = frozen_views(source,nodes); table = next(iter(views.values()))
        table['fact_links'].append(copy.deepcopy(table['fact_links'][0]))
        self.assertEqual(projection.bind_cell(facts[0],views,source,private['author_staging']['blocks'])['status'],'unresolved')
        table['fact_links'].pop();table['fact_links'][0]['row_cell_index']=[0,1]
        self.assertIsNone(projection.bind_cell(facts[0],views,source,private['author_staging']['blocks'])['cell'])

    def test_fact_containment_does_not_acquire_whole_cell(self):
        source,nodes,facts,private = bundle_fixture('<table><tr><td>left<ix:nonFraction>7</ix:nonFraction>right</td></tr></table>')
        blocks = [{'anchor':projection.anchor_at(source,facts[0].start,facts[0].stop)}]
        bound = projection.bind_cell(facts[0],frozen_views(source,nodes),source,blocks)
        self.assertEqual(bound['status'],'unresolved')
        self.assertEqual(bound['reason'],'cell_not_wholly_in_fixed_context')

    def test_all_entries_empty_unsupported_and_duplicates_retained_without_helpers(self):
        source,nodes,facts,private = bundle_fixture('<div><ix:nonFraction name="SECRET_QNAME" contextRef="SECRET_CONTEXT" unitRef="SECRET_UNIT" scale="8" sign="-">7</ix:nonFraction><ix:nonFraction xsi:nil="true"/><ix:nonFraction format="SECRET_FORMAT">—</ix:nonFraction></div>')
        private['question_free_registry']['facts'].append(copy.deepcopy(private['question_free_registry']['facts'][0]))
        result = projection.project_bundle(source,private,{},copy.deepcopy(private['author_staging']),None)
        self.assertEqual(len(result['occurrences']),4)
        self.assertEqual([x['occurrence_handle'] for x in result['occurrences']],['o0000','o0001','o0002','o0003'])
        self.assertEqual([x['source_text']['decoded_text'] for x in result['occurrences']],['7','','—','7'])
        self.assertNotIn('SECRET_',json.dumps(result))
        self.assertEqual(result['whole_source_context'],private['author_staging'])
        self.assertTrue(all(x['visibility']['status']=='unverified' for x in result['occurrences']))
        self.assertEqual(result['occurrences'][0]['locator'],result['occurrences'][3]['locator'])
        second = copy.deepcopy(private);second['author_staging']['bundle_id']='p00t01'
        repeated = projection.project_bundle(source,second,{},copy.deepcopy(second['author_staging']),None)
        self.assertEqual(len(repeated['occurrences']),4)
        self.assertEqual(repeated['occurrences'][0]['locator'],result['occurrences'][0]['locator'])

    def test_unresolved_occurrence_keeps_position_without_claiming_lexical_text(self):
        source,nodes,facts,private = bundle_fixture('<div><ix:nonFraction>7</ix:nonFraction><ix:nonFraction>8</ix:nonFraction></div>')
        private['question_free_registry']['facts'][0]['anchor']['span_sha256']='0'*64
        result=projection.project_bundle(source,private,{},copy.deepcopy(private['author_staging']),None)
        self.assertEqual(len(result['occurrences']),2)
        self.assertEqual(result['occurrences'][0]['locator_status'],'unresolved')
        self.assertIsNone(result['occurrences'][0]['source_text'])
        self.assertIsNone(result['occurrences'][0]['locator'])
        self.assertEqual(result['occurrences'][1]['source_text']['decoded_text'],'8')

    def test_hidden_cues_remain_unverified_enums_not_copied_css(self):
        source,nodes,facts,private = bundle_fixture('<div hidden="hidden" style="display:none!important;SECRET_PROPERTY:SECRET_VALUE"><ix:nonFraction>7</ix:nonFraction></div>')
        result=projection.project_bundle(source,private,{},copy.deepcopy(private['author_staging']),None)
        v=result['occurrences'][0]['visibility']
        self.assertEqual(v['status'],'unverified')
        self.assertIn('html_hidden_attribute',v['recognized_cue_kinds'])
        self.assertIn('inline_display_none',v['recognized_cue_kinds'])
        self.assertNotIn('SECRET_',json.dumps(result))

    def test_extra_author_metadata_is_rejected_before_projection(self):
        source,nodes,facts,private=bundle_fixture('<div><ix:nonFraction>7</ix:nonFraction></div>')
        private['author_staging']['secret_debug_payload']='SECRET_METADATA'
        with self.assertRaises(projection.ProjectionError):
            projection.project_bundle(source,private,{},copy.deepcopy(private['author_staging']),None)

    def test_identity_reference_rejects_unlisted_helper_alias(self):
        source,nodes,facts,private=bundle_fixture('<div><ix:nonFraction>7</ix:nonFraction></div>')
        with self.assertRaises(projection.ProjectionError):
            projection.project_bundle(source,private,{},copy.deepcopy(private['author_staging']),
                                      {'arbitrary_helper_alias':'SECRET_IDENTITY_METADATA'})

    def test_row_or_cell_text_cannot_transport_nontext_metadata(self):
        for destination in ('row','cell'):
            with self.subTest(destination=destination):
                source,nodes,facts,private=bundle_fixture('<div><ix:nonFraction>7</ix:nonFraction></div>')
                span={'declared':None,'parsed':1,'status':'default_one'}
                cell={'text':'7','rowspan':span,'colspan':copy.deepcopy(span)}
                row={'text':'7','cells':[cell]}
                (row if destination=='row' else cell)['text']={'arbitrary_helper_alias':'SECRET_NONTEXT_METADATA'}
                private['author_staging']['blocks'][0]['rows']=[row]
                with self.assertRaises(projection.ProjectionError):
                    projection.project_bundle(source,private,{},copy.deepcopy(private['author_staging']),None)


class StreamBoundAdversarialTests(unittest.TestCase):
    def test_declared_and_actual_expansion_are_bounded_before_unlimited_read(self):
        record={'external_records':{'filename':'authored.gz','bytes':1,'sha256':'a'*64},
                'storage':{'uncompressed_bytes':9,'uncompressed_sha256':'b'*64}}
        with mock.patch.object(projection,'MAX_TABLE_STREAM',8), mock.patch.object(projection.gzip,'open') as opening:
            with self.assertRaisesRegex(projection.ProjectionError,'declared_expanded_table_stream_limit'):
                projection.load_tables(record)
            opening.assert_not_called()
        class BoundedStream(io.BytesIO):
            def __init__(self):
                super().__init__(b'X'*100+b'\n');self.sizes=[];self.returned=0
            def readline(self,size=-1):
                self.sizes.append(size)
                if size<0:raise AssertionError('unbounded expansion read')
                result=super().readline(size);self.returned+=len(result);return result
            def __iter__(self):raise AssertionError('unbounded gzip iteration')
        stream=BoundedStream();record['storage']['uncompressed_bytes']=8
        with mock.patch.object(projection,'MAX_TABLE_STREAM',8), mock.patch.object(projection,'checked_file',return_value=Path('/authored.gz')), mock.patch.object(projection.gzip,'open',return_value=stream):
            with self.assertRaisesRegex(projection.ProjectionError,'expanded_table_stream_limit'):
                projection.load_tables(record)
        self.assertEqual(stream.sizes,[9]);self.assertEqual(stream.returned,9)


def raster_fixture(base='/private/authored_view', modern=False):
    source_sha='1'*64
    block={'block_id':'paragraph_0','dom_path':'/authored[1]/block[1]',
           'anchor':{'byte_start':4,'byte_stop':14,'span_sha256':'2'*64}}
    def artifact(name,raw):
        return {'path':base+'/'+name,'bytes':len(raw),'sha256':projection.sha(raw)}
    pdf=artifact('rendered/source-block.pdf',b'authored PDF')
    pages=[artifact('raster/page-'+str(i)+'.png',('authored PNG '+str(i)).encode()) for i in (1,2)]
    assembly=artifact('source-assembly.html',b'authored assembly')
    fragment={'path':base+'/source-fragment.xml','bytes':10,'sha256':block['anchor']['span_sha256']}
    rendered=copy.deepcopy(block)|{'pages':2,'source_assembly_sha256':assembly['sha256'],'pdf':pdf,'pngs':pages,
                                  'roles':['SECRET_RENDER_ROLE'],'candidate_bundles':['SECRET_HINT']}
    admitted=copy.deepcopy(block)|{'decision':'admitted_as_qualified_inspection_view','raster_pages_available':2,
                                  'raster_page_indices_inspected':[0,1],'private_label':'SECRET_LABEL'}
    if modern:
        admitted['artifact_bindings']={'rendered/source-block.pdf':copy.deepcopy(pdf),'source-fragment.xml':fragment,
                                       'source-assembly.html':assembly}
        admitted['raster_bindings']=copy.deepcopy(pages)
    else:
        admitted['reviewed_artifacts']=[{'filename':a['path'][len(base)+1:],'bytes':a['bytes'],'sha256':a['sha256']}
                                        for a in [pdf,*pages,fragment,assembly]]
    admission={'sources':[{'source_sha256':source_sha,'blocks':[admitted]}]}
    execution={'sources':[{'source_sha256':source_sha,'blocks':[rendered]}]}
    return source_sha,block,admission,execution


class RasterMapAdversarialTests(unittest.TestCase):
    def project(self,fixture):
        source,block,admission,execution=fixture
        return projection.project_qualified_view(source,block,'b000',admission,execution,
                    {'path':'results/authored_admission.json','sha256':'a'*64},
                    {'path':'results/authored_execution.json','sha256':'b'*64})

    def test_both_admission_shapes_preserve_all_pages_without_helper_aliases(self):
        for modern in (False,True):
            with self.subTest(modern=modern):
                result=self.project(raster_fixture(modern=modern))
                self.assertEqual(set(result),{'block_handle','status','admission_receipt','render_receipt',
                                             'source_assembly_sha256','pdf','pages'})
                self.assertEqual([p['page_index'] for p in result['pages']],[0,1])
                self.assertEqual(len(result['pages']),2)
                self.assertNotIn('SECRET_',json.dumps(result))
                self.assertTrue(all(set(p)=={'page_index','path','bytes','sha256'} for p in result['pages']))

    def test_wrong_source_dom_anchor_and_duplicate_block_refused(self):
        for fault in ('source','dom','anchor','duplicate'):
            with self.subTest(fault=fault):
                values=raster_fixture();execution=values[3];b=execution['sources'][0]['blocks'][0]
                if fault=='source':execution['sources'][0]['source_sha256']='9'*64
                if fault=='dom':b['dom_path']='/other[1]'
                if fault=='anchor':b['anchor']['span_sha256']='9'*64
                if fault=='duplicate':execution['sources'][0]['blocks'].append(copy.deepcopy(b))
                with self.assertRaises(projection.ProjectionError):self.project(values)

    def test_missing_reordered_duplicate_uninspected_or_unqualified_pages_refused(self):
        for fault in ('missing','reordered','duplicate','uninspected','excluded'):
            with self.subTest(fault=fault):
                values=raster_fixture();a=values[2]['sources'][0]['blocks'][0];e=values[3]['sources'][0]['blocks'][0]
                if fault=='missing':e['pngs'].pop()
                if fault=='reordered':e['pngs'].reverse()
                if fault=='duplicate':e['pngs'][1]=copy.deepcopy(e['pngs'][0])
                if fault=='uninspected':a['raster_page_indices_inspected']=[0]
                if fault=='excluded':a['decision']='excluded'
                with self.assertRaises(projection.ProjectionError):self.project(values)

    def test_wrong_raster_digest_assembly_or_extra_artifact_metadata_refused(self):
        for fault in ('digest','assembly','extra_png','extra_admitted','missing_pdf'):
            with self.subTest(fault=fault):
                values=raster_fixture();a=values[2]['sources'][0]['blocks'][0];e=values[3]['sources'][0]['blocks'][0]
                if fault=='digest':e['pngs'][0]['sha256']='8'*64
                if fault=='assembly':e['source_assembly_sha256']='8'*64
                if fault=='extra_png':e['pngs'][0]['arbitrary_helper_alias']='SECRET_PNG_HELPER'
                if fault=='extra_admitted':a['reviewed_artifacts'][0]['arbitrary_helper_alias']='SECRET_ADMISSION_HELPER'
                if fault=='missing_pdf':e.pop('pdf')
                with self.assertRaises(projection.ProjectionError):self.project(values)

    def test_extra_receipt_reference_key_refused(self):
        source,block,admission,execution=raster_fixture()
        with self.assertRaises(projection.ProjectionError):
            projection.project_qualified_view(source,block,'b000',admission,execution,
                {'path':'results/a.json','sha256':'a'*64,'extra_hint':'SECRET_REFERENCE_HINT'},
                {'path':'results/e.json','sha256':'b'*64})

    def test_whole_view_integration_requires_actual_artifact_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            values=raster_fixture(base=temp);source,block,admission,execution=values
            for name,raw in [('rendered/source-block.pdf',b'authored PDF'),('raster/page-1.png',b'authored PNG 1'),('raster/page-2.png',b'authored PNG 2')]:
                p=Path(temp)/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
            a='results/authored_admission.json';e='results/authored_execution.json'
            old={'original_population_block_lineage':[dict(block,source_sha256=source,
                    derived_decision='admitted_as_qualified_inspection_view',original_receipt=a)]}
            new={'dependency_block_lineage':[]};protocol={'input_bindings':{a:'a'*64,e:'b'*64}}
            def read(path):
                if Path(path)==projection.ROOT/a:return admission
                if Path(path)==projection.ROOT/e:return execution
                raise AssertionError('unexpected metadata read')
            with mock.patch.object(projection,'RASTER_RECEIPTS',{a:e}),mock.patch.object(projection,'read_json',side_effect=read):
                self.assertEqual(len(projection.whole_view_checks(source,[block],old,new,protocol)),1)
                (Path(temp)/'raster/page-2.png').write_bytes(b'tampered')
                with self.assertRaises(projection.ProjectionError):projection.whole_view_checks(source,[block],old,new,protocol)
                (Path(temp)/'raster/page-2.png').unlink()
                with self.assertRaises(projection.ProjectionError):projection.whole_view_checks(source,[block],old,new,protocol)


if __name__ == '__main__':
    unittest.main()
