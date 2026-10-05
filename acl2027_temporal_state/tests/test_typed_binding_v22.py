"""Authored Inline XBRL fixtures. These counts are not filing or QA results."""

import unittest

from temporal_state.typed_binding_v22 import (
    ASPECTS, COVERAGE_PROVENANCE, BindingError, build_world, compare_filings,
    compare_policies, conflict_profile, dimension_residual_profile, namespace_year_profile,
    open_scope_profile, summarize_document,
)
from temporal_state.typed_reader_v15_1 import read_inline_xbrl


def document(body):
    source = (
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" '
        'xmlns:xbrli="http://www.xbrl.org/2003/instance" '
        'xmlns:xbrldi="http://xbrl.org/2006/xbrldi" '
        'xmlns:c="urn:authored:concept" xmlns:u="urn:authored:unit" '
        'xmlns:d="urn:authored:dimension"><head/><body>'
        '<ix:header><ix:resources>'
        '<xbrli:context id="y2024"><xbrli:entity>'
        '<xbrli:identifier scheme="urn:authored:entity">A</xbrli:identifier>'
        '<xbrli:segment><xbrldi:explicitMember dimension="d:Population">d:Consolidated</xbrldi:explicitMember></xbrli:segment>'
        '</xbrli:entity><xbrli:period><xbrli:instant>2024-12-31</xbrli:instant></xbrli:period></xbrli:context>'
        '<xbrli:context id="y2023"><xbrli:entity>'
        '<xbrli:identifier scheme="urn:authored:entity">A</xbrli:identifier>'
        '<xbrli:segment><xbrldi:explicitMember dimension="d:Population">d:Consolidated</xbrldi:explicitMember></xbrli:segment>'
        '</xbrli:entity><xbrli:period><xbrli:instant>2023-12-31</xbrli:instant></xbrli:period></xbrli:context>'
        '<xbrli:context id="seg"><xbrli:entity>'
        '<xbrli:identifier scheme="urn:authored:entity">A</xbrli:identifier>'
        '<xbrli:segment><xbrldi:explicitMember dimension="d:Population">d:Segment</xbrldi:explicitMember></xbrli:segment>'
        '</xbrli:entity><xbrli:period><xbrli:instant>2024-12-31</xbrli:instant></xbrli:period></xbrli:context>'
        '<xbrli:unit id="usd"><xbrli:measure>u:USD</xbrli:measure></xbrli:unit>'
        '</ix:resources></ix:header>' + body + '</body></html>'
    ).encode("utf-8")
    return read_inline_xbrl(source, source_version="authored:binding-v22")


def fact(name, context, value, hidden=False):
    tag = f'<ix:nonFraction name="c:{name}" contextRef="{context}" unitRef="usd" decimals="0">{value}</ix:nonFraction>'
    return f"<ix:hidden>{tag}</ix:hidden>" if hidden else tag


