#!/usr/bin/env python3
"""Preserve question-only disagreements before source reference review."""
import argparse
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    roster = json.loads((ROOT / "data/tempo_v26/roster.json").read_text())
    inputs, records = [], []
    for batch in range(1, 4):
        author_path = ROOT / f"data/tempo_v26/query_admission_{batch:02d}.json"
        review_path = ROOT / f"data/tempo_v26/query_admission_review_{batch:02d}.json"
        a, b = [json.loads(f.read_text()) for f in (author_path, review_path)]
        authors = a.get("records", a.get("items"))
        reviews = {r["item_id"]: r for r in b.get("records", b.get("items"))}
        for f in (author_path, review_path):
            inputs.append({"path": str(f.relative_to(ROOT)), "sha256": sha256(f.read_bytes()).hexdigest()})
        for item in authors:
            review = reviews[item["item_id"]]
            assert item["query_sha256"] == review["query_sha256"]
            reviewed = review.get("recommended_status", review.get("reviewer_recommended_status", review.get("reviewed_status")))
            possible = {item["status"], reviewed, *review.get("alternative_statuses", [])}
            route = bool(possible & {"target_present", "underspecified", "representation_capacity"})
            records.append({"item_id": item["item_id"], "query_sha256": item["query_sha256"],
                            "author_status": item["status"], "review_recommendation": reviewed,
                            "agreement": item["status"] == reviewed, "source_review_candidate": route,
                            "final_eligibility": None,
                            "reason": "Retain disputed or plausible targets for source review; no outcome-based replacement." if route else
                                      "Both question reviews identify no usable temporal target or missing essential images."})
    assert len(records) == 60 and len({r["item_id"] for r in records}) == 60
    result = {"schema_version": "v26.question_review_merge.1", "created_utc": datetime.now(timezone.utc).isoformat(),
              "script_sha256": sha256(Path(__file__).read_bytes()).hexdigest(), "inputs": inputs,
              "human_gold": False, "model_predictions_read": False,
              "source_review_route_does_not_authorize_inference": True,
              "all_sixty_retained": True, "records": records,
              "summary": {"question_review_agreements": sum(r["agreement"] for r in records),
                          "source_review_candidates": sum(r["source_review_candidate"] for r in records),
                          "author_counts": dict(Counter(r["author_status"] for r in records)),
                          "review_counts": dict(Counter(str(r["review_recommendation"]) for r in records))},
              "limits": ["The two roles are assistant annotations, not independent humans.",
                         "Temporal interpretation differs from answer content.",
                         "Missing answer dates do not imply missing requested temporal constraints.",
                         "Broad answer complexity alone does not establish representation failure.",
                         "Final references require the exact delivered packets and a second review."]}
    with args.output.open("x") as out:
        json.dump(result, out, indent=2)
        out.write("\n")
    print(json.dumps(result["summary"], sort_keys=True))


if __name__ == "__main__":
    main()
