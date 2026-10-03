"""Pure-regex text primitives for the supersession detector.

The split mirrors Section 3:

* **PHRASES** -- maximal capitalized spans ("United Kingdom", "Bank of England"); these
  name subjects and string-valued objects. Phrase-level matching is what stops two subjects
  sharing only a generic suffix ("... Liberal Party" vs "... Republican Party") from
  cross-linking.
* **VALUES** (value objects) -- numbers (unit-normalised) plus proper-noun spans.
* **FRAME** -- the relation words left after removing value objects and copula/article
  function words, e.g. "head of government of".
"""

from __future__ import annotations

import re
from typing import List, Optional, Set

# Words dropped from the FRAME. Articles, copulas, and prepositions are all function words:
# a shared preposition ("... **of** ...") must not by itself make two relations match, or
# "capital of X" and "head of government of X" cross-link on the bare "of". The relation is
# carried by content words ("head", "government", "capital"), so only those remain.
_FRAME_DROP = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "its", "their", "his", "her", "now", "currently", "new",
    # prepositions / conjunctions (function words, never the relation itself)
    "of", "for", "to", "in", "on", "at", "with", "from", "by", "as", "into",
    "onto", "per", "over", "under", "about", "against", "between", "during", "and", "or",
}

# Broader stopword set for the string substitution test.
_CONTENT_STOP = _FRAME_DROP | {
    "of", "for", "to", "in", "on", "at", "with", "from", "and", "or",
    "this", "that", "it", "has", "have", "had", "will", "would",
}

# Lowercase tokens allowed *inside* a proper-noun phrase ("Bank of England").
_CONNECTORS = {
    "of", "the", "and", "for", "de", "von", "van", "der", "den", "da", "di",
    "del", "della", "la", "le", "el", "al", "bin", "ibn", "san", "santa",
}

# Capitalized words that are *not* proper nouns: sentence-initial function words and the
# like. A phrase made only of these (e.g. a leading "The") is dropped, and they do not count
# toward the ">= 2 proper nouns" gate. This stops "The UK ..." and "The France ..." from
# cross-linking on a shared leading "The".
_PHRASE_STOP = {
    "the", "a", "an", "this", "that", "these", "those", "it", "its", "his", "her",
    "their", "he", "she", "they", "we", "you", "i", "in", "on", "at", "by", "for",
    "to", "of", "and", "but", "as", "with", "from", "after", "before", "when",
    "while", "however", "moreover", "now",
    # sentence-initial adverbs / recency-cue openers that are never subjects
    "effective", "revised", "updated", "amended", "following", "per", "beginning",
    "starting", "since", "under", "previously", "formerly", "subsequently", "later",
    # calendar words are temporal markers, not subjects (a shared "July" must not link two
    # unrelated entities that happen to be dated the same month)
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
}

# Nationality/demonym adjectives are descriptors, never subjects: a shared "American" must not
# link two unrelated people. Hyphenated forms ("Indian-American", "Indian-born") count too.
_DEMONYMS = {
    "american", "british", "english", "scottish", "welsh", "irish", "canadian",
    "australian", "indian", "french", "german", "italian", "spanish", "portuguese",
    "dutch", "belgian", "swiss", "austrian", "swedish", "norwegian", "danish", "finnish",
    "chinese", "japanese", "korean", "taiwanese", "singaporean", "russian", "polish",
    "greek", "turkish", "israeli", "brazilian", "mexican", "argentine", "european",
    "african", "asian", "hispanic", "latino", "latina", "pakistani", "bangladeshi", "born",
}

# Role acronyms are relation words, not proper nouns: "CEO of Apple" is (relation, subject),
# not one glued phrase, and each expands to canonical relation tokens so a lead that writes
# only "CEO" still matches one that writes "chief executive officer".
_ROLE_ACRONYMS = {
    "ceo": {"chief", "executive", "officer"}, "cfo": {"chief", "financial", "officer"},
    "cto": {"chief", "technology", "officer"}, "coo": {"chief", "operating", "officer"},
    "cmo": {"chief", "marketing", "officer"}, "cio": {"chief", "information", "officer"},
}

# Spelled-out office titles are relation words, not subjects -- the same normalization as the
# role acronyms above, extended to full titles. This makes the parser robust to the arbitrary
# capitalization of titles across encyclopedic prose: "Chancellor of Germany" (capitalized,
# otherwise glued into a subject phrase) and "chancellor of Germany" (lowercase) both yield the
# clean subject "Germany" and the relation token "chancellor". Ambiguous words that also occur
# inside entity names ("General" in "General Motors", "Premier" in "Premier League") are excluded.
_ROLE_WORDS = {
    "president", "prime", "minister", "chancellor", "chief", "executive", "officer",
    "secretary", "secretary-general", "chairman", "chairwoman", "chair", "pope", "manager",
    "coach",
}


def _is_demonym(tok: str) -> bool:
    t = tok.lower().strip("'\u2019.,;:")
    if t in _DEMONYMS:
        return True
    parts = [p for p in re.split(r"[-\u2013]", t) if p]
    return len(parts) > 1 and all(p in _DEMONYMS for p in parts)


