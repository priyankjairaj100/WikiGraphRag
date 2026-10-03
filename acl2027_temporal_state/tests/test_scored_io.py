"""Adversarial checks for the question-free shared-score cache boundary."""
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

from temporal_state.bounded import DayBounds, EvidenceSpan, TemporalClaim
from temporal_state.decoder import Link, Mention, Penalty, Problem, Reading
from temporal_state.models import Source
from temporal_state.scored_io import cache_digest, dump_cache, load_cache, make_cache


def fixture():
    text = "Acme named Ada CEO effective 2026-01-01."
    source = Source("s1", text, "2026-02-01")
    claim = TemporalClaim("c1", "s1", "Acme", "CEO", "Ada",
        reported_at="2026-02-01", start=DayBounds("2026-01-01", "2026-01-01"),
        state_observed_at=("2026-02-01",),
        evidence_spans=(EvidenceSpan(0, len(text), text),))
    reading = Reading("resolved", claim.key, "Ada", 2.0,
                      effective_start="2026-01-01", observed_at="2026-02-01")
    mention = Mention("m1", "s1", text.index("Ada"), text.index("Ada") + 3,
                      (reading, Reading("unresolved", None, None, 0.0)))
    problem = Problem("2026-02-01", (source,), (mention,), (), "authored test scores")
    aliases = [{"subject": "Acme", "alias": "Acme", "source_id": "s1",
                "evidence_spans": [{"start": 0, "end": 4, "quote": "Acme"}]}]
    provenance = {"evidence_type": "authored_diagnostic", "candidate_model_id": None,
                  "candidate_model_revision": None, "scorer_model_id": None,
                  "scorer_model_revision": None, "prompt_sha256": None,
                  "config_sha256": "d" * 64, "score_definition": "authored test scores"}
    return problem, {("m1", "resolved"): claim}, aliases, provenance


class ScoredCacheTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "cache.json"
        self.parts = fixture()
        self.cache = make_cache(*self.parts)
        dump_cache(self.cache, self.path)

    def json(self):
        return json.loads(self.path.read_text())

    def write_resigned(self, data):
        data.pop("digest", None)
        digest = sha256(json.dumps(data, ensure_ascii=False, sort_keys=True,
                                  separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        data["digest"] = digest
        self.path.write_text(json.dumps(data))

    def test_stale_score_edit_rejected_and_hash_changes_with_new_scores(self):
        data = self.json()
        data["problem"]["mentions"][0]["readings"][0]["unary_score"] = 20
        self.path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            load_cache(self.path)
        problem, claims, aliases, provenance = self.parts
        mention = problem.mentions[0]
        altered = replace(mention, readings=(replace(mention.readings[0], unary_score=20), mention.readings[1]))
        second = make_cache(replace(problem, mentions=(altered,)), claims, aliases, provenance)
        self.assertNotEqual(second.digest, self.cache.digest)

    def test_duplicate_keys_rejected_even_with_unchanged_semantics(self):
        text = self.path.read_text()
        text = text.replace('"beta":1.0', '"beta":1.0,"beta":1.0')
        self.path.write_text(text)
        with self.assertRaisesRegex(ValueError, "Duplicate JSON key"):
            load_cache(self.path)

    def test_nonfinite_and_boolean_scores_rejected(self):
        problem, claims, aliases, provenance = self.parts
        mention = problem.mentions[0]
        for value in (float("nan"), float("inf"), True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                changed = replace(mention, readings=(replace(mention.readings[0], unary_score=value), mention.readings[1]))
                make_cache(replace(problem, mentions=(changed,)), claims, aliases, provenance)
        self.path.write_text('{"digest":"' + "a" * 64 + '","bad":NaN}')
        with self.assertRaisesRegex(ValueError, "Nonfinite"):
            load_cache(self.path)

    def test_future_source_cannot_enter_prefix(self):
        problem, claims, aliases, provenance = self.parts
        with self.assertRaisesRegex(ValueError, "available in this prefix"):
            make_cache(replace(problem, sources=(replace(problem.sources[0], available_at="2026-02-02"),)),
                       claims, aliases, provenance)

    def test_future_or_unknown_context_cannot_hide_in_claim_or_graph(self):
        problem, claims, aliases, provenance = self.parts
        with self.assertRaisesRegex(ValueError, "Dangling"):
            make_cache(problem, {("m1", "resolved"): replace(claims["m1", "resolved"], context_source_ids=("future",))},
                       aliases, provenance)
        with self.assertRaisesRegex(ValueError, "eligible prefix"):
            make_cache(replace(problem, context_source_ids=("future",)), claims, aliases, provenance)

    def test_claim_context_must_be_visible_to_decoder(self):
        problem, claims, aliases, provenance = self.parts
        problem = replace(problem, sources=problem.sources + (Source("s2", "Earlier context", "2026-01-30"),))
        claims = {("m1", "resolved"): replace(claims["m1", "resolved"], context_source_ids=("s2",))}
        with self.assertRaisesRegex(ValueError, "also be declared"):
            make_cache(problem, claims, aliases, provenance)
        cache = make_cache(replace(problem, context_source_ids=("s2",)), claims, aliases, provenance)
        self.assertIn("s2", cache.problem.context_source_ids)

    def test_source_and_evidence_cannot_be_rebound(self):
        problem, claims, aliases, provenance = self.parts
        claim = claims["m1", "resolved"]
        source2 = replace(problem.sources[0], source_id="s2")
        with self.assertRaisesRegex(ValueError, "key/value/source"):
            make_cache(replace(problem, sources=problem.sources + (source2,)),
                       {("m1", "resolved"): replace(claim, source_id="s2")}, aliases, provenance)
        bad = replace(claim, evidence_spans=(EvidenceSpan(0, 4, "Fake"),))
        with self.assertRaisesRegex(ValueError, "Nonmatching evidence"):
            make_cache(problem, {("m1", "resolved"): bad}, aliases, provenance)

    def test_evidence_must_anchor_to_the_mention(self):
        problem, claims, aliases, provenance = self.parts
        claim = replace(claims["m1", "resolved"], evidence_spans=(EvidenceSpan(0, 4, "Acme"),))
        with self.assertRaisesRegex(ValueError, "overlap"):
            make_cache(problem, {("m1", "resolved"): claim}, aliases, provenance)

    def test_reading_identity_value_must_match_bounded_binding(self):
        problem, claims, aliases, provenance = self.parts
        claim = claims["m1", "resolved"]
        for change in ({"value": "Bea"}, {"subject": "Different Co"}, {"scope": "regional"}):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "key/value/source"):
                make_cache(problem, {("m1", "resolved"): replace(claim, **change)}, aliases, provenance)

    def test_bounded_start_cannot_be_projected_to_a_fake_exact_date(self):
        problem, claims, aliases, provenance = self.parts
        claim = replace(claims["m1", "resolved"], start=DayBounds("2026-01-01", "2026-01-31"))
        with self.assertRaisesRegex(ValueError, "minimal bounded"):
            make_cache(problem, {("m1", "resolved"): claim}, aliases, provenance)
        mention = problem.mentions[0]
        mention = replace(mention, readings=(replace(mention.readings[0], effective_start=None), mention.readings[1]))
        checked = make_cache(replace(problem, mentions=(mention,)), {("m1", "resolved"): claim}, aliases, provenance)
        self.assertIsNone(checked.problem.mentions[0].readings[0].effective_start)

    def test_all_observations_preserved_and_projection_uses_earliest(self):
        problem, claims, aliases, provenance = self.parts
        claim = replace(claims["m1", "resolved"], state_observed_at=("2026-02-01", "2026-01-20"))
        with self.assertRaisesRegex(ValueError, "minimal bounded"):
            make_cache(problem, {("m1", "resolved"): claim}, aliases, provenance)
        mention = problem.mentions[0]
        mention = replace(mention, readings=(replace(mention.readings[0], observed_at="2026-01-20"), mention.readings[1]))
        checked = make_cache(replace(problem, mentions=(mention,)), {("m1", "resolved"): claim}, aliases, provenance)
        self.assertEqual(checked.claims["m1", "resolved"].state_observed_at, ("2026-02-01", "2026-01-20"))

    def test_unknown_role_qualifier_preserved_as_null_not_empty_text(self):
        problem, claims, aliases, provenance = self.parts
        original = replace(claims["m1", "resolved"], role_qualifier=None)
        checked = make_cache(problem, {("m1", "resolved"): original}, aliases, provenance)
        dump_cache(checked, self.path)
        restored = load_cache(self.path)
        self.assertEqual(restored.claims["m1", "resolved"], original)
        self.assertIsNone(restored.claims["m1", "resolved"].role_qualifier)
        for invalid in ([], {}, 0, False):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "role_qualifier"):
                make_cache(problem, {("m1", "resolved"): replace(original, role_qualifier=invalid)}, aliases, provenance)

    def test_missing_resolved_or_added_unresolved_binding_rejected(self):
        problem, claims, aliases, provenance = self.parts
        for bindings in ({}, {**claims, ("m1", "unresolved"): claims["m1", "resolved"]}):
            with self.assertRaisesRegex(ValueError, "Every resolved reading"):
                make_cache(problem, bindings, aliases, provenance)

    def test_duplicate_bindings_rejected(self):
        data = self.json()
        data["claims"].append(data["claims"][0])
        self.write_resigned(data)
        with self.assertRaisesRegex(ValueError, "Duplicate reading"):
            load_cache(self.path)

    def test_unknown_label_question_answer_fields_fail_every_schema_level(self):
        for level in ("top", "problem", "source", "mention", "reading", "claim", "bound", "span", "alias", "provenance"):
            for leaked in ("gold", "question", "answer"):
                with self.subTest(level=level, leaked=leaked):
                    data = self.json()
                    containers = {
                        "top": data, "problem": data["problem"],
                        "source": data["problem"]["sources"][0],
                        "mention": data["problem"]["mentions"][0],
                        "reading": data["problem"]["mentions"][0]["readings"][0],
                        "claim": data["claims"][0]["claim"],
                        "bound": data["claims"][0]["claim"]["start"],
                        "span": data["claims"][0]["claim"]["evidence_spans"][0],
                        "alias": data["aliases"][0], "provenance": data["provenance"],
                    }
                    containers[level][leaked] = "should never enter inference"
                    self.write_resigned(data)
                    with self.assertRaises(ValueError):
                        load_cache(self.path)
                    dump_cache(self.cache, self.path)

    def test_aliases_require_exact_source_support(self):
        problem, claims, aliases, provenance = self.parts
        for update in ({"source_id": "future"}, {"subject": "Unknown"},
                       {"evidence_spans": [{"start": 0, "end": 4, "quote": "Fake"}]}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                make_cache(problem, claims, [{**aliases[0], **update}], provenance)

    def test_source_hash_verifies_text_even_after_payload_is_resigned(self):
        data = self.json()
        data["problem"]["sources"][0]["text"] += " Additional context."
        self.write_resigned(data)
        with self.assertRaisesRegex(ValueError, "Source text digest"):
            load_cache(self.path)

    def test_authored_scores_cannot_claim_a_model_scorer(self):
        problem, claims, aliases, provenance = self.parts
        with self.assertRaisesRegex(ValueError, "must not claim"):
            make_cache(problem, claims, aliases, {**provenance, "scorer_model_id": "some/model"})
        smoke = make_cache(problem, claims, aliases, {**provenance, "evidence_type": "migration_singleton_smoke"})
        self.assertIsNone(smoke.provenance["scorer_model_id"])

    def test_model_provenance_requires_explicit_nonfloating_revisions(self):
        problem, claims, aliases, provenance = self.parts
        pinned = {**provenance, "evidence_type": "pinned_model_scores"}
        with self.assertRaisesRegex(ValueError, "require explicit"):
            make_cache(problem, claims, aliases, pinned)
        pinned.update(candidate_model_id="test/candidate", candidate_model_revision="main",
                      scorer_model_id="test/scorer", scorer_model_revision="123abc", prompt_sha256="a" * 64)
        with self.assertRaisesRegex(ValueError, "Floating"):
            make_cache(problem, claims, aliases, pinned)

    def test_model_prompt_configuration_and_scores_all_enter_digest(self):
        problem, claims, aliases, provenance = self.parts
        for change in ({"candidate_model_id": "explicit-unpinned-agent"},
                       {"config_sha256": "b" * 64}, {"prompt_sha256": "c" * 64}):
            altered = make_cache(problem, claims, aliases, {**provenance, **change})
            self.assertNotEqual(self.cache.digest, altered.digest)

    def test_link_null_beta_and_penalty_edits_create_distinct_shared_objectives(self):
        problem, claims, aliases, provenance = self.parts
        second = replace(problem.mentions[0], mention_id="m2")
        claims = {**claims, ("m2", "resolved"): replace(claims["m1", "resolved"], claim_id="c2")}
        link = Link("l1", "m2", "resolved", "m1", "resolved", "RESTATES", 0.5)
        problem = replace(problem, mentions=problem.mentions + (second,), links=(link,),
                          penalties=(Penalty("test_cost", 0.2),))
        base = make_cache(problem, claims, aliases, provenance)
        changes = (
            replace(problem, links=(replace(link, score=0.9),)),
            replace(problem, mentions=(replace(problem.mentions[0], null_score=0.4), second)),
            replace(problem, beta=0.25),
            replace(problem, penalties=(Penalty("test_cost", 0.7),)),
        )
        self.assertEqual(len({make_cache(p, claims, aliases, provenance).digest for p in changes}), 4)
        for changed in changes:
            self.assertNotEqual(base.digest, make_cache(changed, claims, aliases, provenance).digest)
        dump_cache(base, self.path)
        data = self.json()
        data["claims"].reverse()
        self.write_resigned(data)
        with self.assertRaisesRegex(ValueError, "canonical mention/reading"):
            load_cache(self.path)

    def test_cache_is_deeply_immutable_and_input_edits_do_not_leak(self):
        self.parts[2][0]["evidence_spans"][0]["quote"] = "Fake"
        self.parts[3]["score_definition"] = "mutated"
        self.assertEqual(self.cache.aliases[0]["evidence_spans"][0]["quote"], "Acme")
        with self.assertRaises(TypeError):
            self.cache.claims["m1", "resolved"] = None
        with self.assertRaises(TypeError):
            self.cache.aliases[0]["evidence_spans"][0]["quote"] = "Fake"
        self.assertEqual(cache_digest(self.cache), self.cache.digest)

    def test_hash_reproducible_despite_json_whitespace_and_property_order(self):
        data = self.json()
        self.path.write_text(json.dumps(dict(reversed(list(data.items()))), indent=4))
        reread = load_cache(self.path)
        self.assertEqual(reread.digest, self.cache.digest)
        self.assertEqual(cache_digest(reread), self.cache.digest)
        with self.assertRaisesRegex(ValueError, "object digest"):
            cache_digest(replace(self.cache, digest="0" * 64))


if __name__ == "__main__":
    unittest.main()