class TypedBindingWorld(unittest.TestCase):
    def test_open_period_leaves_two_classes_and_sufficiency_picks_one(self):
        parsed = document(fact("Revenue", "y2024", "100") + fact("Revenue", "y2023", "90"))
        world = build_world(parsed, {"concept": "{urn:authored:concept}Revenue"})
        self.assertEqual(len(world.classes), 2)
        self.assertEqual({item.normalized_value for item in world.classes}, {"100", "90"})
        aspect, sufficiency, certificate = compare_policies(world, certificate_limit=1)
        self.assertEqual(aspect.outcome, "abstained")
        self.assertEqual(sufficiency.outcome, "emitted")
        self.assertEqual(sufficiency.acquired_facts, 1)
        self.assertGreater(sufficiency.rivals_unresolved, 0)
        self.assertEqual(certificate.outcome, "abstained")
        self.assertFalse(certificate.candidate_id)
        self.assertEqual(world.problem.coverage.provenance, COVERAGE_PROVENANCE)

    def test_same_number_under_two_scopes_is_not_one_answer(self):
        parsed = document(fact("Revenue", "y2024", "100") + fact("Revenue", "seg", "100"))
        world = build_world(parsed, {"concept": "{urn:authored:concept}Revenue"})
        self.assertEqual(len(world.classes), 2)
        self.assertEqual(compare_policies(world)[0].outcome, "abstained")

    def test_fully_specified_binding_makes_the_three_policies_agree(self):
        parsed = document(fact("Revenue", "y2024", "100") + fact("Revenue", "y2023", "90"))
        target = next(fact for fact in parsed["facts"] if fact["normalized_value"] == "100")
        world = build_world(parsed, {key: target["reported_aspects"][key]
                                     for key in ("concept", "entity", "period", "unit", "dimensions")})
        self.assertEqual(len(world.classes), 1)
        decisions = compare_policies(world)
        self.assertTrue(all(item.outcome == "emitted" and item.candidate_id == world.classes[0].candidate_id
                            for item in decisions))
        self.assertEqual(decisions[2].acquired_facts, 1)

    def test_hidden_and_unresolved_facts_do_not_invent_a_rival(self):
        parsed = document(fact("Revenue", "y2024", "100") + fact("Revenue", "y2023", "1", hidden=True))
        summary = summarize_document(parsed, source_version="authored:binding-v22")
        self.assertEqual(summary["eligible_facts"], 1)
        self.assertEqual(summary["excluded_hidden"], 1)
        self.assertEqual(summary["multi_class_concepts"], 0)
        self.assertEqual(summary["natural_questions"], 0)
        self.assertEqual(summary["underspecified_residual"], 0)

    def test_open_coverage_blocks_the_unique_answer(self):
        parsed = document(fact("Revenue", "y2024", "100"))
        world = build_world(parsed, {"concept": "{urn:authored:concept}Revenue"}, coverage_cleared=False)
        self.assertEqual(compare_policies(world)[2].outcome, "abstained")

    def test_no_matching_fact_raises_instead_of_inventing_one(self):
        parsed = document(fact("Revenue", "y2024", "100"))
        with self.assertRaises(BindingError):
            build_world(parsed, {"concept": "{urn:authored:concept}Missing"})

    def test_summary_counts_the_residual_without_storing_values(self):
        parsed = document(fact("Revenue", "y2024", "100") + fact("Revenue", "y2023", "90")
                          + fact("Cash", "y2024", "5"))
        summary = summarize_document(parsed, source_version="authored:binding-v22")
        self.assertEqual(summary["multi_class_concepts"], 1)
        self.assertEqual(summary["unique_binding_concepts"], 1)
        self.assertEqual(summary["underspecified_residual"], 1)
        self.assertEqual(summary["fully_specified_all_policies_agree"], 1)
        self.assertNotIn("100", json_blob(summary))


def json_blob(summary):
    import json
    return json.dumps(summary)


def synthetic(rows):
    facts = []
    for ordinal, (concept, value, scale) in enumerate(rows):
        facts.append({
            "fact_ordinal": ordinal,
            "binding_status": "reported_aspects_resolved",
            "status": "normalized",
            "resolved_aspects": {key: True for key in ASPECTS},
            "visibility": "rendering_unverified",
            "normalized_value": value,
            "scale_property": scale,
            "reported_aspects": {
                "concept": concept,
                "entity": {"scheme": "urn:authored:entity", "identifier": "A"},
                "period": {"kind": "instant", "lexemes": {"instant": "2024-12-31"}},
                "unit": {"shape": "simple_product", "measures": ["{urn:authored:unit}USD"],
                         "numerator_measures": [], "denominator_measures": []},
                "dimensions": [],
            },
        })
    return {"facts": facts}


