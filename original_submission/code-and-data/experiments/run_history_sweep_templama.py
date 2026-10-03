"""External-data long-history current-value QA: a wide-margin win on data we did not author.

The end-to-end TempLAMA money plot gains only ~$+0.03$ because TempLAMA histories are short
(median two values), so one stale distractor barely crowds the reader's budget. This runner
isolates the SAME which-value mechanism as the history-length sweep (main-paper Table 3) but on
the LONG-HISTORY slice of that very third-party TempLAMA (Dhingra et al. 2022) -- football
head-coach and squad chronologies and multi-post politicians with four to eight competing
values -- where budget pressure is genuine. Data and labels are not ours.

For each long-history chronology the reader is shown the competing present-tense values (each
with its date) and asked for the current one, under four conditions:
* flat        -- all values, chronological order (arbitrary wrt the current-value query), neutral;
* date_prompt -- all values + an explicit trust-the-most-recent-date instruction;
* date_rerank -- all values, newest-first order, neutral;
* lifecycle   -- only the active value (stale claims filtered before the budget).

date_prompt and date_rerank are the strongest cheap baselines (the reader sees the dates and is
told, or ordered, to trust the newest); lifecycle removes the stale values instead of
reordering them. Uses the local Qwen reader (cached) so the comparison is against a reader that
actually reads the whole context; the extractive reader is a deterministic flat-vs-lifecycle
cross-check.

Usage: python -m experiments.run_history_sweep_templama [--reader qwen|extractive]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from typing import Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wikigraphrag.core.claim import Claim  # noqa: E402
from wikigraphrag.data.io import load_claims  # noqa: E402
from wikigraphrag.eval.detection import _norm_value  # noqa: E402
from wikigraphrag.eval.qa import clean_match, exact_match  # noqa: E402
from wikigraphrag.eval.stats import paired_bootstrap  # noqa: E402
from wikigraphrag.readers.base import build_prompt_dated, build_prompt_neutral  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LONG = 4  # a chronology is "long-history" once it carries >= LONG competing values


def _changepoints(group: List[Claim]) -> List[Claim]:
    group = sorted(group, key=lambda c: c.timestamp)
    pts: List[Claim] = []
    for c in group:
        if not pts or _norm_value(pts[-1].value) != _norm_value(c.value):
            pts.append(c)
    return pts


def _question(query: str) -> str:
    blank = query.replace("_X_", "____").strip()
    return (f"{blank}  As of the most recent date shown, what single value fills the blank "
            f"(____)? Answer with just that value.")


def _make_reader(name: str):
    if name == "extractive":
        from wikigraphrag.readers.base import ExtractiveReader
        return ExtractiveReader()
    if name == "qwen":
        from wikigraphrag.readers.qwen import QwenReader
        return QwenReader(max_new_tokens=24)
    raise SystemExit(f"unknown reader {name!r}")


def _answer(reader, name: str, question: str, ctx: List[Claim], dated: bool) -> str:
    if name == "extractive":
        return reader.answer(question, ctx)
    prompt = build_prompt_dated(question, ctx) if dated else build_prompt_neutral(question, ctx)
    return reader.generate(prompt, max_new_tokens=24)


_CONDS = ("flat", "date_prompt", "date_rerank", "lifecycle")


def _contexts(seq: List[Claim]) -> Dict[str, Tuple[List[Claim], bool]]:
    oldest_first = list(seq)
    newest_first = list(reversed(seq))
    active = [seq[-1]]
    return {
        "flat": (oldest_first, False),
        "date_prompt": (oldest_first, True),
        "date_rerank": (newest_first, False),
        "lifecycle": (active, False),
    }


def _score_keys(reader, name: str, keyed: List[Tuple[object, List[Claim]]]
                ) -> Tuple[Dict[str, List[float]], Dict[str, List[float]]]:
    em: Dict[str, List[float]] = {c: [] for c in _CONDS}
    clean: Dict[str, List[float]] = {c: [] for c in _CONDS}
    for _key, seq in keyed:
        active = seq[-1]
        gold = active.value
        stale = [c.value for c in seq[:-1] if _norm_value(c.value) != _norm_value(gold)]
        question = _question(seq[0].meta.get("query", "_X_"))
        for cond, (ctx, dated) in _contexts(seq).items():
            ans = _answer(reader, name, question, ctx, dated)
            em[cond].append(float(exact_match(ans, gold)))
            clean[cond].append(float(clean_match(ans, gold, stale)))
    return em, clean


def run(reader_name: str, per_m_cap: int = 40) -> dict:
    claims = load_claims(os.path.join(ROOT, "data", "templama", "claims.jsonl"))
    by_key: Dict[object, List[Claim]] = defaultdict(list)
    for c in claims:
        if c.gold_key is not None and c.timestamp is not None:
            by_key[c.gold_key].append(c)
    chrono = {k: _changepoints(v) for k, v in by_key.items()}
    chrono = {k: v for k, v in chrono.items() if len(v) >= 2}

    reader = _make_reader(reader_name)

    # --- headline: pooled long-history slice (full histories, m >= LONG) ---
    long_keyed = [(k, v) for k, v in chrono.items() if len(v) >= LONG]
    em, clean = _score_keys(reader, reader_name, long_keyed)
    mean = lambda xs: round(sum(xs) / len(xs), 4) if xs else 0.0
    rel_counts = Counter(v[0].relation for _, v in long_keyed)
    pooled = {
        "n_keys": len(long_keyed),
        "mean_m": round(sum(len(v) for _, v in long_keyed) / max(1, len(long_keyed)), 2),
        "mean_flat_aep": round(sum(1.0 / len(v) for _, v in long_keyed) / max(1, len(long_keyed)), 4),
        "em": {c: mean(em[c]) for c in _CONDS},
        "clean_em": {c: mean(clean[c]) for c in _CONDS},
        "lifecycle_vs_flat": paired_bootstrap(em["lifecycle"], em["flat"]),
        "lifecycle_vs_date_rerank": paired_bootstrap(em["lifecycle"], em["date_rerank"]),
        "lifecycle_vs_date_prompt": paired_bootstrap(em["lifecycle"], em["date_prompt"]),
        "relations": dict(rel_counts),
    }

    # --- trend: per-m sweep (truncate to the latest m values) ---
    by_m: Dict[int, dict] = {}
    for m in (2, 3, 4, 5):
        keyed_m = [(k, v[-m:]) for k, v in sorted(chrono.items(), key=lambda kv: str(kv[0]))
                   if len(v) >= m]
        keyed_m = keyed_m[:per_m_cap]  # bound reader calls; the m>=4 headline above uses all keys
        if not keyed_m:
            continue
        em_m, _ = _score_keys(reader, reader_name, keyed_m)
        by_m[m] = {"n_keys": len(keyed_m), "flat_aep": round(1.0 / m, 4),
                   "em": {c: mean(em_m[c]) for c in _CONDS}}

    card = {
        "dataset": "templama", "source": "Yova/templama (Dhingra et al. 2022)",
        "reader": reader_name, "long_threshold_m": LONG,
        "long_history_pooled": pooled, "by_m": by_m,
    }
    out = os.path.join(ROOT, "results", f"history_templama_{reader_name}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(card, fh, indent=2, ensure_ascii=False)
    return card


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--reader", default="qwen")
    ap.add_argument("--per-m-cap", type=int, default=40)
    args = ap.parse_args()
    print(json.dumps(run(args.reader, args.per_m_cap), indent=2, ensure_ascii=False))
