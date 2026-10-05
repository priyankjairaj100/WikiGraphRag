"""Authored Inline XBRL fixtures. These counts are not filing or QA results."""

import unittest

from temporal_state.typed_binding_v22 import (
    COVERAGE_PROVENANCE, BindingError, build_world, compare_policies, summarize_document,
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
