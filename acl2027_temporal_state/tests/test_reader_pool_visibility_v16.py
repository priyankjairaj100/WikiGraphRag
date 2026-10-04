"""Authored empty-subtree characterization fixtures; no filings or QA labels."""
import importlib.util
from pathlib import Path
import unittest
from lxml import etree

SPEC=importlib.util.spec_from_file_location('pool_visibility_characterization_v16',Path(__file__).resolve().parents[1]/'scripts/characterize_reader_pool_visibility_v16.py')
m=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(m)


def element(body):
    return etree.fromstring(('<div xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL">'+body+'</div>').encode())[0]


class ReaderPoolVisibilityTests(unittest.TestCase):
    def test_unicode_whitespace_including_nbsp_is_empty(self):
        r=m.characterize_subtree(element('<td style="display:none"> \t&#160;&#x2003;\n</td>'))
        self.assertTrue(r['strictly_empty_layout']);self.assertTrue(r['unicode_whitespace_only_text'])
    def test_zero_width_character_is_not_whitespace(self):
        r=m.characterize_subtree(element('<td style="display:none">&#x200B;</td>'))
        self.assertFalse(r['strictly_empty_layout']);self.assertTrue(r['non_whitespace_text'])
    def test_truly_empty_nested_layout(self):
        r=m.characterize_subtree(element('<tr style="visibility:collapse"><td width="30"/><td><span> </span></td></tr>'))
        self.assertTrue(r['strictly_empty_layout'])
    def test_descendant_tail_is_inside_subtree(self):
        r=m.characterize_subtree(element('<td><span/>UNIT</td>'))
        self.assertTrue(r['non_whitespace_text']);self.assertFalse(r['strictly_empty_layout'])
    def test_root_tail_is_outside_cue_subtree(self):
        r=m.characterize_subtree(element('<td style="display:none"/>OUTSIDE VALUE'))
        self.assertTrue(r['strictly_empty_layout']);self.assertTrue(r['no_text_nodes_with_characters'])
    def test_empty_numeric_fact_still_blocks_empty(self):
        r=m.characterize_subtree(element('<td><ix:nonFraction/></td>'))
        self.assertTrue(r['numeric_facts']);self.assertEqual(r['numeric_fact_occurrences'],1)
        self.assertFalse(r['strictly_empty_layout'])
    def test_fraction_counts_numeric(self):
        r=m.characterize_subtree(element('<td><ix:fraction/></td>'))
        self.assertEqual(r['numeric_fact_occurrences'],1)
    def test_empty_nonnumeric_fact_is_not_empty_layout(self):
        r=m.characterize_subtree(element('<td><ix:nonNumeric/></td>'))
        self.assertFalse(r['numeric_facts']);self.assertTrue(r['non_layout_elements'])
        self.assertFalse(r['strictly_empty_layout'])
    def test_image_without_alt_blocks_empty(self):
        r=m.characterize_subtree(element('<td><img/></td>'))
        self.assertTrue(r['images_or_embedded_media']);self.assertFalse(r['non_whitespace_alt_text'])
        self.assertFalse(r['strictly_empty_layout'])
    def test_alt_text_and_title_are_counted_without_releasing_values(self):
        r=m.characterize_subtree(element('<td title="PRIVATE_LABEL"><img alt="PRIVATE_ALT"/></td>'))
        self.assertTrue(r['non_whitespace_alt_text']);self.assertEqual(r['label_attribute_counts_by_name'],{'title':1,'alt':1})
        self.assertNotIn('PRIVATE',str(r))
    def test_aria_and_header_attributes_block_empty(self):
        r=m.characterize_subtree(element('<th aria-labelledby="label-id" headers="column-a" scope="col"/>'))
        self.assertTrue(r['non_whitespace_label_attributes']);self.assertFalse(r['strictly_empty_layout'])
    def test_unknown_nonempty_attribute_blocks_empty(self):
        r=m.characterize_subtree(element('<td data-binding="unknown"/>'))
        self.assertTrue(r['other_nonempty_attributes']);self.assertFalse(r['strictly_empty_layout'])
    def test_unknown_whitespace_attribute_is_not_content(self):
        r=m.characterize_subtree(element('<td data-binding=" &#160; "/>'))
        self.assertFalse(r['other_nonempty_attributes']);self.assertTrue(r['strictly_empty_layout'])
    def test_inline_generated_content_or_image_url_blocks_empty(self):
        for style in ("display:none;content:'header'","visibility:collapse;background-image:url(image.png)"):
            r=m.characterize_subtree(element('<td style="'+style+'"/>'))
            self.assertTrue(r['other_nonempty_attributes']);self.assertFalse(r['strictly_empty_layout'])
    def test_id_class_hooks_retained_without_css_assurance(self):
        r=m.characterize_subtree(element('<td id="slot" class="generated-content"/>'))
        self.assertTrue(r['strictly_empty_layout']);self.assertEqual(r['id_or_class_attribute_count'],2)
        self.assertFalse(r['computed_css_evaluated']);self.assertFalse(r['rendering_verified'])
    def test_comments_not_visible_text(self):
        r=m.characterize_subtree(element('<td><!-- COMMENT VALUE --></td>'))
        self.assertTrue(r['strictly_empty_layout']);self.assertEqual(r['comment_or_processing_instruction_count'],1)
    def test_nonlayout_element_blocks_empty(self):
        r=m.characterize_subtree(element('<td><a/></td>'))
        self.assertTrue(r['non_layout_elements']);self.assertFalse(r['strictly_empty_layout'])
    def test_candidate_requires_every_cue_to_be_empty(self):
        empty=m.characterize_subtree(element('<td/>'));nonempty=m.characterize_subtree(element('<td>header</td>'))
        candidates=[{'source_sha256':'a','cues':[{'path':'/1','kinds':['display_none'],'characterization':empty}]},
                    {'source_sha256':'a','cues':[{'path':'/2','kinds':['display_none'],'characterization':empty},{'path':'/3','kinds':['visibility_hidden'],'characterization':nonempty}]}]
        r=m.summarize(candidates)
        self.assertEqual(r['hiding_only_candidate_count'],2)
        self.assertEqual(r['candidates_whose_every_recognized_cue_subtree_is_strictly_empty_layout'],1)
        self.assertEqual(r['cue_subtree_counts_by_overlapping_category']['non_whitespace_text'],1)
    def test_shared_cue_node_distinct_count_is_explicit(self):
        empty=m.characterize_subtree(element('<td/>'))
        c={'source_sha256':'a','cues':[{'path':'/1','kinds':['display_none'],'characterization':empty}]}
        r=m.summarize([c,c])
        self.assertEqual(r['cue_subtree_occurrences_sum_over_candidates'],2)
        self.assertEqual(r['distinct_source_cue_nodes'],1)

if __name__=='__main__':unittest.main()
