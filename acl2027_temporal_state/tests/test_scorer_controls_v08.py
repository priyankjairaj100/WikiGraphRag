"""Synthetic contract fixtures only; these tests never execute a model."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("scorer_controls_v08", ROOT / "scripts/scorer_controls_v08.py")
controls = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controls)


class ScorerControlContracts(unittest.TestCase):
    def fixture(self, cache="cold"):
        row = {"item_id": "SYNTHETIC_ONLY", "kind": "reading", "condition_id": "yes_no",
               "cache_condition": cache, "process_index": 0,
               "prompt": {"input_token_ids": [11, 22, 33, 44]}}
        request = controls.backend.one_token_request(row["prompt"]["input_token_ids"])
        request["cache_prompt"] = cache == "warm"
        response = {"truncated": False, "tokens_evaluated": 4, "tokens_predicted": 1,
                    "timings": {"cache_n": 0 if cache == "cold" else 3,
                                "prompt_n": 4 if cache == "cold" else 1, "prompt_ms": 1},
                    "completion_probabilities": [{"top_logprobs": [
                        {"id": 9454, "logprob": -0.5}, {"id": 2753, "logprob": -1.5}]}]}
        return row, request, response

    def test_first_item_rule_is_independent_of_scores(self):
        _, rows = controls.plan()
        self.assertEqual([(x["item_id"], x["kind"]) for x in rows[::4]],
                         [("s0001", "link"), ("s0003", "unresolved"), ("s0004", "reading"), ("s0006", "no_link")])
        self.assertEqual(len(rows), 16)

    def test_valid_cold_and_warm_full_token_accounting(self):
        for cache in ("cold", "warm"):
            measured = controls.extract(*self.fixture(cache))
            self.assertEqual(measured["input_tokens"], measured["cache_tokens"] + measured["prompt_tokens"])
            self.assertEqual(measured["logodds"], 1)

    def test_cold_must_actually_evaluate_full_prefix(self):
        row, request, response = self.fixture()
        response["timings"].update(cache_n=1, prompt_n=3)
        with self.assertRaisesRegex(ValueError, "cold request reused"):
            controls.extract(row, request, response)

    def test_warm_must_actually_reuse_prefix(self):
        row, request, response = self.fixture("warm")
        response["timings"].update(cache_n=0, prompt_n=4)
        with self.assertRaisesRegex(ValueError, "warm request did not reuse"):
            controls.extract(row, request, response)

    def test_malformed_prefix_accounting_rejected(self):
        for cache, prompt in ((1, 4), (-1, 5), (4, 0), (False, 4), (0, 4.0)):
            row, request, response = self.fixture()
            response["timings"].update(cache_n=cache, prompt_n=prompt)
            with self.subTest(cache=cache, prompt=prompt), self.assertRaises(ValueError):
                controls.extract(row, request, response)

    def test_truncation_and_generation_length_fail_closed(self):
        for field, value in (("truncated", True), ("tokens_evaluated", 3), ("tokens_predicted", 2)):
            row, request, response = self.fixture()
            response[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                controls.extract(row, request, response)

    def test_hidden_request_policy_changes_rejected(self):
        for field, value in (("temperature", 1), ("cache_prompt", True), ("n_predict", 2), ("logit_bias", [[9454, 100]])):
            row, request, response = self.fixture()
            request[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "request differs"):
                controls.extract(row, request, response)

    def test_unsupported_or_invalid_label_distributions_rejected(self):
        for case in ("duplicate", "missing", "mass", "nonfinite"):
            row, request, response = self.fixture()
            top = response["completion_probabilities"][0]["top_logprobs"]
            if case == "duplicate":
                top.append(deepcopy(top[0]))
            elif case == "missing":
                top.pop()
            elif case == "mass":
                top[0]["logprob"] = top[1]["logprob"] = -0.01
            else:
                top[0]["logprob"] = float("nan")
            with self.subTest(case=case), self.assertRaises((ValueError, KeyError)):
                controls.extract(row, request, response)


if __name__ == "__main__":
    unittest.main()
