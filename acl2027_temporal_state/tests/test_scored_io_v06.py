"""Strict provenance for real but unversioned candidate/scorer executions."""
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

from test_scored_io import fixture
from temporal_state.corrections import CorrectionPolicy
from temporal_state.correction_io import (correction_cache_digest, dump_correction_cache,
    load_correction_cache, make_correction_cache)
from temporal_state.scored_io import cache_digest, dump_cache, load_cache, make_cache


def unversioned_fixture():
    problem, claims, aliases, provenance = fixture()
    description = ("Ordinal source-support judgments from separate fresh-context conversational "
                   "candidate and scorer executions; revisions unknown; scores are not probabilities.")
    provenance.update(evidence_type="unversioned_model_scores",
                      candidate_model_id="conversational/candidate-agent",
                      candidate_model_revision=None,
                      scorer_model_id="conversational/scorer-agent",
                      scorer_model_revision=None,
                      prompt_sha256="a" * 64, config_sha256="b" * 64,
                      score_definition=description)
    return replace(problem, score_provenance=description), claims, aliases, provenance


class UnversionedScoredCacheTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "cache.json"
        self.parts = unversioned_fixture()
        self.cache = make_cache(*self.parts)
        dump_cache(self.cache, self.path)

    def data(self):
        return json.loads(self.path.read_text())

    def resign(self, data):
        data.pop("digest", None)
        data["digest"] = sha256(json.dumps(data, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        self.path.write_text(json.dumps(data))

    def test_roundtrip_explicit_unknown_revision_provenance(self):
        self.assertEqual(self.data()["schema_version"], "0.6")
        restored = load_cache(self.path)
        self.assertEqual(restored.digest, self.cache.digest)
        self.assertEqual(cache_digest(restored), self.cache.digest)
        for prefix in ("candidate", "scorer"):
            self.assertEqual(restored.provenance[f"{prefix}_model_id"],
                             self.parts[3][f"{prefix}_model_id"])
            self.assertIsNone(restored.provenance[f"{prefix}_model_revision"])

    def test_candidate_and_scorer_ids_cannot_be_missing_or_blank(self):
        problem, claims, aliases, provenance = self.parts
        for field in ("candidate_model_id", "scorer_model_id"):
            for value in (None, "", "  ", 1, False, {}):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    make_cache(problem, claims, aliases, {**provenance, field: value})

    def test_every_nonnull_revision_rejected_including_fake_unknown_strings(self):
        problem, claims, aliases, provenance = self.parts
        for field in ("candidate_model_revision", "scorer_model_revision"):
            for value in ("abc123", "unknown", "unversioned", "main", "", 1, False):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    make_cache(problem, claims, aliases, {**provenance, field: value})

    def test_prompt_and_config_hashes_required_and_strict(self):
        problem, claims, aliases, provenance = self.parts
        for field in ("prompt_sha256", "config_sha256"):
            for value in (None, "", "A" * 64, "x" * 64, "a" * 63, True):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    make_cache(problem, claims, aliases, {**provenance, field: value})

    def test_score_definition_required_and_bound_to_problem(self):
        problem, claims, aliases, provenance = self.parts
        for value in (None, "", "  ", "Different semantics", 1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                make_cache(problem, claims, aliases, {**provenance, "score_definition": value})

    def test_old_schema_cannot_silently_accommodate_new_evidence_type(self):
        data = self.data()
        data["schema_version"] = "0.4"
        self.resign(data)
        with self.assertRaisesRegex(ValueError, "evidence type does not match"):
            load_cache(self.path)

    def test_new_schema_cannot_relabel_old_evidence_types(self):
        problem, claims, aliases, provenance = fixture()
        for kind in ("authored_diagnostic", "migration_singleton_smoke", "pinned_model_scores"):
            prov = {**provenance, "evidence_type": kind}
            if kind == "pinned_model_scores":
                prov.update(candidate_model_id="test/candidate", candidate_model_revision="123abc",
                            scorer_model_id="test/scorer", scorer_model_revision="456def",
                            prompt_sha256="a" * 64)
            dump_cache(make_cache(problem, claims, aliases, prov), self.path)
            data = self.data()
            data["schema_version"] = "0.6"
            self.resign(data)
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, "evidence type does not match"):
                load_cache(self.path)

    def test_stale_model_prompt_config_and_score_edits_rejected(self):
        for where, field, value in (("provenance", "candidate_model_id", "changed/candidate"),
                ("provenance", "scorer_model_id", "changed/scorer"),
                ("provenance", "prompt_sha256", "c" * 64),
                ("provenance", "config_sha256", "d" * 64),
                ("reading", "unary_score", 3.0)):
            dump_cache(self.cache, self.path)
            data = self.data()
            target = (data["problem"]["mentions"][0]["readings"][0]
                      if where == "reading" else data[where])
            target[field] = value
            self.path.write_text(json.dumps(data))
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "digest mismatch"):
                load_cache(self.path)

    def test_new_model_provenance_fields_change_digest(self):
        problem, claims, aliases, provenance = self.parts
        for field, value in (("candidate_model_id", "changed/candidate"),
                             ("scorer_model_id", "changed/scorer"),
                             ("prompt_sha256", "c" * 64), ("config_sha256", "d" * 64)):
            with self.subTest(field=field):
                self.assertNotEqual(self.cache.digest,
                    make_cache(problem, claims, aliases, {**provenance, field: value}).digest)

    def test_correction_wrapper_roundtrips_nested_new_cache(self):
        outer = make_correction_cache(self.cache, CorrectionPolicy())
        dump_correction_cache(outer, self.path)
        data = self.data()
        self.assertEqual(data["schema_version"], "0.5")
        self.assertEqual(data["base_cache"]["schema_version"], "0.6")
        restored = load_correction_cache(self.path)
        self.assertEqual(restored.base.digest, self.cache.digest)
        self.assertEqual(correction_cache_digest(restored), outer.digest)
        self.assertEqual(restored.base.provenance["evidence_type"], "unversioned_model_scores")

    def test_correction_wrapper_rejects_stale_nested_new_cache(self):
        outer = make_correction_cache(self.cache, CorrectionPolicy())
        dump_correction_cache(outer, self.path)
        data = self.data()
        data["base_cache"]["provenance"]["prompt_sha256"] = "c" * 64
        self.resign(data)  # Outer consistency cannot mask a stale nested payload.
        with self.assertRaisesRegex(ValueError, "Nested base cache digest mismatch"):
            load_correction_cache(self.path)

    def test_existing_schema_payloads_and_dump_bytes_are_identical(self):
        # Recorded before the v0.6 extension, using the existing v0.4 fixture.
        expected = {
            "authored_diagnostic": (
                "44b26b54949a01e57fd104b16300717f10eff7faea0ae3daf51baf6daf16cc0b",
                "17c7c4df89a84462d8f67cf35e53720fc9c6cea81bdb50098e7beee82d794ebf"),
            "migration_singleton_smoke": (
                "57f66066e55ad7fb96b5598927be5bc41176d3a7fd8b1f8212693da0210fae20",
                "b4f3444395173acb138ffddbf49514b33d6b1e9c6f4e85ef1cf650702a798dc8"),
            "pinned_model_scores": (
                "6609a80df99f18f9ca643d13c2e01a4d0908aba7aab0506c3ee318da93f9c1fa",
                "e31ed2c0ddff7e61d884d6b828d5ee20805be908252a3f486180e7c307606e07"),
        }
        problem, claims, aliases, provenance = fixture()
        for kind, (digest, dump_sha) in expected.items():
            prov = {**provenance, "evidence_type": kind}
            if kind == "pinned_model_scores":
                prov.update(candidate_model_id="test/candidate", candidate_model_revision="123abc",
                            scorer_model_id="test/scorer", scorer_model_revision="456def",
                            prompt_sha256="a" * 64)
            cache = make_cache(problem, claims, aliases, prov)
            dump_cache(cache, self.path)
            with self.subTest(kind=kind):
                self.assertEqual(self.data()["schema_version"], "0.4")
                self.assertEqual(cache.digest, digest)
                self.assertEqual(sha256(self.path.read_bytes()).hexdigest(), dump_sha)
                self.assertEqual(load_cache(self.path).digest, digest)

    def test_pinned_model_contract_stays_stricter(self):
        problem, claims, aliases, provenance = self.parts
        prov = {**provenance, "evidence_type": "pinned_model_scores"}
        with self.assertRaisesRegex(ValueError, "explicit candidate/scorer revisions"):
            make_cache(problem, claims, aliases, prov)
        for floating in ("main", "latest", "master", "unversioned", "unknown"):
            with self.subTest(floating=floating), self.assertRaisesRegex(ValueError, "Floating"):
                make_cache(problem, claims, aliases,
                    {**prov, "candidate_model_revision": floating,
                     "scorer_model_revision": "123abc"})


if __name__ == "__main__":
    unittest.main()
