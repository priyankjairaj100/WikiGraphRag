"""Offline, authored correction contracts; no research accuracy comparison.

Inputs are regenerated from explicit synthetic specifications. Expectations live
in a separate file and are not loaded until the complete prediction ledger has
been written. No natural corpus, model, QA gold, or network is accessed.
"""
from dataclasses import asdict
from hashlib import sha256
import json
from math import fsum, isclose
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from temporal_state.bounded import DayBounds, EvidenceSpan, TemporalClaim
from temporal_state.correction_io import dump_correction_cache, load_correction_cache, make_correction_cache
from temporal_state.correction_pipeline import answer_corrected, decode_correction_cache
from temporal_state.corrections import (CorrectionEvidence, CorrectionPolicy, SourceAuthority,
    SupportDependency, materialize_corrected_selection)
from temporal_state.coupled import TemporalInfeasible
from temporal_state.decoder import Link, Mention, Problem, Reading
from temporal_state.models import Question, Source
from temporal_state.pipeline import METHODS
from temporal_state.representation_io import answer_question
from temporal_state.scored_io import make_cache

DATA = ROOT / "data/correction_diagnostics_v05"
PREDICTIONS = ROOT / "results/correction_v05_predictions.json"
REPLAY = ROOT / "results/correction_v05_replay.json"


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n")