def _is_phrase_token(tok: str) -> bool:
    """A capitalized token that can form part of a subject/value proper-noun phrase; role
    acronyms and spelled-out office titles are excluded so they fall into the relation frame
    instead (a capitalized "Chancellor of Germany" then matches a lowercase "chancellor of
    Germany", and the subject is the clean entity either way)."""
    lw = tok.lower().strip("'\u2019.,;:")
    return _is_cap(tok) and lw not in _ROLE_ACRONYMS and lw not in _ROLE_WORDS


# Trailing corporate designators are dropped so an org named in full ("Twitter, Inc.",
# "Walt Disney Company") matches the same org named plainly ("Twitter", "Walt Disney").
_CORP_SUFFIX = {
    "inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "limited",
    "llc", "plc", "lp", "llp", "group", "holdings", "gmbh", "ag", "sa", "nv", "se",
}


def _strip_corp(phrase: str) -> str:
    words = phrase.split()
    while len(words) > 1 and words[-1].lower().strip("'\u2019.,;:") in _CORP_SUFFIX:
        words.pop()
    return " ".join(words)


_WORD = re.compile(r"[A-Za-z\u00c0-\u024f][A-Za-z\u00c0-\u024f.'\u2019\-]*")
# A number with optional leading currency and optional trailing unit/scale word.
_NUMBER = re.compile(
    r"(?P<cur>[£$€\u00a3]?)\s*"
    r"(?P<num>\d[\d,]*(?:\.\d+)?)"
    r"\s*(?P<scale>%|percent|per\s?cent|thousand|million|billion|trillion|bn|bln|k|m)?",
    re.IGNORECASE,
)
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")

_SCALE = {
    "k": 1e3, "thousand": 1e3,
    "m": 1e6, "million": 1e6,
    "bn": 1e9, "bln": 1e9, "billion": 1e9,
    "trillion": 1e12,
}


def sentences(text: str) -> List[str]:
    """Lightweight sentence-aware segmentation (Section 3, "sentence-aware spans")."""
    text = text.strip()
    if not text:
        return []
    # protect a few common abbreviations from premature splits
    protected = re.sub(r"\b(Mr|Mrs|Ms|Dr|St|Inc|Ltd|Corp|vs|No)\.", r"\1<DOT>", text)
    parts = _SENT_SPLIT.split(protected)
    return [p.replace("<DOT>", ".").strip() for p in parts if p.strip()]


def _is_cap(tok: str) -> bool:
    return bool(tok) and tok[0].isupper()


def _norm(tok: str) -> str:
    """Lowercase and strip possessives / surrounding punctuation: ``Acme's`` -> ``acme``.

    Restatements often differ only in surface form ("Acme's" vs "Acme", "Smith." vs
    "Smith"); normalising here keeps the changed-value test from reading those as a
    substitution.
    """
    t = tok.lower().strip("'\u2019.,;:!?()[]{}\"")
    if t.endswith("'s") or t.endswith("\u2019s"):
        t = t[:-2]
    return t


def proper_noun_phrases(text: str) -> List[str]:
    """Maximal capitalized spans, allowing lowercase connectors between caps.

    A phrase never *starts* with a capitalized function word, so a sentence-initial "The" is
    dropped rather than glued onto the following name ("The CEO of Acme" -> "CEO of Acme").
    """
    toks = _WORD.findall(text)
    phrases: List[str] = []
    cur: List[str] = []
    i = 0
    while i < len(toks):
        w = toks[i]
        if _is_phrase_token(w):
            if not cur and w.lower() in _PHRASE_STOP:
                i += 1  # do not let a phrase begin with a function word
                continue
            cur.append(w)
            i += 1
        elif cur and w.lower() in _CONNECTORS and i + 1 < len(toks) and _is_phrase_token(toks[i + 1]):
            cur.append(w.lower())
            i += 1
        else:
            if cur:
                phrases.append(" ".join(cur))
                cur = []
            i += 1
    if cur:
        phrases.append(" ".join(cur))
    # drop phrases with no capitalized content, phrases that are only function words, and
    # pure nationality/demonym phrases ("American", "Indian-American") that never name a subject
    out: List[str] = []
    for p in phrases:
        words = [w for w in p.split() if w.lower() not in _CONNECTORS]
        if not any(_is_phrase_token(w) for w in words):
            continue
        if all(w.lower() in _PHRASE_STOP for w in words):
            continue
        if words and all(_is_demonym(w) for w in words):
            continue
        out.append(_strip_corp(p))
    return out


def proper_noun_count(text: str) -> int:
    """Number of genuine proper-noun tokens -- the ">= 2 proper nouns" gate.

    Capitalized function words (a sentence-initial "The", etc.) are excluded so they do not
    inflate the count and let an underspecified claim pass the phrase-match path.
    """
    return sum(1 for w in _WORD.findall(text)
               if _is_phrase_token(w) and w.lower() not in _PHRASE_STOP and not _is_demonym(w))


