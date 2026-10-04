"""Authored checks; the transform checks call the real pinned Arelle module."""
import importlib.util
from decimal import localcontext
from pathlib import Path
import sys
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import reference_numeric_v15 as ref

IX = "http://www.xbrl.org/2013/inlineXBRL"
TR3 = "http://www.xbrl.org/inlineXBRL/transformation/2015-02-26"
TR4 = "http://www.xbrl.org/inlineXBRL/transformation/2020-02-12"
HAVE_ARELLE = importlib.util.find_spec("arelle") is not None


def element(body, attributes="", namespace=TR4):
    xml = (f'<ix:nonFraction xmlns:ix="{IX}" xmlns:ixt="{namespace}" '
           'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
           f'{attributes}>{body}</ix:nonFraction>').encode()
    return ref.parse_document(xml).getroot()


class ReferenceParsingTests(unittest.TestCase):
    def test_decimal_scale_sign_exact_under_low_precision(self):
        with localcontext() as context:
            context.prec = 3
            value = ref.normalize_nonfraction(element(
                "123456789012345678901234567890.1234500", 'scale="-2" sign="-"'))
        self.assertEqual(value["status"], "normalized")
        self.assertEqual(value["canonical_value"], "-1234567890123456789012345678.9012345")

    def test_decimal_lexical_forms(self):
        for lexical, expected in (("+.50", "0.5"), ("5.", "5"), ("-0.0", "0"),
                                  (" 12.3400\n", "12.34")):
            with self.subTest(lexical=lexical):
                self.assertEqual(ref.normalize_nonfraction(element(lexical))["canonical_value"], expected)

    def test_nil_is_not_zero_and_does_not_transform_display(self):
        got = ref.normalize_nonfraction(element("display unavailable", 'xsi:nil="true" format="ixt:fixed-zero"'))
        self.assertEqual(got["status"], "nil")
        self.assertIsNone(got["canonical_value"])

    def test_invalid_boolean_has_distinct_status(self):
        self.assertEqual(ref.normalize_nonfraction(element("5", 'xsi:nil="yes"'))["status"], "invalid_nil")

    def test_negative_and_sign_and_scale_failures(self):
        for body, attr, status in (("-5", "", "invalid_negative_before_sign"),
                ("5", 'sign="+"', "invalid_sign"), ("5", 'scale="1.5"', "invalid_scale"),
                ("5", 'scale="100001"', "unsupported_resource_bound")):
            with self.subTest(status=status):
                self.assertEqual(ref.normalize_nonfraction(element(body, attr))["status"], status)

    def test_unsupported_numeric_types_are_not_zero(self):
        for body in ("", "NaN", "INF", "1e3", "1,000"):
            got = ref.normalize_nonfraction(element(body))
            self.assertEqual(got["status"], "unsupported_numeric_lexical")
            self.assertIsNone(got["canonical_value"])

    def test_sign_is_exact_string_not_whitespace_collapsed(self):
        for sign in (" - ", "\t-", "-\n", "", "+"):
            node = element("5")
            node.set("sign", sign)
            self.assertEqual(ref.normalize_nonfraction(node)["status"], "invalid_sign")

    def test_many_zero_scale_digits_do_not_trigger_python_int_limit(self):
        got = ref.normalize_nonfraction(element("5", 'scale="' + "0" * 5000 + '2"'))
        self.assertEqual(got["canonical_value"], "500")

    def test_nested_facts_order_and_text(self):
        outer = element('1<ix:nonFraction>2</ix:nonFraction>3')
        nodes = list(ref.iter_nonfractions(outer))
        self.assertEqual(len(nodes), 2)
        self.assertEqual([ref.normalize_nonfraction(n)["canonical_value"] for n in nodes], ["123", "2"])

    def test_nonfraction_exclude_not_silently_accepted(self):
        got = ref.normalize_nonfraction(element('1<ix:exclude>bad</ix:exclude>2'))
        self.assertEqual(got["status"], "unsupported_structure")

    def test_no_external_entity_resolution(self):
        with self.assertRaises(ValueError):
            ref.parse_document(b'<!DOCTYPE a [<!ENTITY ex SYSTEM "file:///etc/passwd">]><a>&ex;</a>')

    def test_comment_text_is_not_numeric_content(self):
        got = ref.normalize_nonfraction(element('1<!-- ignored -->2'))
        self.assertEqual(got["canonical_value"], "12")