def digest(data):
    return sha256(json.dumps(data, sort_keys=True, separators=(",", ":"),
                            ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def span(text, quote):
    start = text.index(quote)
    return EvidenceSpan(start, start + len(quote), quote)


def build_case(case_id, values=("Ada", "Bea"), edges=((1, 0, "CORRECTS"),),
               *, same_day=False, other_issuer=False, missing_certificate=False,
               copy_dependency=False, neighbor=False, stale_date=False):
    """Construct candidate inputs only; no expected answers enter this function."""
    sources, mentions, claims, authorities = [], [], {}, []
    by_outgoing = {child: (parent, relation) for child, parent, relation in edges}
    count = 2 if neighbor else len(values)
    for i in range(count):
        issuer = "Other" if other_issuer and i == 1 else "Org"
        start = "2024-01-02" if stale_date and i == 2 else "2024-01-01"
        text = (f"{issuer}: statement s{i}: Org CEO {values[i]} holds the role from "
                f"{start} through 2024-01-31, ending 2024-02-01.")
        if i in by_outgoing:
            target, relation = by_outgoing[i]
            if relation == "CORRECTS":
                text += f" This statement wholly replaces the Org CEO assertion in statement s{target}."
            elif copy_dependency:
                text += f" This assertion is copied solely from statement s{target} and has no independent support."
            else:
                text += f" An independent observation confirms the same episode as statement s{target}."
        if neighbor and i == 0:
            text += " Org CFO Drew holds the role from 2024-01-01 through 2024-01-31, ending 2024-02-01."
        day = f"2024-01-{10 if same_day else 10 + i:02d}"
        source = Source(f"s{i}", text, day, uri=f"synthetic://{case_id}/s{i}",
                        availability_basis="authored_calendar_day", provenance="authored_control_not_natural_source")
        sources.append(source)
        authorities.append(SourceAuthority(source.source_id, issuer, (span(text, issuer),)))
    for i, value in enumerate(values):
        source = sources[0] if neighbor and i == 2 else sources[i]
        relation = "CFO" if neighbor and i == 2 else "CEO"
        start = "2024-01-02" if stale_date and i == 2 else "2024-01-01"
        claim = TemporalClaim(f"c{i}", source.source_id, "Org", relation, value,
            reported_at=source.available_at, start=DayBounds(start, start),
            end=DayBounds("2024-02-01", "2024-02-01"),
            evidence_spans=(span(source.text, source.text),),
            raw_assertion_id=f"authored_{case_id}_{i}",
            derivation_note="Authored synthetic assertion; no extraction model.")
        name_span = span(source.text, value)
        mentions.append(Mention(f"m{i}", source.source_id, name_span.start, name_span.end,
            (Reading("r", claim.key, value, 10.0, effective_start=start, effective_end="2024-02-01"),
             Reading("u", None, None, 0.0))))
        claims[f"m{i}", "r"] = claim
    links, certificates = [], []
    for child, parent, relation in edges:
        link_id = f"l{child}{parent}"
        reference = (span(sources[child].text,
            f"This statement wholly replaces the Org CEO assertion in statement s{parent}.")
            if relation == "CORRECTS" else None)
        links.append(Link(link_id, f"m{child}", "r", f"m{parent}", "r", relation,
            2.0 if relation == "CORRECTS" else 1.0,
            reference_span=(reference.start, reference.end) if reference else None))
        if reference and not missing_certificate:
            certificates.append(CorrectionEvidence(link_id, reference))
    dependency = ()
    if copy_dependency:
        quote = "This assertion is copied solely from statement s0 and has no independent support."
        dependency = (SupportDependency("c2", "c0", (span(sources[2].text, quote),)),)
    config = {"case_id": case_id, "values": values, "edges": edges,
        "same_day": same_day, "other_issuer": other_issuer,
        "missing_certificate": missing_certificate, "copy_dependency": copy_dependency,
        "neighbor": neighbor, "stale_date": stale_date,
        "scores": {"resolved_unary": 10.0, "unresolved_unary": 0.0,
                   "correction_pair": 2.0, "restatement_pair": 1.0, "null_link": 0.0}}
    score_definition = "Authored contract scores: resolved 10, unresolved 0, CORRECTS 2, RESTATES 1, null 0."
    problem = Problem("2024-02-02", tuple(sources), tuple(mentions), tuple(links), score_definition)
    base_provenance = {"evidence_type": "authored_diagnostic", "candidate_model_id": None,
        "candidate_model_revision": None, "scorer_model_id": None,
        "scorer_model_revision": None, "prompt_sha256": None,
        "config_sha256": digest(config), "score_definition": score_definition}
    base = make_cache(problem, claims, [], base_provenance)
    policy = CorrectionPolicy(source_authorities=tuple(authorities),
        correction_evidence=tuple(certificates), support_dependencies=dependency)
    provenance = {"evidence_type": "authored_diagnostic", "policy_builder_id": None,
        "policy_builder_revision": None, "prompt_sha256": None, "config_sha256": digest(config),
        "policy_description": "Authored exact-span correction/dependency control; not a natural annotation or model output."}
    return make_correction_cache(base, policy, provenance), config


def build_inputs():
    specifications = (
        ("atomic_replacement", {}),
        ("correction_chain", {"values": ("Ada", "Bea", "Cy"), "edges": ((1, 0, "CORRECTS"), (2, 1, "CORRECTS"))}),
        ("sibling_conflict", {"values": ("Ada", "Bea", "Cy"), "edges": ((1, 0, "CORRECTS"), (2, 0, "CORRECTS"))}),
        ("unrelated_issuer", {"other_issuer": True}),
        ("missing_certificate", {"missing_certificate": True}),
        ("independent_corroboration", {"values": ("Ada", "Bea", "Ada"), "edges": ((1, 0, "CORRECTS"), (2, 0, "RESTATES"))}),
        ("explicit_copy_dependency", {"values": ("Ada", "Bea", "Ada"), "edges": ((1, 0, "CORRECTS"), (2, 0, "RESTATES")), "copy_dependency": True}),
        ("neighboring_relation", {"values": ("Ada", "Bea", "Drew"), "neighbor": True}),
        ("same_day_reference", {"same_day": True}),
        ("withdraw_before_date_closure", {"values": ("Ada", "Bea", "Ada"), "edges": ((1, 0, "CORRECTS"), (2, 0, "RESTATES")), "stale_date": True}),
    )
    cases, questions = [], []
    for name, options in specifications:
        cache, config = build_case(name, **options)
        path = DATA / "caches" / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        dump_correction_cache(cache, path)
        cases.append({"case_id": name, "cache_file": f"caches/{name}.json",
            "correction_cache_sha256": cache.digest, "base_cache_sha256": cache.base.digest,
            "authored_specification": config})
        for role in (("CEO", "CFO") if name == "neighboring_relation" else ("CEO",)):
            qid = name + ("_cfo" if role == "CFO" else "")
            questions.append({"case_id": name, "query_mode": "reported_actual",
                "question": asdict(Question(qid, f"Who was {role} of Org on 2024-01-05?",
                                             "2024-01-05", cache.base.problem.cutoff))})
    write_json(DATA / "manifest.json", {"schema_version": "0.5", "evidence_level": "authored_contracts_only",
        "model_executed": False, "input_construction": "regenerated_from_explicit_script_specifications", "cases": cases})
    write_json(DATA / "questions.json", {"evidence_level": "authored_queries_not_natural_benchmark", "questions": questions})


def deterministic_result(result):
    record = asdict(result)
    record["diagnostics"].pop("elapsed_seconds")
    return record


def main():
    build_inputs()
    manifest = json.loads((DATA / "manifest.json").read_text())
    question_rows = json.loads((DATA / "questions.json").read_text())["questions"]
    caches = {row["case_id"]: load_correction_cache(DATA / row["cache_file"]) for row in manifest["cases"]}
    states = {(name, method): decode_correction_cache(cache, method, restarts=10, seed=7)
              for name, cache in caches.items() for method in METHODS}
    predictions = []
    for row in question_rows:
        name, question = row["case_id"], Question(**row["question"])
        cache = caches[name]
        for method in METHODS:
            state = states[name, method]
            prediction = answer_corrected(cache, state, question, query_mode=row["query_mode"])
            links = {link.link_id: link for link in cache.base.problem.links}
            disabled = {mid: (None if lid is not None and links[lid].relation == "CORRECTS" else lid)
                        for mid, lid in state.result.link_ids}
            try:
                control_memory = materialize_corrected_selection(cache.base.problem,
                    dict(state.result.reading_ids), disabled, cache.base.claims, cache.policy)
                control_prediction = answer_question(control_memory, question, control_memory.claims,
                    cache.base.aliases, cache.base.problem.sources, query_mode=row["query_mode"])
                control = {"status": "materialized", "prediction": asdict(control_prediction)}
            except TemporalInfeasible as exc:
                control = {"status": "infeasible", "reason": str(exc)}
            predictions.append({"case_id": name, "method": method, "query_mode": row["query_mode"],
                "correction_cache_sha256": cache.digest, "base_cache_sha256": cache.base.digest,
                "decoder": deterministic_result(state.result), "prediction": asdict(prediction),
                "objective_decomposition": dict(state.memory.diagnostics["objective_decomposition"]),
                "same_selection_CORRECTS_disabled": control})
    ledger = {"evidence_level": "authored_correction_implementation_contracts_only", "model_executed": False,
        "method_advantage_claim": False, "timing_policy": "elapsed time excluded; performance not evaluated",
        "cache_hashes": {name: cache.digest for name, cache in caches.items()},
        "predictions_written_before_expectations_loaded": True, "predictions": predictions}
    write_json(PREDICTIONS, ledger)

    # Hard boundary: no expectations file or evaluation label was read above.
    expectations_path = DATA / "expectations.json"
    expected = json.loads(expectations_path.read_text())
    checks = []

    def record(name, passed, case_id=None, method=None, question_id=None):
        checks.append({"check": name, "passed": bool(passed), "case_id": case_id,
                       "method": method, "question_id": question_id})

    for row in predictions:
        name, method, prediction = row["case_id"], row["method"], row["prediction"]
        qid = prediction["question_id"]
        target = expected["questions"][qid]
        contract = expected["cases"][name]
        state = states[name, method]
        correction_ledger = state.memory.diagnostics["correction_ledger"]
        record("authored_answer_contract", prediction["status"] == target["status"]
               and tuple(prediction["values"]) == tuple(target["values"]), name, method, qid)
        control = row["same_selection_CORRECTS_disabled"]
        record("fixed_graph_without_corrections_contract",
            control["status"] == target["without_corrections"]["materialization"] and
            (control["status"] == "infeasible" or
             (control["prediction"]["status"] == target["without_corrections"]["status"] and
              tuple(control["prediction"]["values"]) == tuple(target["without_corrections"]["values"]))), name, method, qid)
        for field in ("active_claim_ids", "inactive_claim_ids", "directly_withdrawn_claim_ids", "inactive_temporal_link_ids"):
            record(field + "_contract", correction_ledger[field] == contract[field], name, method, qid)
        selected_links = sorted(lid for _, lid in state.result.link_ids if lid is not None)
        record("selected_links_contract", selected_links == contract["selected_link_ids"], name, method, qid)
        record("all_authored_resolved_readings_retained", all(rid == "r" for _, rid in state.result.reading_ids), name, method, qid)
        record("no_event_endpoints_rewritten", all(
            asdict(claim) == asdict(caches[name].base.claims[mid, rid])
            for mid, rid in state.result.reading_ids if rid == "r"
            for claim in state.memory.claims if claim.claim_id == caches[name].base.claims[mid, rid].claim_id), name, method, qid)
        record("notice_effects_retained", all(notice["notice_effect_retained"] for notice in correction_ledger["operative_notices"]), name, method, qid)
        record("same_day_reference_contract", any(notice["same_day_reference"] for notice in correction_ledger["operative_notices"])
               == contract["same_day_reference"], name, method, qid)
    for name in caches:
        selections = {(states[name, method].result.reading_ids, states[name, method].result.link_ids,
                       states[name, method].result.objective) for method in METHODS}
        record("four_methods_match_on_authored_easy_scores", len(selections) == 1, name)
        record("all_methods_same_frozen_cache", len({states[name, method].cache_digest for method in METHODS}) == 1, name)
        for method in METHODS:
            state = states[name, method]
            parts = state.memory.diagnostics["objective_decomposition"]
            total = fsum(parts[key] for key in (
                "active_claim_unary_net", "inactive_claim_unary_net", "unresolved_unary_net",
                "active_temporal_link_net", "inactive_temporal_link_net", "correction_link_net", "null_link_score"))
            record("historical_objective_decomposition_sum", isclose(total, state.result.objective,
                rel_tol=1e-12, abs_tol=1e-9) and parts["total"] == state.result.objective, name, method)
    report = {"evidence_level": "authored_implementation_contracts_not_research_accuracy",
        "model_executed": False, "method_advantage_claim": False,
        "cases": len(caches), "questions": len(question_rows), "methods": list(METHODS),
        "method_question_predictions": len(predictions), "method_case_decodes": len(states),
        "contract_checks": len(checks), "all_checks_passed": all(item["passed"] for item in checks),
        "prediction_sha256": sha256(PREDICTIONS.read_bytes()).hexdigest(),
        "expectation_sha256": sha256(expectations_path.read_bytes()).hexdigest(), "checks": checks,
        "limitations": ["Candidates, scores, authorities, correction references and expected behavior were authored.",
            "One resolved reading plus unresolved per mention makes these easy algorithm controls.",
            "No natural extraction accuracy, learned-score result, held-out result, scalability or algorithmic novelty claim.",
            "Removing CORRECTS from a fixed selected graph is a materialization control, not a retrained or reoptimized baseline.",
            "Certificates are exact source slices; semantic authority and reference entailment are not independently verified."]}
    write_json(REPLAY, report)
    print(json.dumps({key: report[key] for key in ("cases", "questions", "method_question_predictions",
        "method_case_decodes", "contract_checks", "all_checks_passed")}, indent=2))
    if not report["all_checks_passed"]:
        print(json.dumps([check for check in checks if not check["passed"]], indent=2))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
