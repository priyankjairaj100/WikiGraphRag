"""Exact-span layout contracts; these checks do not score research answers."""
import importlib.util
from pathlib import Path
import unittest


path=Path(__file__).resolve().parents[1]/'scripts/render_layout_contexts_v13.py'
spec=importlib.util.spec_from_file_location('render_layout_contexts_v13',path)
layout=importlib.util.module_from_spec(spec)
spec.loader.exec_module(layout)


class LayoutContracts(unittest.TestCase):
    def test_column_spaces_and_line_order_are_preserved(self):
        page='\n           older             newest\n                       middle\nRevenue       7                 9\nCost          2                 3\n'
        original=' '.join(page.split())
        restored=layout.restore_span(page,0,len(page.split()),original)
        self.assertEqual(restored.split(),page.split())
        self.assertIn('older             newest\n                       middle',restored)
        self.assertIn('Revenue       7                 9',restored)
        self.assertNotEqual(restored,original)

    def test_partial_span_never_injects_absent_header_or_units(self):
        page='Units millions\nHeader old new\nRevenue    7    9\nOther  2  3'
        restored=layout.restore_span(page,5,8,'Revenue 7 9')
        self.assertEqual(restored,'Revenue    7    9')
        self.assertNotIn('millions',restored)
        self.assertNotIn('Header',restored)

    def test_unicode_whitespace_and_numeric_signs_roundtrip(self):
        page='alpha\u00a0\u00a0β\t(7.4)\n  −8.0\u2003+9.0'
        self.assertEqual(layout.restore_span(page,1,5,'β (7.4) −8.0 +9.0'),'β\t(7.4)\n  −8.0\u2003+9.0')

    def test_wrong_source_or_boundaries_fail_closed(self):
        with self.assertRaises(ValueError):
            layout.restore_span('old 7 new 9',0,2,'old 9')
        with self.assertRaises(ValueError):
            layout.restore_span('old 7',0,3,'old 7')

    def test_headers_and_chunk_order_do_not_change(self):
        chunks=[{'document_id':'old','pdf_page':7,'chunk_id':'old:p007:c02','version_order':1},
                {'document_id':'new','pdf_page':2,'chunk_id':'new:p002:c01','version_order':2}]
        text={'old:p007:c02':'Revenue   7\nCost   2','new:p002:c01':'Revenue    9\nCost 3'}
        rendered=layout.render_chunks(chunks,text)
        self.assertEqual(rendered,'[old p.7 | chunk=old:p007:c02 | version=1]\nRevenue   7\nCost   2\n\n[new p.2 | chunk=new:p002:c01 | version=2]\nRevenue    9\nCost 3')


if __name__=='__main__':
    unittest.main()
