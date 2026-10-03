"""Authored scoring algebra controls; these do not measure natural accuracy."""
from dataclasses import replace
import unittest

from temporal_state.decoder import Link, Mention, Penalty, Problem, Reading, evaluate_assignment
from temporal_state.models import Source
from temporal_state.objectives import make_objective_function, objective_breakdown, objective_spec


def fixture():
    sources = tuple(Source(f"s{i}", "Org: explicitly replaces or restates an earlier record.",
                           f"2024-01-{10+i:02d}") for i in range(3))
    mentions = tuple(Mention(f"m{i}", f"s{i}", 0, 3,
                             (Reading("r", ("org", "role", ""), "B" if i == 1 else "A", 10+i),
                              Reading("u", None, None, -2)), null_score=1)
                     for i in range(3))
    links = (Link("fix", "m1", "r", "m0", "r", "CORRECTS", 3, reference_span=(0, 3)),
             Link("repeat", "m2", "r", "m0", "r", "RESTATES", 5))
    p = Problem("2024-02-01", sources, mentions, links, "authored objective test")
    selected = {mention.mention_id: mention.readings[0] for mention in p.mentions}
    return p, selected, (None, links[0], links[1]), (("m1",), ("m2",))


def shifted(p, unary_offsets=None, outgoing_offsets=None):
    unary_offsets = unary_offsets or {}
    outgoing_offsets = outgoing_offsets or {}
    mentions = tuple(replace(m,
        readings=tuple(replace(r, unary_score=r.unary_score+unary_offsets.get(m.mention_id, 0))
                       for r in m.readings),
        null_score=m.null_score+outgoing_offsets.get(m.mention_id, 0)) for m in p.mentions)
    links = tuple(replace(link, score=link.score+outgoing_offsets.get(link.mention_id, 0)/p.beta)
                  for link in p.links)
    return replace(p, mentions=mentions, links=links)


def same_selection(p, selected, chosen):
    readings = {(m.mention_id, r.reading_id): r for m in p.mentions for r in m.readings}
    links = {link.link_id: link for link in p.links}
    return ({mid: readings[mid, r.reading_id] for mid, r in selected.items()},
            tuple(links[link.link_id] if link else None for link in chosen))


