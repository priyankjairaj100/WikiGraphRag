"""Authored fixtures only. No natural filing values, questions or outputs loaded."""
import copy
import gzip
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

SPEC=importlib.util.spec_from_file_location('reader_pool_v16', Path(__file__).resolve().parents[1]/'scripts/select_reader_pool_v16.py')
pool=importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(pool)


def fixture(n=4):
    table={'table_ordinal':0,'dom_path':'/authored/table[1]','anchor':{'byte_start':1,'byte_stop':999,'span_sha256':'authored'},
           'parent_table_ordinal':None,'child_table_ordinals':[], 'orphan_cells':[],
           'visibility':{'status':'no_recognized_hiding_cue','cues':[]},
           'rows':[{'text':'Net sales\t101\t102','cells':[{'text':'Net sales','visibility':{'status':'no_recognized_hiding_cue','cues':[]}}]}],
           'captions':[], 'paragraph_refs':{'preceding':[],'following':[],'attachment':'neighbors_only'},'fact_links':[]}
    facts=[]
    for i in range(n):
        anchor={'byte_start':10+i*10,'byte_stop':19+i*10,'span_sha256':'s'+str(i)}
        fact={'fact_ordinal':i,'numeric_status':'normalized','binding_status':'reported_aspects_resolved',
              'normalized_value':str(100+i),'reported_aspects':{'period':{'kind':'duration','lexemes':['2022-01-01','2022-12-31'] if i%2==0 else ['2023-01-01','2023-12-31']}},
              'anchor':dict(anchor,dom_path=table['dom_path']+'/fact['+str(i+1)+']')}
        facts.append(fact)
        table['fact_links'].append({'fact_ordinal':i,'anchor':anchor,'path_from_table':'/fact['+str(i+1)+']','row_cell_index':[0,0]})
    return table,facts


def assess(n=4, **kwargs):
    table,facts=fixture(n)
    return pool.assess_table(table,facts,'Net sales 101 102',**kwargs)


