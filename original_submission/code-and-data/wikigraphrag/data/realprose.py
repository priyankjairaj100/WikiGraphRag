"""RealProse: a diverse, multi-relation real-prose supersession benchmark (in-repo).

The single-relation ``realwiki`` set (CEO leads only) answered *whether* the zero-LLM detector
survives genuine encyclopedic prose. RealProse answers the harder question a reviewer actually
cares about: does it *generalize* across relation types and domains it was never tuned on? Each
claim is one genuine English Wikipedia lead sentence about an office-holder; chaining a subject's
holders in time yields real supersessions whose gold comes from the known chronology (its
*order*, not any parse of ours). Five relation families, thirty subjects, four continents:

* company chief executive officer (reusing the ``realwiki`` chronologies);
* national head of government (prime minister / chancellor: UK, DE, CA, IN, JP, AU, ES, NZ);
* national head of state (president: US, FR, BR, MX);
* football club manager (Manchester United, Arsenal, Liverpool, Manchester City);
* international-organization leader (UN, Federal Reserve, FIFA, the papacy, ECB, World Bank, IOC).

All claims share one pooled index, so every same-relation pair with a *different* subject
("President of the United States" vs "President of FIFA", eight distinct national "prime
minister" timelines) is a hard negative the detector must not cross-link -- the discriminative
regime of the main paper, now on in-the-wild prose. Lead extracts are cached to disk
(deterministic and offline after the first run) with 429 backoff.

Run:  python -m wikigraphrag.data.realprose
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from ..core.claim import Claim
from .io import save_claims
from .realwiki import CEOS

API = "https://en.wikipedia.org/w/api.php"
UA = "WikiGraphRAG-repro/0.1 (research reproduction of a RAG paper; local user)"

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
OUT_DIR = os.path.join(ROOT, "data", "realprose")
CACHE_DIR = os.path.join(OUT_DIR, "cache")

CEO_CUE = r"chief executive|\bCEO\b"
GOV_CUE = r"prime minister|chancellor|premier|head of government|taoiseach"
PRES_CUE = r"president"
MGR_CUE = r"manager|head coach|coach|manage[ds]"
SG_CUE = r"secretary.general"
CHAIR_CUE = r"chair"
POPE_CUE = r"pope|catholic church|bishop of rome"

# (subject, relation, category, role_cue, [(wikipedia_title, start_year, display_value), ...]).
# Years give within-subject order (fractional where a succession is same-year); only the ORDER
# is load-bearing for detection gold, so the chronologies are robust to off-by-months.
CHRONOLOGIES: List[Tuple[str, str, str, str, List[Tuple[str, float, str]]]] = [
    # -- national head of government -------------------------------------------------
    ("United Kingdom", "prime minister", "national_head_of_gov", GOV_CUE, [
        ("Tony Blair", 1997, "Tony Blair"), ("Gordon Brown", 2007, "Gordon Brown"),
        ("David Cameron", 2010, "David Cameron"), ("Theresa May", 2016, "Theresa May"),
        ("Boris Johnson", 2019, "Boris Johnson"), ("Liz Truss", 2022.7, "Liz Truss"),
        ("Rishi Sunak", 2022.8, "Rishi Sunak"), ("Keir Starmer", 2024, "Keir Starmer")]),
    ("Germany", "chancellor", "national_head_of_gov", GOV_CUE, [
        ("Helmut Kohl", 1982, "Helmut Kohl"), ("Gerhard Schröder", 1998, "Gerhard Schröder"),
        ("Angela Merkel", 2005, "Angela Merkel"), ("Olaf Scholz", 2021, "Olaf Scholz"),
        ("Friedrich Merz", 2025, "Friedrich Merz")]),
    ("Canada", "prime minister", "national_head_of_gov", GOV_CUE, [
        ("Jean Chrétien", 1993, "Jean Chrétien"), ("Paul Martin", 2003, "Paul Martin"),
        ("Stephen Harper", 2006, "Stephen Harper"), ("Justin Trudeau", 2015, "Justin Trudeau"),
        ("Mark Carney", 2025, "Mark Carney")]),
    ("India", "prime minister", "national_head_of_gov", GOV_CUE, [
        ("Atal Bihari Vajpayee", 1998, "Atal Bihari Vajpayee"),
        ("Manmohan Singh", 2004, "Manmohan Singh"), ("Narendra Modi", 2014, "Narendra Modi")]),
    ("Japan", "prime minister", "national_head_of_gov", GOV_CUE, [
        ("Shinzō Abe", 2012, "Shinzō Abe"), ("Yoshihide Suga", 2020, "Yoshihide Suga"),
        ("Fumio Kishida", 2021, "Fumio Kishida"), ("Shigeru Ishiba", 2024, "Shigeru Ishiba")]),
    ("Australia", "prime minister", "national_head_of_gov", GOV_CUE, [
        ("John Howard", 1996, "John Howard"), ("Kevin Rudd", 2007, "Kevin Rudd"),
        ("Julia Gillard", 2010, "Julia Gillard"), ("Tony Abbott", 2013, "Tony Abbott"),
        ("Malcolm Turnbull", 2015, "Malcolm Turnbull"), ("Scott Morrison", 2018, "Scott Morrison"),
        ("Anthony Albanese", 2022, "Anthony Albanese")]),
    ("Spain", "prime minister", "national_head_of_gov", GOV_CUE, [
        ("José Luis Rodríguez Zapatero", 2004, "Zapatero"), ("Mariano Rajoy", 2011, "Mariano Rajoy"),
        ("Pedro Sánchez", 2018, "Pedro Sánchez")]),
    ("New Zealand", "prime minister", "national_head_of_gov", GOV_CUE, [
        ("Jacinda Ardern", 2017, "Jacinda Ardern"), ("Chris Hipkins", 2023, "Chris Hipkins"),
        ("Christopher Luxon", 2023.9, "Christopher Luxon")]),
    # -- national head of state ------------------------------------------------------
    ("United States", "president", "national_head_of_state", PRES_CUE, [
        ("Bill Clinton", 1993, "Bill Clinton"), ("George W. Bush", 2001, "George W. Bush"),
        ("Barack Obama", 2009, "Barack Obama"), ("Donald Trump", 2017, "Donald Trump"),
        ("Joe Biden", 2021, "Joe Biden"), ("Donald Trump", 2025, "Donald Trump")]),
    ("France", "president", "national_head_of_state", PRES_CUE, [
        ("Jacques Chirac", 1995, "Jacques Chirac"), ("Nicolas Sarkozy", 2007, "Nicolas Sarkozy"),
        ("François Hollande", 2012, "François Hollande"), ("Emmanuel Macron", 2017, "Emmanuel Macron")]),
    ("Brazil", "president", "national_head_of_state", PRES_CUE, [
        ("Luiz Inácio Lula da Silva", 2003, "Lula da Silva"), ("Dilma Rousseff", 2011, "Dilma Rousseff"),
        ("Michel Temer", 2016, "Michel Temer"), ("Jair Bolsonaro", 2019, "Jair Bolsonaro"),
        ("Luiz Inácio Lula da Silva", 2023, "Lula da Silva")]),
    ("Mexico", "president", "national_head_of_state", PRES_CUE, [
        ("Vicente Fox", 2000, "Vicente Fox"), ("Felipe Calderón", 2006, "Felipe Calderón"),
        ("Enrique Peña Nieto", 2012, "Enrique Peña Nieto"),
        ("Andrés Manuel López Obrador", 2018, "López Obrador"),
        ("Claudia Sheinbaum", 2024, "Claudia Sheinbaum")]),
    # -- football club manager -------------------------------------------------------
    ("Manchester United", "manager", "club_manager", MGR_CUE, [
        ("Alex Ferguson", 1986, "Alex Ferguson"), ("David Moyes", 2013, "David Moyes"),
        ("Louis van Gaal", 2014, "Louis van Gaal"), ("José Mourinho", 2016, "José Mourinho"),
        ("Ole Gunnar Solskjær", 2018, "Ole Gunnar Solskjær"), ("Erik ten Hag", 2022, "Erik ten Hag"),
        ("Ruben Amorim", 2024, "Ruben Amorim")]),
    ("Arsenal", "manager", "club_manager", MGR_CUE, [
        ("Arsène Wenger", 1996, "Arsène Wenger"), ("Unai Emery", 2018, "Unai Emery"),
        ("Mikel Arteta", 2019, "Mikel Arteta")]),
    ("Liverpool", "manager", "club_manager", MGR_CUE, [
        ("Rafael Benítez", 2004, "Rafael Benítez"), ("Roy Hodgson", 2010, "Roy Hodgson"),
        ("Kenny Dalglish", 2011, "Kenny Dalglish"), ("Brendan Rodgers", 2012, "Brendan Rodgers"),
        ("Jürgen Klopp", 2015, "Jürgen Klopp"), ("Arne Slot", 2024, "Arne Slot")]),
    ("Manchester City", "manager", "club_manager", MGR_CUE, [
        ("Roberto Mancini", 2009, "Roberto Mancini"), ("Manuel Pellegrini", 2013, "Manuel Pellegrini"),
        ("Pep Guardiola", 2016, "Pep Guardiola")]),
    # -- international-organization leader --------------------------------------------
    ("United Nations", "secretary-general", "org_leader", SG_CUE, [
        ("Boutros Boutros-Ghali", 1992, "Boutros-Ghali"), ("Kofi Annan", 1997, "Kofi Annan"),
        ("Ban Ki-moon", 2007, "Ban Ki-moon"), ("António Guterres", 2017, "António Guterres")]),
    ("Federal Reserve", "chair", "org_leader", CHAIR_CUE, [
        ("Alan Greenspan", 1987, "Alan Greenspan"), ("Ben Bernanke", 2006, "Ben Bernanke"),
        ("Janet Yellen", 2014, "Janet Yellen"), ("Jerome Powell", 2018, "Jerome Powell")]),
    ("FIFA", "president", "org_leader", PRES_CUE, [
        ("João Havelange", 1974, "João Havelange"), ("Sepp Blatter", 1998, "Sepp Blatter"),
        ("Gianni Infantino", 2016, "Gianni Infantino")]),
    ("Catholic Church", "pope", "org_leader", POPE_CUE, [
        ("Pope John Paul II", 1978, "John Paul II"), ("Pope Benedict XVI", 2005, "Benedict XVI"),
        ("Pope Francis", 2013, "Francis"), ("Pope Leo XIV", 2025, "Leo XIV")]),
    ("European Central Bank", "president", "org_leader", PRES_CUE, [
        ("Wim Duisenberg", 1998, "Wim Duisenberg"), ("Jean-Claude Trichet", 2003, "Jean-Claude Trichet"),
        ("Mario Draghi", 2011, "Mario Draghi"), ("Christine Lagarde", 2019, "Christine Lagarde")]),
    ("World Bank", "president", "org_leader", PRES_CUE, [
        ("James Wolfensohn", 1995, "James Wolfensohn"), ("Paul Wolfowitz", 2005, "Paul Wolfowitz"),
        ("Robert Zoellick", 2007, "Robert Zoellick"), ("Jim Yong Kim", 2012, "Jim Yong Kim"),
        ("David Malpass", 2019, "David Malpass"), ("Ajay Banga", 2023, "Ajay Banga")]),
    ("International Olympic Committee", "president", "org_leader", PRES_CUE, [
        ("Juan Antonio Samaranch", 1980, "Juan Antonio Samaranch"), ("Jacques Rogge", 2001, "Jacques Rogge"),
        ("Thomas Bach", 2013, "Thomas Bach"), ("Kirsty Coventry", 2025, "Kirsty Coventry")]),
]

_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")


def _records() -> List[Tuple[str, str, str, str, str, float, str]]:
    """Flatten to (title, subject, relation, category, role_cue, year, display)."""
    out: List[Tuple[str, str, str, str, str, float, str]] = []
    for title, org, year, disp in CEOS:
        out.append((title, org, "chief executive officer", "company_ceo", CEO_CUE, float(year), disp))
    for subject, relation, category, cue, holders in CHRONOLOGIES:
        for title, year, disp in holders:
            out.append((title, subject, relation, category, cue, float(year), disp))
    return out


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


def _operative(lead: str, role_cue: str) -> str:
    """Head sentence plus the first sentence that names the role, parentheticals stripped."""
    lead = re.sub(r"\([^)]*\)", "", lead)
    lead = re.sub(r"\s+", " ", lead).strip()
    sents = [s.strip() for s in _SENT.split(lead) if s.strip()]
    if not sents:
        return ""
    head = sents[0]
    role = next((s for s in sents[:6] if re.search(role_cue, s, re.IGNORECASE)), None)
    if role and role is not head:
        return (head + " " + role).strip()
    return role or head


def build(write: bool = True) -> List[Claim]:
    claims: List[Claim] = []
    for i, (title, subject, relation, category, cue, year, value) in enumerate(_records()):
        lead = _lead(title)
        text = _operative(lead, cue)
        if not text:
            continue
        claims.append(Claim(
            cid=f"rp:{i}", text=text, timestamp=year, doc_id=f"rp:{i}",
            subject=subject, relation=relation, value=value,
            meta={"realprose": True, "title": title, "subject": subject,
                  "relation": relation, "category": category}))
    if write:
        os.makedirs(OUT_DIR, exist_ok=True)
        save_claims(claims, os.path.join(OUT_DIR, "claims.jsonl"))
    return claims


def _f1(tp: int, fp: int, fn: int) -> Tuple[float, float, float]:
    p = tp / (tp + fp) if (tp + fp) else 1.0
    r = tp / (tp + fn) if (tp + fn) else 1.0
    f = 2 * p * r / (p + r) if (p + r) else 0.0
    return round(p, 4), round(r, 4), round(f, 4)


def evaluate(claims: List[Claim]) -> dict:
    from ..detect import SupersessionDetector
    from ..eval.detection import gold_superseded

    cat = {c.cid: c.meta.get("category", "?") for c in claims}
    edges = SupersessionDetector().detect(claims)
    detected = {e.older for e in edges}
    gold = gold_superseded(claims)

    tp, fp, fn = len(detected & gold), len(detected - gold), len(gold - detected)
    p, r, f = _f1(tp, fp, fn)

    per: Dict[str, Dict[str, int]] = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})
    for cid in detected & gold:
        per[cat[cid]]["tp"] += 1
    for cid in detected - gold:
        per[cat[cid]]["fp"] += 1
    for cid in gold - detected:
        per[cat[cid]]["fn"] += 1
    per_cat = {}
    for k, v in sorted(per.items()):
        pp, rr, ff = _f1(v["tp"], v["fp"], v["fn"])
        per_cat[k] = {"precision": pp, "recall": rr, "f1": ff, **v}

    by_cat_claims: Dict[str, int] = defaultdict(int)
    for c in claims:
        by_cat_claims[cat[c.cid]] += 1

    return {
        "dataset": "realprose",
        "n_claims": len(claims), "n_subjects": len(CHRONOLOGIES) + len({o for _, o, _, _ in CEOS}),
        "n_categories": len(by_cat_claims), "claims_per_category": dict(sorted(by_cat_claims.items())),
        "n_gold_superseded": len(gold),
        "precision": p, "recall": r, "f1": f, "tp": tp, "fp": fp, "fn": fn,
        "per_category": per_cat,
        "false_positives": sorted(detected - gold),
        "false_negatives": sorted(gold - detected),
        "note": "genuine English Wikipedia leads across 5 relation families; gold from known "
                "chronology order; single pooled index (same-relation different-subject pairs "
                "are hard negatives)",
    }


if __name__ == "__main__":
    cl = build()
    card = evaluate(cl)
    with open(os.path.join(ROOT, "results", "detection_realprose.json"), "w", encoding="utf-8") as fh:
        json.dump(card, fh, indent=2, ensure_ascii=False)
    print(json.dumps({k: v for k, v in card.items()
                      if k not in ("false_positives", "false_negatives")}, indent=2, ensure_ascii=False))
