"""Freeze a stratified discovery roster without reading question text or answers."""
import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

DOMAIN_SALT = "wikigraph-v26-20261006-domains\0"
ITEM_SALT = "wikigraph-v26-20261006-items\0"
REVISION = "1dba7027d0afa628993c6dd794e9b68c657a6fac"


def sha(value):
    return hashlib.sha256(value).hexdigest()


def select(inventory):
    assert inventory["revision"] == REVISION
    records = inventory["records"]
    keys = [(r["domain"], r["id"]) for r in records]
    assert len(keys) == len(set(keys))
    domains = sorted(inventory["domains"], key=lambda d: sha((DOMAIN_SALT + d).encode()))
    pools = {}
    for domain in domains:
        pools[domain] = sorted(
            [r for r in records if r["domain"] == domain and not r["previously_exposed_v25"]],
            key=lambda r: sha((ITEM_SALT + domain + "\0" + r["id"]).encode()),
        )
    chosen = []
    position = 0
    while len(chosen) < 60:
        before = len(chosen)
        for domain in domains:
            if position < len(pools[domain]):
                r = pools[domain][position]
                chosen.append({
                    "item_id": f"v26-{len(chosen)+1:03d}",
                    **r,
                    "within_domain_rank": position + 1,
                })
                if len(chosen) == 60:
                    break
        assert len(chosen) > before, "Not enough eligible records"
        position += 1
    return domains, chosen


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--inventory", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    raw = args.inventory.read_bytes()
    data = json.loads(raw)
    domains, records = select(data)
    result = {
        "schema_version": 1,
        "frozen_utc": datetime.now(timezone.utc).isoformat(),
        "dataset": data["dataset"],
        "revision": REVISION,
        "inventory_sha256": sha(raw),
        "script_sha256": sha(Path(__file__).read_bytes()),
        "role": "prospective stratified development feasibility; not confirmatory",
        "selection_uses": ["domain", "id", "previously_exposed_v25"],
        "selection_excludes": ["query content", "answers", "gold document IDs", "model outcomes"],
        "domain_salt": DOMAIN_SALT,
        "item_salt": ITEM_SALT,
        "sampling_rule": "Hash domains and eligible IDs separately; take domain round-robin until 60.",
        "domain_order": domains,
        "domain_counts": dict(Counter(r["domain"] for r in records)),
        "exposed_v25_excluded": 6,
        "replace_exclusions": False,
        "new_native_predictions_at_freeze": 0,
        "records": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(json.dumps({"items": len(records), "domain_counts": result["domain_counts"],
                      "sha256": sha(args.output.read_bytes())}, sort_keys=True))


if __name__ == "__main__":
    main()
