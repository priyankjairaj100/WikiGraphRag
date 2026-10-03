"""Quantify how often long histories occur across the real chronology benchmarks.

History length m(key) = number of DISTINCT values a subject-relation key ever holds.
Key-weighted == query-weighted for the current-answer task (one current-answer
query per key), so "fraction of keys with m>=k" is exactly the fraction of
current-answer questions whose history is long.
"""
import json
import os
from collections import defaultdict, Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")


def load(path):
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def key_of(r):
    m = r.get("meta") or {}
    if m.get("key"):
        return str(m["key"])
    return f"{r.get('subject')}|{r.get('relation')}"


def analyze(name, path):
    if not os.path.exists(path):
        return None
    rows = load(path)
    values = defaultdict(set)          # key -> set of distinct values
    claims_per_key = Counter()         # key -> #claims (raw snapshots)
    for r in rows:
        v = r.get("value")
        if v is None:
            continue
        values[key_of(r)].add(str(v))
        claims_per_key[key_of(r)] += 1
    m_by_key = {k: len(vs) for k, vs in values.items()}
    hist = Counter(m_by_key.values())          # m -> #keys
    hist_claims = Counter(claims_per_key.values())  # #claims -> #keys
    n_keys = len(m_by_key)
    n_claims = len(rows)
    total_values = sum(m_by_key.values())

    def frac_ge(k):
        c = sum(n for m, n in hist.items() if m >= k)
        return c, (c / n_keys if n_keys else 0.0)

    out = {
        "name": name,
        "n_claims": n_claims,
        "n_keys": n_keys,
        "total_distinct_values": total_values,
        "mean_m": round(total_values / n_keys, 3) if n_keys else 0,
        "max_m": max(hist) if hist else 0,
        "hist": {int(m): int(hist[m]) for m in sorted(hist)},
        "hist_claims": {int(m): int(hist_claims[m]) for m in sorted(hist_claims)},
        "ge": {k: {"keys": frac_ge(k)[0], "frac": round(frac_ge(k)[1], 3)}
               for k in (2, 3, 4, 5)},
    }
    return out


def main():
    targets = [
        ("TempLAMA (3rd-party)", os.path.join(DATA, "templama", "claims.jsonl")),
        ("Wikidata", os.path.join(DATA, "wikidata", "claims.jsonl")),
        ("Real-world", os.path.join(DATA, "realworld", "claims.jsonl")),
        ("RealProse", os.path.join(DATA, "realprose", "claims.jsonl")),
        ("Wikidata-prose", os.path.join(DATA, "wikidata_prose", "claims.jsonl")),
    ]
    results = []
    for name, path in targets:
        r = analyze(name, path)
        if r is not None:
            results.append(r)

    lines = []
    for r in results:
        lines.append(f"### {r['name']}")
        lines.append(f"claims={r['n_claims']}  keys={r['n_keys']}  "
                     f"distinct_values={r['total_distinct_values']}  "
                     f"mean_m={r['mean_m']}  max_m={r['max_m']}")
        lines.append("  distinct-value m -> #keys : " +
                     "  ".join(f"m={m}:{n}" for m, n in r["hist"].items()))
        lines.append("  raw-claim  c -> #keys : " +
                     "  ".join(f"c={m}:{n}" for m, n in r["hist_claims"].items()))
        for k in (2, 3, 4, 5):
            g = r["ge"][k]
            lines.append(f"  m>={k}: {g['keys']} keys ({g['frac']*100:.1f}%)")
        # keys with exactly m==2
        exactly2 = r["hist"].get(2, 0)
        lines.append(f"  m==2 (exactly): {exactly2} keys")
        lines.append("")
    text = "\n".join(lines)
    with open(os.path.join(ROOT, "results", "_history_length_stats.txt"), "w",
              encoding="utf-8") as f:
        f.write(text)
    print(text)


if __name__ == "__main__":
    main()
