"""Independent authored boundary challenges; not natural QA or XBRL conformance.

Fixtures are deliberately authored here, independently of implementation fixtures.
No downloaded filing body, QA reference or reader answer is used.
"""
from copy import deepcopy
from decimal import localcontext
from hashlib import sha256
import unittest

from temporal_state.typed_reader_v15 import read_inline_xbrl, make_reported_query, lookup, ReaderError

IX = 'http://www.xbrl.org/2013/inlineXBRL'
XBRLI = 'http://www.xbrl.org/2003/instance'


def context(cid='c', *, entity='issuer-A', year='2024', extra='', identifier_extra='', period=None):
    if period is None:
        period = f'<xbrli:instant>{year}-12-31</xbrli:instant>'
    return (f'<xbrli:context id="{cid}"><xbrli:entity>'
            f'<xbrli:identifier scheme="urn:issuer">{entity}</xbrli:identifier>{identifier_extra}'
            f'</xbrli:entity><xbrli:period>{period}</xbrli:period>{extra}</xbrli:context>')


def unit(uid='u', *, content='<xbrli:measure>iso:USD</xbrli:measure>'):
    return f'<xbrli:unit id="{uid}">{content}</xbrli:unit>'


def fact(value='12', *, attrs='', context_id='c', unit_id='u', concept='ex:Revenue', accuracy='decimals="0"'):
    return (f'<ix:nonFraction name="{concept}" contextRef="{context_id}" '
            f'unitRef="{unit_id}" {accuracy} {attrs}>{value}</ix:nonFraction>')


def document(facts=None, *, contexts=None, units=None, namespaces='', prefix=''):
    if facts is None:
        facts = fact()
    if contexts is None:
        contexts = context()
    if units is None:
        units = unit()
    return (prefix + f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="{IX}" '
            f'xmlns:xbrli="{XBRLI}" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
            f'xmlns:ex="urn:authored:concepts" xmlns:iso="urn:authored:currency" '
            f'xmlns:dim="http://xbrl.org/2006/xbrldi" {namespaces}>'
            '<head/><body><ix:header><ix:resources>' + contexts + units +
            '</ix:resources></ix:header>' + facts + '</body></html>').encode()


def read(source=None, version='authored:v1'):
    return read_inline_xbrl(document() if source is None else source, source_version=version)


