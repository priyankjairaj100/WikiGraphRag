"""Authored identity-selection controls; no natural source files."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import select_identity_dependencies_v17 as m


def document(body, head=""):
    return (f'<html xmlns="http://www.w3.org/1999/xhtml" '
            f'xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" '
            f'xmlns:dei="http://xbrl.sec.gov/dei/2099"><head>{head}</head><body>{body}</body></html>').encode()


def fact(field, value, **attrs):
    extra = " ".join(f'{key}="{value}"' for key, value in attrs.items())
    return f'<ix:nonNumeric name="dei:{field}" {extra}>{value}</ix:nonNumeric>'


def select(body):
    raw = document(body)
    record = {"source_path": "authored_fixture", "external_filename": "fixture.html",
              "source_sha256": hashlib.sha256(raw).hexdigest(), "source_bytes": len(raw)}
    return m.select_source(raw, record)


def field_record(public, field):
    return next(f for f in public["identity_fields"] if f["field"] == field)


class IdentitySelectionTests(unittest.TestCase):
    def test_three_complete_visible_fields(self):
        public, private = select(''.join('<p>' + fact(k, v) + '</p>' for k, v in
            zip(m.FIELDS, ['Example Company', 'Annual Report', '2099-12-31'])))
        self.assertEqual(len(public['blocks']), 3)
        self.assertTrue(all(f['status'] == 'candidates_pending_render_and_review' for f in public['identity_fields']))
        self.assertEqual(len(private['identity_field_candidates']), 3)

    def test_original_byte_spans_and_hashes(self):
        body = '<p>Report for the period ended ' + fact('DocumentPeriodEndDate', '2099-12-31') + '</p>'
        public, _ = select(body)
        raw = document(body)
        block = public['blocks'][0]
        span = raw[block['anchor']['byte_start']:block['anchor']['byte_stop']]
        self.assertEqual(span.decode(), body)
        self.assertEqual(hashlib.sha256(span).hexdigest(), block['anchor']['span_sha256'])

    def test_issuer_hidden_fallback_exact_unicode(self):
        public, _ = select('<ix:header><ix:hidden>' + fact('EntityRegistrantName', 'EXAMPLE COMPANY') +
                           '</ix:hidden></ix:header><p>Example&#160;Company</p>')
        f = field_record(public, 'EntityRegistrantName')
        self.assertEqual(f['selection_mechanism'], 'hidden_dei_issuer_exact_visible_text_fallback')
        self.assertEqual(len(f['selected_candidate_blocks']), 1)

    def test_no_fuzzy_issuer_alias(self):
        public, _ = select('<ix:hidden>' + fact('EntityRegistrantName', 'Example Company') +
                           '</ix:hidden><p>Example Corp.</p>')
        self.assertEqual(field_record(public, 'EntityRegistrantName')['status'], 'unavailable')

    def test_no_missing_issuer_fallback(self):
        public, _ = select('<p>Example Company</p>')
        self.assertEqual(field_record(public, 'EntityRegistrantName')['unavailable_reason'], 'no_dei_field')

    def test_hidden_date_no_visible_text_fallback(self):
        public, _ = select('<ix:hidden>' + fact('DocumentPeriodEndDate', '2099-12-31') +
                           '</ix:hidden><p>2099-12-31</p>')
        self.assertEqual(field_record(public, 'DocumentPeriodEndDate')['status'], 'unavailable')

    def test_hidden_report_type_no_visible_text_fallback(self):
        public, _ = select('<ix:hidden>' + fact('DocumentType', 'Annual Report') +
                           '</ix:hidden><p>Annual Report</p>')
        self.assertEqual(field_record(public, 'DocumentType')['status'], 'unavailable')

    def test_conflicting_identity_values_unavailable(self):
        public, _ = select('<p>' + fact('DocumentType', 'Annual Report') + '</p><p>' +
                           fact('DocumentType', 'Quarterly Report') + '</p>')
        self.assertEqual(field_record(public, 'DocumentType')['unavailable_reason'], 'conflicting_or_empty_dei_values')

    def test_empty_field_unavailable(self):
        public, _ = select('<p>' + fact('DocumentType', '&#160;') + '</p>')
        self.assertEqual(field_record(public, 'DocumentType')['status'], 'unavailable')

    def test_empty_plus_nonempty_field_unavailable(self):
        public, _ = select('<p>' + fact('DocumentType', '&#160;') + '</p><p>' +
                           fact('DocumentType', 'Annual Report') + '</p>')
        self.assertEqual(field_record(public, 'DocumentType')['status'], 'unavailable')

    def test_empty_bindings_protocol_cannot_claim_twelve_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)
            protocol = {'schema_version': 'identity_dependency_selection_v17', 'rule': m.RULE,
                        'sources': [], 'code_bindings': {}, 'input_bindings': {}}
            (p / 'protocol.json').write_text(json.dumps(protocol))
            with self.assertRaisesRegex(ValueError, 'incomplete_frozen_dependency_population'):
                m.run(p / 'protocol.json', p / 'sources', p / 'private', p / 'result.json')
            self.assertFalse((p / 'private').exists())

    def test_first_two_xml_order_no_third(self):
        public, _ = select(''.join('<p>' + fact('DocumentType', 'Annual Report') + f'<span>{i}</span></p>' for i in range(3)))
        f = field_record(public, 'DocumentType')
        self.assertEqual(f['syntactically_eligible_unique_blocks'], 3)
        self.assertEqual(f['eligible_not_selected'], 1)
        self.assertEqual(len(f['selected_candidate_blocks']), 2)
        self.assertEqual([b['anchor']['byte_start'] for b in public['blocks']], sorted(b['anchor']['byte_start'] for b in public['blocks']))

    def test_whole_block_shared_by_fields(self):
        public, _ = select('<p>' + fact('DocumentType', 'Annual Report') + fact('DocumentPeriodEndDate', '2099-12-31') + '</p>')
        self.assertEqual(len(public['blocks']), 1)
        self.assertEqual(public['blocks'][0]['identity_fields'], ['DocumentType', 'DocumentPeriodEndDate'])

    def test_hidden_ancestor_rejected(self):
        for attrs in ['hidden="hidden"', 'style="display:none"', 'style="visibility:collapse"']:
            with self.subTest(attrs=attrs):
                public, _ = select('<div ' + attrs + '><p>' + fact('DocumentType', 'Annual Report') + '</p></div>')
                self.assertEqual(field_record(public, 'DocumentType')['status'], 'unavailable')

    def test_hidden_descendant_rejects_block(self):
        public, _ = select('<p>' + fact('DocumentType', 'Annual Report') + '<span hidden="hidden">x</span></p>')
        self.assertEqual(field_record(public, 'DocumentType')['status'], 'unavailable')

    def test_hidden_issuer_descendant_not_fallback_trigger(self):
        public, _ = select('<p>' + fact('EntityRegistrantName', 'Example<span hidden="hidden"> Company</span>') +
                           '</p><p>Example Company</p>')
        self.assertEqual(field_record(public, 'EntityRegistrantName')['status'], 'unavailable')

    def test_text_cap_no_truncation(self):
        public, _ = select('<p>' + fact('DocumentType', 'Annual Report') + 'x' * 1600 + '</p>')
        self.assertEqual(field_record(public, 'DocumentType')['status'], 'unavailable')

    def test_element_cap(self):
        public, _ = select('<p>' + fact('DocumentType', 'Annual Report') + '<span/>' * 257 + '</p>')
        self.assertEqual(field_record(public, 'DocumentType')['status'], 'unavailable')

    def test_numeric_occurrence_cap(self):
        public, _ = select('<p>' + fact('DocumentType', 'Annual Report') + '<ix:nonFraction>7</ix:nonFraction>' * 17 + '</p>')
        self.assertEqual(field_record(public, 'DocumentType')['status'], 'unavailable')

    def test_byte_cap(self):
        public, _ = select('<p data-padding="' + 'a' * 16500 + '">' + fact('DocumentType', 'Annual Report') + '</p>')
        self.assertEqual(field_record(public, 'DocumentType')['status'], 'unavailable')

    def test_ancestor_depth_cap(self):
        public, _ = select('<p>' + '<span>' * 8 + fact('DocumentType', 'Annual Report') + '</span>' * 8 + '</p>')
        self.assertEqual(field_record(public, 'DocumentType')['status'], 'unavailable')

    def test_nearest_complete_small_block(self):
        public, private = select('<div><p>' + fact('DocumentType', 'Annual Report') + '</p><p>unrelated</p></div>')
        self.assertIn('/{http://www.w3.org/1999/xhtml}p[1]', public['blocks'][0]['dom_path'])
        self.assertNotIn('unrelated', private['identity_field_candidates'][0]['whole_block_text'])

    def test_no_optional_card_fields(self):
        public, _ = select('<p>' + fact('EntityCentralIndexKey', '001234') + fact('DocumentFiscalYearFocus', '2099') + '</p>')
        self.assertEqual(len(public['identity_fields']), 3)
        self.assertEqual(public['blocks'], [])

    def test_namespace_not_local_name_only(self):
        public, _ = select('<p xmlns:fake="http://example.test/dei">' +
                           '<ix:nonNumeric name="fake:DocumentType">Annual Report</ix:nonNumeric></p>')
        self.assertEqual(field_record(public, 'DocumentType')['unavailable_reason'], 'no_dei_field')

    def test_public_does_not_contain_identity_text(self):
        public, private = select('<p>' + fact('EntityRegistrantName', 'UNIQUE AUTHORED COMPANY') + '</p>')
        self.assertNotIn('UNIQUE AUTHORED COMPANY', json.dumps(public))
        self.assertIn('UNIQUE AUTHORED COMPANY', json.dumps(private))

    def test_bad_source_hash_rejected(self):
        with self.assertRaisesRegex(ValueError, 'source_size_or_hash_mismatch'):
            m.select_source(document('<p>x</p>'), {'source_sha256': '0' * 64})

    def test_exclusive_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'receipt.json'
            m.write_new(path, {'status': 'first'})
            with self.assertRaises(FileExistsError):
                m.write_new(path, {'status': 'second'})
            self.assertEqual(json.loads(path.read_text())['status'], 'first')

    def test_unclosed_review_blocks_freeze(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'review.json'
            path.write_text(json.dumps({'status': 'pending', 'open_blockers': []}))
            with self.assertRaisesRegex(ValueError, 'independent_review_not_closed'):
                m.freeze(Path(directory) / 'protocol.json', path)


if __name__ == '__main__':
    unittest.main()