class SourceCheck(unittest.TestCase):
    def test_close_duplicate_is_bucketed_without_keeping_the_value(self):
        profile = conflict_profile(synthetic([
            ("{urn:tax/2024}Revenue", "100", 0),
            ("{urn:tax/2024}Revenue", "100.05", 3),
        ]))
        self.assertEqual(profile["conflict_bindings"], 1)
        self.assertEqual(profile["ratio_buckets"]["within_0.1pct"], 1)
        self.assertEqual(profile["scale_attribute_differs"], 1)
        self.assertFalse(profile["values_recorded"])
        self.assertNotIn("100", json_blob(profile))

    def test_large_gap_is_not_called_a_near_duplicate(self):
        profile = conflict_profile(synthetic([
            ("{urn:tax/2024}Revenue", "100", 0),
            ("{urn:tax/2024}Revenue", "250", 0),
        ]))
        self.assertEqual(profile["ratio_buckets"]["larger"], 1)

    def test_taxonomy_year_is_not_an_exact_join(self):
        older = synthetic([("{urn:tax/2023}Revenue", "100", 0)])
        newer = synthetic([("{urn:tax/2024}Revenue", "90", 0)])
        exact = compare_filings(older, newer, "exact")
        loose = compare_filings(older, newer, "local_name_diagnostic")
        self.assertEqual(exact["shared"], 0)
        self.assertTrue(exact["admitted_join"])
        self.assertEqual(loose["shared"], 1)
        self.assertEqual(loose["not_same_value"], 1)
        self.assertFalse(loose["admitted_join"])


def scoped_fact(ordinal, concept, value, period, member):
    return {
        "fact_ordinal": ordinal,
        "binding_status": "reported_aspects_resolved",
        "status": "normalized",
        "resolved_aspects": {key: True for key in ASPECTS},
        "visibility": "rendering_unverified",
        "normalized_value": value,
        "scale_property": 0,
        "reported_aspects": {
            "concept": concept,
            "entity": {"scheme": "urn:authored:entity", "identifier": "A"},
            "period": {"kind": "instant", "lexemes": {"instant": period}},
            "unit": {"shape": "simple_product", "measures": ["{urn:authored:unit}USD"],
                     "numerator_measures": [], "denominator_measures": []},
            "dimensions": [{"kind": "explicitMember", "dimension": "{urn:authored:dimension}Population",
                            "member": member, "placement": "segment"}],
        },
    }


class OpenScopeProbe(unittest.TestCase):
    def test_an_unnamed_period_is_value_changing_and_an_unnamed_dimension_is_not(self):
        document = {"facts": [
            scoped_fact(0, "{urn:tax/2024}Revenue", "100", "2024-12-31", "{urn:tax/2024}Consolidated"),
            scoped_fact(1, "{urn:tax/2024}Revenue", "90", "2023-12-31", "{urn:tax/2024}Consolidated"),
        ]}
        opened = open_scope_profile(document)["omitted_aspect"]
        self.assertEqual(opened["period"]["value_changing_groups"], 1)
        self.assertEqual(opened["dimensions"]["value_changing_groups"], 0)
        self.assertEqual(opened["dimensions"]["unique_groups"], 2)

    def test_an_unnamed_dimension_changes_the_value(self):
        document = {"facts": [
            scoped_fact(0, "{urn:tax/2024}Revenue", "100", "2024-12-31", "{urn:tax/2024}Consolidated"),
            scoped_fact(1, "{urn:tax/2024}Revenue", "40", "2024-12-31", "{urn:tax/2024}Segment"),
        ]}
        opened = open_scope_profile(document)["omitted_aspect"]
        self.assertEqual(opened["dimensions"]["value_changing_groups"], 1)
        self.assertEqual(opened["period"]["value_changing_groups"], 0)

    def test_the_same_value_under_two_periods_is_scope_only(self):
        document = {"facts": [
            scoped_fact(0, "{urn:tax/2024}Revenue", "100", "2024-12-31", "{urn:tax/2024}Consolidated"),
            scoped_fact(1, "{urn:tax/2024}Revenue", "100", "2023-12-31", "{urn:tax/2024}Consolidated"),
        ]}
        opened = open_scope_profile(document)["omitted_aspect"]
        self.assertEqual(opened["period"]["same_value_different_scope_groups"], 1)
        self.assertEqual(opened["period"]["value_changing_groups"], 0)

    def test_a_year_token_namespace_is_classified_and_not_admitted(self):
        older = {"facts": [scoped_fact(0, "{http://fasb.org/us-gaap/2023}Revenue", "100", "2023-12-31", "{urn:d}All")]}
        newer = {"facts": [scoped_fact(0, "{http://fasb.org/us-gaap/2024}Revenue", "80", "2023-12-31", "{urn:d}All")]}
        profile = namespace_year_profile(older, newer)
        self.assertFalse(profile["admitted_join"])
        self.assertEqual(profile["namespace_class"]["year_token_only"], 1)
        self.assertEqual(profile["not_same_value"]["year_token_only"], 1)
        dated = {"facts": [scoped_fact(0, "{http://fasb.org/us-gaap/2024-01-31}Revenue", "80", "2023-12-31", "{urn:d}All")]}
        calendar = namespace_year_profile(older, dated)
        self.assertEqual(calendar["namespace_class"]["calendar_token_only"], 1)
        self.assertFalse(calendar["admitted_join"])
        other = {"facts": [scoped_fact(0, "{http://fasb.org/srt/2024}Revenue", "80", "2023-12-31", "{urn:d}All")]}
        mixed = namespace_year_profile(older, other)
        self.assertEqual(mixed["namespace_class"]["other_namespace"], 1)


