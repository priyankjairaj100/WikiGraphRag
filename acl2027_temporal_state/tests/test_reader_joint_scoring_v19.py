"""Invented controls only. No natural source, question, reference or prediction."""
from copy import deepcopy
import json
import unittest

from temporal_state.reader_binding_v18 import ASPECTS, BindingError, EvidencePack
from temporal_state.reader_joint_scoring_v19 import (
    REFERENCE_SCHEMA, blind_permutation, project_output, score_output, validate_reference,
)


def fact(handle="f1", value="12", source="a", year="2024"):
    values = {
        "concept": "{urn:invented}Revenue",
        "entity": {"scheme": "urn:invented:entity", "identifier": "Alpha"},
        "period": {"kind": "duration", "lexemes": {"startDate": year + "-01-01", "endDate": year + "-12-31"}},
        "unit": {"shape": "simple_product", "measures": ["{urn:invented}USD"],
                 "numerator_measures": [], "denominator_measures": []},
        "population": {"label": "Explicit invented group", "dimensions": []},
        "source": {"sha256": source * 64, "version": "Invented " + source},
    }
    witnesses = [{"handle": "w" + handle + str(i), "aspect": a, "text": "Invented " + a + " " + handle}
                 for i, a in enumerate(ASPECTS)]
    row = {"handle": handle, "value": value, "labels": ["Revenue"],
           "bindings": {a: {"value": values[a], "witnesses": [witnesses[i]["handle"]]}
                        for i, a in enumerate(ASPECTS)}}
    return row, witnesses


def pack(*items):
    items = items or (fact(),)
    return EvidencePack({"schema_version": "reader_evidence_pack_v18", "pack_id": "p1",
                         "blocks": [{"handle": "b1", "text": "Invented complete context"},
                                    {"handle": "b2", "text": ""}],
                         "facts": [x[0] for x in items], "witnesses": [w for x in items for w in x[1]]})


def citations(p, handle):
    f = next(f for f in p.snapshot()["facts"] if f["handle"] == handle)
    return {a: list(f["bindings"][a]["witnesses"]) if f["bindings"][a] else [] for a in ASPECTS}


def direct(p, handles=("f1",), action="answered"):
    facts = {f["handle"]: f for f in p.snapshot()["facts"]}
    return {"action": action, "unknown": action != "answered",
            "clarify_aspects": ["source"] if action == "clarify" else [],
            "quantities": [{"value": facts[h]["value"], "fact_handle": h,
                            "binding_witnesses": citations(p, h)} for h in handles]}


def proposal(p, handles=("f1",), decision="answer"):
    return {"decision": decision, "unknown": False,
            "clarify_aspects": ["source"] if decision == "clarify" else [],
            "hypotheses": [{"claims": [{"fact_handle": h, "binding_witnesses": citations(p, h)}
                                        for h in handles]}] if handles else []}


def reference(p, groups=(("f1",),), action="answered"):
    facts = {f["handle"]: f for f in p.snapshot()["facts"]}
    claims = []
    for group in groups:
        f = facts[group[0]]
        allowed = {a: list(dict.fromkeys(w for h in group for w in citations(p, h)[a])) for a in ASPECTS}
        claims.append({"value": f["value"], "scope": {a: f["bindings"][a]["value"] for a in ASPECTS},
                       "realizations": [{"occurrence_handles": list(group), "allowed_citations": allowed,
                                         "sufficient_citation_sets": [citations(p, h) for h in group]}]})
    return {"schema_version": REFERENCE_SCHEMA, "expected_action": action, "claims": claims}


