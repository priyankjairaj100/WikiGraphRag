"""Resource refusals and unchanged source markup for the LO inspection pipeline."""
import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.spec_from_file_location('lo_pipeline', ROOT / 'scripts/render_source_blocks_lo_v16_1.py')
lo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lo)


def html(body, css=''):
    return ('<html xmlns="http://www.w3.org/1999/xhtml"><head><style>' + css +
            '</style></head><body>' + body + '</body></html>').encode()


class ResourceTests(unittest.TestCase):
    def test_merged_table_is_preserved_and_allowed(self):
        fixture = lo.load_module(ROOT / 'tests/test_source_render_v16.py', 'lo_fixture')
        assembled = lo.source_render.prepare_blocks(fixture.FIXTURE, fixture.selection())[0]['html']
        self.assertEqual('passive_resource_profile_passed', lo.resource_guard(assembled)['status'])
        self.assertIn(b'rowspan="2"', assembled)
        self.assertIn(b'colspan="2"', assembled)

    def test_image_is_refused(self):
        self.assertEqual('refused', lo.resource_guard(html('<img src="https://x.invalid/image"/>'))['status'])

    def test_script_is_refused(self):
        self.assertEqual('refused', lo.resource_guard(html('<script>alert(1)</script>'))['status'])

    def test_inline_event_is_refused(self):
        self.assertEqual('refused', lo.resource_guard(html('<div onclick="x()">text</div>'))['status'])

    def test_external_link_is_refused(self):
        self.assertEqual('refused', lo.resource_guard(html('<a href="file:///local">text</a>'))['status'])

    def test_fragment_link_is_allowed(self):
        self.assertEqual('passive_resource_profile_passed', lo.resource_guard(html('<a href="#note">text</a>'))['status'])

    def test_css_import_is_refused(self):
        self.assertEqual('refused', lo.resource_guard(html('<p>x</p>', '@import "https://x.invalid/a.css";'))['status'])

    def test_css_url_is_refused(self):
        self.assertEqual('refused', lo.resource_guard(html('<p>x</p>', 'p {background: URL ("https://x.invalid")}'))['status'])

    def test_css_escape_is_refused(self):
        self.assertEqual('refused', lo.resource_guard(html('<p>x</p>', r'p {background: \75rl("x")}'))['status'])

    def test_string_only_css_image_functions_refused(self):
        for name in ['image-set', '-webkit-image-set', 'image', 'src', 'attr']:
            with self.subTest(function=name):
                css = 'p {background: ' + name + '("https://x.invalid/asset" 1x)}'
                self.assertEqual('refused', lo.resource_guard(html('<p>x</p>', css))['status'])

    def test_passive_color_functions_allowed(self):
        css = 'p {color:rgba(0,0,0,0.5); width:calc(90% - 1px)}'
        self.assertEqual('passive_resource_profile_passed', lo.resource_guard(html('<p>x</p>', css))['status'])

    def test_source_meta_refresh_refused(self):
        self.assertEqual('refused', lo.resource_guard(html('<meta http-equiv="refresh" content="0;url=https://x.invalid"/>'))['status'])

    def test_hidden_or_reordered_css_requires_manual_review(self):
        result = lo.resource_guard(html('<p>x</p>', 'p {display:none; direction: rtl; order:2;}'))
        self.assertEqual(['direction', 'display', 'order'], result['css_property_review_flags'])
        self.assertTrue(result['full_css_manual_review_required'])

    def test_foreign_svg_is_refused(self):
        self.assertEqual('refused', lo.resource_guard(html('<svg xmlns="http://www.w3.org/2000/svg"/>'))['status'])

    def test_entity_forbidden_before_guard(self):
        with self.assertRaises(lo.source_render.byte_reader.ReaderError):
            lo.resource_guard(b'<!DOCTYPE html [<!ENTITY x SYSTEM "file:///x">]>' + html('<p>&x;</p>'))


if __name__ == '__main__':
    unittest.main()