class DimensionResidual(unittest.TestCase):
    def test_the_amount_alone_does_not_authorize_a_scoped_answer(self):
        document = {"facts": [
            scoped_fact(0, "{urn:tax/2024}Revenue", "100", "2024-12-31", "{urn:tax/2024}Consolidated"),
        ]}
        world = build_world(document, {"concept": "{urn:tax/2024}Revenue"}, split_scope=True)
        self.assertEqual([item.action_id for item in world.problem.actions], ["fact-0/amount", "fact-0/scope"])
        amount_only = compare_policies(world, certificate_limit=1)[2]
        both = compare_policies(world, certificate_limit=2)[2]
        self.assertEqual(amount_only.outcome, "abstained")
        self.assertEqual(amount_only.acquired_facts, 1)
        self.assertEqual(both.outcome, "emitted")
        self.assertEqual(both.acquired_facts, 2)

    def test_one_complete_fact_does_not_clear_a_second_dimension(self):
        document = {"facts": [
            scoped_fact(0, "{urn:tax/2024}Revenue", "100", "2024-12-31", "{urn:tax/2024}Consolidated"),
            scoped_fact(1, "{urn:tax/2024}Revenue", "40", "2024-12-31", "{urn:tax/2024}Segment"),
        ]}
        world = build_world(document, {"concept": "{urn:tax/2024}Revenue"}, split_scope=True)
        decision = compare_policies(world, certificate_limit=2)[2]
        self.assertEqual(decision.outcome, "abstained")
        profile = dimension_residual_profile(document)
        self.assertEqual(profile["dimension_value_changing_groups"], 1)
        self.assertEqual(profile["same_dimension_value_conflicts"], 0)
        self.assertEqual(profile["policy_outcomes"], {"abstained/emitted/abstained": 1})
        self.assertFalse(profile["values_recorded"])

    def test_same_dimension_numbers_are_not_counted_as_a_dimension_split(self):
        document = {"facts": [
            scoped_fact(0, "{urn:tax/2024}Revenue", "100", "2024-12-31", "{urn:tax/2024}Consolidated"),
            scoped_fact(1, "{urn:tax/2024}Revenue", "101", "2024-12-31", "{urn:tax/2024}Consolidated"),
        ]}
        profile = dimension_residual_profile(document)
        self.assertEqual(profile["same_dimension_value_conflicts"], 1)
        self.assertEqual(profile["dimension_value_changing_groups"], 0)