class JointTargetTests(unittest.TestCase):
    def test_same_common_view_and_exact_success_for_proposal_and_direct(self):
        p = pack()
        one = score_output(p, proposal(p), reference(p), output_kind="proposal")
        two = score_output(p, direct(p), reference(p), output_kind="direct")
        self.assertEqual(one["projection"], two["projection"])
        self.assertTrue(one["score"]["joint_correct"])
        self.assertTrue(two["score"]["joint_correct"])

    def test_equal_value_wrong_each_scope_fails(self):
        replacements = {
            "concept": "{urn:different}Revenue",
            "entity": {"scheme": "urn:invented:entity", "identifier": "Beta"},
            "period": {"kind": "duration", "lexemes": {"startDate": "2023-01-01", "endDate": "2023-12-31"}},
            "unit": {"shape": "simple_product", "measures": ["{urn:invented}EUR"],
                     "numerator_measures": [], "denominator_measures": []},
            "population": {"label": "Explicit different subgroup", "dimensions": []},
            "source": {"sha256": "b" * 64, "version": "Invented b"},
        }
        for aspect, changed in replacements.items():
            with self.subTest(aspect=aspect):
                other = fact("f2")
                other[0]["bindings"][aspect]["value"] = changed
                p = pack(fact(), other)
                out = score_output(p, direct(p, ("f2",)), reference(p), output_kind="direct")
                self.assertEqual(out["projection"]["quantities"][0]["value"], "12")
                self.assertFalse(out["score"]["joint_correct"])
                self.assertEqual(out["score"]["missing_requested_claim_count"], 1)

    def test_two_physical_sources_equal_values_do_not_merge(self):
        p = pack(fact(), fact("f2", source="b"))
        out = score_output(p, direct(p, ("f2", "f1")), reference(p, (("f1",), ("f2",))), output_kind="direct")
        self.assertTrue(out["score"]["joint_correct"])
        self.assertEqual(out["audit"]["normalized_quantity_count"], 2)

    def test_missing_second_claim_never_gets_joint_credit(self):
        p = pack(fact(), fact("f2", source="b"))
        out = score_output(p, direct(p), reference(p, (("f1",), ("f2",))), output_kind="direct")
        self.assertFalse(out["score"]["joint_correct"])
        self.assertEqual(out["score"]["matched_quantity_claim_count"], 1)
        self.assertEqual(out["score"]["missing_requested_claim_count"], 1)

    def test_extra_supported_claim_still_fails_requested_target(self):
        p = pack(fact(), fact("f2", source="b"))
        out = score_output(p, direct(p, ("f1", "f2")), reference(p), output_kind="direct")
        self.assertFalse(out["score"]["joint_correct"])
        self.assertEqual(out["score"]["unmatched_material_quantity_count"], 1)
        self.assertEqual(out["audit"]["exposed_unsupported_quantity_count"], 0)

    def test_wrong_stated_value_is_preserved_and_unsupported(self):
        p = pack()
        answer = direct(p)
        answer["quantities"][0]["value"] = "13"
        out = score_output(p, answer, reference(p), output_kind="direct")
        self.assertEqual(out["projection"]["quantities"][0]["value"], "13")
        self.assertFalse(out["score"]["joint_correct"])
        self.assertTrue(out["score"]["unsupported_material_answer"])

    def test_value_is_exact_not_numeric_tolerance(self):
        p = pack(fact(value="12.000000001"))
        answer = direct(p)
        answer["quantities"][0]["value"] = "12"
        self.assertFalse(score_output(p, answer, reference(p), output_kind="direct")["score"]["joint_correct"])

    def test_claim_and_citation_order_do_not_change_score(self):
        p = pack(fact(), fact("f2", source="b"))
        target = reference(p, (("f1",), ("f2",)))
        one = score_output(p, direct(p, ("f1", "f2")), target, output_kind="direct")
        target["claims"].reverse()
        two = score_output(p, direct(p, ("f2", "f1")), target, output_kind="direct")
        self.assertEqual(one["projection"], two["projection"])
        self.assertEqual(one["score"], two["score"])

    def test_dimension_order_is_not_silently_normalized(self):
        a, b = fact(), fact("f2")
        a[0]["bindings"]["population"]["value"]["dimensions"] = [{"d": "one"}, {"d": "two"}]
        b[0]["bindings"]["population"]["value"]["dimensions"] = [{"d": "two"}, {"d": "one"}]
        p = pack(a, b)
        self.assertFalse(score_output(p, direct(p, ("f2",)), reference(p), output_kind="direct")["score"]["joint_correct"])


