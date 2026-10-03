"""Temporally-consistent multi-hop (as-of join) benchmark.

Chains two time-varying relations so that answering requires resolving BOTH hops at the same
query time T:

    A  --parent organization-->  B(t)          (a firm's parent changes over time)
    B  --chief executive officer-->  C(t)        (that parent's CEO changes over time)

Question: "Who was the CEO of the parent organization of A in year T?" The gold answer is the
CEO of A's parent *as of T*. A current-value graph returns the present parent's present CEO
(wrong on historical T); flat retrieval mixes eras. Only as-of composition over the detector's
validity intervals is correct. Deterministic given ``seed``.

Run: ``python -m wikigraphrag.data.multihop_asof``.
"""

from __future__ import annotations

import json
import os
import random
from typing import List, Tuple

from ..core.claim import Claim
from .io import save_claims

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
OUT_DIR = os.path.join(ROOT, "data", "multihop_asof")

R1 = "parent organization"
R2 = "chief executive officer"

_FIRST = ["Alice", "Bob", "Carol", "David", "Eve", "Frank", "Grace", "Henry", "Iris", "Jack",
          "Karen", "Leo", "Mia", "Nate", "Olga", "Paul", "Quinn", "Rosa", "Sam", "Tina",
          "Uma", "Victor", "Wendy", "Xavier", "Yara", "Zane"]
_LAST = ["Adams", "Brooks", "Chen", "Diaz", "Evans", "Ford", "Gupta", "Hansen", "Ito", "Jones",
         "Kim", "Lopez", "Meyer", "Novak", "Owens", "Patel", "Rao", "Reyes", "Sato", "Turner"]
_GBASE = ["Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta", "Eta", "Theta", "Iota", "Kappa",
          "Lambda", "Mu", "Nu", "Xi", "Omicron", "Pi", "Rho", "Sigma", "Tau", "Upsilon",
          "Phi", "Chi", "Psi", "Omega"]
_CBASE = ["Acme", "Globex", "Initrode", "Umbrella", "Soylent", "Hooli", "Vandelay", "Wonka",
          "Stark", "Wayne", "Cyberdyne", "Tyrell", "Weyland", "Massive", "Aperture", "Prestige",
          "Oscorp", "Roxxon", "Nakatomi", "Abstergo", "Encom", "Primatech", "Rekall", "Virtucon",
          "Monarch", "Sirius", "Sterling", "Pawnee", "Sabre", "Gringotts", "Shinra", "Rossum",
          "Delos", "Wutai", "Spectre", "Bishop", "Zorg", "Duff", "Krusty", "Vance",
          "Ryder", "Pierce", "Halcyon", "Meridian", "Novacom", "Paradyne", "Quorum", "Zephyr"]


def build(seed: int = 0, n_companies: int = 40, write: bool = True) -> Tuple[List[Claim], list]:
    rng = random.Random(seed)
    people = [f"{f} {l}" for f in _FIRST for l in _LAST]
    rng.shuffle(people)
    groups = [f"{g} {suf}" for suf in ("Holdings", "Group", "Partners", "Capital") for g in _GBASE]
    rng.shuffle(groups)
    pi = gi = 0

    def person() -> str:
        nonlocal pi
        p = people[pi]
        pi += 1
        return p

    def group() -> str:
        nonlocal gi
        g = groups[gi]
        gi += 1
        return g

    claims: List[Claim] = []
    queries: list = []
    cid = 0

    def add(subject: str, relation: str, value: str, year: int) -> None:
        nonlocal cid
        claims.append(Claim(
            cid=f"mh:{cid}",
            text=f"In {year}, the {relation} of {subject} was {value}.",
            timestamp=float(year), doc_id=f"mh:{cid}",
            subject=subject, relation=relation, value=value, meta={"multihop": True},
        ))
        cid += 1

    for k in range(n_companies):
        a = f"{_CBASE[k]} Corporation"
        b1, b2 = group(), group()
        y0 = rng.randint(2000, 2005)
        y_switch = rng.randint(2015, 2019)
        add(a, R1, b1, y0)              # parent = B1 from y0
        add(a, R1, b2, y_switch)        # parent switches to B2 at y_switch

        # B1's CEO chronology, all within [y0, y_switch)
        yrs1 = sorted({y0, *rng.sample(range(y0 + 1, y_switch), rng.randint(1, 2))})
        b1_ceos = [(y, person()) for y in yrs1]
        for y, c in b1_ceos:
            add(b1, R2, c, y)
        # B2's CEO chronology, all from y_switch onward (distinct people)
        yrs2 = sorted({y_switch, *rng.sample(range(y_switch + 1, 2024), rng.randint(1, 2))})
        for y in yrs2:
            add(b2, R2, person(), y)

        # query at a historical T inside one of B1's CEO windows: parent must be B1 (not latest B2)
        i = rng.randrange(len(b1_ceos))
        lo = b1_ceos[i][0]
        hi = b1_ceos[i + 1][0] if i + 1 < len(b1_ceos) else y_switch
        T = lo if hi <= lo + 1 else (lo + hi) // 2
        queries.append({
            "company": a, "T": T, "gold_parent": b1, "gold_answer": b1_ceos[i][1],
            "question": f"Who was the {R2} of the {R1} of {a} in {T}?",
        })

    if write:
        os.makedirs(OUT_DIR, exist_ok=True)
        save_claims(claims, os.path.join(OUT_DIR, "claims.jsonl"))
        with open(os.path.join(OUT_DIR, "queries.json"), "w", encoding="utf-8") as fh:
            json.dump(queries, fh, indent=2)
    return claims, queries


if __name__ == "__main__":
    cs, qs = build()
    print(json.dumps({"claims": len(cs), "queries": len(qs)}, indent=2))
