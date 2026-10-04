"""Authored XML challenges only; no natural filings, labels, or QA outcomes."""
from copy import deepcopy
from decimal import localcontext
import hashlib
import unittest

from temporal_state.typed_reader_v15 import (
    ReaderError, read_inline_xbrl, make_reported_query, lookup,
)


def fixture(facts=None, *, contexts=None, units=None, extra=""):
    contexts = contexts if contexts is not None else context()
    units = units if units is not None else '<x:unit id="u"><x:measure>iso:USD</x:measure></x:unit>'
    facts = facts if facts is not None else fact()
    return ('''<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
xmlns:x="http://www.xbrl.org/2003/instance" xmlns:xd="http://xbrl.org/2006/xbrldi"
xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:c="urn:authored:concept"
xmlns:d="urn:authored:dimension" xmlns:iso="urn:authored:currency"
xmlns:t15="http://www.xbrl.org/inlineXBRL/transformation/2015-02-26"
xmlns:t20="http://www.xbrl.org/inlineXBRL/transformation/2020-02-12"
xmlns:t22="http://www.xbrl.org/inlineXBRL/transformation/2022-02-16">
<body><ix:header><ix:resources>''' + contexts + units + '</ix:resources></ix:header>' + facts + extra + '</body></html>').encode()


def context(ident="ctx", *, issuer="entity-A", period=None, segment="", scenario=""):
    period = period if period is not None else '<x:instant>2024-12-31</x:instant>'
    seg = '<x:segment>' + segment + '</x:segment>' if segment else ''
    scen = '<x:scenario>' + scenario + '</x:scenario>' if scenario else ''
    return f'<x:context id="{ident}"><x:entity><x:identifier scheme="urn:issuer">{issuer}</x:identifier>{seg}</x:entity><x:period>{period}</x:period>{scen}</x:context>'


def fact(value="12.50", *, attrs='', context_ref="ctx", unit_ref="u", name="c:Revenue", accuracy='decimals="2"'):
    return f'<ix:nonFraction name="{name}" contextRef="{context_ref}" unitRef="{unit_ref}" {accuracy} {attrs}>{value}</ix:nonFraction>'


def read(source):
    return read_inline_xbrl(source, source_version="authored-v1")


