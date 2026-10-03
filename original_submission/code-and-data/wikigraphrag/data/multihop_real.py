"""Build a REAL Wikidata two-hop as-of benchmark (no synthetic entities).

Chains two genuine, dated Wikidata relations:
    subsidiary  --parent organization (P749)-->  parent(t)  --CEO (P169)-->  ceo(t)
Question: "Who was the chief executive officer of the parent organization of {sub} in {T}?"
The gold answer is the CEO of the subsidiary's parent *as of T*, both hops resolved at T. This
is the real-data counterpart to the synthetic multi-hop benchmark, addressing the concern that
the capability rests on constructed entities. Uses the wbgetentities REST API (cached to disk,
deterministic offline after first run), resolving all entity-id values to English labels.

Run:  python -m wikigraphrag.data.multihop_real
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from ..core.claim import Claim
from .io import save_claims

API = "https://www.wikidata.org/w/api.php"
UA = "WikiGraphRAG-repro/0.1 (research reproduction of a RAG paper; local user)"

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
OUT_DIR = os.path.join(ROOT, "data", "multihop_real")
CACHE_DIR = os.path.join(OUT_DIR, "cache")

R1, R2 = "parent organization", "chief executive officer"

# Subsidiaries whose parent and/or parent's CEO varies over time (real acquisitions).
SUBSIDIARIES: List[str] = [
    "Pixar", "Marvel Entertainment", "Lucasfilm", "21st Century Fox", "ABC (television network)",
    "Whole Foods Market", "Twitch (service)", "Zappos", "Audible (service)", "Ring (company)",
    "LinkedIn", "GitHub", "Skype", "Mojang Studios", "ZeniMax Media", "Activision Blizzard",
    "YouTube", "DeepMind", "Fitbit", "Nest Labs", "Waze",
    "Beats Electronics", "Shazam (application)",
    "Sun Microsystems", "NetSuite", "Red Hat", "Instagram", "WhatsApp", "Oculus VR",
    "Volvo Cars", "Jaguar Cars", "Land Rover", "Boston Dynamics", "Arm (company)",
    "Nokia", "Motorola Mobility", "Tumblr", "Yahoo!", "LucasArts",
]

# properties -> qualifier keys for start/end
P_START, P_END = "P580", "P582"


def _api(params: Dict[str, str], cache_name: str, max_retries: int = 6) -> dict:
    cache_path = os.path.join(CACHE_DIR, cache_name)
    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    url = API + "?" + urllib.parse.urlencode({**params, "format": "json"})
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.load(resp)
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False)
            time.sleep(0.15)
            return data
        except urllib.error.HTTPError as exc:  # noqa: PERF203
            if exc.code == 429 and attempt < max_retries - 1:
                time.sleep(8 * (attempt + 1))
                continue
            raise
    raise RuntimeError("unreachable")


def _search(name: str) -> Optional[str]:
    key = "search_" + "".join(c if c.isalnum() else "_" for c in name)[:60] + ".json"
    data = _api({"action": "wbsearchentities", "search": name, "language": "en",
                 "type": "item", "limit": "1"}, key)
    hits = data.get("search", [])
    return hits[0]["id"] if hits else None


def _entities(qids: List[str]) -> dict:
    out: Dict[str, dict] = {}
    uniq = sorted(set(qids))
    for i in range(0, len(uniq), 50):
        batch = uniq[i : i + 50]
        key = f"ent_{i:04d}_{batch[0]}_{len(batch)}.json"
        data = _api({"action": "wbgetentities", "ids": "|".join(batch),
                     "props": "labels|claims", "languages": "en"}, key)
        out.update(data.get("entities", {}))
    return out


def _year(t: Optional[str]) -> Optional[int]:
    if not t:
        return None
    try:
        return int(t.lstrip("+")[:4])
    except ValueError:
        return None


def _chronology(ent: dict, prop: str) -> List[Tuple[str, Optional[int], Optional[int]]]:
    """Return [(value_qid, start_year, end_year)] for a time-qualified entity property."""
    out = []
    for st in ent.get("claims", {}).get(prop, []):
        snak = st.get("mainsnak", {})
        if snak.get("snaktype") != "value":
            continue
        dv = snak.get("datavalue", {})
        if dv.get("type") != "wikibase-entityid":
            continue
        vq = dv["value"].get("id")
        quals = st.get("qualifiers", {})
        sy = _year(quals.get(P_START, [{}])[0].get("datavalue", {}).get("value", {}).get("time")) \
            if quals.get(P_START) else None
        ey = _year(quals.get(P_END, [{}])[0].get("datavalue", {}).get("value", {}).get("time")) \
            if quals.get(P_END) else None
        out.append((vq, sy, ey))
    return out


def _label(ent: dict) -> Optional[str]:
    return ent.get("labels", {}).get("en", {}).get("value")


def build(write: bool = True) -> Tuple[List[Claim], list]:
    # 1) resolve subsidiary names -> QIDs, fetch their entities
    sub_qids = {name: _search(name) for name in SUBSIDIARIES}
    sub_qids = {n: q for n, q in sub_qids.items() if q}
    sub_ents = _entities(list(sub_qids.values()))

    # 2) parent chronologies (P749) per subsidiary
    parent_chron: Dict[str, list] = {}
    parent_qids: set = set()
    for name, sq in sub_qids.items():
        ent = sub_ents.get(sq, {})
        chron = [(pq, sy, ey) for (pq, sy, ey) in _chronology(ent, "P749") if pq]
        if not chron:
            continue
        parent_chron[name] = chron
        parent_qids.update(pq for pq, _, _ in chron)

    # 3) fetch parents, build their CEO (P169) chronologies
    parent_ents = _entities(sorted(parent_qids))
    ceo_chron: Dict[str, list] = {}
    ceo_qids: set = set()
    for pq in parent_qids:
        chron = [(cq, sy, ey) for (cq, sy, ey) in _chronology(parent_ents.get(pq, {}), "P169") if cq]
        ceo_chron[pq] = chron
        ceo_qids.update(cq for cq, _, _ in chron)

    # 4) resolve all labels (subsidiaries, parents, CEOs)
    label_ents = _entities(sorted(parent_qids | ceo_qids | set(sub_qids.values())))

    def lab(qid: Optional[str]) -> Optional[str]:
        return _label(label_ents.get(qid, {})) if qid else None

    # 5) emit claims + as-of queries
    claims: List[Claim] = []
    queries: list = []
    cid = 0

    def add(subject: str, relation: str, value: str, year: int, meta: dict) -> None:
        nonlocal cid
        claims.append(Claim(
            cid=f"mr:{cid}", text=f"In {year}, the {relation} of {subject} was {value}.",
            timestamp=float(year), doc_id=f"mr:{cid}",
            subject=subject, relation=relation, value=value, meta=meta))
        cid += 1

    def asof(chron_years, T):
        """Value in force at year T from [(val, start, end)] sorted by start."""
        best = None
        for val, sy, ey in chron_years:
            s = sy if sy is not None else -9999
            e = ey if ey is not None else 9999
            if s <= T <= e:
                if best is None or s > best[1]:
                    best = (val, s)
        return best[0] if best else None

    for name, chron in parent_chron.items():
        sub_label = lab(sub_qids[name]) or name
        # hop-1 claims: subsidiary's parent over time (need a start year each)
        h1 = sorted([(pq, sy) for pq, sy, _ in chron if sy is not None], key=lambda x: x[1])
        if not h1:
            continue
        # keep parents that have a datable CEO chronology
        usable_parents = [(pq, sy) for pq, sy in h1 if any(s is not None for _, s, _ in ceo_chron.get(pq, []))]
        if not usable_parents:
            continue
        for pq, sy in h1:
            add(sub_label, R1, lab(pq) or pq, sy, {"multihop_real": True, "hop": 1, "qid": pq})
        # emit hop-2 claims: each usable parent's CEO over time
        for pq in {p for p, _ in usable_parents}:
            plabel = lab(pq) or pq
            for cq, csy, _ in sorted([(c, s, e) for c, s, e in ceo_chron.get(pq, []) if s is not None],
                                     key=lambda x: x[1]):
                add(plabel, R2, lab(cq) or cq, csy, {"multihop_real": True, "hop": 2, "qid": cq})

        # parent in force at year T = latest acquisition start <= T
        def parent_at(T: int) -> Optional[str]:
            best = None
            for p2, s2, _ in chron:
                if s2 is not None and s2 <= T and (best is None or s2 > best[1]):
                    best = (p2, s2)
            return best[0] if best else None

        # naive current answer: latest parent -> its latest CEO
        cur_parent = parent_at(9999)
        cc = sorted([(c, s) for c, s, _ in ceo_chron.get(cur_parent, []) if s is not None],
                    key=lambda x: x[1])
        cur_ceo = cc[-1][0] if cc else None

        # candidate probe years: each CEO start of each usable parent, clamped to its acquisition
        cand: List[Tuple[int, str, str]] = []
        for pq2, acq in usable_parents:
            for cq, csy, _ in ceo_chron.get(pq2, []):
                if csy is not None:
                    cand.append((max(acq, csy), pq2, cq))
        cand.sort()

        gold, chosen = None, None
        for T, pq2, cq in cand:
            if parent_at(T) != pq2:
                continue
            ceo_at = asof(ceo_chron.get(pq2, []), T)
            if ceo_at is None:
                continue
            differs = (ceo_at != cur_ceo) or (pq2 != cur_parent)
            if differs:
                gold, chosen = ceo_at, (T, pq2)
                break
            if gold is None:
                gold, chosen = ceo_at, (T, pq2)
        if gold is None or chosen is None:
            continue
        T, pq2 = chosen
        queries.append({
            "company": sub_label, "T": T,
            "gold_parent": lab(pq2) or pq2, "gold_answer": lab(gold) or gold,
            "question": f"Who was the {R2} of the {R1} of {sub_label} in {T}?",
        })

    if write:
        os.makedirs(OUT_DIR, exist_ok=True)
        save_claims(claims, os.path.join(OUT_DIR, "claims.jsonl"))
        with open(os.path.join(OUT_DIR, "queries.json"), "w", encoding="utf-8") as fh:
            json.dump(queries, fh, indent=2)
    return claims, queries


if __name__ == "__main__":
    cl, qs = build()
    summary = {"claims": len(cl), "queries": len(qs),
               "subjects": sorted({q["company"] for q in qs})}
    with open(os.path.join(ROOT, "results", "_multihop_real_build.txt"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