class AliasCitationTests(unittest.TestCase):
    def test_aliases_merge_identically_and_keep_union_citations(self):
        p = pack(fact(), fact("f2"))
        target = reference(p, (("f1", "f2"),))
        a = score_output(p, direct(p, ("f2", "f1")), target, output_kind="direct")
        b = score_output(p, proposal(p, ("f1", "f2")), target, output_kind="proposal")
        self.assertEqual(a["projection"], b["projection"])
        self.assertTrue(a["score"]["joint_correct"])
        self.assertEqual(a["audit"]["raw_emitted_quantity_count"], 2)
        self.assertEqual(a["audit"]["collapsed_duplicate_quantity_count"], 1)
        self.assertEqual(a["projection"]["quantities"][0]["occurrence_handles"], ["f1", "f2"])
        self.assertEqual(len(a["projection"]["quantities"][0]["citations"]["unit"]), 2)

    def test_single_alias_expands_to_complete_group_in_both_arms(self):
        p = pack(fact(), fact("f2"))
        target = reference(p, (("f1", "f2"),))
        a = score_output(p, direct(p, ("f2",)), target, output_kind="direct")
        b = score_output(p, proposal(p, ("f2",)), target, output_kind="proposal")
        self.assertEqual(a["projection"], b["projection"])
        self.assertTrue(a["score"]["joint_correct"])

    def test_whole_group_must_be_explicitly_whitelisted(self):
        p = pack(fact(), fact("f2"))
        out = score_output(p, direct(p), reference(p), output_kind="direct")
        self.assertFalse(out["score"]["joint_correct"])
        self.assertEqual(out["projection"]["quantities"][0]["occurrence_handles"], ["f1", "f2"])

    def test_missing_sufficient_citation_fails_even_when_schema_accepts(self):
        p = pack()
        answer = direct(p)
        answer["quantities"][0]["binding_witnesses"]["period"] = []
        out = score_output(p, answer, reference(p), output_kind="direct")
        self.assertTrue(out["audit"]["schema_valid"])
        self.assertFalse(out["score"]["joint_correct"])
        self.assertTrue(out["score"]["unsupported_material_answer"])

    def test_wrong_fact_citation_is_invalid_not_repaired(self):
        p = pack(fact(), fact("f2", source="b"))
        answer = direct(p)
        answer["quantities"][0]["binding_witnesses"]["unit"] = citations(p, "f2")["unit"]
        out = score_output(p, answer, reference(p), output_kind="direct")
        self.assertEqual(out["projection"]["action"], "invalid_output")
        self.assertIsNone(out["score"]["unsupported_material_answer"])

    def test_sufficient_sets_cannot_be_mixed_aspect_by_aspect(self):
        row, witnesses = fact()
        for i, aspect in enumerate(ASPECTS):
            h = "alt" + str(i)
            witnesses.append({"handle": h, "aspect": aspect, "text": "Invented alternative witness"})
            row["bindings"][aspect]["witnesses"].append(h)
        p = pack((row, witnesses))
        target = reference(p)
        options = [{a: [citations(p, "f1")[a][i]] for a in ASPECTS} for i in (0, 1)]
        target["claims"][0]["realizations"][0]["sufficient_citation_sets"] = options
        answer = direct(p)
        answer["quantities"][0]["binding_witnesses"] = {a: options[i % 2][a] for i, a in enumerate(ASPECTS)}
        self.assertFalse(score_output(p, answer, target, output_kind="direct")["score"]["joint_correct"])
        answer["quantities"][0]["binding_witnesses"] = options[1]
        self.assertTrue(score_output(p, answer, target, output_kind="direct")["score"]["joint_correct"])

    def test_extra_unapproved_but_pack_valid_citation_fails(self):
        row, witnesses = fact()
        witnesses.append({"handle": "extra", "aspect": "unit", "text": "Invented unrelated unit note"})
        row["bindings"]["unit"]["witnesses"].append("extra")
        p = pack((row, witnesses))
        target = reference(p)
        real = target["claims"][0]["realizations"][0]
        real["allowed_citations"]["unit"].remove("extra")
        real["sufficient_citation_sets"][0]["unit"].remove("extra")
        out = score_output(p, direct(p), target, output_kind="direct")
        self.assertTrue(out["audit"]["schema_valid"])
        self.assertFalse(out["score"]["joint_correct"])

    def test_unresolved_competitor_retained_and_support_fails(self):
        unknown = fact("f2")
        unknown[0]["bindings"]["population"] = None
        p = pack(fact(), unknown)
        out = score_output(p, direct(p), reference(p), output_kind="direct")
        self.assertFalse(out["score"]["joint_correct"])
        self.assertEqual(out["projection"]["quantities"][0]["occurrence_handles"], ["f1", "f2"])
        self.assertEqual(out["audit"]["exposed_unsupported_quantity_count"], 1)


