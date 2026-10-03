"""Authored contract tests, not natural-data answer-level evaluation."""
from copy import deepcopy
from dataclasses import asdict, replace
import unittest

from temporal_state.bounded import DayBounds, EvidenceSpan, TemporalClaim
from temporal_state.corrections import (
    CorrectionEvidence, CorrectionPolicy, SourceAuthority, SupportDependency,
    materialize_corrected_selection,
)
from temporal_state.decoder import Link, Mention, Problem, Reading
from temporal_state.models import Source
from temporal_state.support_answers import SupportQuery, answer_support


def fixture():
    # IDs deliberately have opaque punctuation: no semantic parsing is valid.
    ids = ("a:old:17", "b:new:2", "c:derived:3", "d:control:4", "e:chain:5")
    texts = (
        "Lab: input is 10.",
        "Lab: correction: input was 10; replace that statement with input is 12.",
        "Lab: reported result depends essentially on the old input statement.",
        "Lab: facility is in Delhi, independently of the input measurement.",
        "Lab: final result depends essentially on the reported result.",
    )
    values = ("10", "12", "reported result", "Delhi", "final result")
    relations = ("input", "input", "result", "location", "final")
    sources, mentions, claims, authorities = [], [], {}, []
    for i, (cid, text, value, relation) in enumerate(zip(ids, texts, values, relations)):
        sid, mid = f"source {i}", f"mention {i}"
        sources.append(Source(sid, text, "2024-01-10", uri=f"https://example.invalid/{i}"))
        evidence = (EvidenceSpan(0, len(text), text),)
        claim = TemporalClaim(cid, sid, "Lab", relation, value, evidence_spans=evidence)
        claims[mid, "chosen"] = claim
        mentions.append(Mention(mid, sid, 0, len(text),
                        (Reading("chosen", claim.key, value, 0.0), Reading("unknown", None, None, 0.0))))
        authorities.append(SourceAuthority(sid, "Lab", (EvidenceSpan(0, 3, "Lab"),)))
    ref = EvidenceSpan(0, len(texts[1]), texts[1])
    link = Link("link:replace:1", "mention 1", "chosen", "mention 0", "chosen", "CORRECTS", 0.0,
                reference_span=(ref.start, ref.end))
    problem = Problem("2024-01-10", tuple(sources), tuple(mentions), (link,), "authored; not scores")
    reading_ids = {mention.mention_id: "chosen" for mention in mentions}
    link_ids = {mention.mention_id: None for mention in mentions}
    link_ids["mention 1"] = link.link_id
    policy = CorrectionPolicy(source_authorities=tuple(authorities),
        correction_evidence=(CorrectionEvidence(link.link_id, ref),),
        support_dependencies=(SupportDependency(ids[2], ids[0], claims["mention 2", "chosen"].evidence_spans),
                              SupportDependency(ids[4], ids[2], claims["mention 4", "chosen"].evidence_spans)))
    return dict(problem=problem, reading_ids=reading_ids, link_ids=link_ids, claims=claims, policy=policy)


def lookup(args, cid, memory=None):
    if memory is None:
        memory = materialize_corrected_selection(**args)
    return answer_support(SupportQuery(cid), memory=memory, **args)


