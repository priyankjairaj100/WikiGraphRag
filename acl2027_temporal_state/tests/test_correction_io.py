"""Strict correction-cache boundary, lineage and evidence/provenance checks."""
from dataclasses import FrozenInstanceError, replace
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

from temporal_state.bounded import DayBounds, EvidenceSpan, TemporalClaim
from temporal_state.correction_io import (correction_cache_digest, dump_correction_cache,
    load_correction_cache, make_correction_cache)
from temporal_state.corrections import (CorrectionEvidence, CorrectionPolicy,
    SourceAuthority, SupportDependency)
from temporal_state.decoder import Link, Mention, Problem, Reading
from temporal_state.models import Source
from temporal_state.scored_io import dump_cache, make_cache


def span(text, quote):
    start = text.index(quote)
    return EvidenceSpan(start, start + len(quote), quote)


def fixture():
    texts = (
        "Acme reported Ada became CEO on 2026-01-01.",
        "Acme corrects its previous report: Bea became CEO on 2026-01-01, replacing the Ada claim.",
        "Acme repeats the original report: Ada became CEO on 2026-01-01.",
    )
    sources = tuple(Source(f"s{i}", text, f"2026-02-0{i}") for i, text in enumerate(texts, 1))
    mentions, claims = [], {}
    for i, (source, value) in enumerate(zip(sources, ("Ada", "Bea", "Ada")), 1):
        claim = TemporalClaim(f"c{i}", source.source_id, "Acme", "CEO", value,
            reported_at=source.available_at,
            start=DayBounds("2026-01-01", "2026-01-01"),
            state_observed_at=(source.available_at,),
            evidence_spans=(span(source.text, source.text),),
            context_source_ids=("s1",) if i > 1 else ())
        context = claim.context_source_ids
        reading = Reading("resolved", claim.key, value, 1.0,
            effective_start="2026-01-01", observed_at=source.available_at,
            context_source_ids=context)
        name_span = span(source.text, value)
        mentions.append(Mention(f"m{i}", source.source_id, name_span.start, name_span.end,
            (reading, Reading("unresolved", None, None, 0.0)), context_source_ids=context))
        claims[f"m{i}", "resolved"] = claim
    correction_span = span(texts[1], "corrects its previous report")
    link = Link("correct21", "m2", "resolved", "m1", "resolved", "CORRECTS", 2.0,
        context_source_ids=("s1",), reference_span=(correction_span.start, correction_span.end))
    problem = Problem("2026-02-03", sources, tuple(mentions), (link,), "authored cache test scores")
    provenance = {"evidence_type": "authored_diagnostic", "candidate_model_id": None,
        "candidate_model_revision": None, "scorer_model_id": None,
        "scorer_model_revision": None, "prompt_sha256": None,
        "config_sha256": "a" * 64, "score_definition": problem.score_provenance}
    base = make_cache(problem, claims, [], provenance)
    policy = CorrectionPolicy(source_authorities=tuple(
        SourceAuthority(source.source_id, "Acme", (span(source.text, "Acme"),)) for source in sources),
        correction_evidence=(CorrectionEvidence("correct21", correction_span),),
        support_dependencies=(SupportDependency("c3", "c1", (span(texts[2], "original report"),)),))
    return base, policy


