"""Independent invented scorer controls; no natural/private input access."""
from copy import deepcopy
import unittest

from temporal_state.reader_binding_v18 import ASPECTS
from temporal_state.reader_joint_scoring_v19 import blind_permutation, score_output
import test_reader_joint_scoring_v19 as authored


class IndependentJointScorerControls(unittest.TestCase):
    def test_failed_material_stays_review_required_and_blindable_for_both_arms(self):
        p = authored.pack()
        target = authored.reference(p, (), action="insufficient")
        for kind, output in (("direct", authored.direct(p)),
                             ("proposal", authored.proposal(p))):
            with self.subTest(kind=kind):
                result = score_output(p, output, target, output_kind=kind,
                                      execution_status="failed", output_limited=True)
                self.assertEqual(result["projection"]["action"], "execution_failed")
                self.assertEqual(result["audit"]["material_answer_status"], "unresolved_requires_review")
                self.assertEqual(len(result["audit"]["raw_sha256"]), 64)
                self.assertIsNone(result["score"]["unsupported_material_answer"])
                self.assertIsNone(result["score"]["unmatched_material_quantity_count"])
                self.assertFalse(result["score"]["joint_correct"])
                self.assertFalse(result["score"]["safe_incorrect_clarification"])
                blinded = blind_permutation([{"output_id": "private", "projection": result["projection"]}], seed="fixed")
                self.assertEqual(len(blinded["grader_records"]), 1)
                self.assertEqual(blinded["grader_records"][0]["projection"]["action"], "execution_failed")

    def test_alias_collapse_does_not_erase_wrong_source_or_requested_denominator(self):
        p = authored.pack(authored.fact(), authored.fact("f2"), authored.fact("f3", source="b"))
        output = authored.direct(p, ("f1", "f2", "f3"))
        complete = authored.reference(p, (("f1", "f2"), ("f3",)))
        result = score_output(p, output, complete, output_kind="direct")
        self.assertEqual(result["audit"]["raw_emitted_quantity_count"], 3)
        self.assertEqual(result["audit"]["normalized_quantity_count"], 2)
        self.assertEqual(result["audit"]["collapsed_duplicate_quantity_count"], 1)
        self.assertEqual(result["score"]["requested_claim_count"], 2)
        self.assertEqual(result["score"]["matched_quantity_claim_count"], 2)
        self.assertTrue(result["score"]["joint_correct"])
        narrower = authored.reference(p, (("f1", "f2"),))
        extra = score_output(p, output, narrower, output_kind="direct")
        self.assertFalse(extra["score"]["joint_correct"])
        self.assertEqual(extra["score"]["unmatched_material_quantity_count"], 1)
        self.assertFalse(extra["score"]["unsupported_material_answer"])
        self.assertFalse(extra["score"]["safe_incorrect_clarification"])

    def test_correlated_citations_fail_identically_across_common_arm_projection(self):
        fact, witnesses = authored.fact()
        for i, aspect in enumerate(ASPECTS):
            handle = "separate" + str(i)
            witnesses.append({"handle": handle, "aspect": aspect, "text": "Invented second coherent option"})
            fact["bindings"][aspect]["witnesses"].append(handle)
        p = authored.pack((fact, witnesses))
        target = authored.reference(p)
        options = [{a: [authored.citations(p, "f1")[a][i]] for a in ASPECTS} for i in (0, 1)]
        target["claims"][0]["realizations"][0]["sufficient_citation_sets"] = options
        mixed = {a: options[i % 2][a] for i, a in enumerate(ASPECTS)}
        direct = authored.direct(p)
        proposal = authored.proposal(p)
        direct["quantities"][0]["binding_witnesses"] = deepcopy(mixed)
        proposal["hypotheses"][0]["claims"][0]["binding_witnesses"] = deepcopy(mixed)
        a = score_output(p, direct, target, output_kind="direct")
        b = score_output(p, proposal, target, output_kind="proposal")
        self.assertEqual(a["projection"], b["projection"])
        self.assertEqual(a["score"], b["score"])
        self.assertFalse(a["score"]["joint_correct"])
        self.assertEqual(a["score"]["missing_requested_claim_count"], 1)

    def test_invalid_refusal_cannot_claim_zero_material_risk(self):
        p = authored.pack()
        target = authored.reference(p, (), action="insufficient")
        output = authored.direct(p, action="insufficient")
        output["unrecognized_comment"] = "Invented amount still asserted"
        result = score_output(p, output, target, output_kind="direct")
        self.assertFalse(result["score"]["joint_correct"])
        self.assertFalse(result["score"]["correct_insufficient"])
        self.assertFalse(result["score"]["safe_incorrect_clarification"])
        self.assertIsNone(result["score"]["unsupported_material_answer"])
        self.assertIsNone(result["audit"]["exposed_unsupported_quantity_count"])
        self.assertEqual(result["score"]["material_answer_status"], "unresolved_requires_review")
        self.assertEqual(len(result["audit"]["raw_sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
