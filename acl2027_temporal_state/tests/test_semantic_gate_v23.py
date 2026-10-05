"""Authored contract tests; fictional reviewer labels are not human evaluation."""
import copy
import unittest

from temporal_state.semantic_gate_v23 import (
    OBLIGATIONS, PILOT_CRITERIA, REQUIRED_ASPECTS, SCHEMA, analyze_pilot, authorize, class_key,
    coverage_decision, digest, input_hash, model_input, text_digest,
    proposal_development_gate, validate_judgments, validate_proposals, verifier_input,
)


def fixture():
    config = {name: digest(name) for name in ("proposer", "verifier", "coverage_protocol")}
    item = {"schema": SCHEMA, "item_id": "authored_control", "question": "What was A's 2020 revenue in USD?",
            "answer_slots": ["answer"], "normalization_spec": "Authored fixture: decimal value string; literal stated scopes; identity relation.",
            "normalization_sha256": text_digest("Authored fixture: decimal value string; literal stated scopes; identity relation."),
            "corpus_policy": {"policy_id": "fixture", "sources": [{"source_id": "S1", "source_sha256": digest("fictional source"),
                "immutable_reference": "authored://fixture"}], "included_regions": [{"source_id": "S1", "locator": "whole", "rationale": "entire authored source"}],
                "excluded_regions": [], "cutoff": "2020-12-31", "scope_definition": "One fictional source; not a real filing"},
            "observed_evidence": [{"evidence_id": "E1", "source_id": "S1", "source_sha256": digest("fictional source"),
                "locator": "whole", "text": "A. Year 2020. Revenue USD 10. All business.",
                "text_sha256": text_digest("A. Year 2020. Revenue USD 10. All business.")}]}
    claim = {"claim_id": "c1", "slot": "answer", "value": "10", "aspects": {
        "concept": "revenue", "entity": "A", "period": "2020", "unit": "USD", "dimensions": {},
        "source_identity": "S1", "comparison_relation": "identity"}, "citations": ["E1"]}
    proposal = {"input_sha256": input_hash(item), "config_sha256": config["proposer"],
                "candidates": [{"candidate_id": "p1", "claims": [claim]}]}
    verification = {"input_sha256": digest(verifier_input(item, proposal, config["proposer"])),
                    "config_sha256": config["verifier"], "judgments": [{"judgment_id": "j1", "candidate_id": "p1", "claim_id": "c1",
                        "verdict": "support", "bindings": {a: ["E1"] for a in ("value", *REQUIRED_ASPECTS)}, "reason": "Authored support fixture"}]}
    audit = {"input_sha256": input_hash(item), "proposal_sha256": digest(proposal),
             "corpus_policy_sha256": digest(item["corpus_policy"]), "protocol_sha256": config["coverage_protocol"],
             "reviewer": {"identity": "fictional-test-only-reviewer", "kind": "human", "independent_of_proposal": True,
                          "independent_of_reference": True}, "status": "complete", "unresolved_classes": [],
             "source_reviews": [{"source_id": "S1", "source_sha256": digest("fictional source"),
                 "inspected_regions": ["whole"], "review_text": "Authored exhaustive source check fixture",
                 "review_sha256": text_digest("Authored exhaustive source check fixture")}],
             "obligations": [{"obligation": name, "status": "satisfied", "source_ids": ["S1"],
                              "finding": "Authored obligation fixture; not an actual review"} for name in OBLIGATIONS]}
    return item, proposal, verification, audit, config


def bundle_fixture():
    item, proposal, verification, audit, config = fixture()
    run = {"item_id": item["item_id"], "proposal": proposal, "verification": verification,
           "coverage_audit": audit, "winner_id": "p1", "raw_output": "10 USD"}
    reference = {"item_id": item["item_id"], "input_sha256": input_hash(item), "class_ids": ["r1"],
                 "scope_status": "complete", "source_reviews": [{"source_id": "S1", "source_sha256": digest("fictional source"),
                     "review_text": "Authored reference source check", "review_sha256": text_digest("Authored reference source check")}],
                 "class_inventory_review": "Only class: A 2020 all-business revenue USD 10; authored fixture"}
    review = {"item_id": item["item_id"], "input_sha256": input_hash(item), "run_sha256": digest(run),
              "protocol_sha256": digest("review protocol"), "history_id": "fictional_A",
              "reviewer": {"identity": "fictional-test-only-reference-reviewer", "kind": "human", "independent_of_models": True,
                           "reference_written_before_prediction": True},
              "reference_record": reference, "reference_complete": True, "reference_class_ids": ["r1"],
              "candidate_matches": [{"candidate_id": "p1", "reference_class_id": "r1", "source_consistent": True}],
              "refutation_labels": [], "exposed_answer": True, "joint_answer_correct": True, "review_text": "Authored scoring fixture"}
    return {"schema": SCHEMA, "manifest": {"status": "frozen", "population": "authored_contract_controls",
                "pilot_criteria_sha256": digest(PILOT_CRITERIA), "configs": config,
                "item_input_hashes": {item["item_id"]: input_hash(item)}, "reference_hashes": {item["item_id"]: digest(reference)},
                "review_protocol_sha256": digest("review protocol")},
            "items": [item], "runs": [run], "reviews": [review]}