class ActionExecutionTests(unittest.TestCase):
    def setUp(self):
        self.p = pack()
        self.control = reference(self.p, (), action="insufficient")

    def test_correct_insufficient_requires_empty_material_output(self):
        for kind, output in (("direct", direct(self.p, (), "insufficient")),
                             ("proposal", proposal(self.p, (), "insufficient"))):
            with self.subTest(kind=kind):
                out = score_output(self.p, output, self.control, output_kind=kind)
                self.assertTrue(out["score"]["correct_insufficient"])
                self.assertEqual(out["audit"]["material_answer_status"], "none")

    def test_valid_refusals_do_not_hide_quantities(self):
        for action in ("clarify", "insufficient"):
            with self.subTest(action=action):
                out = score_output(self.p, direct(self.p, action=action), self.control, output_kind="direct")
                self.assertEqual(len(out["projection"]["quantities"]), 1)
                self.assertFalse(out["score"]["joint_correct"])
                self.assertTrue(out["score"]["unsupported_material_answer"])
                self.assertFalse(out["score"]["safe_incorrect_clarification"])

    def test_clarify_is_safe_but_wrong_for_specified_control(self):
        out = score_output(self.p, direct(self.p, (), "clarify"), self.control, output_kind="direct")
        self.assertFalse(out["score"]["joint_correct"])
        self.assertTrue(out["score"]["safe_incorrect_clarification"])
        self.assertFalse(out["score"]["correct_insufficient"])

    def test_answerable_abstention_is_incorrect(self):
        out = score_output(self.p, direct(self.p, (), "insufficient"), reference(self.p), output_kind="direct")
        self.assertFalse(out["score"]["joint_correct"])
        self.assertEqual(out["score"]["missing_requested_claim_count"], 1)

    def test_malformed_material_is_unresolved_not_safe(self):
        for raw in ('The amount is 12, despite missing support.', '{"action":', None,
                    '{"action":"insufficient","action":"answered"}'):
            with self.subTest(raw=raw):
                out = score_output(self.p, raw, self.control, output_kind="direct")
                self.assertEqual(out["projection"]["action"], "invalid_output")
                self.assertFalse(out["score"]["joint_correct"])
                self.assertIsNone(out["score"]["unsupported_material_answer"])
                self.assertEqual(out["score"]["material_answer_status"], "unresolved_requires_review")
                self.assertFalse(out["score"]["safe_incorrect_clarification"])

    def test_invalid_proposal_trace_is_not_user_facing_answer(self):
        raw = proposal(self.p)
        raw["unexpected"] = "12"
        out = score_output(self.p, raw, self.control, output_kind="proposal")
        self.assertEqual(out["projection"]["action"], "invalid_output")
        self.assertEqual(out["score"]["material_answer_status"], "unresolved_requires_review")

    def test_output_limit_retains_content_but_never_passes(self):
        out = score_output(self.p, direct(self.p), reference(self.p), output_kind="direct", output_limited=True)
        self.assertEqual(out["projection"]["action"], "answered")
        self.assertEqual(len(out["projection"]["quantities"]), 1)
        self.assertFalse(out["score"]["joint_correct"])
        self.assertEqual(out["score"]["matched_quantity_claim_count"], 1)

    def test_unexecuted_and_failed_are_distinct_from_invalid_and_safe(self):
        for state in ("unattempted", "failed", "blocked_after_stop"):
            with self.subTest(state=state):
                out = score_output(self.p, None, self.control, output_kind="direct", execution_status=state)
                self.assertEqual(out["projection"]["action"], "execution_failed" if state == "failed" else "not_executed")
                self.assertEqual(out["score"]["execution_status"], state)
                self.assertIsNone(out["score"]["schema_valid"])
                self.assertIsNone(out["score"]["unsupported_material_answer"])
                self.assertFalse(out["score"]["joint_correct"])

    def test_failure_cannot_silently_drop_a_partial_material_output(self):
        out = score_output(self.p, "amount 12", self.control, output_kind="direct", execution_status="failed")
        self.assertEqual(out["projection"]["action"], "execution_failed")
        self.assertEqual(out["audit"]["material_answer_status"], "unresolved_requires_review")
        self.assertIsNotNone(out["audit"]["raw_sha256"])
        self.assertFalse(out["score"]["joint_correct"])
        self.assertIsNone(out["score"]["unsupported_material_answer"])

    def test_failure_with_complete_response_never_passes_or_claims_safety(self):
        out = score_output(self.p, direct(self.p), reference(self.p), output_kind="direct",
                           execution_status="failed", output_limited=True)
        self.assertFalse(out["score"]["joint_correct"])
        self.assertEqual(out["audit"]["material_answer_status"], "unresolved_requires_review")
        self.assertTrue(out["audit"]["output_limited"])

    def test_unattempted_cell_with_output_is_an_input_ledger_error(self):
        with self.assertRaisesRegex(BindingError, "unattempted_cell_has_observed_output"):
            project_output(self.p, "amount 12", output_kind="direct", execution_status="unattempted")