class SupportAnswersTests(unittest.TestCase):
    def test_active_support_preserves_unknown_event_bounds_and_modality(self):
        args = fixture()
        args["claims"]["mention 1", "chosen"] = replace(
            args["claims"]["mention 1", "chosen"], modality="uncertain", polarity="negative")
        result = lookup(args, "b:new:2")
        self.assertEqual(result.status, "active_support")
        self.assertEqual(result.selected_assertion.start, DayBounds())
        self.assertEqual(result.selected_assertion.end, DayBounds())
        self.assertEqual(result.selected_assertion.state_observed_at, ())
        self.assertEqual(result.selected_assertion.modality, "uncertain")
        self.assertEqual(result.selected_assertion.polarity, "negative")
        self.assertEqual([reason.kind for reason in result.reasons], ["selected_active"])

    def test_direct_withdrawal_returns_structured_certificate_and_authority(self):
        result = lookup(fixture(), "a:old:17")
        self.assertEqual(result.status, "withdrawn_support")
        self.assertEqual(result.selected_assertion.value, "10")
        self.assertEqual(result.reasons[0].kind, "explicit_replacement")
        self.assertEqual(result.reasons[0].related_assertion_id, "b:new:2")
        self.assertEqual(result.reasons[0].correction_link_id, "link:replace:1")
        certificate = [item for item in result.evidence if item.role == "correction_reference"]
        self.assertEqual(len(certificate), 1)
        self.assertEqual(certificate[0].source_id, "source 1")
        self.assertEqual(certificate[0].assertion_ids, ("b:new:2", "a:old:17"))
        authority = [item for item in result.evidence if item.role == "source_authority"]
        self.assertEqual({item.authority_id for item in authority}, {"Lab"})

    def test_dependency_chain_carries_correction_cause_without_unrelated_control(self):
        result = lookup(fixture(), "e:chain:5")
        self.assertEqual(result.status, "withdrawn_support")
        self.assertEqual(result.reasons[0].kind, "inactive_prerequisite")
        self.assertEqual(result.reasons[0].related_assertion_id, "c:derived:3")
        self.assertEqual(result.evidence_source_ids, ("source 0", "source 1", "source 2", "source 4"))
        edges = [item.assertion_ids for item in result.evidence if item.role == "essential_dependency"]
        self.assertEqual(set(edges), {("e:chain:5", "c:derived:3"), ("c:derived:3", "a:old:17")})
        self.assertEqual(len([item for item in result.evidence if item.role == "correction_reference"]), 1)

    def test_unaffected_control_retains_original_exact_endpoints(self):
        args = fixture()
        claim = replace(args["claims"]["mention 3", "chosen"],
                        start=DayBounds("2024-01-01", "2024-01-01"),
                        end=DayBounds("2024-02-01", "2024-02-01"))
        args["claims"]["mention 3", "chosen"] = claim
        result = lookup(args, claim.claim_id)
        self.assertEqual(result.status, "active_support")
        self.assertIs(result.selected_assertion, claim)
        self.assertEqual(result.evidence_source_ids, ("source 3",))
        self.assertEqual(result.context_source_ids, ("source 3",))

    def test_missing_selected_prerequisite_is_not_reported_as_false(self):
        args = fixture()
        args["reading_ids"]["mention 0"] = "unknown"
        args["link_ids"]["mention 1"] = None
        result = lookup(args, "c:derived:3")
        self.assertEqual(result.status, "withdrawn_support")
        self.assertEqual(result.reasons[0].kind, "missing_prerequisite")
        self.assertEqual(result.reasons[0].related_assertion_id, "a:old:17")
        self.assertNotIn("source 0", result.evidence_source_ids)
        self.assertIn("source 0", result.context_source_ids)

    def test_unknown_and_known_unselected_are_distinct_unresolved_states(self):
        args = fixture()
        args["reading_ids"]["mention 3"] = "unknown"
        for cid, reason in (("d:control:4", "assertion_not_selected"), ("not in graph", "unknown_assertion")):
            with self.subTest(cid=cid):
                result = lookup(args, cid)
                self.assertEqual(result.status, "unresolved")
                self.assertEqual(result.reasons[0].kind, reason)
                self.assertIsNone(result.selected_assertion)
                self.assertEqual(result.evidence, ())

    def test_missing_prerequisite_does_not_activate_its_unselected_dependency_edges(self):
        args = fixture()
        args["reading_ids"]["mention 2"] = "unknown"
        result = lookup(args, "e:chain:5")
        self.assertEqual(result.reasons[0].kind, "missing_prerequisite")
        self.assertEqual(result.reasons[0].related_assertion_id, "c:derived:3")
        self.assertEqual(result.evidence_source_ids, ("source 4",))
        self.assertEqual(result.context_source_ids, ("source 2", "source 4"))
        self.assertFalse(any(item.role == "correction_reference" for item in result.evidence))

    def test_direct_and_dependency_reasons_are_both_preserved_when_redundant(self):
        args = fixture()
        claim = replace(args["claims"]["mention 3", "chosen"], relation="result", value="revised result")
        args["claims"]["mention 3", "chosen"] = claim
        mentions = list(args["problem"].mentions)
        mentions[3] = replace(mentions[3], readings=(replace(mentions[3].readings[0], key=claim.key, value=claim.value),
                                                   mentions[3].readings[1]))
        ref = claim.evidence_spans[0]
        direct = Link("direct:dependent", "mention 3", "chosen", "mention 2", "chosen", "CORRECTS", 0.0,
                      reference_span=(ref.start, ref.end))
        args["problem"] = replace(args["problem"], mentions=tuple(mentions), links=args["problem"].links + (direct,))
        args["link_ids"]["mention 3"] = direct.link_id
        args["policy"] = replace(args["policy"], correction_evidence=args["policy"].correction_evidence +
                                 (CorrectionEvidence(direct.link_id, ref),))
        result = lookup(args, "c:derived:3")
        self.assertEqual(result.status, "withdrawn_support")
        self.assertEqual({reason.kind for reason in result.reasons}, {"explicit_replacement", "inactive_prerequisite"})
        self.assertEqual({item.correction_link_id for item in result.evidence if item.role == "correction_reference"},
                         {"link:replace:1", "direct:dependent"})

    def test_no_withdrawal_does_not_arbitrate_competing_assertions(self):
        args = fixture()
        args["link_ids"]["mention 1"] = None
        self.assertEqual(lookup(args, "a:old:17").status, "active_support")
        self.assertEqual(lookup(args, "b:new:2").status, "active_support")
        self.assertEqual(lookup(args, "c:derived:3").status, "active_support")

    def test_chain_does_not_revive_original_target(self):
        args = fixture()
        old = args["claims"]["mention 3", "chosen"]
        newer = replace(old, relation="input", value="14")
        args["claims"]["mention 3", "chosen"] = newer
        mentions = list(args["problem"].mentions)
        mentions[3] = replace(mentions[3], readings=(replace(mentions[3].readings[0], key=newer.key, value="14"),
                                                   mentions[3].readings[1]))
        ref = newer.evidence_spans[0]
        second = Link("second", "mention 3", "chosen", "mention 1", "chosen", "CORRECTS", 0.0,
                      reference_span=(ref.start, ref.end))
        args["problem"] = replace(args["problem"], mentions=tuple(mentions), links=args["problem"].links + (second,))
        args["link_ids"]["mention 3"] = "second"
        args["policy"] = replace(args["policy"], correction_evidence=args["policy"].correction_evidence +
                                 (CorrectionEvidence("second", ref),))
        self.assertEqual(lookup(args, "a:old:17").status, "withdrawn_support")
        self.assertEqual(lookup(args, "b:new:2").status, "withdrawn_support")
        self.assertEqual(lookup(args, "d:control:4").status, "active_support")

    def test_forged_status_or_missing_ledger_is_rejected_even_for_unknown_query(self):
        args = fixture()
        memory = materialize_corrected_selection(**args)
        for change in ("status", "remove"):
            forged = deepcopy(memory)
            if change == "status":
                forged.diagnostics["correction_ledger"]["active_claim_ids"].append("a:old:17")
            else:
                del forged.diagnostics["correction_ledger"]
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "differs"):
                lookup(args, "unknown", forged)

    def test_same_id_different_source_text_or_metadata_is_rejected(self):
        args = fixture()
        memory = materialize_corrected_selection(**args)
        for replacement in (replace(memory.sources[0], text=memory.sources[0].text + " extra"),
                            replace(memory.sources[0], uri="https://other.invalid/")):
            forged = replace(memory, sources=(replacement,) + memory.sources[1:])
            with self.subTest(replacement=replacement), self.assertRaisesRegex(ValueError, "source identity"):
                lookup(args, "a:old:17", forged)

    def test_bool_integer_ledger_forgery_is_rejected(self):
        args = fixture()
        memory = materialize_corrected_selection(**args)
        forged = deepcopy(memory)
        forged.diagnostics["correction_ledger"]["operative_notices"][0]["same_day_reference"] = 1
        self.assertEqual(forged, memory)  # Ordinary dataclass equality is insufficient.
        with self.assertRaisesRegex(ValueError, "differs"):
            lookup(args, "a:old:17", forged)

    def test_invalid_evidence_and_unsupported_policy_fail_in_core_validation(self):
        args = fixture()
        args["policy"] = replace(args["policy"], correction_evidence=(
            replace(args["policy"].correction_evidence[0], coverage="value_only"),))
        # A valid supplied memory cannot authorize unsupported replacement semantics.
        valid = materialize_corrected_selection(**fixture())
        with self.assertRaisesRegex(ValueError, "whole-assertion"):
            lookup(args, "a:old:17", valid)
        args = fixture()
        args["policy"] = replace(args["policy"], source_authorities=(
            replace(args["policy"].source_authorities[0], evidence_spans=(EvidenceSpan(0, 3, "Bad"),)),))
        with self.assertRaisesRegex(ValueError, "exact source"):
            lookup(args, "a:old:17", valid)

    def test_untyped_query_memory_and_claim_map_are_rejected(self):
        args = fixture()
        memory = materialize_corrected_selection(**args)
        with self.assertRaisesRegex(ValueError, "SupportQuery"):
            answer_support("a:old:17", memory=memory, **args)
        with self.assertRaisesRegex(ValueError, "typed core"):
            lookup(args, "a:old:17", asdict(memory))
        for invalid in ("", "   ", None, 10, True):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                SupportQuery(invalid)
        args["claims"] = {"pretend": next(iter(args["claims"].values()))}
        with self.assertRaisesRegex(ValueError, "mention/reading"):
            lookup(args, "a:old:17", memory)

    def test_provenance_is_deterministic_bound_to_source_identity_and_inputs_unchanged(self):
        args = fixture()
        before = deepcopy(args)
        first = lookup(args, "e:chain:5")
        second = lookup(args, "e:chain:5")
        self.assertEqual(first, second)
        self.assertEqual(args, before)
        self.assertEqual(len({item.evidence_id for item in first.evidence}), len(first.evidence))
        source = args["problem"].sources[0]
        args["problem"] = replace(args["problem"], sources=(replace(source, uri="https://changed.invalid/"),) +
                                  args["problem"].sources[1:])
        third = lookup(args, "e:chain:5")
        first_ids = {item.evidence_id for item in first.evidence if item.source_id == source.source_id}
        third_ids = {item.evidence_id for item in third.evidence if item.source_id == source.source_id}
        self.assertTrue(first_ids.isdisjoint(third_ids))


if __name__ == "__main__":
    unittest.main()
