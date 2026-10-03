"""Table 2: the zero-LLM heuristic vs a prompted Qwen judge on identical span pairs.

Usage: ``python -m experiments.run_judge wikidata``. Writes results/judge_<name>.json.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wikigraphrag.data.io import load_claims  # noqa: E402
from wikigraphrag.eval.judge import build_pairs, heuristic_labels, judge_labels, prf  # noqa: E402
from wikigraphrag.readers.qwen import QwenReader  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _make_judge(model: str):
    if model == "qwen":
        return QwenReader(max_new_tokens=8)
    from wikigraphrag.readers.openai_reader import OpenAIReader
    return OpenAIReader(model_name=model, max_new_tokens=8)


def run(name: str, n_neg: int, judge_model: str = "qwen") -> dict:
    claims = load_claims(os.path.join(ROOT, "data", name, "claims.jsonl"))
    pairs = build_pairs(claims, n_neg=n_neg)
    gold = [is_sup for _, _, is_sup in pairs]

    heur = heuristic_labels(pairs)
    judge = judge_labels(pairs, _make_judge(judge_model))

    card = {
        "dataset": name,
        "judge_model": judge_model,
        "n_pairs": len(pairs),
        "n_positive": sum(gold),
        "heuristic": prf(heur, gold),
        "llm_judge": prf(judge, gold),
    }
    tag = judge_model.replace("/", "-")
    out = os.path.join(ROOT, "results", f"judge_{name}_{tag}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(card, fh, indent=2)
    return card


if __name__ == "__main__":
    dataset = sys.argv[1] if len(sys.argv) > 1 else "wikidata"
    n_neg = int(sys.argv[2]) if len(sys.argv) > 2 else 120
    judge_model = sys.argv[3] if len(sys.argv) > 3 else "qwen"
    print(json.dumps(run(dataset, n_neg, judge_model), indent=2))
