"""Authored correction controls; no natural evaluation expectations."""
from dataclasses import replace
import unittest

from temporal_state.bounded import DayBounds, EvidenceSpan, TemporalClaim
from temporal_state.corrections import (
    CorrectionEvidence, CorrectionPolicy, SourceAuthority, SupportDependency,
    make_correction_state_semantics, materialize_corrected_selection,
    validate_correction_policy,
)
from temporal_state.coupled import TemporalInfeasible
from temporal_state.decoder import Link, Mention, Problem, Reading
from temporal_state.models import Question, Source


def fixture(values=("A", "B"), relations=((1, 0, "CORRECTS"),), same_day=False):
    sources, mentions, claims, authorities = [], [], {}, []
    for i, value in enumerate(values):
        text = f"Org: statement {i}: {value}; explicit correction or quotation of the identified statement."
        sid, mid, day = f"s{i}", f"m{i}", f"2024-01-{10 if same_day else 10 + i:02d}"
        sources.append(Source(sid, text, day))
        claim = TemporalClaim(f"c{i}", sid, "Org", "CEO", value,
                              reported_at=day, start=DayBounds("2024-01-01", "2024-01-01"),
                              end=DayBounds("2024-02-01", "2024-02-01"),
                              evidence_spans=(EvidenceSpan(0, len(text), text),), operation="ASSERT")
        claims[mid, "r"] = claim
        readings = (Reading("r", ("org", "ceo", ""), value, 1,
                            effective_start="2024-01-01", effective_end="2024-02-01"),
                    Reading("u", None, None, 0))
        mentions.append(Mention(mid, sid, 0, len(text), readings))
        authorities.append(SourceAuthority(sid, "Org", (EvidenceSpan(0, 3, "Org"),)))
    links, certificates = [], []
    for i, (later, earlier, kind) in enumerate(relations):
        span = EvidenceSpan(0, len(sources[later].text), sources[later].text)
        link = Link(f"l{i}", f"m{later}", "r", f"m{earlier}", "r", kind, 1,
                    reference_span=(span.start, span.end) if kind == "CORRECTS" else None)
        links.append(link)
        if kind == "CORRECTS":
            certificates.append(CorrectionEvidence(link.link_id, span))
    problem = Problem("2024-02-02", tuple(sources), tuple(mentions), tuple(links), "authored diagnostic")
    readings = {m.mention_id: "r" for m in mentions}
    chosen = {m.mention_id: None for m in mentions}
    chosen.update({link.mention_id: link.link_id for link in links})
    policy = CorrectionPolicy(source_authorities=tuple(authorities), correction_evidence=tuple(certificates))
    return problem, readings, chosen, claims, policy


def answer(memory, day="2024-01-05", mode="announced_schedule"):
    return memory.answer_key(Question("q", "Who is CEO of Org?", day, memory.cutoff),
                             ("Org", "CEO", ""), mode)


def dependency(args, child, parent):
    source = args[0].sources[child]
    return SupportDependency(f"c{child}", f"c{parent}",
                             (EvidenceSpan(0, len(source.text), source.text),))


