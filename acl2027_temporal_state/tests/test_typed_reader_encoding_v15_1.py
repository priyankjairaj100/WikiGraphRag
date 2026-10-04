"""Authored encoding amendment checks; frozen prior tests are loaded unchanged."""
import hashlib
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from temporal_state import typed_reader_v15_1 as reader
from tests.test_typed_reader_v15 import fixture, fact


def encoded_fixture(encoding='ASCII', facts=None, extra=''):
    return fixture(facts, extra=extra).replace(b'encoding="UTF-8"', ('encoding="' + encoding + '"').encode('ascii'), 1)


def legacy_control_suite():
    """Bind fresh copies of the frozen test modules to the amended reader.

    No frozen source/test file or module is edited. The temporary import binding
    ends before this function returns; loaded tests retain the amended functions.
    """
    suite = unittest.TestSuite()
    names = ('test_typed_reader_v15', 'test_typed_reader_adversarial_v15')
    with patch.dict(sys.modules, {'temporal_state.typed_reader_v15': reader}):
        for name in names:
            path = Path(__file__).with_name(name + '.py')
            spec = importlib.util.spec_from_file_location('_encoding_amendment_' + name, path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            if module.read_inline_xbrl is not reader.read_inline_xbrl:
                raise AssertionError('legacy controls not bound to amended reader')
            suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(module))
    if suite.countTestCases() != 68:
        raise AssertionError('frozen baseline must contain exactly 68 controls')
    return suite


class EncodingAmendmentTests(unittest.TestCase):
    def read(self, source):
        return reader.read_inline_xbrl(source, source_version='authored-encoding-v1')

    def test_ascii_and_us_ascii_declarations(self):
        for encoding in ('ASCII', 'US-ASCII', 'ascii', 'us-ascii'):
            with self.subTest(encoding=encoding):
                doc = self.read(encoded_fixture(encoding))
                self.assertEqual(doc['facts'][0]['normalized_value'], '12.5')
                self.assertEqual(doc['facts'][0]['status'], 'normalized')

    def test_utf8_keeps_literal_unicode_and_byte_anchors(self):
        source = fixture(extra='<p>é 量</p>')
        doc = self.read(source)
        self.assertEqual(doc['facts'][0]['normalized_value'], '12.5')
        self.assertEqual(doc['source_bytes'], len(source))
        self.assertEqual(doc['source_sha256'], hashlib.sha256(source).hexdigest())

    def test_literal_non_ascii_bytes_under_ascii_are_rejected(self):
        for encoding in ('ASCII', 'US-ASCII'):
            with self.subTest(encoding=encoding):
                with self.assertRaisesRegex(reader.ReaderError, 'non-ASCII source bytes'):
                    self.read(encoded_fixture(encoding, extra='<p>é</p>'))

    def test_numeric_character_references_keep_ascii_source_and_unicode_value(self):
        source = encoded_fixture('ASCII', fact('1&#xA0;234.5', attrs='format="t15:numdotdecimal"'))
        self.assertTrue(source.isascii())
        doc = self.read(source)
        f = doc['facts'][0]
        self.assertEqual(f['lexical_text'], '1\u00a0234.5')
        self.assertEqual(f['normalized_value'], '1234.5')
        self.assertIn(b'&#xA0;', source[f['anchor']['byte_start']:f['anchor']['byte_stop']])

    def test_unsupported_declared_encodings_remain_rejected(self):
        for encoding in ('ISO-8859-1', 'windows-1252', 'UTF-16', 'UTF-32'):
            with self.subTest(encoding=encoding), self.assertRaises(reader.ReaderError):
                self.read(encoded_fixture(encoding))

    def test_utf16_bytes_remain_rejected(self):
        source = fixture().decode().replace('UTF-8', 'UTF-16').encode('utf-16')
        with self.assertRaises(reader.ReaderError):
            self.read(source)

    def test_ascii_source_and_every_dependency_anchor_are_original_bytes(self):
        source = encoded_fixture('US-ASCII', fact('1&#x32;.50'))
        doc = self.read(source)
        digest = hashlib.sha256(source).hexdigest()
        self.assertEqual(doc['source_sha256'], digest)
        self.assertEqual(doc['source_bytes'], len(source))
        for f in doc['facts']:
            for anchor in f['evidence_anchors']:
                fragment = source[anchor['byte_start']:anchor['byte_stop']]
                self.assertEqual(anchor['source_sha256'], digest)
                self.assertEqual(anchor['span_sha256'], hashlib.sha256(fragment).hexdigest())
        self.assertEqual(doc['facts'][0]['normalized_value'], '12.5')

    def test_missing_encoding_declaration_keeps_utf8_default(self):
        source = fixture(extra='<p>é</p>').replace(b' encoding="UTF-8"', b'', 1)
        self.assertEqual(self.read(source)['facts'][0]['status'], 'normalized')


if __name__ == '__main__':
    if sys.argv[1:] == ['--legacy-controls']:
        result = unittest.TextTestRunner(verbosity=1).run(legacy_control_suite())
        raise SystemExit(0 if result.wasSuccessful() else 1)
    unittest.main()
