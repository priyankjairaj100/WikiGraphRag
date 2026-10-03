import unittest

from temporal_state.models import Assertion, Question, Source
from temporal_state.memory import build_memory


class MemoryTests(unittest.TestCase):
    cutoff = "2025-12-31"

    def source(self, sid, available="2020-01-01"):
        return Source(sid, f"Diagnostic source {sid}", available)

    def a(self, aid, value, start, **kw):
        return Assertion(aid, kw.pop("source_id", aid), "Acme", "CEO", value, start, **kw)

    def memory(self, assertions, sources=None, cutoff=None):
        sources = sources if sources is not None else [self.source(a.source_id) for a in assertions]
        return build_memory(sources, assertions, cutoff or self.cutoff)

    def ask(self, memory, event="2021-06-01", text="Who was the CEO of Acme?", policy="episodes"):
        return memory.answer(Question("q", text, event, memory.cutoff), policy)

    def test_restatement_does_not_shift_change_boundary(self):
        a = [self.a("a", "Alice", "2020-01-01"), self.a("b", "Bob", "2021-01-01"),
             self.a("c", "Bob", "2022-01-01", operation="RESTATES")]
        memory = self.memory(a)
        self.assertEqual([(e.value, e.start, e.end) for e in memory.episodes],
                         [("Alice", "2020-01-01", "2021-01-01"), ("Bob", "2021-01-01", None)])
        self.assertEqual(self.ask(memory).values, ("Bob",))
        self.assertEqual(memory.episodes[1].assertion_ids, ("b", "c"))

    def test_reversion_is_a_distinct_episode_and_boundary_is_half_open(self):
        a = [self.a("a", "Alice", "2020-01-01"), self.a("b", "Bob", "2021-01-01"),
             self.a("c", "Alice", "2022-01-01")]
        memory = self.memory(a)
        self.assertEqual(len(memory.episodes), 3)
        self.assertEqual(self.ask(memory, "2021-01-01").values, ("Bob",))
        self.assertEqual(self.ask(memory, "2022-01-01").values, ("Alice",))

    def test_explicit_gap_has_no_latest_state_fallback(self):
        a = [self.a("a", "Alice", "2020-01-01", valid_to="2021-01-01"),
             self.a("b", "Bob", "2022-01-01")]
        self.assertEqual(self.ask(self.memory(a)).status, "abstain")

    def test_future_source_and_future_extraction_context_are_filtered(self):
        sources = [self.source("old"), self.source("future", "2026-01-01")]
        a = [self.a("a", "Alice", "2020-01-01", source_id="old"),
             self.a("b", "Bob", "2021-01-01", source_id="future"),
             self.a("leak", "Carol", "2021-01-01", source_id="old", context_source_ids=("future",))]
        memory = self.memory(a, sources)
        self.assertEqual(self.ask(memory).values, ("Alice",))
        self.assertEqual(memory.diagnostics["omitted_future_context_assertion_ids"], ["leak"])
        self.assertEqual(memory.diagnostics["omitted_future_assertion_ids"], ["b"])

    def test_future_effective_appointment_is_not_current(self):
        a = [self.a("a", "Alice", "2020-01-01"), self.a("b", "Bob", "2023-01-01")]
        sources = [self.source("a"), self.source("b", "2021-01-01")]
        memory = self.memory(a, sources)
        self.assertEqual(self.ask(memory).values, ("Alice",))
        self.assertEqual(self.ask(memory, policy="newest_available").values, ("Bob",))

    def test_correction_replaces_date_without_new_transition(self):
        a = [self.a("a", "Alice", "2020-01-01"), self.a("b", "Bob", "2022-01-01"),
             self.a("fix", "Bob", "2021-01-01", operation="CORRECTS", target_id="b")]
        sources = [self.source("a"), self.source("b"), self.source("fix", "2024-01-01")]
        before = self.memory(a, sources, "2023-12-31")
        after = self.memory(a, sources)
        self.assertEqual(self.ask(before).values, ("Alice",))
        self.assertEqual(self.ask(after).values, ("Bob",))
        self.assertEqual(len(after.episodes), 2)
        self.assertEqual(after.episodes[1].assertion_ids, ("fix",))
        self.assertEqual(after.diagnostics["correction_lineage"], {"fix": "b"})
        self.assertEqual({x.assertion_id for x in after.assertions}, {"a", "b", "fix"})

    def test_simultaneous_conflict_has_no_arbitrary_winner(self):
        a = [self.a("a", "Alice", "2020-01-01"), self.a("b", "Bob", "2020-01-01")]
        prediction = self.ask(self.memory(a))
        self.assertEqual((prediction.status, prediction.values), ("indeterminate", ()))
        self.assertEqual(prediction.evidence_source_ids, ("a", "b"))

    def test_unknown_date_remains_unresolved(self):
        a = [self.a("a", "Alice", "2020-01-01"), self.a("b", "Bob", None)]
        self.assertEqual(self.ask(self.memory(a)).status, "indeterminate")

    def test_explicit_overlap_is_preserved_as_conflict(self):
        a = [self.a("a", "Alice", "2020-01-01", valid_to="2022-01-01"),
             self.a("b", "Bob", "2021-01-01")]
        self.assertEqual(self.ask(self.memory(a)).status, "indeterminate")

    def test_raw_text_routing_ignores_distractor_keys(self):
        a = [self.a("a", "Alice", "2020-01-01"),
             Assertion("b", "b", "Other", "CEO", "Bob", "2020-01-01")]
        memory = self.memory(a)
        self.assertEqual(self.ask(memory).values, ("Alice",))
        self.assertEqual(self.ask(memory, text="Who was the chief executive officer of Acme?").values, ("Alice",))
        self.assertEqual(self.ask(memory, text="Who was the CEO of Unknown?").status, "abstain")

    def test_input_order_does_not_change_episodes(self):
        a = [self.a("a", "Alice", "2020-01-01"), self.a("b", "Bob", "2021-01-01"),
             self.a("c", "Bob", "2022-01-01")]
        self.assertEqual(self.memory(a).episodes, self.memory(list(reversed(a))).episodes)

    def test_rejects_duplicate_ids_dangling_refs_cycles_and_cross_key_corrections(self):
        a = self.a("a", "Alice", "2020-01-01")
        with self.assertRaises(ValueError):
            self.memory([a, a], [self.source("a")])
        with self.assertRaises(ValueError):
            self.memory([a], [self.source("a"), self.source("a")])
        with self.assertRaises(ValueError):
            self.memory([a], [])
        with self.assertRaises(ValueError):
            self.memory([self.a("a", "Alice", "2020-01-01", context_source_ids=("missing",))])
        with self.assertRaises(ValueError):
            self.memory([self.a("a", "Alice", "2020-01-01", operation="CORRECTS", target_id="missing")])
        with self.assertRaises(ValueError):
            self.memory([self.a("a", "Alice", "2020-01-01", operation="CORRECTS", target_id="b"),
                         self.a("b", "Bob", "2020-01-01", operation="CORRECTS", target_id="a")])
        with self.assertRaises(ValueError):
            self.memory([a, Assertion("b", "b", "Other", "CEO", "Bob", "2020-01-01",
                                      operation="CORRECTS", target_id="a")])

    def test_cutoff_mismatch_and_unknown_policy_rejected(self):
        memory = self.memory([self.a("a", "Alice", "2020-01-01")])
        with self.assertRaises(ValueError):
            memory.answer(Question("q", "CEO of Acme", "2021-01-01", "2020-01-01"))
        with self.assertRaises(ValueError):
            self.ask(memory, policy="unsupported")

    def test_correction_chain_and_branching_corrections(self):
        a = [self.a("a", "Alice", "2020-01-01"),
             self.a("b", "Bob", "2020-01-01", operation="CORRECTS", target_id="a"),
             self.a("c", "Carol", "2020-01-01", operation="CORRECTS", target_id="b")]
        self.assertEqual(self.ask(self.memory(a)).values, ("Carol",))
        branch = a[:2] + [self.a("c", "Carol", "2020-01-01", operation="CORRECTS", target_id="a")]
        self.assertEqual(self.ask(self.memory(branch)).status, "indeterminate")

    def test_legacy_policy_alias_explicitly_reports_availability_semantics(self):
        memory = self.memory([self.a("a", "Alice", "2020-01-01")])
        prediction = self.ask(memory, policy="newest_publication")
        self.assertEqual(prediction.diagnostics["policy"], "newest_available")
        self.assertEqual(prediction.diagnostics["legacy_policy_alias"], "newest_publication")

    def test_same_value_explicit_gap_is_not_coalesced(self):
        a = [self.a("a", "Alice", "2020-01-01", valid_to="2020-01-10"),
             self.a("b", "Alice", "2020-01-15", valid_to="2020-01-20")]
        memory = self.memory(a)
        self.assertEqual(len(memory.episodes), 2)
        self.assertEqual(self.ask(memory, "2020-01-12").status, "abstain")
        self.assertEqual(self.ask(memory, "2020-01-15").values, ("Alice",))

    def test_open_same_value_restatement_does_not_extend_explicit_end(self):
        a = [self.a("a", "Alice", "2020-01-01", valid_to="2020-02-01"),
             self.a("b", "Alice", "2020-01-15")]
        memory = self.memory(a)
        self.assertEqual([(e.start, e.end) for e in memory.episodes],
                         [("2020-01-01", "2020-02-01")])
        self.assertEqual(self.ask(memory, "2020-02-01").status, "abstain")
        self.assertEqual(memory.diagnostics["inherited_explicit_end_by_assertion"], {"b": "2020-02-01"})

    def test_later_explicit_end_bounds_earlier_open_same_value_episode(self):
        a = [self.a("a", "Alice", "2020-01-01"),
             self.a("b", "Alice", "2020-01-15", valid_to="2020-02-01")]
        memory = self.memory(a)
        self.assertEqual(memory.episodes[0].end, "2020-02-01")
        self.assertEqual(self.ask(memory, "2020-02-01").status, "abstain")

    def test_correction_cannot_reference_later_available_source(self):
        a = [self.a("a", "Alice", "2020-01-01"),
             self.a("b", "Bob", "2020-01-01", operation="CORRECTS", target_id="a")]
        sources = [self.source("a", "2022-01-01"), self.source("b", "2021-01-01")]
        with self.assertRaisesRegex(ValueError, "cannot predate"):
            self.memory(a, sources)
        same_day = self.memory(a)
        self.assertEqual(same_day.diagnostics["same_day_correction_assertion_ids"], ["b"])

    def test_support_quote_must_be_exact_even_for_future_input(self):
        good = self.a("a", "Alice", "2020-01-01", support_quote="Diagnostic source")
        self.assertEqual(self.ask(self.memory([good])).values, ("Alice",))
        bad = self.a("a", "Alice", "2020-01-01", support_quote="fabricated quotation")
        with self.assertRaisesRegex(ValueError, "exact source span"):
            self.memory([bad], [self.source("a", "2026-01-01")])


if __name__ == "__main__":
    unittest.main()
