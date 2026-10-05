import math
import unittest

from temporal_state.study_analysis_v23 import (
    analyze_rows, clopper_pearson_upper, paired_history_interval,
)


def fixture():
    refusal = {"emitted": False, "joint_correct": False, "unsupported": False,
               "status": "completed", "output_audited": True}
    rows = []
    for h in range(200):
        for q in range(6):
            proposed = dict(refusal)
            # Nonconstant issuer effects, positive primary effect, zero unsafe histories.
            if q == 0 and h % 2 == 0:
                proposed.update(emitted=True, joint_correct=True)
            rows.append({"history_id": f"h{h:03d}", "issuer_id": f"i{h:03d}",
                         "item_id": str(q), "proposed": proposed,
                         "comparator": dict(refusal)})
    return rows


class StudyAnalysisTests(unittest.TestCase):
    def test_exact_risk_boundary_and_invalid_counts(self):
        self.assertAlmostEqual(clopper_pearson_upper(0, 200), 1 - .05 ** (1 / 200))
        self.assertLess(clopper_pearson_upper(4, 200), .05)
        self.assertGreater(clopper_pearson_upper(5, 200), .05)
        self.assertEqual(clopper_pearson_upper(200, 200), 1)
        for args in [(True, 10), (1, 0), (11, 10), (-1, 10)]:
            with self.assertRaises(ValueError):
                clopper_pearson_upper(*args)

    def test_history_pairing_and_undefined_abstain_all_risk(self):
        result = analyze_rows(fixture())
        self.assertTrue(result["statistical_criteria_pass"])
        self.assertAlmostEqual(result["effect"]["mean_gain"], 1 / 12)
        self.assertEqual(result["effect"]["histories"], 200)
        self.assertIsNone(result["arms"]["comparator"]["selective_joint_error_point_estimate"])
        self.assertIn("not_determined", result["claim_admission"])

    def test_practical_gain_boundary_uses_exact_supported_answer_counts(self):
        for improved_histories, expected in [(60, True), (59, False)]:
            with self.subTest(improved_histories=improved_histories):
                rows = fixture()
                for index, row in enumerate(rows):
                    history, question = divmod(index, 6)
                    for arm in ("proposed", "comparator"):
                        correct = question < (4 if arm == "proposed"
                                              and history < improved_histories else 3)
                        row[arm].update(emitted=correct, joint_correct=correct)
                result = analyze_rows(rows)
                effect = result["effect"]
                self.assertEqual(effect["net_supported_answers"], improved_histories)
                self.assertEqual(effect["practical_gain_pass"], expected)
                self.assertEqual(result["statistical_criteria_pass"], expected)
                if improved_histories == 60:
                    self.assertEqual(effect["mean_gain"], .05)
                    self.assertEqual(effect["mean_gain_exact"], "1/20")

    def test_repeated_issuers_or_dropped_rows_are_rejected(self):
        rows = fixture()
        with self.assertRaises(ValueError):
            analyze_rows(rows[:-1])
        for row in rows[6:12]:
            row["issuer_id"] = rows[0]["issuer_id"]
        with self.assertRaises(ValueError):
            analyze_rows(rows)

    def test_unknown_outputs_cannot_manufacture_safe_refusals(self):
        rows = fixture()
        for h in range(5):
            rows[h * 6 + 1]["proposed"].update(
                status="technical_failure", output_audited=False)
        result = analyze_rows(rows)
        self.assertFalse(result["statistical_criteria_pass"])
        arm = result["arms"]["proposed"]
        self.assertEqual(arm["unsafe_histories_for_gate"], 5)
        self.assertEqual(arm["observed_unsupported_histories"], 0)
        self.assertEqual(arm["technical_failures"], 5)

    def test_zero_variance_cannot_create_inferential_success(self):
        result = paired_history_interval([.1] * 200)
        self.assertFalse(result["superiority_pass"])
        self.assertIsNone(result["one_sided_95pct_t_lower"])

    def test_t_bound_matches_closed_form_one_degree_of_freedom(self):
        # Student t with one degree of freedom is Cauchy; independently available
        # quantile tan(pi * (0.95 - 0.5)) and SE=0.5 for the pair [0, 1].
        result = paired_history_interval([0., 1.])
        expected = .5 - .5 * math.tan(math.pi * .45)
        self.assertAlmostEqual(result["one_sided_95pct_t_lower"], expected, places=8)

    def test_bad_grade_and_nonfinite_input_are_rejected(self):
        rows = fixture()
        rows[0]["proposed"]["unsupported"] = True
        with self.assertRaises(ValueError):
            analyze_rows(rows)
        with self.assertRaises(ValueError):
            paired_history_interval([0., math.nan])

    def test_unattempted_emissions_and_ignored_fields_are_rejected(self):
        rows = fixture()
        rows[0]["proposed"].update(status="unattempted", joint_correct=False)
        with self.assertRaises(ValueError):
            analyze_rows(rows)
        rows = fixture()
        rows[0]["proposed"]["ignored_unsafe_flag"] = True
        with self.assertRaises(ValueError):
            analyze_rows(rows)


if __name__ == "__main__":
    unittest.main()