def is_bare_year(num_token: str) -> bool:
    """A bare 4-digit year in [1500, 2199] -- ignored by the numeric changed-value test."""
    s = num_token.strip().replace(",", "")
    return bool(re.fullmatch(r"\d{4}", s)) and 1500 <= int(s) <= 2199


def numeric_magnitude(fragment: str) -> Optional[float]:
    """Unit-normalised magnitude of a numeric fragment, or ``None`` if not numeric.

    Strips currency and thousands separators and applies scale words, so "£1.5 million",
    "1,500,000" and "1.5m" all map to 1.5e6. Bare years and pure identifiers return ``None``
    (they are not magnitudes that can "change").
    """
    m = _NUMBER.search(fragment)
    if not m:
        return None
    raw = m.group("num")
    if is_bare_year(raw) and not m.group("cur") and not m.group("scale"):
        return None
    try:
        val = float(raw.replace(",", ""))
    except ValueError:
        return None
    scale = (m.group("scale") or "").lower().replace(" ", "")
    if scale in ("%", "percent", "percent", "percent"):
        pass  # percentage magnitude compared as-is
    elif scale:
        val *= _SCALE.get(scale, 1.0)
    return val


def numbers(text: str) -> List[float]:
    """All comparable numeric magnitudes in ``text`` (years/identifiers excluded)."""
    out: List[float] = []
    for m in _NUMBER.finditer(text):
        mag = numeric_magnitude(m.group(0))
        if mag is not None:
            out.append(mag)
    return out


def value_objects(text: str) -> Set[str]:
    """Value objects = lowercased proper-noun phrases plus normalised number strings."""
    objs: Set[str] = {p.lower() for p in proper_noun_phrases(text)}
    for n in numbers(text):
        objs.add(f"#{n:g}")
    return objs


def frame_tokens(text: str) -> Set[str]:
    """Relation words: lowercase content tokens minus value objects and copulas/articles."""
    phrase_words = {_norm(w) for p in proper_noun_phrases(text) for w in p.split()}
    out: Set[str] = set()
    for w in _WORD.findall(text):
        lw = _norm(w)
        if lw in _ROLE_ACRONYMS:
            out |= _ROLE_ACRONYMS[lw]  # "CEO" -> {chief, executive, officer}
            continue
        if lw in _ROLE_WORDS:
            out.add(lw)  # spelled-out title ("President", "Chancellor") is a relation word
            continue
        if _is_cap(w):
            continue  # part of a value object / subject phrase
        if not lw or lw in phrase_words or lw in _FRAME_DROP:
            continue
        out.add(lw)
    return out


def value_content_tokens(text: str) -> Set[str]:
    """Content tokens that live in the *value* region, for the string substitution test."""
    toks: Set[str] = set()
    for p in proper_noun_phrases(text):
        for w in p.split():
            if w.lower() in _CONNECTORS:
                continue
            n = _norm(w)
            if n:
                toks.add(n)
    return toks


def content_tokens(text: str) -> Set[str]:
    """All non-stopword normalised tokens (used for the matcher's token-overlap fallback)."""
    out: Set[str] = set()
    for w in _WORD.findall(text):
        n = _norm(w)
        if n and n not in _CONTENT_STOP:
            out.add(n)
    return out


def normalize_phrase(phrase: str) -> str:
    """Normalise a phrase for matching: per-word :func:`_norm`, connectors kept as-is."""
    return " ".join(w if w in _CONNECTORS else _norm(w) for w in phrase.split())


def overlap_coefficient(a: Set[str], b: Set[str]) -> float:
    """``|a n b| / min(|a|, |b|)`` -- 0 when either set is empty."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


_COPULA = re.compile(r"\b(?:is|are|was|were)\b", re.IGNORECASE)


def split_subject_value(text: str) -> "tuple[str, str]":
    """Split a claim at the first copula into (subject-side, value-side).

    A claim ``(s, r, v)`` reads "<relation about subject> **is** <value>", so the copula is a
    reliable boundary between the *subject+relation* and the *value*. Matching subjects on the
    left and comparing values on the right stops a shared value (e.g. two countries whose
    currency is the "Euro") from cross-linking, and stops a subject token that recurs inside a
    value (e.g. "Google" in "Google Incorporated") from masking a real change. If there is no
    copula, both sides fall back to the whole text.
    """
    m = _COPULA.search(text)
    if not m:
        return text, text
    return text[: m.start()], text[m.end():]


def split_subject_value_smart(text: str) -> "tuple[str, str]":
    """Copula split that is robust to word order: the *subject+relation* side is whichever
    side carries the relation frame words, the *value* side is the other.

    This handles both "the currency of Estonia is Euro" (relation on the left, value last) and
    "Peter den Oudsten is the head of government of X" (value first, relation on the right),
    which a fixed left/right split cannot. Falls back to the whole text when there is no
    copula or neither side carries a frame.
    """
    m = _COPULA.search(text)
    if not m:
        return text, text
    left, right = text[: m.start()], text[m.end():]
    lf, rf = len(frame_tokens(left)), len(frame_tokens(right))
    if lf == 0 and rf == 0:
        return text, text
    return (right, left) if rf > lf else (left, right)