class ObjectiveTests(unittest.TestCase):
    def test_historical_reproduces_original_arithmetic(self):
        p, selected, chosen, components = fixture()
        old = evaluate_assignment(p, {mid: r.reading_id for mid, r in selected.items()},
                                  {m.mention_id: link.link_id if link else None
                                   for m, link in zip(p.mentions, chosen)})
        self.assertEqual(make_objective_function(p, "historical")(selected, chosen, components), old)
        self.assertEqual(old, 42)

    def test_anchored_terms_remove_only_inactive_advantages(self):
        p, selected, chosen, components = fixture()
        detail = objective_breakdown(p, selected, chosen, components)
        self.assertEqual(detail["inactive_claim_reference_net"], -2)
        self.assertEqual(detail["inactive_temporal_reference_net"], 1)
        self.assertEqual(detail["correction_link_net"], 3)
        self.assertEqual(detail["total"], 26)
        self.assertEqual(detail["historical_total"], 42)
        self.assertEqual(detail["removed_inactive_advantage"], 16)

    def test_inactive_source_and_target_both_neutralize_temporal_edge(self):
        p, selected, chosen, _ = fixture()
        detail = objective_breakdown(p, selected, chosen, (("m1",),))
        self.assertEqual(detail["inactive_claim_reference_net"], -4)
        self.assertEqual(detail["inactive_temporal_reference_net"], 1)

    def test_unresolved_reading_keeps_its_selected_score(self):
        p, selected, _, _ = fixture()
        selected["m0"] = p.mentions[0].readings[1]
        detail = objective_breakdown(p, selected, (None, None, None), (("m1",), ("m2",)))
        self.assertEqual(detail["unresolved_unary_net"], -2)
        self.assertEqual(detail["null_link_score"], 3)

    def test_best_unresolved_reference_includes_penalties_and_deterministic_tie(self):
        p, selected, chosen, components = fixture()
        mention = p.mentions[0]
        mention = replace(mention, readings=mention.readings + (
            Reading("a", None, None, 4, violations=("v",)), Reading("b", None, None, 1)))
        p = replace(p, mentions=(mention, *p.mentions[1:]), penalties=(Penalty("v", 3),))
        detail = objective_breakdown(p, selected, chosen, components)
        self.assertEqual(detail["reading_terms"][0]["reference_reading_id"], "a")
        self.assertEqual(detail["reading_terms"][0]["scored_net"], 1)

    def test_penalties_are_not_scaled_by_beta(self):
        p, selected, chosen, components = fixture()
        p = replace(p, beta=2, penalties=(Penalty("v", 2),),
                    links=tuple(replace(link, violations=("v",)) for link in p.links))
        selected, chosen = same_selection(p, selected, chosen)
        detail = objective_breakdown(p, selected, chosen, components)
        self.assertEqual(detail["correction_link_net"], 4)
        self.assertEqual(detail["inactive_temporal_reference_net"], 1)
        self.assertEqual(detail["link_terms"][2]["selected_net"], 8)

    def test_inactive_replacement_still_has_operative_correction_score(self):
        p, selected, chosen, _ = fixture()
        detail = objective_breakdown(p, selected, chosen, (("m2",),))
        self.assertEqual(detail["correction_link_net"], 3)
        self.assertEqual(detail["link_terms"][1]["status"], "operative_correction")

    def test_no_inactive_support_equals_historical(self):
        p, selected, _, _ = fixture()
        chosen, components = (None, None, None), (("m0",), ("m1",), ("m2",))
        active = make_objective_function(p)(selected, chosen, components)
        historical = make_objective_function(p, "historical")(selected, chosen, components)
        self.assertEqual(active, historical)

    def test_arbitrary_unary_offsets_change_every_state_by_same_constant(self):
        p, selected, chosen, components = fixture()
        offsets = {"m0": 100, "m1": -30, "m2": 7}
        q = shifted(p, unary_offsets=offsets)
        q_selected, q_chosen = same_selection(q, selected, chosen)
        for mode in ("historical", "active_support"):
            before = make_objective_function(p, mode)(selected, chosen, components)
            after = make_objective_function(q, mode)(q_selected, q_chosen, components)
            self.assertEqual(after-before, sum(offsets.values()))

    def test_outgoing_net_offsets_include_null_and_preserve_ranking(self):
        p, selected, chosen, components = fixture()
        p = replace(p, beta=2)
        offsets = {"m0": -8, "m1": 6, "m2": 16}
        q = shifted(p, outgoing_offsets=offsets)
        q_selected, q_chosen = same_selection(q, selected, chosen)
        before = make_objective_function(p)(selected, chosen, components)
        after = make_objective_function(q)(q_selected, q_chosen, components)
        self.assertEqual(after-before, sum(offsets.values()))

    def test_naive_zeroing_fails_unary_offset_invariance_arithmetic_control(self):
        # One active reading contributes u; a withdrawn reading contributes zero.
        # Shifting all unary alternatives at that mention by c should shift both
        # feasible states equally, but naive zeroing changes only the active one.
        active_score, inactive_score, shift = 3, 0, 10
        self.assertNotEqual((active_score+shift)-active_score, inactive_score-inactive_score)

    def test_valid_correction_can_lose_when_positive_target_margin_exceeds_link_gain(self):
        p, selected, chosen, components = fixture()
        scored = make_objective_function(p)
        corrected = scored(selected, (None, chosen[1], None), components)
        uncorrected = scored(selected, (None, None, None), (("m0",), ("m1",), ("m2",)))
        self.assertEqual(corrected-uncorrected, (3-1)-(10-(-2)))
        self.assertLess(corrected, uncorrected)

    def test_negative_support_margin_gives_withdrawal_reward(self):
        p, selected, chosen, components = fixture()
        m0 = p.mentions[0]
        p = replace(p, mentions=(replace(m0, readings=(replace(m0.readings[0], unary_score=-8),
                                                       m0.readings[1])), *p.mentions[1:]))
        selected, chosen = same_selection(p, selected, chosen)
        detail = objective_breakdown(p, selected, (None, chosen[1], None), components)
        self.assertEqual(detail["inactive_unary_advantage_over_reference"], -6)
        self.assertLess(detail["removed_inactive_advantage"], 0)
        self.assertGreater(detail["total"], detail["historical_total"])

    def test_inactive_reading_raw_score_has_no_effect_given_reference_and_semantics(self):
        p, selected, chosen, components = fixture()
        m0 = p.mentions[0]
        q = replace(p, mentions=(replace(m0, readings=(replace(m0.readings[0], unary_score=900),
                                                       m0.readings[1])), *p.mentions[1:]))
        q_selected, q_chosen = same_selection(q, selected, chosen)
        self.assertEqual(make_objective_function(p)(selected, chosen, components),
                         make_objective_function(q)(q_selected, q_chosen, components))

    def test_malformed_component_and_selection_contracts_rejected(self):
        p, selected, chosen, _ = fixture()
        scorer = make_objective_function(p)
        for components in ((("m1",), ("m1",)), (("missing",),), (("m2",), ("m1",)), [["m1"]]):
            with self.assertRaises(ValueError):
                scorer(selected, chosen, components)
        with self.assertRaises(ValueError):
            scorer({**selected, "m0": replace(selected["m0"], unary_score=99)}, chosen, (("m1",),))
        with self.assertRaises(ValueError):
            scorer(selected, (chosen[1], None, None), (("m1",),))

    def test_missing_unresolved_reference_unknown_mode_and_nonfinite_scores_rejected(self):
        p, _, _, _ = fixture()
        with self.assertRaises(ValueError):
            objective_spec("zero")
        with self.assertRaises(ValueError):
            make_objective_function(replace(p, mentions=(replace(p.mentions[0], readings=p.mentions[0].readings[:1]),)))
        with self.assertRaises(ValueError):
            make_objective_function(replace(p, beta=float("nan")))

    def test_spec_is_returned_by_value(self):
        spec = objective_spec()
        spec["mode"] = "mutated"
        self.assertEqual(objective_spec()["mode"], "active_support")


if __name__ == "__main__":
    unittest.main()