class ReferenceAndBlindingTests(unittest.TestCase):
    def test_invalid_reference_is_an_error_not_model_failure(self):
        p = pack()
        for change, code in (("value", "reference_alias_scope_or_value"), ("null", "reference_unknown_scope"),
                             ("duplicate", "duplicate_reference_target")):
            target = reference(p)
            if change == "value":
                target["claims"][0]["value"] = "13"
            elif change == "null":
                target["claims"][0]["scope"]["period"] = None
            else:
                target["claims"].append(deepcopy(target["claims"][0]))
            with self.subTest(change=change), self.assertRaisesRegex(BindingError, code):
                score_output(p, direct(p), target, output_kind="direct")

    def test_reference_cannot_alias_different_physical_sources(self):
        p = pack(fact(), fact("f2", source="b"))
        with self.assertRaisesRegex(BindingError, "reference_alias_scope_or_value"):
            validate_reference(p, reference(p, (("f1", "f2"),)))

    def test_reference_cannot_whitelist_unobserved_or_wrong_aspect_witness(self):
        p = pack()
        for bad in ("inventedMissing", citations(p, "f1")["period"][0]):
            target = reference(p)
            target["claims"][0]["realizations"][0]["allowed_citations"]["unit"] = [bad]
            with self.subTest(bad=bad), self.assertRaisesRegex(BindingError, "reference_citation_support"):
                validate_reference(p, target)

    def test_inputs_and_returned_projection_do_not_mutate_pack_or_reference(self):
        p = pack()
        target, answer = reference(p), direct(p)
        before = deepcopy((target, answer))
        digest = p.sha256
        out = score_output(p, answer, target, output_kind="direct")
        out["projection"]["quantities"][0]["scope"]["population"]["label"] = "Changed"
        self.assertEqual((target, answer), before)
        self.assertEqual(p.sha256, digest)

    def test_common_view_has_no_method_or_diagnostic_fields(self):
        p = pack()
        out = project_output(p, proposal(p), output_kind="proposal")
        view = out["projection"]
        self.assertEqual(set(view), {"schema_version", "action", "quantities"})
        for forbidden in ("hypotheses", "reason_codes", "unknown", "clarify_aspects", "B1", "proposal"):
            self.assertNotIn(forbidden, json.dumps(view))

    def test_deterministic_permutation_preserves_every_record_and_hides_ids(self):
        p = pack()
        view = project_output(p, direct(p), output_kind="direct")["projection"]
        records = [{"output_id": "private_arm_" + str(i), "projection": view} for i in range(7)]
        a = blind_permutation(records, seed="invented-fixed-seed")
        b = blind_permutation(list(reversed(records)), seed="invented-fixed-seed")
        self.assertEqual(a["grader_records"], b["grader_records"])
        self.assertEqual(a["private_mapping"], b["private_mapping"])
        self.assertNotEqual(a["manifest"]["ordered_input_ids_sha256"], b["manifest"]["ordered_input_ids_sha256"])
        self.assertEqual({r["original_output_id"] for r in a["private_mapping"]}, {r["output_id"] for r in records})
        self.assertNotIn("private_arm", json.dumps(a["grader_records"]))

    def test_permutation_depends_on_seed_and_ids_not_output_content(self):
        p = pack()
        view = project_output(p, direct(p), output_kind="direct")["projection"]
        rows = [{"output_id": str(i), "projection": view} for i in range(10)]
        a = blind_permutation(rows, seed="one")
        modified = deepcopy(rows)
        modified[0]["projection"] = project_output(p, direct(p, (), "insufficient"), output_kind="direct")["projection"]
        b = blind_permutation(modified, seed="one")
        c = blind_permutation(rows, seed="two")
        self.assertEqual(a["private_mapping"], b["private_mapping"])
        self.assertNotEqual(a["private_mapping"], c["private_mapping"])

    def test_duplicate_permutation_ids_and_injected_diagnostics_fail(self):
        p = pack()
        view = project_output(p, direct(p), output_kind="direct")["projection"]
        with self.assertRaisesRegex(BindingError, "duplicate_permutation_id"):
            blind_permutation([{"output_id": "same", "projection": view}] * 2, seed="fixed")
        for location in ("top", "quantity", "citation"):
            changed = deepcopy(view)
            if location == "top":
                changed["reason_codes"] = ["method"]
            elif location == "quantity":
                changed["quantities"][0]["hypotheses"] = []
            else:
                changed["quantities"][0]["citations"]["unit"][0]["arm"] = "direct"
            with self.subTest(location=location), self.assertRaises(BindingError):
                blind_permutation([{"output_id": "x", "projection": changed}], seed="fixed")


if __name__ == "__main__":
    unittest.main()
