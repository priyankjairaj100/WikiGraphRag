"""Authored interface fixtures only; no natural source, question or reference."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import json
import unittest

from temporal_state.reader_binding_v16 import (
    ASPECTS, BindingError, EvidencePack, lexical_baseline, render_proposal,
    score_direct_quantities, validate_direct_output, validate_proposal,
)


def authored_fact(handle="f01", *, value="12", year="2024", entity="Alpha", unit="USD",
                  source="a", concept="{urn:authored:v1}Revenue", population="Whole group"):
    values = {
        "concept": concept, "entity": {"scheme": "urn:authored:issuer", "identifier": entity},
        "period": {"kind": "duration", "lexemes": {"startDate": year + "-01-01", "endDate": year + "-12-31"}},
        "unit": {"shape": "simple_product", "measures": ["{urn:authored:currency}" + unit],
                 "numerator_measures": [], "denominator_measures": []},
        "population": {"label": population, "dimensions": []},
        "source": {"sha256": source * 64, "version": "Report " + source.upper()},
    }
    texts = {"concept": "Revenue", "entity": entity, "period": "Year " + year,
             "unit": unit, "population": population, "source": "Report " + source.upper()}
    witnesses = [{"handle": "w" + handle[1:] + str(i), "aspect": a, "text": texts[a]} for i, a in enumerate(ASPECTS)]
    fact = {"handle": handle, "value": value, "labels": ["Revenue"],
            "bindings": {a: {"value": values[a], "witnesses": [witnesses[i]["handle"]]}
                         for i, a in enumerate(ASPECTS)}}
    return fact, witnesses


def payload(*items):
    if not items:
        items = (authored_fact(),)
    return {"schema_version": "reader_evidence_pack_v16", "pack_id": "p01", "blocks": [],
            "facts": [item[0] for item in items], "witnesses": [w for item in items for w in item[1]]}


def proposal(pack, handles=("f01",), *, decision="answer", unknown=False, joint=False):
    facts = {f["handle"]: f for f in pack.snapshot()["facts"]}
    claims = [{"fact_handle": h, "binding_witnesses": {
        a: facts[h]["bindings"][a]["witnesses"] if facts[h]["bindings"][a] else [] for a in ASPECTS}}
              for h in handles]
    return {"decision": decision, "unknown": unknown, "clarify_aspects": ["source"] if decision == "clarify" else [],
            "hypotheses": [{"claims": claims}] if joint else [{"claims": [c]} for c in claims]}


class EvidencePackTests(unittest.TestCase):
    def test_input_and_returned_copies_cannot_modify_snapshot(self):
        raw = payload()
        pack = EvidencePack(raw)
        sha = pack.sha256
        raw["facts"][0]["value"] = "77"
        exposed = pack.snapshot()
        exposed["facts"][0]["bindings"]["source"]["value"]["version"] = "Changed"
        self.assertEqual(pack.sha256, sha)
        self.assertEqual(pack.snapshot()["facts"][0]["value"], "12")
        with self.assertRaises(FrozenInstanceError):
            pack._serialized = "{}"

    def test_no_source_or_hidden_document_argument(self):
        raw = payload()
        raw["hidden_document"] = {"secret": "different year"}
        with self.assertRaisesRegex(BindingError, "pack_shape"):
            EvidencePack(raw)

    def test_duplicate_json_keys_rejected(self):
        with self.assertRaisesRegex(BindingError, "duplicate_json_key"):
            EvidencePack('{"pack_id":"a","pack_id":"b"}')

    def test_duplicate_and_unobserved_witnesses_rejected(self):
        raw = payload()
        raw["facts"][0]["bindings"]["unit"]["witnesses"] = ["unknown"]
        with self.assertRaises(BindingError):
            EvidencePack(raw)
        raw = payload()
        raw["witnesses"].append(deepcopy(raw["witnesses"][0]))
        with self.assertRaises(BindingError):
            EvidencePack(raw)

    def test_noncanonical_numbers_and_floats_rejected(self):
        for bad in (12, 12.0, "1e2", "12.0", "+12", "-0", "NaN", "00", "0.00"):
            raw = payload()
            raw["facts"][0]["value"] = bad
            with self.subTest(value=bad), self.assertRaises(BindingError):
                EvidencePack(raw)

    def test_fractional_negative_exact_quantity_preserved(self):
        for value in ("0", "-12", "-0.005", "0.005", "123.45", "9" * 100):
            self.assertEqual(EvidencePack(payload(authored_fact(value=value))).snapshot()["facts"][0]["value"], value)

    def test_namespace_and_source_hash_required(self):
        raw = payload()
        raw["facts"][0]["bindings"]["concept"]["value"] = "Revenue"
        with self.assertRaises(BindingError):
            EvidencePack(raw)
        raw = payload()
        raw["facts"][0]["bindings"]["source"]["value"]["sha256"] = "annual report"
        with self.assertRaises(BindingError):
            EvidencePack(raw)

    def test_all_aspect_fields_required_even_when_unavailable(self):
        raw = payload()
        del raw["facts"][0]["bindings"]["source"]
        with self.assertRaises(BindingError):
            EvidencePack(raw)
        raw["facts"][0]["bindings"]["source"] = None
        self.assertIsNone(EvidencePack(raw).snapshot()["facts"][0]["bindings"]["source"])

    def test_fact_limit_is_admission_failure_not_truncation(self):
        raw = payload(*(authored_fact("f" + str(i).zfill(2)) for i in range(65)))
        with self.assertRaisesRegex(BindingError, "facts_limit"):
            EvidencePack(raw)

    def test_compact_roundtrip_deduplicates_values_and_text(self):
        pack = EvidencePack(payload(authored_fact(), authored_fact("f02")))
        compact = pack.prompt_view()
        self.assertEqual(len(compact["values"]), 6)
        self.assertEqual(len(compact["texts"]), 6)
        self.assertEqual(EvidencePack.from_prompt_view(compact).sha256, pack.sha256)
        compact["facts"][0][3][0] = "unknown"
        with self.assertRaises(BindingError):
            EvidencePack.from_prompt_view(compact)

    def test_compact_absent_binding_stays_absent(self):
        raw = payload()
        raw["facts"][0]["bindings"]["source"] = None
        raw["witnesses"] = [w for w in raw["witnesses"] if w["aspect"] != "source"]
        pack = EvidencePack(raw)
        self.assertEqual(EvidencePack.from_prompt_view(pack.prompt_view()).as_json(), pack.as_json())
        self.assertNotIn("Report A", json.dumps(pack.prompt_view()))
        self.assertNotIn("a" * 64, json.dumps(pack.prompt_view()))


class ProposalTests(unittest.TestCase):
    def test_complete_observed_binding_renders_exact_value(self):
        pack = EvidencePack(payload())
        result = render_proposal(pack, proposal(pack))
        self.assertEqual(result["action"], "answered")
        self.assertEqual(result["quantities"][0]["value"], "12")
        self.assertEqual(result["quantities"][0]["scope"]["source"]["sha256"], "a" * 64)

    def test_equal_quantity_with_wrong_year_entity_unit_source_never_aliases(self):
        variants = ({"year": "2023"}, {"entity": "Beta"}, {"unit": "EUR"}, {"source": "b"},
                    {"concept": "{urn:authored:v2}Revenue"}, {"population": "Segment Z"})
        for difference in variants:
            pack = EvidencePack(payload(authored_fact(), authored_fact("f02", **difference)))
            result = render_proposal(pack, proposal(pack))
            with self.subTest(difference=difference):
                self.assertEqual(result["quantities"][0]["occurrence_handles"], ["f01"])
                alternative = render_proposal(pack, proposal(pack, ("f01", "f02")))
                self.assertEqual(alternative["action"], "clarify")
                self.assertEqual(len(alternative["hypotheses"]), 2)

    def test_equal_duplicates_all_retained(self):
        pack = EvidencePack(payload(authored_fact(), authored_fact("f02")))
        result = render_proposal(pack, proposal(pack))
        self.assertEqual(result["action"], "answered")
        self.assertEqual(result["quantities"][0]["occurrence_handles"], ["f01", "f02"])

    def test_conflicting_duplicate_blocks_choose_first(self):
        pack = EvidencePack(payload(authored_fact(), authored_fact("f02", value="13")))
        result = render_proposal(pack, proposal(pack))
        self.assertEqual(result["action"], "insufficient")
        self.assertEqual(result["quantities"], [])
        self.assertEqual(result["hypotheses"][0]["claims"][0]["candidate_handles"], ["f01", "f02"])

    def test_missing_binding_and_absent_witness_cannot_authorize(self):
        for aspect in ASPECTS:
            raw = payload()
            raw["facts"][0]["bindings"][aspect] = None
            raw["witnesses"] = [w for w in raw["witnesses"] if w["aspect"] != aspect]
            pack = EvidencePack(raw)
            with self.subTest(aspect=aspect):
                result = render_proposal(pack, proposal(pack))
                self.assertEqual(result["action"], "insufficient")
                self.assertIn("unavailable_binding", result["reason_codes"])

    def test_known_value_without_citation_cannot_authorize(self):
        pack = EvidencePack(payload())
        proposed = proposal(pack)
        proposed["hypotheses"][0]["claims"][0]["binding_witnesses"]["period"] = []
        self.assertEqual(render_proposal(pack, proposed)["action"], "insufficient")

    def test_wrong_fact_or_wrong_aspect_witness_is_invalid(self):
        pack = EvidencePack(payload())
        proposed = proposal(pack)
        proposed["hypotheses"][0]["claims"][0]["fact_handle"] = "f99"
        self.assertEqual(render_proposal(pack, proposed)["action"], "invalid_output")
        proposed = proposal(pack)
        proposed["hypotheses"][0]["claims"][0]["binding_witnesses"]["period"] = ["w010"]
        self.assertEqual(render_proposal(pack, proposed)["action"], "invalid_output")

    def test_unresolved_possible_match_blocks_even_if_not_proposed(self):
        second = authored_fact("f02", value="13")
        second[0]["bindings"]["unit"] = None
        pack = EvidencePack(payload(authored_fact(), second))
        result = render_proposal(pack, proposal(pack))
        self.assertEqual(result["action"], "insufficient")
        self.assertIn("unresolved_possible_match", result["reason_codes"])

    def test_known_different_entity_excludes_unresolved_other_aspect(self):
        second = authored_fact("f02", value="13", entity="Beta")
        second[0]["bindings"]["unit"] = None
        pack = EvidencePack(payload(authored_fact(), second))
        self.assertEqual(render_proposal(pack, proposal(pack))["action"], "answered")

    def test_null_quantity_is_not_zero_or_silently_dropped(self):
        pack = EvidencePack(payload(authored_fact(), authored_fact("f02", value=None)))
        self.assertEqual(render_proposal(pack, proposal(pack))["action"], "insufficient")

    def test_joint_hypothesis_preserves_two_physical_versions(self):
        pack = EvidencePack(payload(authored_fact(), authored_fact("f02", source="b", year="2023", value="17")))
        result = render_proposal(pack, proposal(pack, ("f01", "f02"), joint=True))
        self.assertEqual(result["action"], "answered")
        self.assertEqual([q["value"] for q in result["quantities"]], ["12", "17"])

    def test_unknown_blocks_even_complete_joint_assignment(self):
        pack = EvidencePack(payload())
        self.assertEqual(render_proposal(pack, proposal(pack, unknown=True))["action"], "clarify")

    def test_precise_clarification_aspects_retained(self):
        pack = EvidencePack(payload())
        proposed = proposal(pack, decision="clarify", unknown=True)
        proposed["clarify_aspects"] = ["period", "source"]
        result = render_proposal(pack, proposed)
        self.assertEqual(result["clarify_aspects"], ["period", "source"])
        self.assertEqual(result["quantities"], [])

    def test_clarification_must_have_enum_aspects_and_matching_action(self):
        pack = EvidencePack(payload())
        for aspects, decision in (([], "clarify"), (["date"], "clarify"),
                                  (["period", "period"], "clarify"), (["source"], "answer")):
            proposed = proposal(pack, decision=decision)
            proposed["clarify_aspects"] = aspects
            with self.subTest(aspects=aspects, decision=decision):
                self.assertEqual(render_proposal(pack, proposed)["action"], "invalid_output")

    def test_automatic_ambiguity_names_differing_aspect(self):
        pack = EvidencePack(payload(authored_fact(), authored_fact("f02", source="b")))
        result = render_proposal(pack, proposal(pack, ("f01", "f02")))
        self.assertEqual(result["clarify_aspects"], ["source"])

    def test_refusal_does_not_render_quantities(self):
        pack = EvidencePack(payload())
        for action in ("clarify", "insufficient"):
            result = render_proposal(pack, proposal(pack, decision=action))
            self.assertEqual(result["action"], action)
            self.assertEqual(result["quantities"], [])

    def test_overflow_and_extra_fields_are_invalid_without_repair(self):
        pack = EvidencePack(payload())
        proposed = proposal(pack)
        proposed["hypotheses"] *= 9
        self.assertEqual(render_proposal(pack, proposed)["action"], "invalid_output")
        proposed = proposal(pack)
        proposed["explanation"] = "unsupported extra claim"
        self.assertEqual(render_proposal(pack, proposed)["action"], "invalid_output")

    def test_malformed_output_and_non_boolean_unknown_rejected(self):
        pack = EvidencePack(payload())
        for output in ("{", "```json\n{}\n```", [], None):
            self.assertEqual(render_proposal(pack, output)["action"], "invalid_output")
        output = proposal(pack)
        output["unknown"] = 0
        with self.assertRaises(BindingError):
            validate_proposal(pack, output)

    def test_schema_does_not_claim_semantic_question_correctness(self):
        # The interface has no question argument: a valid but wrongly mapped
        # occurrence still renders and must fail external semantic/reference QA.
        pack = EvidencePack(payload(authored_fact(), authored_fact("f02", year="2023")))
        result = render_proposal(pack, proposal(pack, ("f02",)))
        self.assertEqual(result["action"], "answered")
        self.assertEqual(result["quantities"][0]["scope"]["period"]["lexemes"]["endDate"], "2023-12-31")


class LexicalTests(unittest.TestCase):
    def test_explicit_lexical_constraints_match_authored_year(self):
        pack = EvidencePack(payload(authored_fact(), authored_fact("f02", year="2023")))
        result = render_proposal(pack, lexical_baseline(pack, "What was Alpha revenue in USD for 2024?"))
        self.assertEqual(result["action"], "answered")
        self.assertEqual(result["quantities"][0]["occurrence_handles"], ["f01"])

    def test_unit_entity_constraints_do_not_select_equal_wrong_values(self):
        pack = EvidencePack(payload(authored_fact(), authored_fact("f02", unit="EUR"),
                                    authored_fact("f03", entity="Beta")))
        result = render_proposal(pack, lexical_baseline(pack, "What was Alpha revenue in USD for 2024?"))
        self.assertEqual(result["action"], "answered")
        self.assertEqual(result["quantities"][0]["occurrence_handles"], ["f01"])

    def test_no_year_or_unrecognized_synonym_refuses(self):
        pack = EvidencePack(payload())
        for question in ("What was Alpha revenue?", "What was Alpha turnover for 2024?",
                         "What was Alpha adjusted revenue for 2024?"):
            self.assertNotEqual(render_proposal(pack, lexical_baseline(pack, question))["action"], "answered")

    def test_tied_distinct_scopes_clarify(self):
        pack = EvidencePack(payload(authored_fact(), authored_fact("f02", source="b")))
        result = render_proposal(pack, lexical_baseline(pack, "What was Alpha revenue for 2024?"))
        self.assertEqual(result["action"], "clarify")

    def test_explicit_plural_forms_joint_assignment(self):
        pack = EvidencePack(payload(authored_fact(), authored_fact("f02", year="2023", source="b")))
        proposed = lexical_baseline(pack, "Report Alpha revenue for both 2023 and 2024.")
        self.assertEqual(len(proposed["hypotheses"]), 1)
        self.assertEqual(len(proposed["hypotheses"][0]["claims"]), 2)
        self.assertEqual(render_proposal(pack, proposed)["action"], "answered")

    def test_missing_year_witness_still_retained_as_unknown(self):
        raw = payload()
        raw["facts"][0]["bindings"]["period"] = None
        raw["witnesses"] = [w for w in raw["witnesses"] if w["aspect"] != "period"]
        pack = EvidencePack(raw)
        self.assertNotEqual(render_proposal(pack, lexical_baseline(pack, "Alpha revenue for 2024"))["action"], "answered")


class DirectOutputTests(unittest.TestCase):
    def direct(self, pack, *, value="12", action="answered"):
        claim = proposal(pack)["hypotheses"][0]["claims"][0]
        return {"action": action, "unknown": False, "clarify_aspects": ["source"] if action == "clarify" else [],
                "quantities": [{"value": value, **claim}]}

    def test_wrong_direct_value_preserved_and_scored_not_repaired(self):
        pack = EvidencePack(payload())
        wrong = self.direct(pack, value="99")
        self.assertEqual(validate_direct_output(pack, wrong)["quantities"][0]["value"], "99")
        scored = score_direct_quantities(pack, wrong)
        self.assertFalse(scored["checks"][0]["value_matches"])
        self.assertEqual(scored["checks"][0]["stated_value"], "99")
        self.assertEqual(scored["semantic_question_correctness"], "not_checked")

    def test_refusal_with_unsupported_quantity_still_scored(self):
        pack = EvidencePack(payload())
        scored = score_direct_quantities(pack, self.direct(pack, value="99", action="insufficient"))
        self.assertEqual(len(scored["checks"]), 1)
        self.assertFalse(scored["checks"][0]["value_matches"])

    def test_direct_missing_witness_is_not_complete_support(self):
        pack = EvidencePack(payload())
        output = self.direct(pack)
        output["quantities"][0]["binding_witnesses"]["source"] = []
        scored = score_direct_quantities(pack, output)
        self.assertFalse(scored["checks"][0]["support_complete"])

    def test_empty_answer_or_unknown_answer_is_invalid(self):
        pack = EvidencePack(payload())
        for output in ({"action": "answered", "unknown": False, "clarify_aspects": [], "quantities": []},
                       {**self.direct(pack), "unknown": True}):
            self.assertEqual(score_direct_quantities(pack, output)["action"], "invalid_output")

    def test_direct_clarification_contract_matches_proposer(self):
        pack = EvidencePack(payload())
        output = {"action": "clarify", "unknown": True, "clarify_aspects": ["period"], "quantities": []}
        self.assertEqual(score_direct_quantities(pack, output)["clarify_aspects"], ["period"])
        output["clarify_aspects"] = []
        self.assertEqual(score_direct_quantities(pack, output)["action"], "invalid_output")


if __name__ == "__main__":
    unittest.main()
