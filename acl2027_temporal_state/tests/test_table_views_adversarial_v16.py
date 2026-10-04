"""Separate-agent authored table-view challenges; no filing/QA data are read."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'build_table_views_v16.py'
spec = importlib.util.spec_from_file_location('adversarial_table_view_impl', SCRIPT)
views = importlib.util.module_from_spec(spec)
spec.loader.exec_module(views)


def doc(body):
    return ('<html xmlns="http://www.w3.org/1999/xhtml" xmlns:h="http://www.w3.org/1999/xhtml" '
            'xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" xmlns:a="urn:authored">'
            '<head/><body>' + body + '</body></html>').encode('utf-8')


def fact(label, attrs=''):
    return ('<ix:nonFraction name="a:Amount" contextRef="c" unitRef="u" decimals="0" '
            + attrs + '>' + label + '</ix:nonFraction>')


def extract(source):
    bound = views.byte_reader.read_inline_xbrl(source, source_version='authored:table-audit')['facts']
    return views.extract_views(source, bound, source_path='authored-table-audit.html', source_sha256=views.sha(source))


def table_records(records):
    return [r for r in records if r['record_type'] == 'table']


def all_anchors(value):
    if isinstance(value, dict):
        if {'byte_start', 'byte_stop', 'span_sha256'}.issubset(value):
            yield value
        for child in value.values():
            yield from all_anchors(child)
    elif isinstance(value, list):
        for child in value:
            yield from all_anchors(child)


class TableViewsAdversarial(unittest.TestCase):
    def test_three_level_tables_form_exact_disjoint_fact_partition(self):
        body = fact('0') + '<table><tr><td>' + fact('1') + '<table><tr><td>' + fact('2')
        body += '<table><tr><td>' + fact('not-a-number') + '</td></tr></table>'
        body += '</td></tr></table>' + fact('4') + '</td></tr></table>' + fact('5')
        records, public = extract(doc(body))
        outer, middle, inner = table_records(records)
        self.assertEqual([[f['fact_ordinal'] for f in t['fact_links']] for t in (outer, middle, inner)], [[1, 4], [2], [3]])
        population = records[0]['outside_table_fact_ordinals'] + [f['fact_ordinal'] for t in (outer, middle, inner) for f in t['fact_links']]
        self.assertEqual(sorted(population), list(range(6)))
        self.assertEqual(len(set(population)), 6)
        self.assertEqual(public['counts']['facts'], 6)
        self.assertEqual(outer['rows'][0]['cells'][0]['nested_table_ordinals'], [1])
        self.assertEqual(middle['rows'][0]['cells'][0]['nested_table_ordinals'], [2])

    def test_fact_in_inner_table_cannot_inherit_outer_cell_index(self):
        source = doc('<table><tr><td>outer<table>' + fact('7') + '<tr><td>' + fact('8') + '</td></tr></table></td></tr></table>')
        records, _ = extract(source)
        outer, inner = table_records(records)
        self.assertEqual(outer['fact_links'], [])
        self.assertEqual(inner['fact_links'][0]['row_cell_index'], None)
        self.assertEqual(inner['fact_links'][1]['row_cell_index'], [0, 0])

    def test_nested_fact_occurrences_are_not_collapsed_by_equal_text(self):
        source = doc(fact(fact('9')) + '<table><tr><td>' + fact('9') + fact('9') + '</td></tr></table>')
        records, _ = extract(source)
        self.assertEqual(records[0]['outside_table_fact_ordinals'], [0, 1])
        links = table_records(records)[0]['fact_links']
        self.assertEqual([r['fact_ordinal'] for r in links], [2, 3])
        self.assertNotEqual(links[0]['anchor'], links[1]['anchor'])

    def test_nested_table_text_removed_even_through_inline_wrapper(self):
        source = doc('<table><tr><td>A<em>B<table><tr><td>EXCLUDED_FROM_OUTER</td></tr></table>C</em>D</td></tr></table>')
        records, _ = extract(source)
        outer, inner = table_records(records)
        self.assertEqual(outer['rows'][0]['cells'][0]['text'], 'ABCD')
        self.assertEqual(inner['rows'][0]['cells'][0]['text'], 'EXCLUDED_FROM_OUTER')

    def test_caption_ownership_does_not_duplicate_nested_caption(self):
        source = doc('<table><caption>outer caption</caption><tr><td><table><caption>inner caption</caption></table></td></tr></table>')
        records, _ = extract(source)
        self.assertEqual([[c['text'] for c in t['captions']] for t in table_records(records)], [['outer caption'], ['inner caption']])

    def test_prefix_spelling_does_not_change_xhtml_table_recognition(self):
        source = doc('<h:table><h:tr><h:th>h</h:th><h:td>' + fact('11') + '</h:td></h:tr></h:table>')
        records, public = extract(source)
        self.assertEqual(public['counts']['tables'], 1)
        self.assertEqual(public['counts']['cells'], 2)
        self.assertEqual(table_records(records)[0]['fact_links'][0]['row_cell_index'], [0, 1])

    def test_foreign_table_local_name_is_not_xhtml_table(self):
        source = doc('<a:table><a:tr><a:td>' + fact('12') + '</a:td></a:tr></a:table>')
        records, public = extract(source)
        self.assertEqual(public['counts']['tables'], 0)
        self.assertEqual(records[0]['outside_table_fact_ordinals'], [0])

    def test_comments_processing_instructions_and_skipped_nodes_preserve_tails(self):
        source = doc('<table><tr><td>A<!-- COMMENT_SENTINEL -->B<?audit hidden?>C<script>SCRIPT_SENTINEL</script>D<style>STYLE_SENTINEL</style>E</td></tr></table>')
        records, _ = extract(source)
        self.assertEqual(table_records(records)[0]['rows'][0]['cells'][0]['text'], 'ABCDE')

    def test_block_separators_do_not_split_inline_numeric_fragments(self):
        source = doc('<table><tr><td><div>Year 2024</div><div><span>1</span><span>2</span></div><p>A <b>B</b></p></td></tr></table>')
        records, _ = extract(source)
        self.assertEqual(table_records(records)[0]['rows'][0]['cells'][0]['text'], 'Year 2024 12 A B')

    def test_non_xml_whitespace_not_coerced_to_ascii_space(self):
        source = doc('<table><tr><td>\t A\r\nB&#xA0;C&#x2003;D <br/> E </td></tr></table>')
        records, _ = extract(source)
        self.assertEqual(table_records(records)[0]['rows'][0]['cells'][0]['text'], 'A B\u00a0C\u2003D E')

    def test_span_declarations_remain_unexpanded_and_invalid_is_not_one(self):
        source = doc('<table><tr><td rowspan="0" colspan=" +2 ">x</td></tr></table>')
        records, public = extract(source)
        cell = table_records(records)[0]['rows'][0]['cells'][0]
        self.assertEqual(cell['rowspan']['parsed'], 0)
        self.assertEqual(cell['colspan']['declared'], ' +2 ')
        self.assertIsNone(cell['colspan']['parsed'])
        self.assertEqual(public['counts']['cells'], 1)
        self.assertEqual(table_records(records)[0]['grid_expansion'], 'not_attempted')

    def test_every_nested_anchor_hashes_exact_original_utf8_bytes(self):
        source = doc('<p>π £ 前</p><table title="greater &gt; less"><caption>α</caption><tr><td data-mark=">">' + fact('13') + '<table/></td></tr></table><p>after</p>')
        records, public = extract(source)
        for anchor in all_anchors([records, public]):
            a, b = anchor['byte_start'], anchor['byte_stop']
            self.assertGreaterEqual(a, 0)
            self.assertLess(a, b)
            self.assertLessEqual(b, len(source))
            self.assertEqual(views.sha(source[a:b]), anchor['span_sha256'])
            self.assertTrue(source[a:b].startswith(b'<'))
        inner = table_records(records)[1]['anchor']
        self.assertEqual(source[inner['byte_start']:inner['byte_stop']], b'<table/>')

    def test_swapped_fact_records_with_same_values_are_rejected(self):
        source = doc('<table><tr><td>' + fact('14') + fact('14') + '</td></tr></table>')
        typed = views.byte_reader.read_inline_xbrl(source, source_version='authored')['facts']
        typed.reverse()
        with self.assertRaises(views.ViewError):
            views.extract_views(source, typed, source_path='authored', source_sha256=views.sha(source))

    def test_matching_ordinal_cannot_hide_tampered_anchor_digest(self):
        source = doc('<table>' + fact('15') + '</table>')
        typed = views.byte_reader.read_inline_xbrl(source, source_version='authored')['facts']
        typed[0]['anchor']['span_sha256'] = '0' * 64
        with self.assertRaises(views.ViewError):
            views.extract_views(source, typed, source_path='authored', source_sha256=views.sha(source))

    def test_public_inventory_omits_private_cells_captions_and_paragraphs(self):
        source = doc('<p>PARAGRAPH_PRIVATE_SENTINEL</p><table><caption>CAPTION_PRIVATE_SENTINEL</caption><tr><td headers="HEADER_PRIVATE_SENTINEL">CELL_PRIVATE_SENTINEL' + fact('915827364') + '</td></tr></table>')
        records, public = extract(source)
        blob = views.encoded(public)
        for sentinel in ('PARAGRAPH_PRIVATE_SENTINEL', 'CAPTION_PRIVATE_SENTINEL', 'HEADER_PRIVATE_SENTINEL', 'CELL_PRIVATE_SENTINEL', '915827364', 'normalized_value'):
            self.assertNotIn(sentinel.encode(), blob)
        self.assertIn(b'CELL_PRIVATE_SENTINEL', views.encoded(records))
        self.assertEqual(set(public), {'counts', 'tables'})
        self.assertTrue(all(not r['rendering_verified'] for r in records if r['record_type'] == 'source'))

    def test_source_bearing_destination_cannot_be_inside_repository(self):
        with tempfile.TemporaryDirectory(dir='/dev/shm') as scratch:
            base = Path(scratch)
            with self.assertRaises(views.ViewError):
                views.execute(base/'unused.json', base, base, views.ROOT/'not-created-private-audit', base/'public.json')

    def test_source_bearing_destination_symlink_cannot_point_into_repository(self):
        with tempfile.TemporaryDirectory(dir='/dev/shm') as scratch:
            base = Path(scratch)
            alias = base/'repo_alias'
            alias.symlink_to(views.ROOT, target_is_directory=True)
            with self.assertRaises(views.ViewError):
                views.execute(base/'unused.json', base, base, alias/'not-created-private-audit', base/'public.json')

    def test_public_output_cannot_be_mixed_into_private_directory(self):
        with tempfile.TemporaryDirectory(dir='/dev/shm') as scratch:
            base = Path(scratch)
            with self.assertRaises(views.ViewError):
                views.execute(base/'unused.json', base, base, base/'external', base/'external'/'summary.json')


if __name__ == '__main__':
    unittest.main()
