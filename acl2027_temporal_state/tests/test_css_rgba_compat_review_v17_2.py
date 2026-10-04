"""Focused independent authored literal-boundary review; no natural data/processes."""
import unittest
import css_rgba_compat_v17_2 as compat
from temporal_state import typed_reader_v15_1 as source_reader

XH = '{http://www.w3.org/1999/xhtml}'


def document(style, prefix=''):
    return ('<html xmlns="http://www.w3.org/1999/xhtml"><head/><body>'
            + prefix + '<p style="' + style + '">Authored sentinel</p>'
            '</body></html>').encode('utf-8')


def transform(raw):
    return compat.transform(raw, source_reader._parse, XH)


class IndependentLiteralBoundaryReview(unittest.TestCase):
    def assert_unchanged_or_refused(self, raw):
        try:
            derived, ledger = transform(raw)
        except compat.CompatibilityRefusal:
            return
        self.assertEqual(derived, raw)
        self.assertEqual(ledger['patch_count'], 0)

    def test_non_ascii_rgb_digits_are_outside_literal_profile(self):
        for token in ['rgba(١,2,3,1)', 'rgba(1,２,3,1)']:
            with self.subTest(token=token), self.assertRaises(compat.CompatibilityRefusal):
                transform(document('color:' + token))

    def test_identifier_and_hash_token_tails_are_not_rgba_functions(self):
        for token in ['♥rgba(0,0,0,1)', '😀rgba(0,0,0,1)',
                      '#rgba(0,0,0,1)', '--rgba(0,0,0,1)', 'xrgba(0,0,0,1)']:
            with self.subTest(token=token):
                self.assert_unchanged_or_refused(document('color:' + token))

    def test_function_spelling_requires_css_function_token(self):
        for token in ['rgba (0,0,0,1)', 'rgba\t(0,0,0,1)', 'rgba/**/(0,0,0,1)']:
            with self.subTest(token=token):
                self.assert_unchanged_or_refused(document('color:' + token))

    def test_comment_colon_is_not_declaration_separator(self):
        raw = document('/* note:rgba(0,0,0,0.5); */color:rgba(2,3,4,1)')
        try:
            derived, ledger = transform(raw)
        except compat.CompatibilityRefusal:
            # Conservative refusal is admissible; a comment token must never be changed.
            derived, ledger = None, None
        if derived is not None:
            self.assertEqual(derived, raw.replace(b'rgba(2,3,4,1)', b'rgba(2,3,4,100%)'))
            self.assertEqual(ledger['patch_count'], 1)
        for style in ['border-bottom:1/**/px solid rgba(0,0,0,1)',
                      'color:rgba(0,0,0,1)!im/**/portant']:
            with self.subTest(style=style):
                self.assert_unchanged_or_refused(document(style))

    def test_quoted_comments_and_other_attribute_spans_stay_exact(self):
        raw = document('font-family:&quot;rgba(0,0,0,1):;&quot;;'
                       'color:rgba(2,3,4,1)/* rgba(2,3,4,1) */',
                       '<p title="style=rgba(2,3,4,1)">rgba(2,3,4,1)</p>')
        derived, ledger = transform(raw)
        self.assertEqual(ledger['patch_count'], 1)
        self.assertEqual(derived.count(b'rgba(2,3,4,1)'), 3)
        self.assertIn(b'&quot;rgba(0,0,0,1):;&quot;', derived)

    def test_multibyte_entity_offsets_and_reverse_ledger(self):
        raw = document('font-family:&quot;Café&quot;;color:rgba(1,2,3,1);'
                       '&#98;ackground-color:rgba(4,5,6,1);'
                       'border-bottom:1px solid rgba(7,8,9,0.01)', '<p>Ω</p>')
        derived, ledger = transform(raw)
        self.assertEqual(ledger['patch_count'], 2)
        cursor = 0
        chunks = []
        for p in ledger['patches']:
            self.assertGreaterEqual(p['byte_start'], cursor)
            self.assertEqual(raw[p['byte_start']:p['byte_stop']], b'1')
            self.assertEqual(p['byte_stop'] - p['byte_start'], 1)
            chunks += [raw[cursor:p['byte_start']], p['new_literal'].encode()]
            cursor = p['byte_stop']
        chunks.append(raw[cursor:])
        self.assertEqual(derived, b''.join(chunks))
        self.assertEqual(len(derived), len(raw) + 3 * ledger['patch_count'])
        self.assertIn(b'&#98;ackground-color:', derived)
        self.assertIn(b'rgba(7,8,9,0.01)', derived)
        self.assertEqual(compat.sha(raw), ledger['original_assembly_sha256'])
        self.assertEqual(compat.sha(derived), ledger['derived_assembly_sha256'])

    def test_encoded_channel_or_token_refuses_before_patch_output(self):
        for style in ['color:rgba(&#49;,2,3,1)', 'color:rgb&#97;(1,2,3,1)',
                      'color:rgba(1,2,3,&#49;)']:
            with self.subTest(style=style), self.assertRaises(compat.CompatibilityRefusal):
                transform(document(style))

    def test_zero_patch_rgba_token_cap_is_enforced(self):
        raw = document(';'.join(['color:rgba(1,2,3,100%)'] * 2049))
        with self.assertRaisesRegex(compat.CompatibilityRefusal, 'token_cap'):
            transform(raw)


if __name__ == '__main__':
    unittest.main()
