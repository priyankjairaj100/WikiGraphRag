#!/usr/bin/env python3
"""Offline audit of the preserved original experiment. Never call a model.

Prompt recovery enumerates source-claim orders and checks their exact cache keys.
Search heuristics only reduce this forensic enumeration. They are not retrievers
being evaluated. A missing key remains missing. Original files stay unchanged.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
import itertools
import json
import math
from pathlib import Path
import random
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = ROOT.parent / "original_submission" / "code-and-data"
sys.path.insert(0, str(ORIGINAL))
from wikigraphrag.data.io import load_claims
from wikigraphrag.detect import SupersessionDetector
from wikigraphrag.eval.detection import gold_superseded, _norm_value
from wikigraphrag.eval.qa import normalize_answer
from wikigraphrag.readers.base import build_prompt, build_prompt_dated, build_prompt_neutral
from experiments.run_qa_realprose import _questions, _name_hit, _name_clean

MODEL = "Qwen/Qwen2.5-3B-Instruct"
CONDITIONS = ("flat", "date_prompt", "date_rerank", "lifecycle", "gold")
BUILDERS = {"flat": build_prompt, "date_prompt": build_prompt_dated,
            "date_rerank": build_prompt_neutral, "lifecycle": build_prompt_neutral,
            "gold": build_prompt_neutral}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def cache_key(prompt):
    return hashlib.sha256(f"{MODEL}\x0024\x00{prompt}".encode()).hexdigest()


def words(text):
    return re.findall(r"[a-z0-9]+", text.lower())


def lexical_order(claims, question):
    """Forensic search order only. No embedding inference or retrieval replay."""
    docs = [Counter(words(c.text)) for c in claims]
    lengths = [sum(d.values()) for d in docs]
    avg = sum(lengths) / len(lengths)
    dfs = Counter(t for d in docs for t in d)
    idfs = {t: math.log((len(docs) - n + .5) / (n + .5)) for t, n in dfs.items()}
    epsilon = .25 * sum(idfs.values()) / len(idfs)
    idfs = {t: max(epsilon, v) if v < 0 else v for t, v in idfs.items()}
    scores = []
    for i, d in enumerate(docs):
        score = sum(idfs.get(t, 0) * d[t] * 2.5 /
                    (d[t] + 1.5 * (.25 + .75 * lengths[i] / avg))
                    for t in words(question) if d[t])
        scores.append(score)
    return sorted(range(len(claims)), key=lambda i: (-scores[i], claims[i].cid))


def strict_metrics(answer, gold, stale):
    a, g = normalize_answer(answer), normalize_answer(gold)
    at, gt = a.split(), g.split()
    phrase = bool(gt) and any(at[i:i + len(gt)] == gt for i in range(len(at) - len(gt) + 1))
    stale_phrases = []
    for value in stale:
        st = normalize_answer(value).split()
        if st and any(at[i:i + len(st)] == st for i in range(len(at) - len(st) + 1)):
            stale_phrases.append(value)
    return {
        "original_name_hit": _name_hit(answer, gold),
        "original_name_clean": _name_clean(answer, gold, stale),
        "normalized_full_string_equality": bool(g) and a == g,
        "whole_token_gold_phrase": phrase,
        "whole_token_gold_phrase_without_stale_phrase": phrase and not stale_phrases,
        "stale_whole_token_phrases": stale_phrases,
        "original_hit_without_gold_phrase": _name_hit(answer, gold) and not phrase,
        "semantic_person_identity": "not_adjudicated",
    }


def source_audit(claims, questions):
    """Assistant-reviewed source semantics; exact released-text locators only."""
    path = ORIGINAL / "data/realprose/claims.jsonl"
    raw = path.read_bytes()
    offsets = {}
    start = 0
    for lineno, line in enumerate(raw.splitlines(keepends=True), 1):
        obj = json.loads(line)
        offsets[obj["cid"]] = {"line_1based": lineno, "byte_start": start,
            "byte_stop": start + len(line), "line_sha256": hashlib.sha256(line).hexdigest()}
        start += len(line)
    flags = {
        "disney": ("explicit_ended_tenure", "who was the chief executive officer of the Walt Disney Company from 2020 to 2022"),
        "intel": ("explicit_ended_tenure", "He was chief executive officer of Intel from February 2021 to December 2024"),
        "starbucks": ("explicit_ended_tenure", "who was the president and chief executive officer of Starbucks Coffee Company from 2017 to 2022"),
        "twitter": ("explicit_ended_tenure", "She served as chief executive officer of X Corp from 2023 to 2025"),
        "japan": ("explicit_ended_tenure", "who served as Prime Minister of Japan and President of the Liberal Democratic Party from 2024 until his resignation in 2025"),
        "federal reserve": ("explicit_ended_tenure", "who served as the 16th chair of the Federal Reserve from 2018 to 2026"),
        "liverpool": ("past_most_recent_role_only", "who was most recently the head coach of Premier League club Liverpool"),
        "manchester united": ("current_role_names_different_club", "who is currently the head coach of Serie A club AC Milan"),
        "manchester city": ("requested_club_absent", "Josep \"Pep\" Guardiola Sala is a Spanish football manager and former player from Catalonia."),
    }
    records = []
    def anchor(c, span):
        p = c.text.index(span)
        return {"source_file": str(path.relative_to(ROOT.parent)), "source_sha256": hashlib.sha256(raw).hexdigest(),
                "claim_id": c.cid, **offsets[c.cid], "decoded_text_char_start": p,
                "decoded_text_char_stop": p + len(span), "span_text": span,
                "span_sha256": hashlib.sha256(span.encode()).hexdigest()}
    for qi, (q, gold, stale, key) in enumerate(questions):
        group = [c for c in claims if c.gold_key == key]
        newest = max(group, key=lambda c: c.timestamp)
        if key[0] in flags:
            status, span = flags[key[0]]
        else:
            status, span = "latest_claim_explicitly_asserts_requested_current_role", newest.text
        extra = []
        if key[0] == "disney":
            c = next(c for c in group if c.cid == "rp:10")
            extra.append(anchor(c, "twice, from 2005 to 2020 and from 2022 to 2026"))
        if key[0] == "starbucks":
            c = next(c for c in group if c.cid == "rp:18")
            extra.append(anchor(c, "from 1986 to 2000, from 2008 to 2017, and interim CEO from 2022 to 2023"))
        records.append({"question_index": qi, "question": q, "question_contains_current": "current" in q,
            "explicit_query_time": None, "key": list(key), "reference_value": gold,
            "reference_selection": "max(group, key=timestamp); curated tenure start",
            "latest_claim_id": newest.cid, "latest_claim_time": newest.timestamp,
            "status": status, "current_support_mismatch": key[0] in flags,
            "evidence": anchor(newest, span), "additional_same_key_chronology_evidence": extra,
            "interpretation": "Released prose does not assert this reference as current in the requested role." if key[0] in flags
                              else "Released prose explicitly asserts the requested role; external factual truth was not checked."})
    return {"scope": "All 35 latest reference claims inspected; complete same-key histories additionally checked for nine flagged questions.",
        "assistant_reviewed": True, "independent_human_gold": False, "external_world_truth_checked": False,
        "reference_labels_changed": False, "posthoc_exclusions_applied": False,
        "statuses": dict(Counter(r["status"] for r in records)),
        "current_support_mismatches": sum(r["current_support_mismatch"] for r in records),
        "limitations": ["Latest-observed curated holder differs from currently supported holder.",
            "No common current query date or archived publication date is supplied.",
            "End dates and different-club descriptions do not identify the real current holder.",
            "This source audit does not establish that any live-web fact is true or false."],
        "records": records}


def consistent_assignment(recovered, claims):
    """Eliminate contexts impossible under one shared original ranking."""
    ids = {c.cid for c in claims}
    det = {e.older for e in SupersessionDetector().detect(claims)}
    gm = gold_superseded(claims)
    allowed = {"flat": ids, "date_prompt": ids, "lifecycle": ids - det, "gold": ids - gm}
    found = recovered["found"]
    labels = [c for c in CONDITIONS if found[c]]
    viable = {c: set() for c in CONDITIONS}
    assignments = 0
    for choices in itertools.product(*(range(len(found[c])) for c in labels)):
        edges = defaultdict(set)
        for c, j in zip(labels, choices):
            ctx = found[c][j]["context_claim_ids"]
            for a, b in zip(ctx, ctx[1:]):
                edges[a].add(b)
            if c in allowed:
                edges[ctx[-1]].update(allowed[c] - set(ctx))
        degree = {c: 0 for c in ids}
        for dest in edges.values():
            for c in dest:
                degree[c] += 1
        zero = [c for c, d in degree.items() if not d]
        visited = 0
        while zero:
            c = zero.pop()
            visited += 1
            for d in edges[c]:
                degree[d] -= 1
                if degree[d] == 0:
                    zero.append(d)
        if visited == len(ids):
            assignments += 1
            for c, j in zip(labels, choices):
                viable[c].add(j)
    return {"compatible_shared_ranking_assignments": assignments,
        "rule": "Every selected context is an ordered top-five prefix after its condition mask; date ties retain base order.",
        "viable_candidate_indices": {c: sorted(v) for c, v in viable.items()},
        "filtered_found": {c: [found[c][i] for i in sorted(viable[c])] for c in CONDITIONS},
        "ranking_vectors_replayed": False}


def recover_question(task):
    qi, q, gold, stale, key, pool_size = task
    claims = load_claims(str(ORIGINAL / "data/realprose/claims.jsonl"))
    raw = [json.loads(x) for x in (ORIGINAL / "data/cache/qwen_generations.jsonl").read_text().splitlines()]
    cache = {r["key"]: (i + 1, r["out"]) for i, r in enumerate(raw)}
    det = {e.older for e in SupersessionDetector().detect(claims)}
    gold_mask = gold_superseded(claims)
    lex = lexical_order(claims, q)
    latest = max(c.timestamp for c in claims)
    latest_ids = [i for i, c in enumerate(claims) if c.timestamp == latest]
    if len(latest_ids) != 5:
        raise ValueError("The bounded date reconstruction requires exactly five latest claims")
    pools = {
        "flat": lex[:pool_size],
        "lifecycle": [i for i in lex if claims[i].cid not in det][:pool_size],
        "gold": [i for i in lex if claims[i].cid not in gold_mask][:pool_size],
        "date_rerank": latest_ids,
    }
    found = {c: [] for c in CONDITIONS}
    tried = {}
    line = {i: f"- {c.text} (dated {int(c.timestamp)})" for i, c in enumerate(claims)}
    suffix = f"\n\nQuestion: {q}\nAnswer:"
    for label, pool in pools.items():
        labels = ("flat", "date_prompt") if label == "flat" else (label,)
        prefixes = {}
        for cond in labels:
            header = BUILDERS[cond](q, []).split("\n\nQuestion:", 1)[0] + "\n"
            prefixes[cond] = hashlib.sha256(f"{MODEL}\x0024\x00{header}".encode())
        count = 0
        for order in itertools.permutations(pool, 5):
            count += 1
            body = ("\n".join(line[i] for i in order) + suffix).encode()
            for cond in labels:
                h = prefixes[cond].copy()
                h.update(body)
                digest = h.hexdigest()
                if digest not in cache:
                    continue
                ctx = [claims[i] for i in order]
                prompt = BUILDERS[cond](q, ctx)
                if cache_key(prompt) != digest:
                    raise AssertionError("Fast reconstruction differs from the original builder")
                lineno, answer = cache[digest]
                found[cond].append({
                    "cache_key": digest, "cache_line_1based": lineno,
                    "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                    "context_claim_ids": [c.cid for c in ctx],
                    "answer": answer, "scores": strict_metrics(answer, gold, stale),
                    "query_key_claims": sum(c.gold_key == key for c in ctx),
                    "matching_key_latest_value_claims": sum(c.gold_key == key and _norm_value(c.value) == _norm_value(gold) for c in ctx),
                })
        tried[label] = count
    return {"question_index": qi, "question": q, "key": list(key), "gold_value": gold,
            "stale_values": stale, "found": found, "enumerated_contexts": tried,
            "forensic_pool_claim_ids": {k: [claims[i].cid for i in v] for k, v in pools.items()}}


def mcnemar_exact(wins, losses):
    n = wins + losses
    if n == 0:
        return 1.0
    return min(1.0, float(2 * sum(Fraction(math.comb(n, i), 2 ** n) for i in range(min(wins, losses) + 1))))


def paired(a, b):
    if len(a) != len(b) or not a:
        raise ValueError("Aligned nonempty scores required")
    n = len(a)
    diffs = [int(x) - int(y) for x, y in zip(a, b)]
    wins, losses = diffs.count(1), diffs.count(-1)
    rng = random.Random(260601)
    boots = sorted(sum(diffs[rng.randrange(n)] for _ in range(n)) / n for _ in range(10000))
    return {"n_paired": n, "wins": wins, "losses": losses, "ties": n - wins - losses,
            "mean_difference": sum(diffs) / n,
            "paired_question_bootstrap_percentile_95": [boots[249], boots[9749]],
            "bootstrap_draws": 10000, "bootstrap_seed": 260601,
            "exact_mcnemar_two_sided_p": mcnemar_exact(wins, losses),
            "inference_limit": "selected development subjects; no population or held-out superiority claim"}


def margins_only(a, b, n):
    cases = []
    for both in range(max(0, a + b - n), min(a, b) + 1):
        wins, losses = a - both, b - both
        cases.append({"both_correct": both, "wins": wins, "losses": losses,
                      "both_wrong": n - a - b + both, "exact_mcnemar_two_sided_p": mcnemar_exact(wins, losses)})
    return {"method_correct": a, "baseline_correct": b, "n": n,
            "mean_difference": (a - b) / n, "compatible_pairings": cases,
            "paired_case_labels_recovered": False}


def current_key_diagnostics():
    results = []
    for name in ("freshrag", "realworld", "wikidata", "templama", "wikidata_prose", "realwiki", "realprose"):
        claims = load_claims(str(ORIGINAL / f"data/{name}/claims.jsonl"))
        groups = defaultdict(list)
        for c in claims:
            if c.gold_key is not None and c.timestamp is not None:
                groups[c.gold_key].append(c)
        det = {e.older for e in SupersessionDetector().detect(claims)}
        no_value_gate = {e.older for e in SupersessionDetector(use_value=False).detect(claims)}
        original_gold = gold_superseded(claims)
        records = []
        for key, group in sorted(groups.items()):
            if len({_norm_value(c.value) for c in group}) < 2:
                continue
            newest = max(group, key=lambda c: c.timestamp)
            latest = max(c.timestamp for c in group)
            at_latest = [c for c in group if c.timestamp == latest]
            current_values = sorted({_norm_value(c.value) for c in at_latest})
            active = [c for c in group if c.cid not in det]
            matcher_active = [c for c in group if c.cid not in no_value_gate]
            records.append({
                "key": list(key), "n_claims": len(group), "latest_time": latest,
                "latest_claim_ids": [c.cid for c in at_latest],
                "latest_values": current_values,
                "unique_latest_value": len(current_values) == 1,
                "supplied_key_newest_correct_by_task_definition": len(current_values) == 1,
                "original_mask_retains_latest_claim": any(c.cid not in det for c in at_latest),
                "original_mask_retains_only_latest_value_within_key": bool(active) and all(_norm_value(c.value) in current_values for c in active),
                "same_matcher_newest_retains_latest_claim": any(c.cid not in no_value_gate for c in at_latest),
                "same_matcher_newest_retains_only_latest_value_within_key": bool(matcher_active) and all(_norm_value(c.value) in current_values for c in matcher_active),
                "original_retained_claim_ids": [c.cid for c in active],
                "same_matcher_retained_claim_ids": [c.cid for c in matcher_active],
            })
        def detection(mask):
            tp, fp, fn = len(mask & original_gold), len(mask - original_gold), len(original_gold - mask)
            return {"tp": tp, "fp": fp, "fn": fn,
                    "precision": tp / (tp + fp) if tp + fp else None,
                    "recall": tp / (tp + fn) if tp + fn else None,
                    "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None}
        results.append({"dataset": name, "n_claims": len(claims), "changing_keys": len(records),
                        "summary": {field: sum(r[field] for r in records) for field in (
                            "supplied_key_newest_correct_by_task_definition", "original_mask_retains_latest_claim",
                            "original_mask_retains_only_latest_value_within_key", "same_matcher_newest_retains_latest_claim",
                            "same_matcher_newest_retains_only_latest_value_within_key")},
                        "detection_original": detection(det), "detection_same_matcher_without_value_gate": detection(no_value_gate),
                        "records": records})
    return {"evaluation_kind": "supplied-key and supplied-value current-state diagnostics",
            "natural_qa": False, "new_model_calls": 0,
            "warning": "Key/value fields are released reference labels. Newest correctness follows the constructed task definition.",
            "same_matcher_baseline": "Original SupersessionDetector(use_value=False); no gold key used to build this mask.",
            "datasets": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pool-size", type=int, default=12)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--output", type=Path, default=ROOT / "results/original_reassessment_v26.json")
    args = parser.parse_args()
    if not 5 <= args.pool_size <= 16:
        parser.error("Bound forensic enumeration to 5..16 candidates")
    if args.output.exists():
        raise SystemExit("Refusing to overwrite an existing result")
    started = time.monotonic()
    cache_path = ORIGINAL / "data/cache/qwen_generations.jsonl"
    cache_rows = [json.loads(x) for x in cache_path.read_text().splitlines() if x]
    cache_keys = [r["key"] for r in cache_rows]
    if len(set(cache_keys)) != len(cache_keys):
        raise ValueError("Duplicate cache keys require explicit conflict handling")
    claims = load_claims(str(ORIGINAL / "data/realprose/claims.jsonl"))
    questions = _questions(claims)
    tasks = [(i, q, g, stale, key, args.pool_size) for i, (q, g, stale, key) in enumerate(questions)]
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        recovered = list(pool.map(recover_question, tasks))
    for r in recovered:
        consistency = consistent_assignment(r, claims)
        r["unfiltered_found"] = r["found"]
        r["found"] = consistency.pop("filtered_found")
        r["shared_ranking_consistency"] = consistency
    scores = {}
    for condition in CONDITIONS:
        unique = [r for r in recovered if len(r["found"][condition]) == 1]
        scores[condition] = {
            "unique_prompt_records": len(unique),
            "missing_prompt_records": sum(not r["found"][condition] for r in recovered),
            "ambiguous_prompt_records": sum(len(r["found"][condition]) > 1 for r in recovered),
            "scores_on_uniquely_recovered_subset": {
                metric: sum(r["found"][condition][0]["scores"][metric] for r in unique)
                for metric in ("original_name_hit", "original_name_clean", "normalized_full_string_equality",
                               "whole_token_gold_phrase", "whole_token_gold_phrase_without_stale_phrase",
                               "original_hit_without_gold_phrase")},
            "not_an_estimate_for_missing_records": True,
        }
    comparisons = {}
    for metric in ("original_name_hit", "normalized_full_string_equality", "whole_token_gold_phrase_without_stale_phrase"):
        for other in ("flat", "date_prompt", "date_rerank", "gold"):
            common = [r for r in recovered if len(r["found"]["lifecycle"]) == len(r["found"][other]) == 1]
            if common:
                comparisons[f"lifecycle_minus_{other}:{metric}"] = paired(
                    [r["found"]["lifecycle"][0]["scores"][metric] for r in common],
                    [r["found"][other][0]["scores"][metric] for r in common])
                comparisons[f"lifecycle_minus_{other}:{metric}"]["question_indices"] = [r["question_index"] for r in common]
    input_paths = sorted(set([cache_path, ORIGINAL / "data/realprose/claims.jsonl",
        ORIGINAL / "results/qa_realprose_qwen.json", ORIGINAL / "results/qa_realprose_extractive.json",
        ORIGINAL / "experiments/run_qa_realprose.py"] + list((ORIGINAL / "wikigraphrag").rglob("*.py")) +
        [ORIGINAL / f"data/{name}/claims.jsonl" for name in ("freshrag", "realworld", "wikidata", "templama", "wikidata_prose", "realwiki")]))
    result = {
        "version": "v26", "created_utc": datetime.now(timezone.utc).isoformat(),
        "evaluation_kind": "retrospective original-cache and task-definition audit",
        "new_model_calls": 0, "new_external_calls": 0, "held_out": False,
        "runtime_seconds": time.monotonic() - started,
        "script_sha256": sha(__file__),
        "inputs": {str(p.relative_to(ROOT.parent)): sha(p) for p in input_paths},
        "cache_inventory": {"rows": len(cache_rows), "unique_keys": len(set(cache_keys)),
                            "fields": sorted(set().union(*(r.keys() for r in cache_rows))),
                            "stored_prompts": False, "stored_condition_labels": False,
                            "stored_embedding_arrays": len(list((ORIGINAL / "data/cache").rglob("*.npy"))),
                            "cache_key_pins_checkpoint_revision": False,
                            "cache_key_pins_runtime_or_quantization": False},
        "prompt_reconstruction": {"pool_size": args.pool_size, "workers": args.workers,
            "acceptance_rule": "Exact SHA256 equality under the released model-name/token-cap/prompt cache key",
            "limitations": ["Search pools are bounded; missing reconstruction is not a missing generation.",
                "No ranking vectors are available to certify that each reconstructed context came from the final released retrieval ranking.",
                "Neutral prompts do not encode condition labels; lifecycle/gold attribution is conditional on the reconstructed allowed pools.",
                "Cache order and expected answer labels never decide a match.",
                "Strict string and phrase scores measure lexical agreement, not adjudicated person identity.",
                "All data were exposed previously; paired intervals remain descriptive development diagnostics."]},
        "n_questions": len(questions), "scores": scores, "paired_recovered_diagnostics": comparisons,
        "reported_aggregate_pairing_bounds": margins_only(30, 25, 35),
        "records": recovered, "current_state_diagnostics": current_key_diagnostics(),
        "released_text_current_support_audit": source_audit(claims, questions),
        "original_artifacts_modified": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(json.dumps({"output": str(args.output), "scores": scores,
                      "runtime_seconds": time.monotonic() - started}, indent=2))


if __name__ == "__main__":
    main()
