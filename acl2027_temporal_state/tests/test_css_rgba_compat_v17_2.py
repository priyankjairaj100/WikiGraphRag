"""Authored byte-boundary controls only; no native process or natural data."""
import unittest
import css_rgba_compat_v17_2 as compat
import render_source_blocks_lo_v17 as lo


def document(style, text='Authored unchanged text', extra=''):
    return ('<html xmlns="http://www.w3.org/1999/xhtml"><head/>'
            '<body><p '+extra+' style="'+style+'">'+text+'</p></body></html>').encode()


def transform(raw):
    return compat.transform(raw,lo.source_render.byte_reader._parse,lo.source_render.XH)


class CompatibilityTests(unittest.TestCase):
    def test_only_literal_alpha_byte_changes(self):
        raw=document('font-family:&quot;Times New Roman&quot;;color:rgba(12, 23,34, 1);background-color:rgba(0,0,0,0);border-bottom:1pt solid rgba(9,8,7,0.01)')
        derived,ledger=transform(raw)
        self.assertEqual(derived,raw.replace(b'34, 1)',b'34, 100%)'))
        self.assertEqual(ledger['patch_count'],1)
        patch=ledger['patches'][0]
        self.assertEqual(raw[patch['byte_start']:patch['byte_stop']],b'1')
        self.assertEqual(ledger['original_assembly_sha256'],compat.sha(raw))
        self.assertEqual(ledger['derived_assembly_sha256'],compat.sha(derived))

    def test_text_comments_quoted_css_and_other_attributes_unchanged(self):
        raw=document('font-family:&quot;rgba(0,0,0,1)&quot;;/* rgba(0,0,0,1) */font-size:12pt;color:rgba(0,0,0,1)',
                     'rgba(0,0,0,1)', 'title="style=rgba(0,0,0,1)"')
        derived,ledger=transform(raw)
        self.assertEqual(ledger['patch_count'],1)
        self.assertEqual(derived.count(b'rgba(0,0,0,1)'),4)

    def test_transparent_foreground_fails_closed(self):
        with self.assertRaises(compat.CompatibilityRefusal):transform(document('color:rgba(0,0,0,0)'))

    def test_nonliteral_fractional_or_encoded_foreground_refused(self):
        for style in ['color:rgba(0,0,0,1.0)','color:rgba(0,0,0,0.5)',
                      'color:rgba(0,0,0,&#49;)','color:var(--x,rgba(0,0,0,1))',
                      'color:rgba(256,0,0,1)','--x:rgba(0,0,0,1)']:
            with self.subTest(style=style),self.assertRaises(compat.CompatibilityRefusal):transform(document(style))

    def test_existing_percent_is_unchanged(self):
        raw=document('color:rgba(0,0,0,100%)')
        derived,ledger=transform(raw)
        self.assertEqual(raw,derived);self.assertEqual(ledger['patch_count'],0)

    def test_head_alpha_refused_and_ordinary_styles_unaltered(self):
        raw=document('color:#000').replace(b'<head/>',b'<head><style>p{color:rgba(0,0,0,1)}</style></head>')
        with self.assertRaises(compat.CompatibilityRefusal):transform(raw)
        raw=document('font-size:12pt;color:rgb(0,0,0)')
        self.assertEqual(transform(raw)[0],raw)

    def test_unicode_and_attribute_normalization_keep_offsets_exact(self):
        raw=document('font-family:&quot;Caf\u00e9&quot;;\ncolor:rgba(1,2,3,1)')
        derived,ledger=transform(raw)
        self.assertEqual(derived,raw.replace(b'3,1)',b'3,100%)'))
        self.assertEqual(raw[ledger['patches'][0]['byte_start']:ledger['patches'][0]['byte_stop']],b'1')

    def test_patch_cap_is_fail_closed(self):
        raw=('<html xmlns="http://www.w3.org/1999/xhtml"><head/><body>'+''.join(
          '<p style="color:rgba(0,0,0,1)">x</p>' for _ in range(513))+'</body></html>').encode()
        with self.assertRaisesRegex(compat.CompatibilityRefusal,'patch_cap'):transform(raw)


if __name__=='__main__':unittest.main()
