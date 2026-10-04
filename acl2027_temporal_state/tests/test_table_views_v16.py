"""Authored table fixtures only; no source filings or QA labels are loaded."""
import copy
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest

PATH = Path(__file__).resolve().parents[1] / 'scripts' / 'build_table_views_v16.py'
SPEC = importlib.util.spec_from_file_location('table_views_v16', PATH)
view = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(view)


def fixture(body, encoding='UTF-8'):
    return ('<?xml version="1.0" encoding="' + encoding + '"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml" '
            'xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" '
            'xmlns:x="urn:authored"><head/><body>' + body + '</body></html>').encode('utf-8')


def fact(value='12'):
    return '<ix:nonFraction name="x:Amount" contextRef="c" unitRef="u" decimals="0">' + value + '</ix:nonFraction>'


def extract(source):
    typed = view.byte_reader.read_inline_xbrl(source, source_version='authored')['facts']
    return view.extract_views(source, typed, source_path='authored.html', source_sha256=view.sha(source))


def tables(records):
    return [r for r in records if r['record_type'] == 'table']


class TableViewsTests(unittest.TestCase):
    def test_complete_nearest_table_population(self):
        source = fixture(fact('8') + '<table><tr><td>' + fact('9') + '</td><td>' + fact('10') + '</td></tr></table>')
        records, public = extract(source)
        self.assertEqual(public['counts']['facts'], 3)
        self.assertEqual(public['counts']['facts_owned_by_tables'], 2)
        self.assertEqual(records[0]['outside_table_fact_ordinals'], [0])
        self.assertEqual([x['fact_ordinal'] for x in tables(records)[0]['fact_links']], [1, 2])

    def test_nested_table_text_and_fact_ownership_are_not_duplicated(self):
        source = fixture('<table><tr><td>A' + fact('1') + '<table><tr><td>B' + fact('2') + '</td></tr></table>C</td></tr></table>')
        records, public = extract(source)
        outer, inner = tables(records)
        self.assertEqual(outer['rows'][0]['cells'][0]['text'], 'A1C')
        self.assertEqual(inner['rows'][0]['cells'][0]['text'], 'B2')
        self.assertEqual(outer['rows'][0]['cells'][0]['nested_table_ordinals'], [1])
        self.assertEqual(inner['parent_table_ordinal'], 0)
        self.assertEqual([f['fact_ordinal'] for f in outer['fact_links']], [0])
        self.assertEqual([f['fact_ordinal'] for f in inner['fact_links']], [1])
        self.assertEqual(public['counts']['nested_tables'], 1)
        self.assertEqual(public['counts']['facts_owned_by_tables'], 2)

    def test_rowspan_colspan_retained_without_grid_duplication(self):
        source = fixture('<table><tbody><tr><th rowspan="2" colspan="3" scope="col">Year</th><td>Value</td></tr><tr><td>Next</td></tr></tbody></table>')
        records, public = extract(source)
        cell = tables(records)[0]['rows'][0]['cells'][0]
        self.assertEqual(cell['rowspan']['parsed'], 2)
        self.assertEqual(cell['colspan']['parsed'], 3)
        self.assertEqual(cell['scope_declared'], 'col')
        self.assertEqual(public['counts']['cells'], 3)
        self.assertEqual(tables(records)[0]['grid_expansion'], 'not_attempted')

    def test_zero_rowspan_remains_unexpanded(self):
        records, _ = extract(fixture('<table><tr><td rowspan="0">a</td></tr></table>'))
        attr = tables(records)[0]['rows'][0]['cells'][0]['rowspan']
        self.assertEqual(attr['parsed'], 0)
        self.assertEqual(attr['status'], 'declared_remaining_row_group_unexpanded')

    def test_invalid_span_not_silently_defaulted(self):
        records, _ = extract(fixture('<table><tr><td rowspan="-2" colspan="0">a</td></tr></table>'))
        cell = tables(records)[0]['rows'][0]['cells'][0]
        self.assertIsNone(cell['rowspan']['parsed'])
        self.assertIsNone(cell['colspan']['parsed'])
        self.assertEqual(cell['rowspan']['declared'], '-2')

    def test_utf8_offsets_are_bytes_and_quoted_greater_than_is_preserved(self):
        source = fixture('<p>é 量</p><table title="a > b"><tr><td title="c > d">é</td></tr></table>')
        records, _ = extract(source)
        table = tables(records)[0]
        a = table['anchor']
        span = source[a['byte_start']:a['byte_stop']]
        self.assertEqual(span, '<table title="a > b"><tr><td title="c > d">é</td></tr></table>'.encode())
        self.assertEqual(view.sha(span), a['span_sha256'])
        self.assertEqual(a['byte_start'], source.index(b'<table'))

    def test_ascii_character_reference_preserves_source_bytes(self):
        source = fixture('<table><tr><td>&#xE9;&#xA0;A</td></tr></table>', 'ASCII')
        records, _ = extract(source)
        cell = tables(records)[0]['rows'][0]['cells'][0]
        self.assertEqual(cell['text'], 'é\u00a0A')
        self.assertIn(b'&#xE9;', source[cell['anchor']['byte_start']:cell['anchor']['byte_stop']])

    def test_whitespace_rule_preserves_nbsp_and_inline_numeric_concat(self):
        records, _ = extract(fixture('<table><tr><td> A\n\tB<br/> C&#xA0;D <b>1</b><i>2</i></td></tr></table>'))
        self.assertEqual(tables(records)[0]['rows'][0]['cells'][0]['text'], 'A B C\u00a0D 12')

    def test_syntactic_block_separators_prevent_word_fusion(self):
        records, _ = extract(fixture('<table><tr><td><div>Label</div><p>2023</p><span>1</span><span>2</span></td></tr></table>'))
        self.assertEqual(tables(records)[0]['rows'][0]['cells'][0]['text'], 'Label 2023 12')

    def test_script_style_not_included_in_text_but_css_not_rendered(self):
        records, _ = extract(fixture('<table class="hidden"><tr><td>a<style>.x{display:none}</style><script>secret</script>b</td></tr></table>'))
        table = tables(records)[0]
        self.assertEqual(table['rows'][0]['cells'][0]['text'], 'ab')
        self.assertEqual(table['visibility']['status'], 'no_recognized_hiding_cue')
        self.assertFalse(table['visibility']['computed_css_evaluated'])
        self.assertFalse(table['visibility']['rendering_verified'])

    def test_ancestor_hiding_cues_are_retained(self):
        source = fixture('<div style="display: none !important"><table><tr><td hidden="hidden">x</td></tr></table></div>')
        records, public = extract(source)
        table = tables(records)[0]
        self.assertTrue(public['tables'][0]['markup_hiding_cue'])
        self.assertEqual(table['visibility']['status'], 'markup_hiding_cue')
        self.assertEqual(len(table['rows'][0]['cells'][0]['visibility']['cues']), 2)

    def test_paragraph_references_bounded_and_not_semantically_attached(self):
        source = fixture('<p>p0</p><div><span>p1</span></div><p>p2</p><table><tr><td>t</td></tr></table><p>p3</p><h2>p4</h2><p>p5</p>')
        records, public = extract(source)
        refs = tables(records)[0]['paragraph_refs']
        self.assertEqual(refs['preceding'], [1, 2])
        self.assertEqual(refs['following'], [3, 4])
        self.assertEqual(public['counts']['paragraph_blocks'], 6)
        self.assertIn('not_semantic_evidence', refs['attachment'])

    def test_table_paragraphs_never_become_neighbor_paragraph_records(self):
        source = fixture('<p>outside</p><table><tr><td><p>inside</p><div>also inside</div></td></tr></table>')
        records, _ = extract(source)
        paras = [r for r in records if r['record_type'] == 'paragraph']
        self.assertEqual([r['text'] for r in paras], ['outside'])

    def test_expanded_locator_reconstruction_and_same_name_indices(self):
        source = fixture('<table/><div/><table><tbody><tr><td>a</td><th>b</th><td>c</td></tr></tbody></table>')
        records, _ = extract(source)
        table = tables(records)[1]
        row = table['rows'][0]
        self.assertTrue(table['dom_path'].endswith('table[2]'))
        self.assertTrue(row['cells'][2]['path_from_row'].endswith('td[2]'))
        full = table['dom_path'] + row['path_from_table'] + row['cells'][2]['path_from_row']
        _, _, spans = view.parse_bound(source)
        self.assertIn(full, spans)
        self.assertEqual(source[spans[full].start:spans[full].stop], b'<td>c</td>')

    def test_comments_do_not_change_element_sibling_indices(self):
        records, _ = extract(fixture('<table><tr><td>a</td><!-- not an element --><td>b</td></tr></table>'))
        self.assertTrue(tables(records)[0]['rows'][0]['cells'][1]['path_from_row'].endswith('td[2]'))

    def test_self_closing_table_anchor(self):
        source = fixture('<table title="a > b"/>')
        records, _ = extract(source)
        a = tables(records)[0]['anchor']
        self.assertEqual(source[a['byte_start']:a['byte_stop']], b'<table title="a > b"/>')

    def test_orphan_cells_and_facts_are_retained(self):
        source = fixture('<table><td>x' + fact() + '</td>' + fact('3') + '</table>')
        records, public = extract(source)
        table = tables(records)[0]
        self.assertEqual(public['counts']['orphan_cells'], 1)
        self.assertEqual(len(table['fact_links']), 2)
        self.assertTrue(all(f['row_cell_index'] is None for f in table['fact_links']))
        self.assertEqual(table['orphan_cells'][0]['text'], 'x12')

    def test_wrong_source_hash_rejected(self):
        with self.assertRaisesRegex(view.ViewError, 'source_hash_mismatch'):
            view.extract_views(fixture('<table/>'), [], source_path='a', source_sha256='0' * 64)

    def test_wrong_typed_anchor_rejected(self):
        source = fixture('<table><tr><td>' + fact() + '</td></tr></table>')
        typed = view.byte_reader.read_inline_xbrl(source, source_version='authored')['facts']
        typed[0]['anchor']['byte_stop'] -= 1
        with self.assertRaisesRegex(view.ViewError, 'anchor_or_ordinal'):
            view.extract_views(source, typed, source_path='a', source_sha256=view.sha(source))

    def test_wrong_typed_population_rejected(self):
        source = fixture('<table>' + fact() + '</table>')
        with self.assertRaisesRegex(view.ViewError, 'population'):
            view.extract_views(source, [], source_path='a', source_sha256=view.sha(source))

    def test_unsafe_dtd_rejected(self):
        source = fixture('<table/>').replace(b'<html', b'<!DOCTYPE html [<!ENTITY x "value">]><html', 1)
        with self.assertRaises(view.byte_reader.ReaderError):
            extract(source)

    def test_declared_ascii_literal_unicode_rejected(self):
        with self.assertRaises(view.byte_reader.ReaderError):
            extract(fixture('<table><tr><td>é</td></tr></table>', 'ASCII'))

    def test_public_inventory_does_not_include_source_text_or_typed_values(self):
        source = fixture('<p>PRIVATE_PARAGRAPH</p><table><tr><td>PRIVATE_LABEL' + fact('987654') + '</td></tr></table>')
        _, public = extract(source)
        serialized = view.encoded(public)
        for text in (b'PRIVATE_LABEL', b'PRIVATE_PARAGRAPH', b'987654', b'normalized_value'):
            self.assertNotIn(text, serialized)

    def test_output_limit_refuses_record_whole_without_truncation(self):
        out = io.BytesIO()
        first = {'a': '123'}
        writer = view.ExternalWriter(len(view.encoded(first)))
        writer.write(out, first)
        before = out.getvalue()
        with self.assertRaises(view.OutputLimitExceeded):
            writer.write(out, {'b': '456'})
        self.assertEqual(out.getvalue(), before)
        self.assertEqual(writer.bytes, len(before))

    def test_runtime_binding_handles_builtin_or_extension_expat(self):
        bindings = view.runtime_bindings()
        self.assertIn(str(Path(view.sys.executable).resolve()), bindings['binary_files'])
        self.assertIn(bindings['pyexpat_implementation'], ('extension_file', 'built_into_pinned_interpreter'))
        self.assertEqual(bindings['expat_version'], view.expat.EXPAT_VERSION)

    def test_existing_attempt_not_overwritten(self):
        with tempfile.TemporaryDirectory(dir='/dev/shm') as d:
            directory = Path(d)
            with self.assertRaisesRegex(view.ViewError, 'refusing_to_overwrite'):
                view.execute(directory/'missing.json', directory, directory, directory, directory/'out.json')

    def test_fact_ordinals_remain_distinct_for_nested_nonfraction(self):
        source = fixture('<table><tr><td>' + fact('1' + fact('2')) + '</td></tr></table>')
        records, _ = extract(source)
        self.assertEqual([f['fact_ordinal'] for f in tables(records)[0]['fact_links']], [0, 1])

if __name__ == '__main__':
    unittest.main()
