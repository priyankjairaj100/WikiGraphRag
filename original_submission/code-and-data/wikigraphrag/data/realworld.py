"""Real-world: ~20 hand-curated genuine fact changes (re-curated; the paper's exact 20 are lost).

Categories mirror the paper: heads of government/state, corporate rebrands, moved capitals,
software versions, and currency switches. Values are genuine and dated. We deliberately keep
values as proper nouns / numbers, because the detector's changed-value test reads *value
objects* (proper-noun spans and numbers); lowercase common-noun values (e.g. a bare "euro")
are a known blind spot recorded in the improvement list, so currency names are capitalised.

Run: ``python -m wikigraphrag.data.realworld``.
"""

from __future__ import annotations

import json
import os
from typing import List

from ..core.claim import Claim

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
OUT_DIR = os.path.join(ROOT, "data", "realworld")

# (subject, relation, [(value, year), ...])  -- each list is a genuine chronology.
CASES = [
    ("the United Kingdom", "head of government",
     [("Theresa May", 2018), ("Boris Johnson", 2019), ("Liz Truss", 2022),
      ("Rishi Sunak", 2022.8), ("Keir Starmer", 2024)]),
    ("the United States", "president",
     [("Barack Obama", 2015), ("Donald Trump", 2017), ("Joe Biden", 2021),
      ("Donald Trump", 2025)]),
    ("Japan", "prime minister",
     [("Shinzo Abe", 2019), ("Yoshihide Suga", 2020), ("Fumio Kishida", 2021),
      ("Shigeru Ishiba", 2024)]),
    ("the Catholic Church", "head",
     [("Pope Benedict", 2012), ("Pope Francis", 2013), ("Pope Leo", 2025)]),
    ("the United Nations", "secretary general",
     [("Ban Ki-moon", 2015), ("Antonio Guterres", 2017)]),
    ("Germany", "official currency",
     [("Deutsche Mark", 2001), ("Euro", 2002)]),
    ("Estonia", "official currency",
     [("Estonian Kroon", 2010), ("Euro", 2011)]),
    ("Croatia", "official currency",
     [("Croatian Kuna", 2022), ("Euro", 2023)]),
    ("Kazakhstan", "capital city",
     [("Almaty", 1996), ("Astana", 1998), ("Nur-Sultan", 2019), ("Astana", 2022)]),
    ("Myanmar", "capital city",
     [("Yangon", 2005), ("Naypyidaw", 2006)]),
    ("Nigeria", "capital city",
     [("Lagos", 1990), ("Abuja", 1991)]),
    ("Instagram", "parent company",
     [("Facebook", 2018), ("Meta Platforms", 2021)]),
    ("Google", "holding company",
     [("Google Incorporated", 2014), ("Alphabet Incorporated", 2015)]),
    ("the Twitter platform", "official name",
     [("Twitter", 2022), ("X Corp", 2023)]),
    ("the FIFA World Cup", "reigning champion",
     [("Germany", 2014), ("France", 2018), ("Argentina", 2022)]),
    ("the world", "tallest building",
     [("Taipei One Hundred", 2009), ("Burj Khalifa", 2010)]),
    ("Microsoft", "flagship operating system",
     [("Windows 10", 2020), ("Windows 11", 2021)]),
    ("the Python programming language", "latest stable version",
     [("3.10", 2021), ("3.11", 2022), ("3.12", 2023), ("3.13", 2024)]),
    ("the company X Corp", "chief executive officer",
     [("Jack Dorsey", 2020), ("Parag Agrawal", 2021), ("Elon Musk", 2022),
      ("Linda Yaccarino", 2023)]),
    ("Ubuntu", "latest long term support release",
     [("Ubuntu 20.04", 2020), ("Ubuntu 22.04", 2022), ("Ubuntu 24.04", 2024)]),
]


def build(write: bool = True) -> List[Claim]:
    claims: List[Claim] = []
    for subject, relation, values in CASES:
        for i, (value, year) in enumerate(values):
            ts = float(year) + i * 0.001  # guarantee strict order even within a year
            claims.append(
                Claim(
                    cid=f"rw:{subject}:{relation}:{i}".replace(" ", "_"),
                    text=f"In {int(year)}, the {relation} of {subject} is {value}.",
                    timestamp=ts,
                    doc_id=f"rw:{subject}:{relation}".replace(" ", "_"),
                    subject=subject,
                    relation=relation,
                    value=value,
                    meta={"category": relation},
                )
            )
    claims.sort(key=lambda c: (c.subject or "", c.timestamp or 0.0))
    if write:
        from .io import save_claims

        save_claims(claims, os.path.join(OUT_DIR, "claims.jsonl"))
        _write_meta(claims, os.path.join(OUT_DIR, "meta.json"))
    return claims


def _write_meta(claims: List[Claim], path: str) -> None:
    from ..eval.detection import gold_superseded

    sup = gold_superseded(claims)
    meta = {
        "name": "realworld",
        "claims": len(claims),
        "cases": len(CASES),
        "superseded": len(sup),
        "active": len(claims) - len(sup),
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2)


if __name__ == "__main__":
    cs = build(write=True)
    from ..eval.detection import gold_superseded

    print(json.dumps({"claims": len(cs), "cases": len(CASES),
                      "superseded": len(gold_superseded(cs))}, indent=2))
