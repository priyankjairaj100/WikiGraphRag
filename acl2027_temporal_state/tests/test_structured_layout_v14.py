"""Regression: structural evidence must preserve multi-line header geometry."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import retrieve_structured_layout_v14 as revised


class SourceLayoutTests(unittest.TestCase):
    def test_first_line_indent_and_final_newline_are_source_evidence(self):
        text = '           2022       2021\n2023       revised    revised\n'
        span = dict(document_id='authored', history_id='fixture', version_order=1,
                    pdf_page=1, char_start=0, char_stop=len(text), span_id='header')
        seed = dict(document_id='authored', version_order=1, pdf_page=1, chunk_index=0)
        page = {'text': text, 'source_page_sha256': revised.sha(text.encode())}
        bundle = {'seed': seed, 'spans': [span]}
        for condition in ('parent', 'closure'):
            context, records, words = revised.render([bundle], condition, {('authored', 1): page})
            self.assertEqual(context.split('\n', 1)[1], text)
            self.assertEqual(records[0]['text_sha256'], revised.sha(text.encode()))
            self.assertEqual(words, 5)


if __name__ == '__main__':
    unittest.main()