def digest(data):
    return sha256(json.dumps(data, sort_keys=True, ensure_ascii=False,
                            separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class CorrectionCacheTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "correction.json"
        self.base, self.policy = fixture()
        self.cache = make_correction_cache(self.base, self.policy)
        dump_correction_cache(self.cache, self.path)

    def json(self):
        return json.loads(self.path.read_text())

    def write_resigned(self, data, base=False, policy=True):
        if base:
            nested = data["base_cache"]
            nested.pop("digest", None)
            nested["digest"] = digest(nested)
            data["base_digest"] = nested["digest"]
        if policy:
            data["correction_policy_digest"] = digest(data["correction_policy"])
        data.pop("digest", None)
        data["digest"] = digest(data)
        self.path.write_text(json.dumps(data))

    def test_roundtrip_preserves_exact_v04_base_and_lineage(self):
        original = Path(self.directory.name) / "base.json"
        dump_cache(self.base, original)
        before = original.read_bytes()
        restored = load_correction_cache(self.path)
        self.assertEqual(self.json()["base_cache"], json.loads(before))
        dump_cache(restored.base, original)
        self.assertEqual(original.read_bytes(), before)
        self.assertEqual(restored.base.digest, self.base.digest)
        self.assertEqual(restored.policy, self.policy)
        self.assertEqual(correction_cache_digest(restored), self.cache.digest)

    def test_deep_immutability_and_defensive_copy(self):
        self.assertIsNot(self.base, self.cache.base)
        self.assertIsNot(self.policy, self.cache.policy)
        with self.assertRaises(FrozenInstanceError):
            self.cache.digest = "a" * 64
        with self.assertRaises(FrozenInstanceError):
            self.cache.policy.version = "0.6"
        with self.assertRaises(TypeError):
            self.cache.provenance["evidence_type"] = "pinned_model_annotation"
        with self.assertRaises(TypeError):
            self.cache.base.claims["m1", "resolved"] = None
        with self.assertRaises(FrozenInstanceError):
            self.cache.policy.source_authorities[0].evidence_spans[0].quote = "Other"

    def test_missing_certificates_remain_uncertified_candidates(self):
        cache = make_correction_cache(self.base, CorrectionPolicy())
        self.assertEqual(cache.policy.correction_evidence, ())
        self.assertEqual(cache.base.problem.links, self.base.problem.links)
        self.assertEqual(cache.policy.source_authorities, ())

    def test_no_authority_is_inferred_from_source_uri(self):
        problem = replace(self.base.problem, sources=tuple(
            replace(source, uri="https://investor.acme.example/official")
            for source in self.base.problem.sources))
        base = make_cache(problem, self.base.claims, self.base.aliases, self.base.provenance)
        cache = make_correction_cache(base, CorrectionPolicy())
        self.assertFalse(cache.policy.source_authorities)

    def test_stale_outer_policy_edit_rejected(self):
        data = self.json()
        data["correction_policy"]["source_authorities"][0]["authority_id"] = "Other"
        self.path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            load_correction_cache(self.path)

    def test_policy_digest_checked_even_when_outer_is_resigned(self):
        data = self.json()
        data["correction_policy"]["source_authorities"][0]["authority_id"] = "Other"
        self.write_resigned(data, policy=False)
        with self.assertRaisesRegex(ValueError, "policy digest mismatch"):
            load_correction_cache(self.path)

    def test_nested_base_and_lineage_digests_are_independent(self):
        for target in ("base_digest", "nested_score", "nested_digest"):
            with self.subTest(target=target):
                data = self.json()
                if target == "base_digest":
                    data["base_digest"] = "0" * 64
                elif target == "nested_digest":
                    data["base_cache"]["digest"] = "0" * 64
                else:
                    data["base_cache"]["problem"]["mentions"][0]["readings"][0]["unary_score"] = 50
                self.write_resigned(data)
                with self.assertRaisesRegex(ValueError, "base cache digest mismatch"):
                    load_correction_cache(self.path)
                dump_correction_cache(self.cache, self.path)

    def test_forged_wrapper_or_forged_base_object_is_rejected(self):
        for forged in (replace(self.cache, digest="0" * 64),
                       replace(self.cache, base=replace(self.base, digest="0" * 64))):
            with self.assertRaisesRegex(ValueError, "digest mismatch"):
                correction_cache_digest(forged)

    def test_duplicate_json_keys_rejected_at_nested_levels(self):
        text = self.path.read_text()
        self.path.write_text(text.replace('"version":"0.5"', '"version":"0.5","version":"0.5"'))
        with self.assertRaisesRegex(ValueError, "Duplicate JSON key"):
            load_correction_cache(self.path)

    def test_nonfinite_json_numbers_rejected(self):
        for value in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(value=value):
                self.path.write_text('{"digest":' + value + '}')
                with self.assertRaisesRegex(ValueError, "Nonfinite"):
                    load_correction_cache(self.path)

    def test_unknown_fields_rejected_at_every_new_schema_level(self):
        for level in ("outer", "policy", "authority", "certificate", "dependency", "span", "provenance"):
            for leaked in ("gold", "question", "answer"):
                with self.subTest(level=level, field=leaked):
                    data = self.json()
                    policy = data["correction_policy"]
                    containers = {"outer": data, "policy": policy,
                        "authority": policy["source_authorities"][0],
                        "certificate": policy["correction_evidence"][0],
                        "dependency": policy["support_dependencies"][0],
                        "span": policy["source_authorities"][0]["evidence_spans"][0],
                        "provenance": data["correction_provenance"]}
                    containers[level][leaked] = "must not enter inference"
                    self.write_resigned(data)
                    with self.assertRaisesRegex(ValueError, "Unexpected or missing"):
                        load_correction_cache(self.path)
                    dump_correction_cache(self.cache, self.path)

    def test_nested_base_schema_still_rejects_unknown_fields(self):
        data = self.json()
        data["base_cache"]["problem"]["mentions"][0]["answer"] = "unauthorized field"
        self.write_resigned(data, base=True)
        with self.assertRaisesRegex(ValueError, "Unexpected or missing"):
            load_correction_cache(self.path)

    def test_policy_spans_reject_boolean_offsets_and_inexact_quotes(self):
        for field, value in (("start", True), ("start", -1), ("end", 0), ("quote", "Other")):
            with self.subTest(field=field, value=value):
                data = self.json()
                data["correction_policy"]["source_authorities"][0]["evidence_spans"][0][field] = value
                self.write_resigned(data)
                with self.assertRaises(ValueError):
                    load_correction_cache(self.path)
                dump_correction_cache(self.cache, self.path)

    def test_missing_or_unknown_policy_references_are_rejected(self):
        changes = (("source_authorities", "source_id", "missing-source"),
                   ("correction_evidence", "link_id", "missing-link"),
                   ("support_dependencies", "dependent_claim_id", "missing-dependent"),
                   ("support_dependencies", "prerequisite_claim_id", "missing-prerequisite"))
        for collection, name, value in changes:
            with self.subTest(collection=collection, name=name):
                data = self.json()
                data["correction_policy"][collection][0][name] = value
                self.write_resigned(data)
                with self.assertRaises(ValueError):
                    load_correction_cache(self.path)
                dump_correction_cache(self.cache, self.path)

    def test_duplicate_policy_records_are_rejected(self):
        for name in ("source_authorities", "correction_evidence", "support_dependencies"):
            with self.subTest(name=name):
                data = self.json()
                data["correction_policy"][name].append(data["correction_policy"][name][0])
                self.write_resigned(data)
                with self.assertRaises(ValueError):
                    load_correction_cache(self.path)
                dump_correction_cache(self.cache, self.path)

    def test_correction_evidence_must_match_link_reference_offsets(self):
        data = self.json()
        data["correction_policy"]["correction_evidence"][0]["reference_span"] = {
            "start": 0, "end": 4, "quote": "Acme"}
        self.write_resigned(data)
        with self.assertRaises(ValueError):
            load_correction_cache(self.path)

    def test_unsupported_actions_partial_scopes_and_schema_versions_fail(self):
        changes = (("action", "retract"), ("coverage", "partial_interval"))
        for field, value in changes:
            with self.subTest(field=field):
                data = self.json()
                data["correction_policy"]["correction_evidence"][0][field] = value
                self.write_resigned(data)
                with self.assertRaisesRegex(ValueError, "whole-assertion"):
                    load_correction_cache(self.path)
                dump_correction_cache(self.cache, self.path)
        data = self.json()
        data["correction_policy"]["version"] = "0.6"
        self.write_resigned(data)
        with self.assertRaisesRegex(ValueError, "Unsupported correction policy"):
            load_correction_cache(self.path)

    def test_future_source_still_rejected_if_all_hashes_are_resigned(self):
        data = self.json()
        data["base_cache"]["problem"]["sources"][1]["available_at"] = "2026-03-01"
        self.write_resigned(data, base=True)
        with self.assertRaisesRegex(ValueError, "available in this prefix"):
            load_correction_cache(self.path)

    def test_default_provenance_never_claims_a_model_run(self):
        self.assertEqual(self.cache.provenance["evidence_type"], "authored_diagnostic")
        self.assertIsNone(self.cache.provenance["policy_builder_id"])
        self.assertIsNone(self.cache.provenance["policy_builder_revision"])
        self.assertIsNone(self.cache.provenance["prompt_sha256"])

    def test_source_derived_provenance_requires_builder_and_prompt(self):
        source = {**dict(self.cache.provenance), "evidence_type": "source_derived_annotation"}
        for updated in (source, {**source, "policy_builder_id": "conversational_agent"}):
            with self.assertRaisesRegex(ValueError, "explicit builder and prompt"):
                make_correction_cache(self.base, self.policy, updated)
        source.update(policy_builder_id="conversational_agent", prompt_sha256="c" * 64)
        annotated = make_correction_cache(self.base, self.policy, source)
        self.assertIsNone(annotated.provenance["policy_builder_revision"])
        self.assertNotEqual(annotated.digest, self.cache.digest)
        self.assertEqual(annotated.base.digest, self.base.digest)

    def test_pinned_provenance_requires_immutable_revision_and_hashes(self):
        provenance = {**dict(self.cache.provenance), "evidence_type": "pinned_model_annotation",
                      "policy_builder_id": "example/model", "prompt_sha256": "a" * 64}
        for revision in (None, "main", " LATEST "):
            with self.subTest(revision=revision), self.assertRaises(ValueError):
                make_correction_cache(self.base, self.policy,
                                      {**provenance, "policy_builder_revision": revision})
        for bad_hash in ("x" * 64, "A" * 64, "", True):
            with self.subTest(hash=bad_hash), self.assertRaisesRegex(ValueError, "SHA-256"):
                make_correction_cache(self.base, self.policy,
                    {**provenance, "policy_builder_revision": "abc123", "prompt_sha256": bad_hash})

    def test_policy_annotation_and_provenance_both_enter_digest(self):
        policy = replace(self.policy, support_dependencies=())
        changed = make_correction_cache(self.base, policy)
        self.assertNotEqual(changed.digest, self.cache.digest)
        self.assertEqual(changed.base.digest, self.base.digest)
        provenance = {**dict(self.cache.provenance), "policy_description": "Distinct annotation protocol"}
        annotated = make_correction_cache(self.base, self.policy, provenance)
        self.assertNotEqual(annotated.digest, self.cache.digest)


if __name__ == "__main__":
    unittest.main()
