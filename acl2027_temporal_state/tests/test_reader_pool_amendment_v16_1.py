"""Authored-only controls for the explicit visibility-only pool amendment."""
import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest
from lxml import etree

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('pool_amendment_owner_v16_1',ROOT/'scripts/select_reader_pool_v16_1.py')
m=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(m)
F=importlib.util.spec_from_file_location('pool_amendment_base_fixture',ROOT/'tests/test_reader_pool_v16.py')
f=importlib.util.module_from_spec(F);F.loader.exec_module(f)


def gate(body,root_attrs='',table_attrs=''):
    raw=('<div xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" '+root_attrs+'><table '+table_attrs+'>'+body+'</table></div>').encode()
    root=etree.fromstring(raw);table=root[0];paths=m.views.expanded_paths(root);nodes={p:n for n,p in paths.items()}
    return m.hidden_descendant_gate(table,paths,nodes)


def permitted():return gate('<tr><td style="display:none"/></tr>')


class VisibilityPoolAmendmentTests(unittest.TestCase):
    def test_no_cues_is_distinct_from_permission(self):
        g=gate('<tr><td>Revenue</td></tr>');self.assertEqual(g['decision'],'no_recognized_cues');self.assertEqual(g['recognized_cue_count'],0)
    def test_empty_hidden_cell_permitted(self):
        g=permitted();self.assertEqual(g['decision'],'permit_empty_descendant_layout');self.assertEqual(g['recognized_cue_count'],1)
    def test_whitespace_hidden_row_permitted(self):
        g=gate('<tr style="visibility:collapse"><td> &#160;&#x2003; </td></tr>')
        self.assertEqual(g['decision'],'permit_empty_descendant_layout')
    def test_hidden_text_refused(self):
        g=gate('<tr><td style="display:none">USD millions</td></tr>')
        self.assertEqual(g['decision'],'reject_nonempty_or_uncertain_cue')
    def test_zero_width_not_empty(self):
        g=gate('<tr><td style="display:none">&#x200B;</td></tr>')
        self.assertEqual(g['decision'],'reject_nonempty_or_uncertain_cue')
    def test_visible_tail_outside_hidden_cell_is_not_hidden_content(self):
        g=gate('<tr><td style="display:none"/>Visible text</tr>')
        self.assertEqual(g['decision'],'permit_empty_descendant_layout')
    def test_all_nested_cues_must_be_empty(self):
        g=gate('<tr style="visibility:collapse"><td style="display:none"><span/></td></tr>')
        self.assertEqual(g['decision'],'permit_empty_descendant_layout');self.assertEqual(g['recognized_cue_count'],2)
        bad=gate('<tr style="visibility:collapse"><td style="display:none">header</td></tr>')
        self.assertEqual(bad['decision'],'reject_nonempty_or_uncertain_cue')
    def test_cue_on_table_never_permitted(self):
        g=gate('<tr><td/></tr>',table_attrs='style="display:none"')
        self.assertEqual(g['decision'],'reject_nonempty_or_uncertain_cue')
        self.assertIn('cue_on_table_or_ancestor',g['blocking_reason_counts'])
    def test_cue_on_ancestor_never_permitted(self):
        g=gate('<tr><td/></tr>',root_attrs='style="visibility:hidden"')
        self.assertEqual(g['decision'],'reject_nonempty_or_uncertain_cue')
        self.assertIn('cue_on_table_or_ancestor',g['blocking_reason_counts'])
    def test_nil_or_empty_numeric_markup_blocks_permission(self):
        for tag in ('nonFraction','fraction'):
            g=gate('<tr><td style="display:none"><ix:'+tag+'/></td></tr>')
            self.assertEqual(g['decision'],'reject_nonempty_or_uncertain_cue')
    def test_nonnumeric_markup_blocks_permission(self):
        g=gate('<tr><td style="display:none"><ix:nonNumeric/></td></tr>')
        self.assertEqual(g['decision'],'reject_nonempty_or_uncertain_cue')
    def test_image_blocks_permission_even_without_alt(self):
        g=gate('<tr><td style="display:none"><img/></td></tr>')
        self.assertEqual(g['decision'],'reject_nonempty_or_uncertain_cue')
    def test_label_and_header_attributes_block_permission(self):
        for attr in ('title="Unit"','alt="Unit"','headers="year"','aria-label="Revenue"','scope="col"'):
            self.assertEqual(gate('<tr><td style="display:none" '+attr+'/></tr>')['decision'],'reject_nonempty_or_uncertain_cue')
    def test_unknown_nonempty_attribute_blocks_permission(self):
        g=gate('<tr><td style="display:none" data-binding="unknown"/></tr>')
        self.assertEqual(g['decision'],'reject_nonempty_or_uncertain_cue')
    def test_empty_unknown_attribute_not_invented_as_content(self):
        g=gate('<tr><td style="display:none" data-binding=" &#160; "/></tr>')
        self.assertEqual(g['decision'],'permit_empty_descendant_layout')
    def test_id_class_hooks_block_even_when_empty(self):
        for attr in ('id="named"','class="x"','id=""','class=""'):
            g=gate('<tr><td style="display:none" '+attr+'/></tr>')
            self.assertEqual(g['decision'],'reject_nonempty_or_uncertain_cue')
            self.assertIn('id_or_class_attribute_hook_present',g['blocking_reason_counts'])
    def test_css_content_or_url_blocks_permission(self):
        for style in ("display:none;content:'Unit'",'display:none;background-image:url(label.png)'):
            g=gate('<tr><td style="'+style+'"/></tr>')
            self.assertEqual(g['decision'],'reject_nonempty_or_uncertain_cue')
    def test_nonlayout_node_blocks_permission(self):
        self.assertEqual(gate('<tr><td style="display:none"><a/></td></tr>')['decision'],'reject_nonempty_or_uncertain_cue')
    def test_only_hiding_rejection_removed(self):
        table,facts=f.fixture();table['rows'][0]['cells'][0]['visibility']['status']='markup_hiding_cue'
        a=m.assess_table(table,facts,'Net sales',permitted())
        self.assertTrue(a['eligible']);self.assertFalse(a['original_eligible'])
        self.assertEqual(a['original_rejection_reasons'],['recognized_hiding_cue'])
    def test_fact_cap_not_relaxed(self):
        table,facts=f.fixture(33)
        a=m.assess_table(table,facts,'Net sales',permitted())
        self.assertFalse(a['eligible']);self.assertIn('numeric_occurrence_count_outside_4_32',a['rejection_reasons'])
    def test_period_count_not_relaxed(self):
        table,facts=f.fixture()
        for fact in facts:fact['reported_aspects']['period']=copy.deepcopy(facts[0]['reported_aspects']['period'])
        a=m.assess_table(table,facts,'Net sales',permitted())
        self.assertIn('fewer_than_2_resolved_duration_periods',a['rejection_reasons'])
    def test_label_requirement_not_relaxed(self):
        table,facts=f.fixture();table['rows'][0]['text']='unrelated'
        self.assertIn('missing_revenue_row_label',m.assess_table(table,facts,'unrelated',permitted())['rejection_reasons'])
    def test_unknown_view_visibility_without_cue_not_cleared(self):
        table,facts=f.fixture();table['visibility']['status']='unknown'
        a=m.assess_table(table,facts,'Net sales',gate('<tr><td/></tr>'))
        self.assertIn('recognized_hiding_cue',a['rejection_reasons'])
    def test_unknown_view_status_not_cleared_by_other_empty_cue(self):
        table,facts=f.fixture();table['rows'][0]['cells'][0]['visibility']['status']='unknown'
        a=m.assess_table(table,facts,'Net sales',permitted())
        self.assertIn('recognized_hiding_cue',a['rejection_reasons'])
        self.assertTrue(a['unrecognized_view_visibility_status'])
    def test_assessment_preserves_all_rows_cells_facts(self):
        table,facts=f.fixture(6);facts[-1]['numeric_status']='unsupported';facts[-1]['normalized_value']=None
        original=copy.deepcopy((table,facts));a=m.assess_table(table,facts,'Net sales',permitted())
        self.assertEqual((table,facts),original);self.assertEqual(a['fact_count'],6)
        self.assertEqual(a['numeric_status_counts']['unsupported'],1)
    def test_original_rules_unchanged_except_explicit_visibility_policy(self):
        new=copy.deepcopy(m.RULE);new.pop('recognized_hiding_amendment')
        new['recognized_hiding_cues_allowed']=False
        self.assertEqual(new,m.base.RULE)
    def test_original_first_two_document_order_is_reused(self):
        a=[{'table_ordinal':i,'eligible':i in (1,3,5)} for i in range(6)]
        self.assertEqual([r['table_ordinal'] for r in m.base.choose_tables(a)],[1,3])
    def test_existing_protocol_never_overwritten(self):
        with tempfile.TemporaryDirectory(dir='/dev/shm') as d:
            p=Path(d)/'protocol.json';p.write_text('{}')
            with self.assertRaisesRegex(m.base.PoolError,'overwrite'):m.freeze(p)
    def test_existing_attempt_never_overwritten(self):
        with tempfile.TemporaryDirectory(dir='/dev/shm') as d:
            p=Path(d)
            with self.assertRaisesRegex(m.base.PoolError,'overwrite'):m.execute(p/'protocol',p,p,p,p,p/'out')
    def test_private_repository_output_refused(self):
        with tempfile.TemporaryDirectory(dir='/dev/shm') as d:
            p=Path(d)
            with self.assertRaisesRegex(m.base.PoolError,'outside_repository'):
                m.execute(p/'protocol',p,p,p,m.ROOT/'unused_authored_private_attempt',p/'out')

if __name__=='__main__':unittest.main()
