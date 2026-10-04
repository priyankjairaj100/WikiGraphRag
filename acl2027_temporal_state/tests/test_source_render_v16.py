"""Authored fixtures only: source identity and bounded inspection rendering."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'render_source_blocks_v16.py'
spec = importlib.util.spec_from_file_location('render_source_blocks_v16', SCRIPT)
render = importlib.util.module_from_spec(spec)
spec.loader.exec_module(render)

FIXTURE = b'''<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL">
<head><style>body {font-family: sans-serif; font-size: 12pt;} .shell {color: #243448;}
table {border-collapse: collapse; width: 90%;} td, th {border: 1px solid #8192a0; padding: 7px;}
th {background-color: #e7edf3;} .number {text-align: right;}</style>
<link rel="stylesheet" href="https://invalid.example/missing.css"/></head>
<body><div class="shell" style="margin: 9px;">
<p>Authored fixture - no filing data. Units: example units.</p>
<table id="authored"><caption>Authored source table</caption>
<tr><th rowspan="2">Example metric</th><th colspan="2">Example periods</th></tr>
<tr><th>2030</th><th>2029</th></tr>
<tr><td>Example revenue</td><td class="number"><span>1,</span><span>234</span></td>
<td class="number"><ix:nonFraction name="example:Revenue">987</ix:nonFraction></td></tr>
<tr><td>Example margin</td><td class="number">56</td><td class="number">45</td></tr>
</table><p>Source sentence following the table.</p></div></body></html>'''


def selection(source=FIXTURE, tag='table'):
    nodes = render.byte_reader._parse(source)
    node = next(n for n in nodes if n.tag == render.XH + tag)
    return {'source_sha256': render.sha(source), 'source_bytes': len(source),
            'blocks': [{'block_id': 'authored_table', 'dom_path': node.path,
                        'anchor': render.bound_anchor(source, node)}]}


class SourceRenderTests(unittest.TestCase):
    def test_exact_source_fragment_and_inline_markup(self):
        prepared = render.prepare_blocks(FIXTURE, selection())[0]
        self.assertIn(b'<span>1,</span><span>234</span>', prepared['fragment'])
        self.assertIn(b'<ix:nonFraction name="example:Revenue">987</ix:nonFraction>', prepared['html'])
        self.assertIn(prepared['fragment'], prepared['html'])
        self.assertNotIn(b'Source sentence following', prepared['html'])

    def test_original_ancestor_opening_and_source_css(self):
        prepared = render.prepare_blocks(FIXTURE, selection())[0]
        self.assertIn(b'<div class="shell" style="margin: 9px;">', prepared['html'])
        self.assertIn(b'td, th {border: 1px solid', prepared['html'])
        self.assertEqual(1, len(prepared['head_stylesheets']))
        self.assertEqual(1, prepared['external_stylesheet_links_not_loaded'])
        for anchor in prepared['ancestor_openings'] + prepared['head_stylesheets']:
            self.assertEqual(anchor['span_sha256'], render.sha(FIXTURE[anchor['byte_start']:anchor['byte_stop']]))

    def test_wrong_source_hash(self):
        request = selection()
        request['source_sha256'] = '0' * 64
        with self.assertRaisesRegex(render.RenderError, 'identity'):
            render.prepare_blocks(FIXTURE, request)

    def test_wrong_source_length(self):
        request = selection()
        request['source_bytes'] += 1
        with self.assertRaisesRegex(render.RenderError, 'identity'):
            render.prepare_blocks(FIXTURE, request)

    def test_wrong_span_hash(self):
        request = selection()
        request['blocks'][0]['anchor']['span_sha256'] = '0' * 64
        with self.assertRaisesRegex(render.RenderError, 'span'):
            render.prepare_blocks(FIXTURE, request)

    def test_wrong_span_boundaries(self):
        request = selection()
        request['blocks'][0]['anchor']['byte_start'] += 1
        with self.assertRaisesRegex(render.RenderError, 'span'):
            render.prepare_blocks(FIXTURE, request)

    def test_wrong_locator(self):
        request = selection()
        request['blocks'][0]['dom_path'] += '[2]'
        with self.assertRaisesRegex(render.RenderError, 'locator'):
            render.prepare_blocks(FIXTURE, request)

    def test_unsafe_block_id(self):
        request = selection()
        request['blocks'][0]['block_id'] = '../escape'
        with self.assertRaisesRegex(render.RenderError, 'unsafe'):
            render.prepare_blocks(FIXTURE, request)

    def test_duplicate_block_id(self):
        request = selection()
        request['blocks'].append(request['blocks'][0])
        with self.assertRaisesRegex(render.RenderError, 'duplicate'):
            render.prepare_blocks(FIXTURE, request)

    def test_empty_population_refused(self):
        request = selection()
        request['blocks'] = []
        with self.assertRaisesRegex(render.RenderError, 'block_count'):
            render.prepare_blocks(FIXTURE, request)

    def test_ascii_encoding(self):
        source = FIXTURE.replace(b'UTF-8', b'US-ASCII')
        self.assertEqual(1, len(render.prepare_blocks(source, selection(source))))

    def test_utf8_non_ascii_byte_anchors(self):
        source = FIXTURE.replace(b'Example margin', 'Example marge caf\u00e9'.encode())
        item = render.prepare_blocks(source, selection(source))[0]
        self.assertIn('caf\u00e9'.encode(), item['fragment'])

    def test_other_encoding_refused(self):
        source = FIXTURE.replace(b'UTF-8', b'ISO-8859-1')
        with self.assertRaises(render.byte_reader.ReaderError):
            render.prepare_blocks(source, {'source_sha256': render.sha(source),
                                           'source_bytes': len(source), 'blocks': [{}]})

    def test_dtd_external_entity_refused(self):
        source = FIXTURE.replace(b'<html ', b'<!DOCTYPE html [<!ENTITY x SYSTEM "file:///private">]><html ', 1)
        with self.assertRaises(render.byte_reader.ReaderError):
            render.prepare_blocks(source, {'source_sha256': render.sha(source),
                                           'source_bytes': len(source), 'blocks': [{}]})

    def test_prefixed_xhtml_refused(self):
        source = FIXTURE.replace(b'<table ', b'<h:table xmlns:h="http://www.w3.org/1999/xhtml" ').replace(b'</table>', b'</h:table>')
        with self.assertRaisesRegex(render.RenderError, 'prefixed_xhtml'):
            render.prepare_blocks(source, selection(source))

    def test_git_output_refused_before_creation(self):
        with tempfile.TemporaryDirectory(dir='/dev/shm') as folder:
            src = Path(folder) / 'fixture.xml'
            src.write_bytes(FIXTURE)
            with self.assertRaisesRegex(render.RenderError, 'inside_git'):
                render.render_selection(src, selection(), render.ROOT / 'private_forbidden_test')

    def test_existing_output_refused(self):
        with tempfile.TemporaryDirectory(dir='/dev/shm') as folder:
            src = Path(folder) / 'fixture.xml'
            src.write_bytes(FIXTURE)
            with self.assertRaises(FileExistsError):
                render.render_selection(src, selection(), folder)

    def test_rendered_fixture_contains_original_display_tokens(self):
        import fitz
        with tempfile.TemporaryDirectory(dir='/dev/shm') as folder:
            src = Path(folder) / 'fixture.xml'
            src.write_bytes(FIXTURE)
            out = Path(folder) / 'output'
            receipt = render.render_selection(src, selection(), out, max_pages=2)
            self.assertEqual('rendered_inspection_views_pending_visual_review', receipt['status'])
            self.assertIsNone(receipt['archive'])
            self.assertEqual(1, receipt['blocks'][0]['pages'])
            with fitz.open(out / 'authored_table.pdf') as doc:
                text = ''.join(p.get_text() for p in doc)
            for token in ('Authored source table', '2030', '2029', '1,234', '987', '56', '45'):
                self.assertIn(token, text)
            self.assertNotIn('following the table', text)
            self.assertIn('MACHINE-GENERATED SOURCE INSPECTION HEADER', text)
            self.assertEqual(0, receipt['blocks'][0]['extracted_words_outside_media'])

    def test_page_cap_preserves_partial_output_and_failure(self):
        source = FIXTURE.replace(b'Example revenue', b'Long ' * 16000)
        with tempfile.TemporaryDirectory(dir='/dev/shm') as folder:
            src = Path(folder) / 'fixture.xml'
            src.write_bytes(source)
            out = Path(folder) / 'output'
            result = render.render_selection(src, selection(source), out, max_pages=1)
            self.assertEqual('incomplete_or_failed', result['status'])
            self.assertEqual('page_bound_reached', result['blocks'][0]['status'])
            self.assertTrue((out / 'authored_table.pdf').exists())


if __name__ == '__main__':
    unittest.main()