class TypedReaderAdversarial(unittest.TestCase):
    """Tests exercise safety properties, not implementation-internal algorithms."""

    def test_unsigned_value_scaling_is_independent_of_decimal_context(self):
        # More than 28 significant digits: default Decimal arithmetic would round.
        digits = '123456789012345678901234567890123456789012345678901234567891'
        with localcontext() as precision:
            precision.prec = 7
            parsed = read(document(fact(digits, attrs='scale="-4" sign="-"')))
        self.assertEqual(parsed['facts'][0]['normalized_value'], '-' + digits[:-4] + '.' + digits[-4:])

    def test_valid_decimal_lexical_variants_are_normalized(self):
        for lexical, expected in [('+12', '12'), ('.5', '0.5'), ('5.', '5')]:
            with self.subTest(lexical=lexical):
                parsed = read(document(fact(lexical)))
                self.assertEqual(parsed['facts'][0]['normalized_value'], expected)

    def test_nil_placeholder_never_becomes_zero_or_text_quantity(self):
        parsed = read(document(fact('—', attrs='xsi:nil="true"', accuracy='')))
        self.assertIsNone(parsed['facts'][0]['normalized_value'])
        self.assertEqual(parsed['facts'][0]['numeric_status'], 'nil')

    def test_accuracy_does_not_scale_numeric_value(self):
        parsed = read(document(fact('12', accuracy='decimals="-3"') + fact('12', attrs='scale="3"')))
        self.assertEqual([f['normalized_value'] for f in parsed['facts']], ['12', '12000'])

    def test_both_accuracy_attributes_do_not_yield_normalized_fact(self):
        parsed = read(document(fact('12', accuracy='decimals="0" precision="2"')))
        self.assertIsNone(parsed['facts'][0]['normalized_value'])
        self.assertNotEqual(parsed['facts'][0]['numeric_status'], 'normalized')

    def test_equal_numeric_values_do_not_collapse_entity_year_or_unit(self):
        source = document(
            fact('12', context_id='c1') + fact('12', context_id='c2') +
            fact('12', context_id='c3') + fact('12', context_id='c1', unit_id='u2'),
            contexts=context('c1') + context('c2', year='2023') + context('c3', entity='issuer-B'),
            units=unit() + unit('u2', content='<xbrli:measure>xbrli:shares</xbrli:measure>'))
        parsed = read(source)
        self.assertEqual(len(parsed['facts']), 4)
        queries = [make_reported_query(parsed, f) for f in parsed['facts']]
        self.assertNotEqual(queries[0]['period'], queries[1]['period'])
        self.assertNotEqual(queries[0]['entity'], queries[2]['entity'])
        self.assertNotEqual(queries[0]['unit'], queries[3]['unit'])
        self.assertTrue(all(q['scope_mode'] == 'reported_aspects' for q in queries))

    def test_same_concept_local_name_in_different_namespaces_remains_distinct(self):
        parsed = read(document(fact() + fact(concept='other:Revenue'), namespaces='xmlns:other="urn:other:concepts"'))
        left, right = [make_reported_query(parsed, f) for f in parsed['facts']]
        self.assertNotEqual(left['concept'], right['concept'])

    def test_nil_never_becomes_numeric_zero(self):
        parsed = read(document(fact('', attrs='xsi:nil="true"', accuracy='') + fact('0')))
        self.assertIsNone(parsed['facts'][0]['normalized_value'])
        self.assertEqual(parsed['facts'][0]['numeric_status'], 'nil')
        self.assertEqual(parsed['facts'][1]['normalized_value'], '0')

    def test_nil_with_accuracy_is_not_a_normalized_fact(self):
        parsed = read(document(fact('', attrs='xsi:nil="true"')))
        self.assertIsNone(parsed['facts'][0]['normalized_value'])
        self.assertNotEqual(parsed['facts'][0]['numeric_status'], 'normalized')

    def test_non_ascii_digits_and_internal_whitespace_are_not_decimal_lexemes(self):
        for lexical in ['١٢', '１２', '1 2']:
            with self.subTest(lexical=lexical):
                parsed = read(document(fact(lexical)))
                self.assertIsNone(parsed['facts'][0]['normalized_value'])
                self.assertNotEqual(parsed['facts'][0]['numeric_status'], 'normalized')

    def test_negative_lexical_cannot_be_double_negated_to_positive(self):
        parsed = read(document(fact('-12', attrs='sign="-"')))
        self.assertIsNone(parsed['facts'][0]['normalized_value'])
        self.assertNotEqual(parsed['facts'][0]['numeric_status'], 'normalized')

    def test_invalid_sign_is_not_ignored(self):
        for sign in ['+', 'minus', '--', ' - ']:
            with self.subTest(sign=sign):
                parsed = read(document(fact('12', attrs=f'sign="{sign}"')))
                self.assertIsNone(parsed['facts'][0]['normalized_value'])
                self.assertNotEqual(parsed['facts'][0]['numeric_status'], 'normalized')

    def test_ix_exclude_cannot_hide_part_of_nonfraction_quantity(self):
        parsed = read(document(fact('1<ix:exclude>999</ix:exclude>2')))
        self.assertIsNone(parsed['facts'][0]['normalized_value'])
        self.assertNotEqual(parsed['facts'][0]['numeric_status'], 'normalized')

    def test_unbound_concept_prefix_cannot_be_treated_as_literal_qname(self):
        parsed = read(document(fact(concept='missing:Revenue')))
        self.assertNotEqual(parsed['facts'][0]['binding_status'], 'reported_aspects_resolved')

    def test_duplicate_context_id_cannot_select_the_first_entity(self):
        parsed = read(document(contexts=context(entity='issuer-A') + context(entity='issuer-B')))
        self.assertNotEqual(parsed['facts'][0]['binding_status'], 'reported_aspects_resolved')

    def test_two_identifiers_cannot_select_the_first_entity(self):
        extra = '<xbrli:identifier scheme="urn:issuer">issuer-B</xbrli:identifier>'
        parsed = read(document(contexts=context(identifier_extra=extra)))
        self.assertNotEqual(parsed['facts'][0]['binding_status'], 'reported_aspects_resolved')

    def test_mixed_instant_and_duration_is_not_resolved(self):
        period = ('<xbrli:instant>2024-12-31</xbrli:instant>'
                  '<xbrli:startDate>2024-01-01</xbrli:startDate><xbrli:endDate>2024-12-31</xbrli:endDate>')
        parsed = read(document(contexts=context(period=period)))
        self.assertNotEqual(parsed['facts'][0]['binding_status'], 'reported_aspects_resolved')

    def test_empty_unit_is_not_resolved(self):
        parsed = read(document(units=unit(content='')))
        self.assertNotEqual(parsed['facts'][0]['binding_status'], 'reported_aspects_resolved')

    def test_unit_product_and_divide_cannot_be_silently_combined(self):
        content = ('<xbrli:measure>iso:USD</xbrli:measure><xbrli:divide>'
                   '<xbrli:unitNumerator><xbrli:measure>iso:USD</xbrli:measure></xbrli:unitNumerator>'
                   '<xbrli:unitDenominator><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unitDenominator>'
                   '</xbrli:divide>')
        parsed = read(document(units=unit(content=content)))
        self.assertNotEqual(parsed['facts'][0]['binding_status'], 'reported_aspects_resolved')


    def test_precision_zero_is_retained_without_inventing_accuracy(self):
        parsed = read(document(fact('12', accuracy='precision="0"')))
        self.assertEqual(parsed['facts'][0]['normalized_value'], '12')
        self.assertEqual(parsed['facts'][0]['attributes']['precision'], '0')

    def test_format_local_name_does_not_override_unknown_namespace(self):
        parsed = read(document(fact('1,234', attrs='format="fake:num-dot-decimal"'),
                               namespaces='xmlns:fake="urn:unrecognized:transformation"'))
        self.assertIsNone(parsed['facts'][0]['normalized_value'])
        self.assertEqual(parsed['facts'][0]['numeric_status'], 'unsupported')

    def test_transformation_registry_version_changes_legal_grouping(self):
        source = document(fact('1,23', attrs='format="old:numdotdecimal"') +
                          fact('1,23', attrs='format="new:num-dot-decimal"'),
                          namespaces='xmlns:old="http://www.xbrl.org/inlineXBRL/transformation/2015-02-26" '
                                     'xmlns:new="http://www.xbrl.org/inlineXBRL/transformation/2020-02-12"')
        parsed = read(source)
        self.assertIsNone(parsed['facts'][0]['normalized_value'])
        self.assertEqual(parsed['facts'][1]['normalized_value'], '123')

    def test_nested_occurrences_apply_sign_once_to_raw_value(self):
        inner = fact('12', attrs='scale="3" sign="-"', concept='ex:Child')
        parsed = read(document(fact(inner, attrs='scale="3"')))
        self.assertEqual([f['normalized_value'] for f in parsed['facts']], ['12000', '-12000'])

    def test_nested_mismatched_scale_cannot_produce_unique_parent_answer(self):
        inner = fact('12', attrs='scale="6"', concept='ex:Child')
        parsed = read(document(fact(inner, attrs='scale="3"')))
        query = make_reported_query(parsed, parsed['facts'][0])
        self.assertEqual(lookup(parsed, query)['status'], 'blocked')

    def test_repeated_dimension_across_segment_and_scenario_is_not_resolved(self):
        member = '<dim:explicitMember dimension="ex:RegionAxis">ex:North</dim:explicitMember>'
        ctx = context(extra='<xbrli:scenario>' + member + '</xbrli:scenario>').replace(
            '</xbrli:entity>', '<xbrli:segment>' + member + '</xbrli:segment></xbrli:entity>')
        parsed = read(document(contexts=ctx))
        self.assertNotEqual(parsed['facts'][0]['binding_status'], 'reported_aspects_resolved')

    def test_unreduced_unit_ratio_is_not_silently_cancelled(self):
        content = ('<xbrli:divide><xbrli:unitNumerator><xbrli:measure>iso:USD</xbrli:measure>'
                   '</xbrli:unitNumerator><xbrli:unitDenominator><xbrli:measure>iso:USD</xbrli:measure>'
                   '</xbrli:unitDenominator></xbrli:divide>')
        parsed = read(document(units=unit(content=content)))
        self.assertNotEqual(parsed['facts'][0]['binding_status'], 'reported_aspects_resolved')

    def test_nonwhitespace_context_container_text_is_not_ignored(self):
        for malformed in [context().replace('<xbrli:entity>', '<xbrli:entity>unexpected'),
                          context().replace('<xbrli:period>', '<xbrli:period>unexpected'),
                          context().replace('<xbrli:context id="c">', '<xbrli:context id="c">unexpected')]:
            with self.subTest(context=malformed):
                parsed = read(document(contexts=malformed))
                self.assertNotEqual(parsed['facts'][0]['binding_status'], 'reported_aspects_resolved')

    def test_ambiguous_values_and_rounded_candidates_are_never_selected_first(self):
        for second, accuracy in [('13', 'decimals="0"'), ('12.4', 'decimals="1"')]:
            with self.subTest(second=second):
                parsed = read(document(fact('12') + fact(second, accuracy=accuracy)))
                answer = lookup(parsed, make_reported_query(parsed, parsed['facts'][0]))
                self.assertEqual(answer['status'], 'ambiguous')
                self.assertEqual(answer['candidate_ordinals'], [0, 1])

    def test_equal_occurrences_preserve_both_anchors(self):
        parsed = read(document(fact('12') + fact('12')))
        answer = lookup(parsed, make_reported_query(parsed, parsed['facts'][0]))
        self.assertEqual(answer['status'], 'multiple_occurrences_same_value')
        self.assertEqual(answer['candidate_ordinals'], [0, 1])
        self.assertNotEqual(parsed['facts'][0]['anchor'], parsed['facts'][1]['anchor'])

    def test_unresolved_unit_cannot_be_used_to_exclude_possible_match(self):
        parsed = read(document(fact('12') + fact('99', unit_id='bad'),
                               units=unit() + unit('bad', content='<xbrli:measure>missing:USD</xbrli:measure>')))
        answer = lookup(parsed, make_reported_query(parsed, parsed['facts'][0]))
        self.assertEqual(answer['status'], 'blocked')
        self.assertEqual(answer['candidate_ordinals'], [0, 1])

    def test_known_different_entity_excludes_fact_even_if_its_unit_is_unresolved(self):
        parsed = read(document(fact('12') + fact('99', context_id='other', unit_id='bad'),
                               contexts=context() + context('other', entity='issuer-B'),
                               units=unit() + unit('bad', content='<xbrli:measure>missing:USD</xbrli:measure>')))
        answer = lookup(parsed, make_reported_query(parsed, parsed['facts'][0]))
        self.assertEqual(answer['status'], 'unique_reported_value')
        self.assertEqual(answer['candidate_ordinals'], [0])

    def test_underdefined_query_refuses_to_guess_missing_aspect(self):
        parsed = read()
        query = make_reported_query(parsed, parsed['facts'][0])
        for missing in query:
            with self.subTest(missing=missing):
                incomplete = deepcopy(query)
                del incomplete[missing]
                with self.assertRaises(ReaderError):
                    lookup(parsed, incomplete)

    def test_cross_physical_version_query_is_rejected(self):
        parsed = read()
        query = make_reported_query(parsed, parsed['facts'][0])
        query['source_version'] = 'authored:v2'
        with self.assertRaises(ReaderError):
            lookup(parsed, query)

    def test_reported_lookup_does_not_claim_taxonomy_or_financial_scope(self):
        parsed = read()
        answer = lookup(parsed, make_reported_query(parsed, parsed['facts'][0]))
        self.assertFalse(parsed['taxonomy_validated'])
        self.assertFalse(answer['financial_or_natural_language_answer_certified'])
        self.assertIn('dimension_defaults_unresolved', answer['uncertainties'])
        self.assertIn('reported_identifier_not_financial_scope', answer['uncertainties'])

    def test_every_source_anchor_matches_exact_utf8_bytes(self):
        source = document(fact('12', attrs='id="f"'), prefix='<!-- authored £ α bytes -->')
        parsed = read(source)
        for occurrence in parsed['facts']:
            for anchor in occurrence['evidence_anchors'] + [occurrence['anchor']]:
                start, stop = anchor['byte_start'], anchor['byte_stop']
                self.assertGreaterEqual(start, 0)
                self.assertLessEqual(stop, len(source))
                self.assertLess(start, stop)
                self.assertEqual(anchor['source_sha256'], sha256(source).hexdigest())
                self.assertEqual(anchor['span_sha256'], sha256(source[start:stop]).hexdigest())
                self.assertEqual(anchor['source_version'], 'authored:v1')
        anchor = parsed['facts'][0]['anchor']
        self.assertEqual(source[anchor['byte_start']:anchor['byte_stop']], fact('12', attrs='id="f"').encode())

    def test_dtd_entities_are_rejected_before_any_expansion(self):
        for declaration in ['<!DOCTYPE html [<!ENTITY x "12">]>',
                            '<!DOCTYPE html [<!ENTITY x SYSTEM "file:///does-not-exist">]>']:
            with self.subTest(declaration=declaration):
                with self.assertRaises(ReaderError):
                    read(document(fact('&x;'), prefix=declaration))

    def test_oversize_input_fails_before_xml_parse(self):
        with self.assertRaises(ReaderError):
            read_inline_xbrl(document(), source_version='authored:v1', max_source_bytes=16)


if __name__ == '__main__':
    unittest.main()
