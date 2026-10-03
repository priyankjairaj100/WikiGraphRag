"""Transport preserved migrated claims through each decoder without evaluating QA.

This intentionally trivial singleton construction is an admission/serialization
smoke check. Its artificial scores force one existing claim to be retained. It
cannot compare methods: there is no alternative resolved reading or pairwise
interaction, and it reads no question, expectation or answer-label files.
"""
from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from temporal_state.bounded import claim_to_dict, materialize_claim
from temporal_state.decoder import Mention, Problem, Reading
from temporal_state.pilot_io import load_prefix, load_sources
from temporal_state.pipeline import METHODS, decode_cache
from temporal_state.representation_io import file_sha, load_migration
from temporal_state.scored_io import cache_digest, dump_cache, load_cache, make_cache


BASE = ROOT / "data/natural_pilot"
MIGRATED = ROOT / "data/natural_pilot_v03"
OUTPUT = ROOT / "results/natural_cache_smoke_v04.json"
SCORE_DEFINITION = (
    "Artificial admission-only singleton smoke scores: resolved=0; unresolved=-1; "
    "null_link=0; no learned scoring, no alternate resolved reading or pair interaction."
)


def digest_json(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                            separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def main():
    loaded = load_sources(BASE / "manifest.json")
    records, passes = [], []
    checks = 0
    counts = {method: 0 for method in METHODS}
    # Every cache file is scoped to this directory and removed even on failure.
    with tempfile.TemporaryDirectory(prefix="natural-cache-smoke-", dir=ROOT) as temporary:
        temporary = Path(temporary)
        for pass_id, prefix_name, expected_claims in (("a", "early", 15), ("b", "early", 14), ("l", "late", 10)):
            prefix_path = BASE / f"{prefix_name}_prefix_input.json"
            migration_path = MIGRATED / f"pass_{pass_id}.json"
            raw_path = BASE / "annotations" / f"pass_{pass_id}.json"
            prompt_path = MIGRATED / "migration_prompt.txt"
            prefix = load_prefix(prefix_path, loaded)
            migration = load_migration(migration_path, prefix, raw_path, prompt_path)
            if len(migration.claims) != expected_claims:
                raise ValueError("Preserved migration claim inventory changed; review this smoke protocol")
            pass_record = {
                "pass_id": pass_id.upper(), "claims": len(migration.claims),
                "prefix_input_sha256": prefix.input_sha256,
                "raw_extraction_sha256": file_sha(raw_path),
                "migration_sha256": file_sha(migration_path),
                "migration_prompt_sha256": file_sha(prompt_path),
                "candidate_backend": migration.metadata["backend"],
            }
            passes.append(pass_record)
            for index, claim in enumerate(migration.claims):
                history = prefix.metadata[claim.source_id]["history_id"]
                cutoff = prefix.prefixes[history]
                sources = tuple(source for source in prefix.sources
                                if prefix.metadata[source.source_id]["history_id"] == history)
                source_ids = {source.source_id for source in sources}
                expected_dependencies = {claim.source_id, *claim.context_source_ids}
                if not expected_dependencies <= source_ids or any(source.available_at > cutoff for source in sources):
                    raise ValueError("Singleton source/context dependencies escape their original history prefix")
                config = {
                    "protocol": "natural_claim_singleton_admission_smoke_v0.4",
                    "history_id": history, "cutoff": cutoff,
                    "prefix_input_sha256": prefix.input_sha256,
                    "raw_extraction_sha256": pass_record["raw_extraction_sha256"],
                    "migration_sha256": pass_record["migration_sha256"],
                    "migration_prompt_sha256": pass_record["migration_prompt_sha256"],
                    "candidate_backend": pass_record["candidate_backend"],
                    "score_definition": SCORE_DEFINITION,
                    "resolved_score": 0.0, "unresolved_score": -1.0,
                    "null_link_score": 0.0, "beta": 1.0,
                    "links": [], "penalties": [], "aliases": [],
                    "reading_date_projection": "exact_equal_bounds_and_minimum_original_observation",
                    "temporal_policy": "pipeline_common_coupled_feasibility_and_materialization",
                }
                start = claim.start.lower if claim.start.lower == claim.start.upper else None
                end = claim.end.lower if claim.end.lower == claim.end.upper else None
                observed = min(claim.state_observed_at) if claim.state_observed_at else None
                span = claim.evidence_spans[0]
                mention = Mention("claim", claim.source_id, span.start, span.end, (
                    Reading("resolved", claim.key, claim.value, 0.0, start, end, observed,
                            context_source_ids=claim.context_source_ids),
                    Reading("unresolved", None, None, -1.0),
                ), null_score=0.0)
                problem = Problem(cutoff, sources, (mention,), (), SCORE_DEFINITION)
                provenance = {
                    "evidence_type": "migration_singleton_smoke",
                    "candidate_model_id": None, "candidate_model_revision": None,
                    "scorer_model_id": None, "scorer_model_revision": None,
                    "prompt_sha256": pass_record["migration_prompt_sha256"],
                    "config_sha256": digest_json(config), "score_definition": SCORE_DEFINITION,
                }
                original_hash = digest_json(claim_to_dict(claim))
                cache = make_cache(problem, {("claim", "resolved"): claim}, (), provenance)
                cache_path = temporary / f"{pass_id}_{index}.json"
                dump_cache(cache, cache_path)
                disk_sha = file_sha(cache_path)
                restored = load_cache(cache_path)
                if cache_digest(restored) != cache.digest or restored.claims["claim", "resolved"] != claim:
                    raise AssertionError("Scored-cache transport changed the preserved temporal claim")
                method_records = []
                for method in METHODS:
                    decoded = decode_cache(restored, method)
                    if decoded.memory.claims != (claim,):
                        raise AssertionError("Decoder/materializer failed to preserve the full singleton claim")
                    if dict(decoded.result.reading_ids) != {"claim": "resolved"}:
                        raise AssertionError("Artificial admission score failed to retain its sole resolved reading")
                    if any(link_id is not None for _, link_id in decoded.result.link_ids):
                        raise AssertionError("Singleton with no pair interactions acquired a selected link")
                    if decoded.memory.envelopes[claim.claim_id] != materialize_claim(claim):
                        raise AssertionError("Coupled singleton envelope differs from its original bounded interval")
                    dependencies = set(decoded.memory.dependency_source_ids[claim.claim_id])
                    if dependencies != expected_dependencies:
                        raise AssertionError("Materialization changed the singleton evidence dependency set")
                    materialized_hash = digest_json(claim_to_dict(decoded.memory.claims[0]))
                    if materialized_hash != original_hash:
                        raise AssertionError("Full claim digest changed after decoding/materialization")
                    method_records.append({
                        "method": method, "shared_cache_sha256": decoded.cache_digest,
                        "selected_reading": "resolved", "selected_link_count": 0,
                        "selected_objective": decoded.result.objective,
                        "materialized_claim_sha256": materialized_hash,
                        "dependency_source_count": len(dependencies),
                        "full_claim_preserved": True, "singleton_envelope_preserved": True,
                    })
                    checks += 1
                    counts[method] += 1
                records.append({
                    "pass_id": pass_id.upper(), "history_id": history, "claim_id": claim.claim_id,
                    "raw_assertion_id": claim.raw_assertion_id,
                    "claim_sha256": original_hash,
                    "source_count": len(sources),
                    "claim_context_source_count": len(claim.context_source_ids),
                    "expected_dependency_source_count": len(expected_dependencies),
                    "cache_payload_sha256": restored.digest,
                    "cache_file_sha256": disk_sha,
                    "transport_verified": True, "config": config,
                    "config_sha256": provenance["config_sha256"], "methods": method_records,
                })
    if len(records) != 39 or checks != 39 * len(METHODS):
        raise AssertionError("Smoke inventory is incomplete")
    report = {
        "schema_version": "0.4", "status": "passed_admission_transport_smoke_only",
        "claims": len(records), "passes": passes, "methods": list(METHODS),
        "decoder_materializer_checks": checks, "checks_by_method": counts,
        "score_definition": SCORE_DEFINITION,
        "questions_read": False, "expectations_read": False,
        "model_inference_performed": False, "learned_scoring_performed": False,
        "natural_method_comparison": False, "performance_estimate": None,
        "temporary_cache_files_cleaned": True, "records": records,
        "limitations": [
            "These are 39 preserved migrated claims, including overlapping source evidence across passes, not 39 independent research examples.",
            "Every graph has one resolved reading, one unresolved alternative and no pair interactions. Equal retention cannot establish method superiority.",
            "Fixed artificial admission scores deliberately favor preserving the existing claim; they are not learned scores or confidence estimates.",
            "The original migrations are unpinned conversational model interpretations, not new extraction or human verification.",
            "Source availability remains the original development convention, not verified historical first-public availability.",
            "Full claim/temporal-envelope retention and exact source support establish integration consistency, not factual truth or natural QA accuracy.",
        ],
    }
    OUTPUT.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "claims": len(records),
                      "decoder_materializer_checks": checks, "checks_by_method": counts}))


if __name__ == "__main__":
    main()