class TypedReaderTests(unittest.TestCase):
    def first(self, xml):
        return read(xml)["facts"][0]

    def test_exact_decimal_has_no_ambient_rounding(self):
        digits = '1234567890123456789012345678901234567890123456789'
        with localcontext() as ctx:
            ctx.prec = 3
            f = self.first(fixture(fact(digits, attrs='scale="-3" sign="-"')))
        self.assertEqual(f["normalized_value"], '-' + digits[:-3] + '.' + digits[-3:])

    def test_scaling_is_not_decimals(self):
        f = self.first(fixture(fact('1.25', attrs='scale="6"', accuracy='decimals="-6"')))
        self.assertEqual(f['normalized_value'], '1250000')
        self.assertEqual(f['accuracy']['decimals'], '-6')

    def test_registry_version_specific_lexical_rules(self):
        cases = [('t15:numdotdecimal', '1,23', None), ('t20:num-dot-decimal', '1,23', '123'),
                 ('t22:num-dot-decimal', '.5', '0.5'), ('t15:numdotdecimal', '.5', None),
                 ('t15:numdotdecimal', '1,234.50', '1234.5')]
        for fmt, text, expected in cases:
            with self.subTest(format=fmt, lexical=text):
                self.assertEqual(self.first(fixture(fact(text, attrs=f'format="{fmt}"')))['normalized_value'], expected)

    def test_xml_whitespace_collapse_precedes_transform(self):
        f = self.first(fixture(fact(' 1\t\n234.5 ', attrs='format="t15:numdotdecimal"')))
        self.assertEqual(f['normalized_value'], '1234.5')

    def test_fixed_zero_accepts_any_display_and_nil_stays_absent(self):
        zero = self.first(fixture(fact('anything', attrs='format="t22:fixed-zero" sign="-"')))
        nil = self.first(fixture(fact('anything', attrs='format="t22:fixed-zero" xsi:nil="true"', accuracy='')))
        self.assertEqual(zero['normalized_value'], '0')
        self.assertIsNone(nil['normalized_value'])
        self.assertEqual(nil['numeric_status'], 'nil')

    def test_unknown_format_is_not_coerced(self):
        f = self.first(fixture(fact('123', attrs='format="c:numdotdecimal"')))
        self.assertEqual(f['numeric_status'], 'unsupported')
        self.assertIsNone(f['normalized_value'])

    def test_untransformed_exponent_is_explicitly_outside_profile(self):
        f = self.first(fixture(fact('1e3')))
        self.assertEqual(f['numeric_status'], 'unsupported')

    def test_scale_bound_is_unsupported_not_standard_invalid(self):
        f = self.first(fixture(fact('1', attrs='scale="10001"')))
        self.assertEqual(f['numeric_status'], 'unsupported')
        self.assertIsNone(f['normalized_value'])

    def test_precision_zero_retained_without_accuracy_inference(self):
        f = self.first(fixture(fact('12', accuracy='precision="0"')))
        self.assertEqual(f['normalized_value'], '12')
        self.assertEqual(f['accuracy']['interpretation'], 'no_accuracy_information')

    def test_sign_cannot_be_padded_or_embedded_in_lexical(self):
        for text, attrs in [('12', 'sign=" - "'), ('-12', 'sign="-"')]:
            with self.subTest(text=text):
                self.assertEqual(self.first(fixture(fact(text, attrs=attrs)))['numeric_status'], 'invalid')

    def test_nested_occurrences_use_own_sign_once(self):
        child = fact('1.2', attrs='scale="3" sign="-"')
        outer = fact(child, attrs='scale="3"')
        doc = read(fixture(outer))
        self.assertEqual([f['normalized_value'] for f in doc['facts']], ['1200', '-1200'])
        self.assertEqual([f['nested_occurrence'] for f in doc['facts']], [False, True])

    def test_nested_scale_mismatch_blocks_both(self):
        doc = read(fixture(fact(fact('12', attrs='scale="2"'), attrs='scale="3"')))
        self.assertTrue(all(f['status'] == 'invalid' for f in doc['facts']))

    def test_dtd_and_external_entities_rejected_before_resolution(self):
        for declaration in [b'<!DOCTYPE html SYSTEM "file:///etc/passwd">',
                            b'<!DOCTYPE html [<!ENTITY x "hello">]>']:
            with self.subTest(declaration=declaration):
                source = fixture().replace(b'<html ', declaration + b'<html ', 1)
                with self.assertRaises(ReaderError):
                    read(source)

    def test_source_and_all_dependency_anchors_exact(self):
        source = fixture(fact('1&#x32;.5', attrs='id="value"'), extra='<p title="a &gt; b">é</p>')
        doc = read(source)
        self.assertEqual(doc['source_sha256'], hashlib.sha256(source).hexdigest())
        f = doc['facts'][0]
        self.assertEqual(f['normalized_value'], '12.5')
        self.assertGreaterEqual(len(f['evidence_anchors']), 4)
        for anchor in f['evidence_anchors']:
            body = source[anchor['byte_start']:anchor['byte_stop']]
            self.assertEqual(hashlib.sha256(body).hexdigest(), anchor['span_sha256'])
            self.assertEqual(anchor['source_sha256'], doc['source_sha256'])

    def test_utf16_rejected_instead_of_wrong_byte_offsets(self):
        with self.assertRaises(ReaderError):
            read(fixture().decode().replace('UTF-8', 'UTF-16').encode('utf-16'))

    def test_source_size_limit_enforced(self):
        with self.assertRaises(ReaderError):
            read_inline_xbrl(fixture(), source_version='v', max_source_bytes=20)

    def test_real_calendar_leap_day_and_duration_end_boundary(self):
        good = context(period='<x:startDate>2024-02-29</x:startDate><x:endDate>2024-02-29</x:endDate>')
        bad = context(period='<x:instant>2023-02-29</x:instant>')
        doc = read(fixture(contexts=good))
        c = doc['contexts'][0]
        self.assertEqual(doc['facts'][0]['status'], 'normalized')
        self.assertEqual(c['period_boundaries']['endDate']['day_ordinal'] - c['period_boundaries']['startDate']['day_ordinal'], 1)
        self.assertEqual(self.first(fixture(contexts=bad))['binding_status'], 'invalid')

    def test_reverse_duration_and_mixed_timezone(self):
        reverse = context(period='<x:startDate>2025-01-02</x:startDate><x:endDate>2025-01-01</x:endDate>')
        mixed = context(period='<x:startDate>2024-01-01Z</x:startDate><x:endDate>2024-12-31</x:endDate>')
        self.assertEqual(self.first(fixture(contexts=reverse))['binding_status'], 'invalid')
        self.assertEqual(self.first(fixture(contexts=mixed))['binding_status'], 'unresolved')

    def test_extended_year_is_unresolved_not_invalid_calendar_claim(self):
        f = self.first(fixture(contexts=context(period='<x:instant>12345-12-31</x:instant>')))
        self.assertEqual(f['binding_status'], 'unresolved')

    def test_namespace_collision_does_not_alias_concepts(self):
        facts = fact('1') + fact('2', name='z:Revenue', attrs='xmlns:z="urn:different"')
        doc = read(fixture(facts))
        result = lookup(doc, make_reported_query(doc, doc['facts'][0]))
        self.assertEqual(result['status'], 'unique_reported_value')
        self.assertEqual(result['candidate_ordinals'], [0])

    def test_unicode_ncname_is_namespace_resolved(self):
        f = self.first(fixture(fact('3', name='c:量')))
        self.assertEqual(f['concept'], '{urn:authored:concept}量')
        self.assertEqual(f['status'], 'normalized')

    def test_dimension_placement_and_multiplicity(self):
        dim = '<xd:explicitMember dimension="d:Axis">d:Member</xd:explicitMember>'
        duplicate = self.first(fixture(contexts=context(segment=dim, scenario=dim)))
        self.assertEqual(duplicate['binding_status'], 'invalid')
        contexts = context('a', segment=dim) + context('b', scenario=dim)
        doc = read(fixture(fact('1', context_ref='a') + fact('2', context_ref='b'), contexts=contexts))
        result = lookup(doc, make_reported_query(doc, doc['facts'][0]))
        self.assertEqual(result['candidate_ordinals'], [0])

    def test_typed_and_extra_context_content_block_scope(self):
        entries = ['<xd:typedMember dimension="d:Axis"><d:Value>ABC</d:Value></xd:typedMember>',
                   '<d:Uninterpreted>extra</d:Uninterpreted>']
        for entry in entries:
            with self.subTest(entry=entry):
                f = self.first(fixture(contexts=context(segment=entry)))
                self.assertEqual(f['binding_status'], 'unresolved')

    def test_unit_multiplicity_preserved_and_ratio_not_cancelled(self):
        product = '<x:unit id="u"><x:measure>iso:USD</x:measure><x:measure>iso:USD</x:measure></x:unit>'
        ratio = '<x:unit id="u"><x:divide><x:unitNumerator><x:measure>iso:USD</x:measure></x:unitNumerator><x:unitDenominator><x:measure>iso:USD</x:measure></x:unitDenominator></x:divide></x:unit>'
        self.assertEqual(len(self.first(fixture(units=product))['reported_aspects']['unit']['measures']), 2)
        self.assertEqual(self.first(fixture(units=ratio))['binding_status'], 'invalid')

    def test_duplicate_ids_block_instead_of_choose_first(self):
        doc = read(fixture(contexts=context() + context()))
        self.assertEqual(doc['facts'][0]['binding_status'], 'invalid')
        self.assertIn('document_duplicate_ids', doc['issues'])

    def test_id_and_idref_schema_whitespace_collapsed_without_changing_raw_attributes(self):
        xml = fixture(fact('12', context_ref='  ctx  ', unit_ref=' u '),
                      contexts=context(' ctx '),
                      units='<x:unit id=" u "><x:measure>iso:USD</x:measure></x:unit>')
        f = self.first(xml)
        self.assertEqual(f['status'], 'normalized')
        self.assertEqual((f['context_id'], f['unit_id']), ('ctx', 'u'))
        self.assertEqual(f['attributes']['contextRef'], '  ctx  ')
        duplicate = read(fixture(contexts=context('ctx') + context(' ctx ')))
        self.assertEqual(duplicate['duplicate_ids'], ['ctx'])
        self.assertEqual(duplicate['facts'][0]['status'], 'invalid')

    def test_equal_and_unequal_repeated_facts_are_not_merged(self):
        for values, expected in [(['1.0', '1.00'], 'multiple_occurrences_same_value'),
                                 (['100', '101'], 'ambiguous')]:
            doc = read(fixture(''.join(fact(v, accuracy='decimals="-2"') for v in values)))
            result = lookup(doc, make_reported_query(doc, doc['facts'][0]))
            self.assertEqual(result['status'], expected)
            self.assertEqual(len(result['candidates']), 2)

    def test_source_hash_and_version_must_both_match(self):
        doc = read(fixture())
        for key in ('source_sha256', 'source_version'):
            query = make_reported_query(doc, doc['facts'][0])
            query[key] = 'different'
            with self.assertRaises(ReaderError):
                lookup(doc, query)

    def test_query_copy_does_not_mutate_source_aspects(self):
        doc = read(fixture())
        query = make_reported_query(doc, doc['facts'][0])
        query['entity']['identifier'] = 'entity-B'
        self.assertEqual(doc['facts'][0]['reported_aspects']['entity']['identifier'], 'entity-A')
        self.assertEqual(lookup(doc, query)['status'], 'absent')

    def test_incomplete_queries_rejected_and_no_consolidation_inferred(self):
        doc = read(fixture())
        original = make_reported_query(doc, doc['facts'][0])
        for key in original:
            query = deepcopy(original)
            del query[key]
            with self.subTest(missing=key), self.assertRaises(ReaderError):
                lookup(doc, query)
        self.assertIn('dimension_defaults_unresolved', lookup(doc, original)['uncertainties'])
        query = deepcopy(original)
        query['scope_mode'] = 'consolidated'
        with self.assertRaises(ReaderError):
            lookup(doc, query)

    def test_year_entity_unit_all_bind_the_selected_occurrence(self):
        contexts = context('a', issuer='A') + context('b', issuer='B') + context('c', issuer='A', period='<x:instant>2023-12-31</x:instant>')
        units = '<x:unit id="u"><x:measure>iso:USD</x:measure></x:unit><x:unit id="v"><x:measure>iso:EUR</x:measure></x:unit>'
        facts = fact('10', context_ref='a') + fact('20', context_ref='b') + fact('30', context_ref='c') + fact('40', context_ref='a', unit_ref='v')
        doc = read(fixture(facts, contexts=contexts, units=units))
        result = lookup(doc, make_reported_query(doc, doc['facts'][0]))
        self.assertEqual(result['candidate_ordinals'], [0])
        self.assertEqual(result['reported_values'], ['10'])


if __name__ == '__main__':
    unittest.main()