class CorrectionTests(unittest.TestCase):
    def test_replacement_withdraws_support_without_adding_event_boundary(self):
        args = fixture()
        memory = materialize_corrected_selection(*args)
        self.assertEqual([claim.claim_id for claim in memory.claims], ["c1"])
        self.assertEqual(answer(memory).values, ("B",))
        self.assertEqual(memory.claims[0].start, args[3]["m1", "r"].start)
        self.assertEqual(answer(memory).evidence_source_ids, ("s0", "s1"))
        self.assertNotIn("c0", memory.envelopes)
        self.assertEqual(memory.diagnostics["correction_ledger"]["directly_withdrawn_claim_ids"], ["c0"])

    def test_null_link_keeps_conflict_even_with_certificate(self):
        problem, readings, _, claims, policy = fixture()
        memory = materialize_corrected_selection(problem, readings, {"m0": None, "m1": None}, claims, policy)
        self.assertEqual(answer(memory).status, "indeterminate")

    def test_missing_certificate_is_only_selected_infeasibility(self):
        problem, readings, links, claims, _ = fixture()
        policy = CorrectionPolicy()
        validate_correction_policy(problem, claims, policy)
        with self.assertRaisesRegex(TemporalInfeasible, "certificate"):
            materialize_corrected_selection(problem, readings, links, claims, policy)
        materialize_corrected_selection(problem, readings, {"m0": None, "m1": None}, claims, policy)

    def test_chains_do_not_resurrect_original(self):
        memory = materialize_corrected_selection(*fixture(("A", "B", "C"),
                                                          ((1, 0, "CORRECTS"), (2, 1, "CORRECTS"))))
        self.assertEqual(answer(memory).values, ("C",))
        self.assertEqual(memory.diagnostics["correction_ledger"]["inactive_claim_ids"], ["c0", "c1"])
        self.assertTrue(all(row["notice_effect_retained"] for row in
                            memory.diagnostics["correction_ledger"]["operative_notices"]))
        self.assertEqual(answer(memory).evidence_source_ids, ("s0", "s1", "s2"))

    def test_explicit_new_claim_can_reinstate_value_without_reviving_old_claim(self):
        memory = materialize_corrected_selection(*fixture(("A", "B", "A"),
                                                          ((1, 0, "CORRECTS"), (2, 1, "CORRECTS"))))
        self.assertEqual(answer(memory).values, ("A",))
        self.assertEqual(answer(memory).evidence_assertion_ids, ("c2",))

    def test_sibling_replacements_preserve_conflict_without_latest_winner(self):
        memory = materialize_corrected_selection(*fixture(("A", "B", "C"),
                                                          ((1, 0, "CORRECTS"), (2, 0, "CORRECTS"))))
        self.assertEqual(answer(memory).status, "indeterminate")
        self.assertEqual([claim.claim_id for claim in memory.claims], ["c1", "c2"])

    def test_same_day_reference_permitted_without_intraday_time(self):
        memory = materialize_corrected_selection(*fixture(same_day=True))
        self.assertTrue(memory.diagnostics["correction_ledger"]["operative_notices"][0]["same_day_reference"])

    def test_reference_required_even_when_later_day(self):
        problem, readings, links, claims, policy = fixture()
        problem = replace(problem, links=(replace(problem.links[0], reference_span=None),))
        with self.assertRaisesRegex(ValueError, "reference span"):
            validate_correction_policy(problem, claims, policy)

    def test_reference_quote_must_match_exact_source(self):
        problem, _, _, claims, policy = fixture()
        certificate = replace(policy.correction_evidence[0], reference_span=EvidenceSpan(0, 3, "bad"))
        with self.assertRaisesRegex(ValueError, "exact source"):
            validate_correction_policy(problem, claims, replace(policy, correction_evidence=(certificate,)))

    def test_different_asserting_authority_cannot_withdraw_support(self):
        problem, readings, links, claims, policy = fixture()
        authorities = (policy.source_authorities[0], replace(policy.source_authorities[1], authority_id="Other"))
        with self.assertRaisesRegex(TemporalInfeasible, "authority"):
            materialize_corrected_selection(problem, readings, links, claims, replace(policy, source_authorities=authorities))

    def test_authority_evidence_and_eligible_source_are_required(self):
        problem, _, _, claims, policy = fixture()
        for authority in (replace(policy.source_authorities[0], source_id="future"),
                          replace(policy.source_authorities[0], evidence_spans=())):
            with self.assertRaises(ValueError):
                validate_correction_policy(problem, claims, replace(policy, source_authorities=(authority,)))

    def test_partial_edits_and_retractions_explicitly_rejected(self):
        problem, _, _, claims, policy = fixture()
        for certificate in (replace(policy.correction_evidence[0], action="retract"),
                            replace(policy.correction_evidence[0], coverage="value_only")):
            with self.assertRaisesRegex(ValueError, "whole-assertion"):
                validate_correction_policy(problem, claims, replace(policy, correction_evidence=(certificate,)))

    def test_wrong_scope_does_not_withdraw_neighbor(self):
        problem, readings, links, claims, policy = fixture()
        claims["m1", "r"] = replace(claims["m1", "r"], scope="subsidiary")
        mention = problem.mentions[1]
        mention = replace(mention, readings=(replace(mention.readings[0], key=("org", "ceo", "subsidiary")), mention.readings[1]))
        problem = replace(problem, mentions=(problem.mentions[0], mention))
        with self.assertRaisesRegex(TemporalInfeasible, "scope"):
            materialize_corrected_selection(problem, readings, links, claims, policy)

    def test_later_source_cannot_be_corrected_from_earlier_prefix_source(self):
        problem, readings, links, claims, policy = fixture(same_day=True)
        problem = replace(problem, sources=(replace(problem.sources[0], available_at="2024-01-11"), problem.sources[1]))
        with self.assertRaisesRegex(TemporalInfeasible, "later source"):
            materialize_corrected_selection(problem, readings, links, claims, policy)

    def test_correction_cycle_rejected(self):
        with self.assertRaisesRegex(TemporalInfeasible, "acyclic"):
            materialize_corrected_selection(*fixture(("A", "B"),
                                                     ((1, 0, "CORRECTS"), (0, 1, "CORRECTS")), same_day=True))

    def test_shared_episode_does_not_imply_derivative_withdrawal(self):
        args = fixture(("A", "B", "A"), ((1, 0, "CORRECTS"), (2, 0, "RESTATES")))
        memory = materialize_corrected_selection(*args)
        self.assertEqual(answer(memory).status, "indeterminate")
        self.assertEqual([claim.claim_id for claim in memory.claims], ["c1", "c2"])
        self.assertEqual(memory.diagnostics["correction_ledger"]["inactive_temporal_link_ids"], ["l1"])

    def test_explicit_derivative_withdrawal_cascades(self):
        args = fixture(("A", "B", "A", "A"), ((1, 0, "CORRECTS"), (2, 0, "RESTATES"), (3, 2, "RESTATES")))
        policy = replace(args[4], support_dependencies=(dependency(args, 2, 0), dependency(args, 3, 2)))
        memory = materialize_corrected_selection(*args[:4], policy)
        self.assertEqual([claim.claim_id for claim in memory.claims], ["c1"])
        self.assertEqual(memory.diagnostics["correction_ledger"]["inactive_claim_ids"], ["c0", "c2", "c3"])

    def test_missing_selected_prerequisite_marks_dependent_inactive(self):
        args = fixture(("A", "A"), ())
        policy = replace(args[4], support_dependencies=(dependency(args, 1, 0),))
        memory = materialize_corrected_selection(args[0], {"m0": "u", "m1": "r"}, args[2], args[3], policy)
        self.assertEqual(memory.claims, ())
        self.assertEqual(answer(memory).status, "abstain")
        self.assertEqual(memory.diagnostics["correction_ledger"]["inactive_reasons"]["c1"], ["missing_prerequisite:c0"])

    def test_active_essential_support_keeps_provenance(self):
        args = fixture(("A", "A"), ())
        policy = replace(args[4], support_dependencies=(dependency(args, 1, 0),))
        memory = materialize_corrected_selection(*args[:4], policy)
        self.assertEqual(memory.dependency_source_ids["c1"], ("s0", "s1"))

    def test_essential_dependency_cycle_rejected(self):
        args = fixture(("A", "A"), (), same_day=True)
        policy = replace(args[4], support_dependencies=(dependency(args, 1, 0), dependency(args, 0, 1)))
        with self.assertRaisesRegex(ValueError, "acyclic"):
            validate_correction_policy(args[0], args[3], policy)

    def test_future_dependency_and_unknown_claim_rejected(self):
        args = fixture(("A", "A"), ())
        for item in (dependency(args, 0, 1), replace(dependency(args, 1, 0), prerequisite_claim_id="missing")):
            with self.assertRaises(ValueError):
                validate_correction_policy(args[0], args[3], replace(args[4], support_dependencies=(item,)))

    def test_withdrawn_observations_and_contradictory_restatement_dates_removed(self):
        problem, readings, links, claims, policy = fixture(("A", "B", "A"),
                                                         ((1, 0, "CORRECTS"), (2, 0, "RESTATES")))
        claims["m2", "r"] = replace(claims["m2", "r"], start=DayBounds("2024-01-02", "2024-01-02"))
        mention = problem.mentions[2]
        mention = replace(mention, readings=(replace(mention.readings[0], effective_start="2024-01-02"), mention.readings[1]))
        problem = replace(problem, mentions=problem.mentions[:2] + (mention,))
        memory = materialize_corrected_selection(problem, readings, links, claims, policy)
        self.assertEqual(memory.diagnostics["restatement_components"], [["m1"], ["m2"]])

    def test_planned_replacement_remains_excluded_from_actual_query(self):
        args = fixture()
        claims = {key: replace(claim, modality="announced_future") for key, claim in args[3].items()}
        memory = materialize_corrected_selection(*args[:3], claims, args[4])
        self.assertEqual(answer(memory, mode="reported_actual").status, "abstain")

    def test_hook_returns_only_active_components_and_freezes_claim_mapping(self):
        problem, readings, links, claims, policy = fixture()
        hook = make_correction_state_semantics(problem, claims, policy)
        selected = {m.mention_id: m.readings[0] for m in problem.mentions}
        chosen = (None, problem.links[0])
        claims.clear()
        self.assertEqual(hook(selected, chosen), (("m1",),))

    def test_hook_infeasible_correction_returns_none(self):
        problem, _, _, claims, _ = fixture()
        hook = make_correction_state_semantics(problem, claims, CorrectionPolicy())
        self.assertIsNone(hook({m.mention_id: m.readings[0] for m in problem.mentions}, (None, problem.links[0])))

    def test_original_records_retained_unchanged_in_ledger(self):
        args = fixture()
        memory = materialize_corrected_selection(*args)
        self.assertEqual(memory.diagnostics["correction_ledger"]["selected_claims"][0]["value"], "A")
        self.assertEqual(args[3]["m0", "r"].end, DayBounds("2024-02-01", "2024-02-01"))

    def test_control_provenance_preserves_target_and_declared_contexts(self):
        args = fixture(("A", "B", "C"))
        claims = dict(args[3])
        claims["m0", "r"] = replace(claims["m0", "r"], context_source_ids=("s2",))
        memory = materialize_corrected_selection(*args[:3], claims, args[4])
        self.assertEqual(memory.diagnostics["correction_ledger"]["operative_notices"][0]["control_source_ids"],
                         ["s0", "s1", "s2"])
        self.assertNotIn("c0", memory.envelopes)

    def test_cross_key_derivative_withdrawal_preserves_correction_cause(self):
        args = fixture(("A", "B", "X", "Y"))
        problem, readings, links, claims, policy = args
        mentions = list(problem.mentions)
        for i in (2, 3):
            claims[f"m{i}", "r"] = replace(claims[f"m{i}", "r"], relation="CFO")
            mention = mentions[i]
            mentions[i] = replace(mention, readings=(replace(mention.readings[0], key=("org", "cfo", "")), mention.readings[1]))
        problem = replace(problem, mentions=tuple(mentions))
        policy = replace(policy, support_dependencies=(dependency(args, 2, 0),))
        memory = materialize_corrected_selection(problem, readings, links, claims, policy)
        result = memory.answer_key(Question("q", "Who is CFO?", "2024-01-05", memory.cutoff), ("org", "cfo", ""))
        self.assertEqual(result.values, ("Y",))
        self.assertEqual(result.evidence_source_ids, ("s0", "s1", "s2", "s3"))
        self.assertEqual(result.evidence_assertion_ids, ("c3",))

    def test_missing_prerequisite_preserves_unresolved_choice_context(self):
        args = fixture(("A", "B", "A", "C"), ())
        problem, readings, links, claims, policy = args
        mention = problem.mentions[0]
        mention = replace(mention, readings=(mention.readings[0],
                          replace(mention.readings[1], context_source_ids=("s3",))))
        problem = replace(problem, mentions=(mention,) + problem.mentions[1:])
        policy = replace(policy, support_dependencies=(dependency(args, 1, 0),))
        readings.update({"m0": "u", "m3": "u"})
        memory = materialize_corrected_selection(problem, readings, links, claims, policy)
        result = answer(memory)
        self.assertEqual(result.values, ("A",))
        self.assertEqual(result.evidence_assertion_ids, ("c2",))
        self.assertEqual(result.evidence_source_ids, ("s0", "s1", "s2", "s3"))
        self.assertEqual(memory.diagnostics["correction_ledger"]["inactive_claim_control_source_ids"]["c1"],
                         ["s0", "s1", "s3"])

    def test_missing_prerequisite_preserves_resolved_alternative_claim_context(self):
        args = fixture(("A", "B", "A", "C"), ())
        problem, readings, links, claims, policy = args
        alternative = replace(claims["m0", "r"], claim_id="c0_alt", value="A",
                              context_source_ids=("s3",))
        claims["m0", "alt"] = alternative
        mention = problem.mentions[0]
        mention = replace(mention, readings=mention.readings + (replace(mention.readings[0], reading_id="alt"),))
        problem = replace(problem, mentions=(mention,) + problem.mentions[1:])
        policy = replace(policy, support_dependencies=(dependency(args, 1, 0),))
        readings.update({"m0": "alt", "m3": "u"})
        memory = materialize_corrected_selection(problem, readings, links, claims, policy)
        result = answer(memory)
        self.assertEqual(result.values, ("A",))
        self.assertEqual(result.evidence_source_ids, ("s0", "s1", "s2", "s3"))
        self.assertEqual(memory.diagnostics["correction_ledger"]["inactive_claim_control_source_ids"]["c1"],
                         ["s0", "s1", "s3"])


if __name__ == "__main__":
    unittest.main()