class SemanticGateTests(unittest.TestCase):
    def test_default_missing_coverage_blocks_even_fully_supported_candidate(self):
        i, p, v, a, c = fixture()
        self.assertEqual(authorize(i, p, v, None, c, "p1"), {"authorized": False, "reason": "external_audit_missing"})
        self.assertTrue(authorize(i, p, v, a, c, "p1")["authorized"])

    def test_rank_or_boolean_cannot_clear(self):
        i, p, v, a, c = fixture()
        for fake in (True, {"cleared": True, "provenance": "beam_saturated"}, {"status": "complete"}):
            with self.assertRaises(ValueError):
                authorize(i, p, v, fake, c, "p1")

    def test_no_reference_or_parsed_fact_preload(self):
        i, p, v, a, c = fixture()
        self.assertNotIn("item_id", model_input(i))
        i["reference"] = {"value": "10"}
        with self.assertRaises(ValueError):
            model_input(i)

    def test_source_text_tampering_is_rejected(self):
        i, p, v, a, c = fixture()
        i["observed_evidence"][0]["text"] += " changed"
        with self.assertRaises(ValueError):
            authorize(i, p, v, a, c, "p1")

    def test_normalization_instructions_are_supplied_and_hash_bound(self):
        i, p, v, a, c = fixture()
        self.assertIn("normalization_spec", model_input(i))
        i["normalization_spec"] += " changed"
        with self.assertRaises(ValueError):
            model_input(i)

    def test_unacquired_and_missing_scope_premises_rejected(self):
        for mutation in (lambda b: b.pop("entity"), lambda b: b.update(entity=["unseen"])):
            i, p, v, a, c = fixture()
            mutation(v["judgments"][0]["bindings"])
            with self.assertRaises(ValueError):
                validate_judgments(i, p, v, c)

    def test_coverage_model_author_or_missing_region_blocks(self):
        i, p, v, a, c = fixture()
        a["reviewer"]["kind"] = "model"
        self.assertFalse(coverage_decision(i, p, a, c)[0])
        a["reviewer"]["kind"] = "human"
        a["source_reviews"][0]["inspected_regions"] = ["different region"]
        self.assertFalse(coverage_decision(i, p, a, c)[0])

    def test_coverage_obligation_omission_and_hash_changes_rejected(self):
        i, p, v, a, c = fixture()
        a["obligations"].pop()
        with self.assertRaises(ValueError):
            coverage_decision(i, p, a, c)
        i, p, v, a, c = fixture()
        a["proposal_sha256"] = digest("other proposals")
        with self.assertRaises(ValueError):
            coverage_decision(i, p, a, c)

    def test_conflict_blocks_even_with_external_audit(self):
        i, p, v, a, c = fixture()
        refute = copy.deepcopy(v["judgments"][0])
        refute.update(judgment_id="j2", verdict="refute", bindings={"value": ["E1"]})
        v["judgments"].append(refute)
        self.assertEqual(authorize(i, p, v, a, c, "p1")["reason"], "observed_conflict")

    def test_same_number_different_scope_is_different_class(self):
        i, p, v, a, c = fixture()
        rival = copy.deepcopy(p["candidates"][0])
        rival["candidate_id"] = "p2"
        rival["claims"][0]["aspects"]["dimensions"] = {"region": "US"}
        self.assertNotEqual(class_key(rival), class_key(p["candidates"][0]))
        p["candidates"].append(rival)
        v["input_sha256"] = digest(verifier_input(i, p, c["proposer"]))
        a["proposal_sha256"] = digest(p)
        self.assertEqual(authorize(i, p, v, a, c, "p1")["reason"], "unrefuted_rival")

    def test_duplicate_ids_cannot_hide_same_proposition_conflict(self):
        i, p, v, a, c = fixture()
        duplicate = copy.deepcopy(p["candidates"][0])
        duplicate["candidate_id"] = "p2"
        duplicate["claims"][0]["claim_id"] = "different_claim_id"
        p["candidates"].append(duplicate)
        v["input_sha256"] = digest(verifier_input(i, p, c["proposer"]))
        a["proposal_sha256"] = digest(p)
        v["judgments"].append({"judgment_id": "j2", "candidate_id": "p2", "claim_id": "different_claim_id",
                               "verdict": "refute", "bindings": {"value": ["E1"]}, "reason": "Conflicting duplicate proposition"})
        self.assertEqual(authorize(i, p, v, a, c, "p1")["reason"], "observed_conflict")

    def test_complete_joint_slots_required(self):
        i, p, v, a, c = fixture()
        i["answer_slots"].append("previous period")
        p["input_sha256"] = input_hash(i)
        with self.assertRaises(ValueError):
            validate_proposals(i, p, c["proposer"])

    def test_missing_reference_gives_null_not_perfect_metrics(self):
        b = bundle_fixture()
        b["reviews"] = []
        r = analyze_pilot(b)
        self.assertIsNone(r["class_recall"])
        self.assertIsNone(r["joint_bad_and_emit"])
        self.assertIsNone(r["selective_risk"])

    def test_model_authored_reference_not_human_gold(self):
        b = bundle_fixture()
        b["reviews"][0]["reviewer"]["kind"] = "model"
        self.assertIsNone(analyze_pilot(b)["class_recall"])

    def test_reference_cannot_change_after_frozen_hash(self):
        b = bundle_fixture()
        b["reviews"][0]["reference_record"]["class_inventory_review"] += " corrected"
        with self.assertRaises(ValueError):
            analyze_pilot(b)

    def test_invalid_representative_does_not_cover_reference_class(self):
        b = bundle_fixture()
        b["reviews"][0]["candidate_matches"][0]["source_consistent"] = False
        self.assertEqual(analyze_pilot(b)["class_recall"], 0.0)

    def test_abstain_all_has_undefined_selective_risk(self):
        b = bundle_fixture()
        b["runs"][0].update(winner_id=None, raw_output="I cannot determine this.")
        b["reviews"][0].update(run_sha256=digest(b["runs"][0]), exposed_answer=False, joint_answer_correct=None)
        r = analyze_pilot(b)
        self.assertEqual(r["joint_bad_and_emit"], 0.0)
        self.assertIsNone(r["selective_risk"])

    def test_exposed_bad_text_counts_even_without_authorized_winner(self):
        b = bundle_fixture()
        b["runs"][0].update(winner_id=None, raw_output="Malformed JSON, but the answer is 999 USD.")
        b["reviews"][0].update(run_sha256=digest(b["runs"][0]), exposed_answer=True, joint_answer_correct=False)
        r = analyze_pilot(b)
        self.assertEqual(r["joint_bad_and_emit"], 1.0)
        self.assertEqual(r["selective_risk"], 1.0)

    def test_missing_scheduled_run_does_not_disappear(self):
        b = bundle_fixture()
        b["runs"] = []
        b["reviews"] = []
        r = analyze_pilot(b)
        self.assertEqual(r["counts"]["scheduled"], 1)
        self.assertEqual(r["counts"]["missing_runs"], 1)
        self.assertIsNone(r["answer_rate"])

    def test_false_elimination_and_refutation_denominators(self):
        b = bundle_fixture()
        j = b["runs"][0]["verification"]["judgments"][0]
        j.update(verdict="refute", bindings={"value": ["E1"]})
        b["runs"][0].update(winner_id=None, raw_output="Abstain")
        b["reviews"][0].update(run_sha256=digest(b["runs"][0]), exposed_answer=False, joint_answer_correct=None,
                               refutation_labels=[{"judgment_id": "j1", "sound": False}])
        r = analyze_pilot(b)
        self.assertEqual(r["false_refutation_rate"], 1.0)
        self.assertEqual(r["question_false_elimination_rate"], 1.0)

    def test_engineering_gate_stops_on_one_false_elimination(self):
        counts = {"scheduled": 200, "qualified_review_items": 200, "class_recall_eligible_items": 200,
                  "nonempty_reference_items": 100, "questions_with_false_elimination": 0, "reviewed_refutations": 3}
        self.assertTrue(proposal_development_gate(counts, .95, .95, "natural_source_questions")["passed"])
        counts["questions_with_false_elimination"] = 1
        result = proposal_development_gate(counts, 1.0, 1.0, "natural_source_questions")
        self.assertFalse(result["passed"])
        self.assertIn("reviewed_false_candidate_elimination", result["failures"])

    def test_perfect_typed_controls_do_not_pass_natural_pilot(self):
        counts = {"scheduled": 200, "qualified_review_items": 200, "class_recall_eligible_items": 200,
                  "nonempty_reference_items": 100, "questions_with_false_elimination": 0, "reviewed_refutations": 0}
        result = proposal_development_gate(counts, 1.0, 1.0, "typed_controls")
        self.assertFalse(result["passed"])
        self.assertEqual(result["verification_judgment_error_status"], "verification_judgment_error_insufficient_opportunities")
        self.assertFalse(result["automatic_pipeline_comparison_authorized"])

    def test_gate_criteria_change_requires_new_version(self):
        b = bundle_fixture()
        b["manifest"]["pilot_criteria_sha256"] = digest("relaxed after outcome")
        with self.assertRaises(ValueError):
            analyze_pilot(b)


if __name__ == "__main__":
    unittest.main()
