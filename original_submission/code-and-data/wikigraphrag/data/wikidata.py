"""Build the Wikidata detection benchmark from the wbgetentities REST API.

The paper's Wikidata benchmark is head-of-government and CEO chronologies (each older value
superseded by the next) plus capital / headquarters facts as stable *hard negatives*. We
re-curate to the same spec and scale (the original 44 entities are unrecoverable). We use the
REST API rather than WDQS/SPARQL because the query service is under an aggressive outage
rate-limit; every response is cached to disk so the build is deterministic and offline after
the first run.

Run:  ``python -m wikigraphrag.data.wikidata``
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, List, Optional, Tuple

from ..core.claim import Claim, parse_timestamp

API = "https://www.wikidata.org/w/api.php"
UA = "WikiGraphRAG-repro/0.1 (research reproduction of a RAG paper; contact: local user)"

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
OUT_DIR = os.path.join(ROOT, "data", "wikidata")
CACHE_DIR = os.path.join(OUT_DIR, "cache")

MIN_YEAR = 2000  # keep recent chronologies so histories are bounded

# Chronology subjects: head of government (P6) of countries, CEO (P169) of companies.
COUNTRIES: Dict[str, str] = {
    "Q145": "the United Kingdom", "Q142": "France", "Q183": "Germany", "Q38": "Italy",
    "Q29": "Spain", "Q16": "Canada", "Q408": "Australia", "Q17": "Japan",
    "Q668": "India", "Q155": "Brazil", "Q27": "Ireland", "Q664": "New Zealand",
    "Q55": "the Netherlands", "Q34": "Sweden", "Q20": "Norway", "Q41": "Greece",
    "Q45": "Portugal", "Q36": "Poland", "Q31": "Belgium", "Q40": "Austria",
    "Q33": "Finland", "Q35": "Denmark", "Q801": "Israel", "Q213": "the Czech Republic",
    "Q28": "Hungary", "Q43": "Turkey",
}
COMPANIES: Dict[str, str] = {
    "Q312": "Apple", "Q2283": "Microsoft", "Q3884": "Amazon", "Q37156": "IBM",
    "Q248": "Intel", "Q7414": "Disney", "Q37158": "Starbucks", "Q44294": "Ford",
    "Q66": "Boeing", "Q182477": "Nvidia", "Q188093": "Oracle", "Q334800": "PepsiCo",
}

# property -> (relation name, template). {s} subject, {v} value.
PROPS = {
    "P6": ("head of government", "The head of government of {s} is {v}."),
    "P169": ("chief executive officer", "The CEO of {s} is {v}."),
    "P36": ("capital", "The capital of {s} is {v}."),
    "P159": ("headquarters location", "The headquarters of {s} is in {v}."),
}


# ---------------------------------------------------------------------------
# REST API with on-disk cache and 429 backoff
# ---------------------------------------------------------------------------
def _api(params: Dict[str, str], cache_name: str, max_retries: int = 6) -> dict:
    cache_path = os.path.join(CACHE_DIR, cache_name)
    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    query = {**params, "format": "json"}
    url = API + "?" + urllib.parse.urlencode(query)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.load(resp)
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False)
            time.sleep(0.2)  # be polite
            return data
        except urllib.error.HTTPError as exc:  # noqa: PERF203
            if exc.code == 429 and attempt < max_retries - 1:
                time.sleep(10 * (attempt + 1))
                continue
            raise
    raise RuntimeError("unreachable")


def _get_entities(qids: List[str], props: str) -> dict:
    out: Dict[str, dict] = {}
    for i in range(0, len(qids), 50):
        batch = qids[i : i + 50]
        key = f"ent_{props}_{i:04d}_{'_'.join(batch[:1])}_{len(batch)}.json"
        data = _api(
            {"action": "wbgetentities", "ids": "|".join(batch), "props": props},
            key,
        )
        out.update(data.get("entities", {}))
    return out


# ---------------------------------------------------------------------------
# statement parsing
# ---------------------------------------------------------------------------
def _value_qid(stmt: dict) -> Optional[str]:
    snak = stmt.get("mainsnak", {})
    if snak.get("snaktype") != "value":
        return None
    dv = snak.get("datavalue", {})
    if dv.get("type") != "wikibase-entityid":
        return None
    return dv["value"].get("id")


def _start_time(stmt: dict) -> Optional[str]:
    quals = stmt.get("qualifiers", {}).get("P580", [])
    for q in quals:
        dv = q.get("datavalue", {})
        if dv.get("type") == "time":
            return dv["value"]["time"].lstrip("+")
    return None


def _has_end_time(stmt: dict) -> bool:
    return bool(stmt.get("qualifiers", {}).get("P582"))


def _chronology_statements(claims: dict, prop: str) -> List[Tuple[str, str]]:
    """Return ``[(value_qid, start_iso)]`` for a dated, recent, non-deprecated history."""
    out: List[Tuple[str, str]] = []
    for stmt in claims.get(prop, []):
        if stmt.get("rank") == "deprecated":
            continue
        vq = _value_qid(stmt)
        start = _start_time(stmt)
        if vq is None or start is None:
            continue
        year = int(start[0:4]) if start[0:4].isdigit() else 0
        if year < MIN_YEAR:
            continue
        out.append((vq, start))
    out.sort(key=lambda t: t[1])
    return out


def _current_statement(claims: dict, prop: str) -> Optional[str]:
    """The single current value QID for a stable fact (capital / HQ)."""
    stmts = [s for s in claims.get(prop, []) if s.get("rank") != "deprecated"]
    if not stmts:
        return None
    for s in stmts:  # explicit current value
        if s.get("rank") == "preferred":
            return _value_qid(s)
    for s in stmts:  # a value with no end date is the current one
        if not _has_end_time(s):
            return _value_qid(s)
    return _value_qid(stmts[-1])


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------
def build(write: bool = True) -> List[Claim]:
    subjects = {**COUNTRIES, **COMPANIES}
    entities = _get_entities(list(subjects), props="claims")

    # first pass: collect statements and every value QID we must label
    raw: List[Tuple[str, str, str, Optional[str]]] = []  # (subj_qid, prop, val_qid, start)
    need_labels = set(subjects)
    for sq, subj_label in subjects.items():
        claims = entities.get(sq, {}).get("claims", {})
        chrono_prop = "P6" if sq in COUNTRIES else "P169"
        for vq, start in _chronology_statements(claims, chrono_prop):
            raw.append((sq, chrono_prop, vq, start))
            need_labels.add(vq)
        neg_prop = "P36" if sq in COUNTRIES else "P159"
        vq = _current_statement(claims, neg_prop)
        if vq:
            raw.append((sq, neg_prop, vq, None))
            need_labels.add(vq)

    labels = _fetch_labels(sorted(need_labels))

    # second pass: render claims
    built: List[Claim] = []
    for sq, prop, vq, start in raw:
        subj = subjects[sq]
        val = labels.get(vq, vq)
        relation, template = PROPS[prop]
        ts = parse_timestamp(start) if start else float(_CURRENT_YEAR)
        cid = f"{prop}:{sq}:{vq}:{(start or 'current')[:10]}"
        built.append(
            Claim(
                cid=cid,
                text=template.format(s=subj, v=val),
                timestamp=ts,
                doc_id=cid,
                subject=subj,
                relation=relation,
                value=val,
                meta={"subject_qid": sq, "value_qid": vq, "property": prop, "start": start},
            )
        )

    built.sort(key=lambda c: (c.subject or "", c.relation or "", c.timestamp or 0.0))
    if write:
        from .io import save_claims

        path = os.path.join(OUT_DIR, "claims.jsonl")
        save_claims(built, path)
        _write_meta(built, os.path.join(OUT_DIR, "meta.json"))
    return built


def _fetch_labels(qids: List[str]) -> Dict[str, str]:
    labels: Dict[str, str] = {}
    for i in range(0, len(qids), 50):
        batch = qids[i : i + 50]
        key = f"lab_{i:04d}_{len(batch)}.json"
        data = _api(
            {"action": "wbgetentities", "ids": "|".join(batch),
             "props": "labels", "languages": "en"},
            key,
        )
        for qid, ent in data.get("entities", {}).items():
            lab = ent.get("labels", {}).get("en", {}).get("value")
            if lab:
                labels[qid] = lab
    return labels


_CURRENT_YEAR = 2025


def _gold_summary(claims: List[Claim]) -> Dict[str, int]:
    """Count gold superseded / active spans by Eq. (1) for a quick sanity print."""
    from ..eval.detection import gold_superseded  # local import to avoid cycle

    superseded = gold_superseded(claims)
    keys = {}
    for c in claims:
        keys.setdefault(c.gold_key, 0)
        keys[c.gold_key] += 1
    cases = sum(1 for k, n in keys.items() if n >= 2)
    return {
        "claims": len(claims),
        "superseded": len(superseded),
        "active": len(claims) - len(superseded),
        "cases_multi_value": cases,
        "distinct_keys": len(keys),
    }


def _write_meta(claims: List[Claim], path: str) -> None:
    meta = {"name": "wikidata", "min_year": MIN_YEAR, **_gold_summary(claims)}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2)


if __name__ == "__main__":
    cs = build(write=True)
    print(json.dumps(_gold_summary(cs), indent=2))