class ReaderPoolTests(unittest.TestCase):
    def test_eligible_minimum(self):
        self.assertTrue(assess()['eligible'])
    def test_eligible_maximum(self):
        self.assertTrue(assess(32)['eligible'])
    def test_numeric_occurrence_bounds(self):
        for n in (0,3,33):
            self.assertIn('numeric_occurrence_count_outside_4_32',assess(n)['rejection_reasons'])
    def test_layout_parent_allowed(self):
        table,facts=fixture(); table['parent_table_ordinal']=9
        self.assertTrue(pool.assess_table(table,facts,'Net sales')['eligible'])
    def test_child_table_rejected(self):
        table,facts=fixture(); table['child_table_ordinals']=[1]
        self.assertIn('has_child_table',pool.assess_table(table,facts,'Net sales')['rejection_reasons'])
    def test_orphan_cell_rejected(self):
        table,facts=fixture(); table['orphan_cells']=[{'text':'orphan'}]
        self.assertIn('incomplete_row_cell_ownership',pool.assess_table(table,facts,'Net sales')['rejection_reasons'])
    def test_orphan_fact_rejected(self):
        table,facts=fixture(); table['fact_links'][0]['row_cell_index']=None
        self.assertIn('incomplete_row_cell_ownership',pool.assess_table(table,facts,'Net sales')['rejection_reasons'])
    def test_table_hiding_rejected(self):
        table,facts=fixture(); table['visibility']['status']='markup_hiding_cue'
        self.assertIn('recognized_hiding_cue',pool.assess_table(table,facts,'Net sales')['rejection_reasons'])
    def test_cell_hiding_rejected(self):
        table,facts=fixture(); table['rows'][0]['cells'][0]['visibility']['status']='markup_hiding_cue'
        self.assertIn('recognized_hiding_cue',pool.assess_table(table,facts,'Net sales')['rejection_reasons'])
    def test_inner_hiding_rejected(self):
        self.assertIn('recognized_hiding_cue',assess(descendant_hiding=True)['rejection_reasons'])
    def test_word_boundaries_and_case(self):
        for label in ('Revenue','REVENUES','Net Sales','net\u00a0sales','Operating revenues:'):
            table,facts=fixture(); table['rows'][0]['text']=label
            self.assertTrue(pool.assess_table(table,facts,label)['eligible'],label)
        for label in ('prerevenue','revenuesX','net salesmen','income only'):
            table,facts=fixture(); table['rows'][0]['text']=label
            self.assertIn('missing_revenue_row_label',pool.assess_table(table,facts,label)['rejection_reasons'],label)
    def test_net_sales_cannot_match_across_distinct_rows(self):
        table,facts=fixture()
        table['rows']=[{'text':'Net','cells':[]},{'text':'Sales','cells':[]}]
        result=pool.assess_table(table,facts,'Net Sales')
        self.assertIn('missing_revenue_row_label',result['rejection_reasons'])
    def test_revenue_in_neighbor_or_caption_is_not_row_match(self):
        table,facts=fixture(); table['rows'][0]['text']='Other row'; table['captions']=[{'text':'Net sales'}]
        self.assertIn('missing_revenue_row_label',pool.assess_table(table,facts,'Net sales')['rejection_reasons'])
    def test_duration_fact_minimum(self):
        table,facts=fixture(); facts[0]['numeric_status']='nil'
        self.assertIn('fewer_than_4_usable_duration_facts',pool.assess_table(table,facts,'Net sales')['rejection_reasons'])
    def test_unresolved_binding_not_usable(self):
        table,facts=fixture(); facts[0]['binding_status']='unresolved'
        self.assertIn('fewer_than_4_usable_duration_facts',pool.assess_table(table,facts,'Net sales')['rejection_reasons'])
    def test_instant_not_duration(self):
        table,facts=fixture(); facts[0]['reported_aspects']['period']={'kind':'instant','lexemes':['2023-12-31']}
        self.assertIn('fewer_than_4_usable_duration_facts',pool.assess_table(table,facts,'Net sales')['rejection_reasons'])
    def test_two_periods_not_just_two_facts(self):
        table,facts=fixture()
        for fact in facts: fact['reported_aspects']['period']=copy.deepcopy(facts[0]['reported_aspects']['period'])
        self.assertIn('fewer_than_2_resolved_duration_periods',pool.assess_table(table,facts,'Net sales')['rejection_reasons'])
    def test_nil_or_unsupported_retained_and_can_have_resolved_period(self):
        table,facts=fixture(6); facts[4]['numeric_status']='nil'; facts[4]['normalized_value']=None
        facts[5]['numeric_status']='unsupported'; facts[5]['normalized_value']=None
        before=copy.deepcopy(facts)
        result=pool.assess_table(table,facts,'Net sales')
        self.assertTrue(result['eligible']); self.assertEqual(result['fact_count'],6)
        self.assertEqual(result['numeric_status_counts'],{'normalized':4,'nil':1,'unsupported':1})
        self.assertEqual(facts,before)
    def test_length_boundary_counts_unicode_characters(self):
        table,facts=fixture()
        self.assertTrue(pool.assess_table(table,facts,'é'*12000)['eligible'])
        self.assertIn('complete_dom_text_exceeds_12000_characters',pool.assess_table(table,facts,'é'*12001)['rejection_reasons'])
    def test_all_rejection_reasons_preserved(self):
        table,facts=fixture(3); table['child_table_ordinals']=[5]; table['rows'][0]['text']='nothing'
        result=pool.assess_table(table,facts,'x'*12001)
        self.assertEqual(result['rejection_reasons'],['has_child_table','numeric_occurrence_count_outside_4_32','fewer_than_4_usable_duration_facts','missing_revenue_row_label','complete_dom_text_exceeds_12000_characters'])
    def test_first_two_eligible_document_order(self):
        records=[{'table_ordinal':i,'eligible':i in (1,3,4)} for i in range(6)]
        self.assertEqual([x['table_ordinal'] for x in pool.choose_tables(records)],[1,3])
    def test_unfilled_slots_are_not_fabricated(self):
        self.assertEqual(pool.choose_tables([{'table_ordinal':0,'eligible':False}]),[])
        self.assertEqual(len(pool.choose_tables([{'table_ordinal':0,'eligible':True}])),1)
    def test_unsorted_or_duplicate_tables_rejected(self):
        for ids in ([1,0],[0,0]):
            with self.assertRaisesRegex(pool.PoolError,'document_order'):
                pool.choose_tables([{'table_ordinal':i,'eligible':True} for i in ids])
    def test_fact_join_preserves_all_facts(self):
        table,facts=fixture(6); facts[-1]['numeric_status']='unsupported'
        self.assertEqual(pool.joined_facts(table,{f['fact_ordinal']:f for f in facts}),facts)
    def test_wrong_fact_anchor_or_locator_rejected(self):
        for key,value in [('byte_start',10000),('dom_path','/wrong')]:
            table,facts=fixture(); facts[0]['anchor'][key]=value
            with self.assertRaises(pool.PoolError): pool.joined_facts(table,{f['fact_ordinal']:f for f in facts})
    def test_duplicate_and_missing_fact_rejected(self):
        table,facts=fixture(); table['fact_links'].append(copy.deepcopy(table['fact_links'][0]))
        with self.assertRaisesRegex(pool.PoolError,'ordinal'): pool.joined_facts(table,{f['fact_ordinal']:f for f in facts})
        table,facts=fixture()
        with self.assertRaisesRegex(pool.PoolError,'ordinal'): pool.joined_facts(table,{f['fact_ordinal']:f for f in facts[1:]})
    def test_author_packet_excludes_typed_join_and_contexts(self):
        table,facts=fixture(); table['rows'][0]['cells'][0]['text']='Visible 123'
        result=pool.author_packet(table,'Visible 123',[],{'source_sha256':'f'*64})
        text=json.dumps(result)
        for banned in ('fact_links','reported_aspects','normalized_value','context_id','format_qname'):
            self.assertNotIn(banned,text)
        self.assertIn('Visible 123',text); self.assertEqual(result['rows'],table['rows'])
        result['rows'][0]['cells'][0]['text']='mutated'
        self.assertEqual(table['rows'][0]['cells'][0]['text'],'Visible 123')
    def test_source_year_comes_from_frozen_identity(self):
        item={'source_path':'FILE_2099.html','source_sha256':'abc','identity_facts':[{'name':'dei:DocumentFiscalYearFocus','value':'2021','contextref':'HIDDEN_CONTEXT'}],
              'document_fiscal_year_values':['2021'],'filename_matches_document_fiscal_year':False}
        card=pool.source_card(item)
        self.assertEqual(card['internal_fiscal_year_values'],['2021'])
        self.assertFalse(card['filename_year_inference']); self.assertNotIn('HIDDEN_CONTEXT',json.dumps(card))
    def test_gzip_and_plain_view_records(self):
        with tempfile.TemporaryDirectory(dir='/dev/shm') as d:
            p=Path(d)/'a.jsonl'; p.write_bytes(pool.encoded({'record_type':'authored'}))
            z=Path(d)/'a.jsonl.gz'
            with gzip.open(z,'wb') as f:f.write(p.read_bytes())
            self.assertEqual(pool.load_view_records(p),pool.load_view_records(z))
    def test_confined_paths_reject_traversal_and_symlink(self):
        with tempfile.TemporaryDirectory(dir='/dev/shm') as d:
            root=Path(d)/'inner'; root.mkdir(); (root/'linked').symlink_to(Path(d)/'outside')
            for name in ('../outside','/absolute','a/b','linked'):
                with self.assertRaises(pool.PoolError):pool.confined(root,name)
    def test_writer_retains_whole_prior_record_on_limit(self):
        item={'authored':'value'}; f=io.BytesIO(); writer=pool.Writer(len(pool.encoded(item)))
        writer.write(f,item); before=f.getvalue()
        with self.assertRaisesRegex(pool.PoolError,'budget'):writer.write(f,item)
        self.assertEqual(f.getvalue(),before)
    def test_new_json_never_overwrites(self):
        with tempfile.TemporaryDirectory(dir='/dev/shm') as d:
            p=Path(d)/'receipt.json'; pool.new_json(p,{'first':True})
            with self.assertRaises(FileExistsError):pool.new_json(p,{'first':False})
            self.assertTrue(json.loads(p.read_text())['first'])
    def test_existing_attempt_rejected_before_missing_inputs(self):
        with tempfile.TemporaryDirectory(dir='/dev/shm') as d:
            p=Path(d)
            with self.assertRaisesRegex(pool.PoolError,'refusing_to_overwrite'):
                pool.execute(p/'protocol.json',source_dir=p,typed_dir=p,view_dir=p,external_dir=p,output=p/'out.json')
    def test_private_repository_output_rejected(self):
        with tempfile.TemporaryDirectory(dir='/dev/shm') as d:
            p=Path(d)
            with self.assertRaisesRegex(pool.PoolError,'outside_repository'):
                pool.execute(p/'missing',source_dir=p,typed_dir=p,view_dir=p,external_dir=pool.ROOT/'authored_nonexistent_private_attempt',output=p/'out.json')
    def test_protocol_overwrite_rejected_before_input_access(self):
        with tempfile.TemporaryDirectory(dir='/dev/shm') as d:
            p=Path(d)/'existing.json'; p.write_text('{}')
            with self.assertRaisesRegex(pool.PoolError,'overwrite_protocol'):
                pool.prepare_protocol(p,inventory_path=Path(d)/'missing',view_protocol_path=Path(d)/'missing')

if __name__=='__main__':unittest.main()
