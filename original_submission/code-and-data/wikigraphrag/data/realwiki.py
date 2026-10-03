"""Real-Wikipedia prose supersession benchmark (reproducible, in-repo).

Each CEO's genuine English Wikipedia lead sentence becomes one claim about ``(org, chief
executive officer)``; chaining a company's CEOs in time yields real supersessions whose gold
comes from the known chronology, not from any parse of ours. Unlike the templated
``wikidata_prose`` set, these are unedited encyclopedic sentences -- biographical clauses,
pronoun coreference, and multiple competing proper nouns -- so they are the honest raw-prose
stress test for the zero-LLM detector. Lead extracts are cached to disk (deterministic and
offline after the first run) with 429 backoff.

Run:  python -m wikigraphrag.data.realwiki
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, List, Optional, Tuple

from ..core.claim import Claim
from .io import save_claims

API = "https://en.wikipedia.org/w/api.php"
UA = "WikiGraphRAG-repro/0.1 (research reproduction of a RAG paper; local user)"

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
OUT_DIR = os.path.join(ROOT, "data", "realwiki")
CACHE_DIR = os.path.join(OUT_DIR, "cache")

ROLE = "chief executive officer"

# (wikipedia_title, canonical_org, start_year, display_value). Real CEO chronologies.
CEOS: List[Tuple[str, str, int, str]] = [
    ("Bill Gates", "Microsoft", 1975, "Bill Gates"),
    ("Steve Ballmer", "Microsoft", 2000, "Steve Ballmer"),
    ("Satya Nadella", "Microsoft", 2014, "Satya Nadella"),
    ("Steve Jobs", "Apple", 1997, "Steve Jobs"),
    ("Tim Cook", "Apple", 2011, "Tim Cook"),
    ("Jeff Bezos", "Amazon", 1994, "Jeff Bezos"),
    ("Andy Jassy", "Amazon", 2021, "Andy Jassy"),
    ("Larry Page", "Google", 2011, "Larry Page"),
    ("Sundar Pichai", "Google", 2015, "Sundar Pichai"),
    ("Michael Eisner", "Disney", 1984, "Michael Eisner"),
    ("Bob Iger", "Disney", 2005, "Bob Iger"),
    ("Bob Chapek", "Disney", 2020, "Bob Chapek"),
    ("Ginni Rometty", "IBM", 2012, "Ginni Rometty"),
    ("Arvind Krishna", "IBM", 2020, "Arvind Krishna"),
    ("Indra Nooyi", "PepsiCo", 2006, "Indra Nooyi"),
    ("Ramon Laguarta", "PepsiCo", 2018, "Ramon Laguarta"),
    ("Brian Krzanich", "Intel", 2013, "Brian Krzanich"),
    ("Pat Gelsinger", "Intel", 2021, "Pat Gelsinger"),
    ("Howard Schultz", "Starbucks", 1986, "Howard Schultz"),
    ("Kevin Johnson (executive)", "Starbucks", 2017, "Kevin Johnson"),
    ("Parag Agrawal", "Twitter", 2021, "Parag Agrawal"),
    ("Linda Yaccarino", "Twitter", 2023, "Linda Yaccarino"),
    ("Mary Barra", "General Motors", 2014, "Mary Barra"),
    ("Rick Wagoner", "General Motors", 2000, "Rick Wagoner"),
    ("Dara Khosrowshahi", "Uber", 2017, "Dara Khosrowshahi"),
    ("Travis Kalanick", "Uber", 2010, "Travis Kalanick"),
]

_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")


def _api(params: Dict[str, str], cache_name: str, max_retries: int = 6) -> dict:
    cache_path = os.path.join(CACHE_DIR, cache_name)
    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    url = API + "?" + urllib.parse.urlencode({**params, "format": "json"})
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                data = json.load(resp)
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False)
            time.sleep(0.4)
            return data
        except urllib.error.HTTPError as exc:  # noqa: PERF203
            if exc.code == 429 and attempt < max_retries - 1:
                time.sleep(6 * (attempt + 1))
                continue
            raise
    raise RuntimeError("unreachable")


def _lead(title: str) -> str:
    key = "lead_" + re.sub(r"[^A-Za-z0-9]", "_", title)[:60] + ".json"
    data = _api({"action": "query", "prop": "extracts", "exintro": "1",
                 "explaintext": "1", "redirects": "1", "titles": title}, key)
    for _, p in data.get("query", {}).get("pages", {}).items():
        return p.get("extract", "") or ""
    return ""


def _operative(lead: str) -> str:
    """The lead sentence stating the CEO role, with parentheticals (birth dates, IPA,
    ``née``) stripped so they neither add distractors nor break sentence splitting."""
    lead = re.sub(r"\([^)]*\)", "", lead)
    lead = re.sub(r"\s+", " ", lead).strip()
    sents = [s.strip() for s in _SENT.split(lead) if s.strip()]
    if not sents:
        return ""
    head = sents[0]
    role = next((s for s in sents[:5]
                 if re.search(r"chief executive|\bCEO\b", s, re.IGNORECASE)), None)
    if role and role is not head:
        return (head + " " + role).strip()
    return role or head


def build(write: bool = True) -> List[Claim]:
    claims: List[Claim] = []
    for i, (title, org, year, value) in enumerate(CEOS):
        lead = _lead(title)
        text = _operative(lead)
        if not text:
            continue
        claims.append(Claim(
            cid=f"rw:{i}", text=text, timestamp=float(year), doc_id=f"rw:{i}",
            subject=org, relation=ROLE, value=value,
            meta={"realwiki": True, "title": title, "org": org}))
    if write:
        os.makedirs(OUT_DIR, exist_ok=True)
        save_claims(claims, os.path.join(OUT_DIR, "claims.jsonl"))
    return claims


if __name__ == "__main__":
    from ..eval.detection import evaluate_detection, gold_superseded

    cl = build()
    res = evaluate_detection(cl)
    gold = gold_superseded(cl)
    card = {
        "dataset": "realwiki", "n_claims": len(cl), "n_gold_superseded": len(gold),
        "precision": res["precision"], "recall": res["recall"], "f1": res["f1"],
        "tp": res["tp"], "fp": res["fp"], "fn": res["fn"],
        "note": "genuine English Wikipedia CEO lead paragraphs; gold from known chronologies",
    }
    with open(os.path.join(ROOT, "results", "detection_realwiki.json"), "w", encoding="utf-8") as fh:
        json.dump(card, fh, indent=2)
    print(json.dumps(card, indent=2))