@unittest.skipUnless(HAVE_ARELLE, "optional pinned external Arelle runtime not installed")
class ActualArelleTransformTests(unittest.TestCase):
    def test_pinned_actual_package_and_source(self):
        from arelle import FunctionIxt
        self.assertIs(ref._registry(), FunctionIxt.ixtNamespaceFunctions)
        self.assertEqual(ref._sha(Path(FunctionIxt.__file__)), ref.FUNCTION_IXT_SHA256)

    def test_registry_transform_then_scale_then_sign(self):
        got = ref.normalize_nonfraction(element("1,234.50", 'format="ixt:num-dot-decimal" scale="2" sign="-"'), include_private=True)
        self.assertEqual(got["canonical_value"], "-123450")
        self.assertEqual(got["_external_transformed_text"], "1234.5")
        self.assertEqual(got["format_namespace"], TR4)

    def test_xml_whitespace_collapse_before_registry(self):
        got = ref.normalize_nonfraction(element("1\t234.50", 'format="ixt:num-dot-decimal"'))
        self.assertEqual(got["canonical_value"], "1234.5")

    def test_actual_comma_decimal_and_fixedzero(self):
        cases = [("1.234,50", "num-comma-decimal", "1234.5"),
                 ("arbitrary display", "fixed-zero", "0")]
        for text, local, expected in cases:
            got = ref.normalize_nonfraction(element(text, f'format="ixt:{local}"'))
            self.assertEqual(got["canonical_value"], expected)

    def test_registry_namespace_is_not_ignored(self):
        for ns in ("https://example.invalid/transforms", "http://www.sec.gov/inlineXBRL/transformation/2015-08-31"):
            got = ref.normalize_nonfraction(element("1,234", 'format="ixt:num-dot-decimal"', ns))
            self.assertEqual(got["status"], "unsupported_format")

    def test_registry_version_controls_local_name(self):
        # TR3 has numdotdecimal, TR4 has num-dot-decimal: no alias fallback.
        bad = ref.normalize_nonfraction(element("1,234", 'format="ixt:num-dot-decimal"', TR3))
        good = ref.normalize_nonfraction(element("1,234", 'format="ixt:numdotdecimal"', TR3))
        self.assertEqual(bad["status"], "unsupported_format")
        self.assertEqual(good["canonical_value"], "1234")

    def test_sec_numwordsen_remains_unhandled(self):
        got = ref.normalize_nonfraction(element("five", 'format="ixt:numwordsen"',
            "http://www.sec.gov/inlineXBRL/transformation/2015-08-31"))
        self.assertEqual(got["status"], "unsupported_format")

    def test_unbound_and_invalid_qnames(self):
        for fmt in ("missing:num-dot-decimal", "ixt:x:y", "ixt:{uri}name", "ixt:"):
            got = ref.normalize_nonfraction(element("1", f'format="{fmt}"'))
            self.assertEqual(got["status"], "invalid_format_qname")

    def test_transform_failure_is_retained(self):
        got = ref.normalize_nonfraction(element("bad", 'format="ixt:num-dot-decimal"'))
        self.assertEqual(got["status"], "transform_error")
        self.assertIsNone(got["canonical_value"])
        self.assertNotIn("bad", str(got))

    def test_raw_lexical_fields_are_opt_in(self):
        got = ref.normalize_nonfraction(element("1,234", 'format="ixt:num-dot-decimal"'))
        self.assertFalse(any(k.startswith("_external") for k in got))


if __name__ == "__main__":
    unittest.main()
