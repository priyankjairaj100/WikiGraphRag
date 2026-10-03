"""LLM claim-normalization front-end (Fig 3).

On raw prose the zero-LLM detector's matcher stops being exact (Proposition 1's only failure
mode). The fix the paper proposes -- and we implement -- is a thin LLM front-end that extracts
a ``(subject, relation, value)`` triple from each sentence and re-renders it as a canonical
claim; the *unchanged* detector then runs over the normalized claims. Generations are cached
by the reader, so this runs once.
"""

from __future__ import annotations

import json
import re
from typing import List, Optional, Sequence

from ..core.claim import Claim

_PROMPT = (
    "Extract the single main factual triple from the sentence as compact JSON with keys "
    "\"subject\", \"relation\", \"value\". The value is the attribute's current filler.\n"
    "Sentence: {sentence}\n"
    "Output only the JSON object."
)

_JSON = re.compile(r"\{.*?\}", re.DOTALL)


def _parse_triple(resp: str) -> Optional[dict]:
    m = _JSON.search(resp)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if all(k in d and d[k] for k in ("subject", "relation", "value")):
        return {k: str(d[k]).strip() for k in ("subject", "relation", "value")}
    return None


class ClaimNormalizer:
    def __init__(self, reader) -> None:
        self.reader = reader

    def normalize(self, claim: Claim) -> Claim:
        """Return a canonicalised copy of ``claim`` ("The {relation} of {subject} is {value}.").

        Timestamp and gold annotations are preserved so detection scoring is unchanged; only
        the *text* the detector reads is canonicalised. Falls back to the original text if the
        model does not return a usable triple.
        """
        resp = self.reader.generate(_PROMPT.format(sentence=claim.text), max_new_tokens=64)
        tri = _parse_triple(resp)
        if tri is None:
            return claim
        text = f"The {tri['relation']} of {tri['subject']} is {tri['value']}."
        return Claim(
            cid=claim.cid,
            text=text,
            timestamp=claim.timestamp,
            doc_id=claim.doc_id,
            subject=claim.subject,
            relation=claim.relation,
            value=claim.value,
            valid_time=claim.valid_time,
            span=claim.span,
            meta={**dict(claim.meta), "normalized_from": claim.text, "triple": tri},
        )

    def normalize_all(self, claims: Sequence[Claim]) -> List[Claim]:
        return [self.normalize(c) for c in claims]
