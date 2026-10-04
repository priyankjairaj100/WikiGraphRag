"""v18 evidence interface: preserve blank source blocks; no hidden source access.

Versioned from v16 after v17 exposed blank source blocks; tested with authored
fixtures only. Semantic question mapping needs external grading; syntax is not
entailment, and an accepted blank block supplies no binding witness.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import unicodedata

ASPECTS = ("concept", "entity", "period", "unit", "population", "source")
MAX_FACTS = 64
MAX_HYPOTHESES = 8
MAX_CLAIMS = 8
_HANDLE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,31}\Z")
_DECIMAL = re.compile(r"(?:0|-?[1-9][0-9]*)(?:\.[0-9]*[1-9])?\Z|0\.[0-9]*[1-9]\Z|-0\.[0-9]*[1-9]\Z")
_QNAME = re.compile(r"\{[^{}]+\}[^{}\s]+\Z")


class BindingError(ValueError):
    """Strict input rejection; messages contain codes, never source values."""


def _fail(code):
    raise BindingError(code)


def _keys(value, expected, code):
    if not isinstance(value, dict) or set(value) != set(expected):
        _fail(code)


def _text(value, code, *, empty=False):
    if not isinstance(value, str) or (not empty and not value.strip()):
        _fail(code)


def _handle(value):
    if not isinstance(value, str) or not _HANDLE.fullmatch(value):
        _fail("invalid_handle")


def _canonical(value):
    return isinstance(value, str) and len(value) <= 15000 and bool(_DECIMAL.fullmatch(value))


def _dump(value):
    try:
        serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        serialized.encode("utf-8")
        return serialized
    except UnicodeError:
        _fail("non_utf8_json")
    except (ValueError, TypeError, RecursionError):
        _fail("non_json_value")


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("duplicate_json_key")
        result[key] = value
    return result


def _load(value):
    if isinstance(value, str):
        try:
            size = len(value.encode("utf-8"))
        except UnicodeError:
            _fail("non_utf8_json")
        if size > 2_000_000:
            _fail("json_size_limit")
        try:
            return json.loads(value, object_pairs_hook=_pairs,
                              parse_constant=lambda _: _fail("nonfinite_json"))
        except (json.JSONDecodeError, RecursionError):
            _fail("invalid_json")
    # A canonical round trip detaches every nested caller-owned object.
    return _load(_dump(value))


def _list(value, code, *, maximum=None):
    if not isinstance(value, list) or (maximum is not None and len(value) > maximum):
        _fail(code)


def _qname(value):
    if not isinstance(value, str) or not _QNAME.fullmatch(value):
        _fail("expanded_qname_required")


def _aspect_value(aspect, value):
    if aspect == "concept":
        _qname(value)
    elif aspect == "entity":
        _keys(value, ("scheme", "identifier"), "entity_shape")
        for text in value.values():
            _text(text, "entity_text")
    elif aspect == "source":
        _keys(value, ("sha256", "version"), "source_shape")
        if not isinstance(value["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", value["sha256"]):
            _fail("source_sha256")
        _text(value["version"], "source_version")
    elif aspect == "period":
        _keys(value, ("kind", "lexemes"), "period_shape")
        names = {"instant": {"instant"}, "duration": {"startDate", "endDate"}, "forever": {"forever"}}
        if not isinstance(value["kind"], str) or value["kind"] not in names:
            _fail("period_kind")
        _keys(value["lexemes"], names[value["kind"]], "period_lexemes")
        for text in value["lexemes"].values():
            _text(text, "period_text", empty=value["kind"] == "forever")
        if value["kind"] == "forever" and value["lexemes"]["forever"] != "":
            _fail("forever_lexeme")
    elif aspect == "unit":
        _keys(value, ("shape", "measures", "numerator_measures", "denominator_measures"), "unit_shape")
        for key in ("measures", "numerator_measures", "denominator_measures"):
            _list(value[key], "unit_measures")
            for name in value[key]:
                _qname(name)
        if value["shape"] == "simple_product":
            if not value["measures"] or value["numerator_measures"] or value["denominator_measures"]:
                _fail("unit_shape")
        elif value["shape"] == "divide":
            if value["measures"] or not value["numerator_measures"] or not value["denominator_measures"]:
                _fail("unit_shape")
            if set(value["numerator_measures"]) & set(value["denominator_measures"]):
                _fail("unreduced_unit")
        else:
            _fail("unit_shape")
    elif aspect == "population":
        _keys(value, ("label", "dimensions"), "population_shape")
        _text(value["label"], "population_label")
        _list(value["dimensions"], "population_dimensions")
        # Preserve the supplied reported dimension representation exactly. This
        # interface neither infers defaults nor validates taxonomy membership.
        for dimension in value["dimensions"]:
            if not isinstance(dimension, dict) or not dimension:
                _fail("dimension_shape")


@dataclass(frozen=True, slots=True, init=False)
class EvidencePack:
    """Immutable serialized acquired view; stores no source-document pointer."""
    _serialized: str

    def __init__(self, payload):
        pack = _load(payload)
        _keys(pack, ("schema_version", "pack_id", "blocks", "witnesses", "facts"), "pack_shape")
        if pack["schema_version"] != "reader_evidence_pack_v18":
            _fail("pack_schema")
        _handle(pack["pack_id"])
        _list(pack["blocks"], "blocks_limit", maximum=64)
        _list(pack["witnesses"], "witnesses_limit", maximum=512)
        _list(pack["facts"], "facts_limit", maximum=MAX_FACTS)
        seen = set()
        for block in pack["blocks"]:
            _keys(block, ("handle", "text"), "block_shape")
            _handle(block["handle"])
            _text(block["text"], "block_text", empty=True)
            if block["handle"] in seen:
                _fail("duplicate_handle")
            seen.add(block["handle"])
        witnesses = {}
        for witness in pack["witnesses"]:
            _keys(witness, ("handle", "aspect", "text"), "witness_shape")
            _handle(witness["handle"])
            _text(witness["text"], "witness_text")
            if witness["aspect"] not in ASPECTS:
                _fail("witness_aspect")
            if witness["handle"] in seen:
                _fail("duplicate_handle")
            seen.add(witness["handle"])
            witnesses[witness["handle"]] = witness
        for fact in pack["facts"]:
            _keys(fact, ("handle", "value", "labels", "bindings"), "fact_shape")
            _handle(fact["handle"])
            if fact["handle"] in seen:
                _fail("duplicate_handle")
            seen.add(fact["handle"])
            if fact["value"] is not None and not _canonical(fact["value"]):
                _fail("noncanonical_value")
            _list(fact["labels"], "fact_labels")
            for label in fact["labels"]:
                _text(label, "fact_label")
            _keys(fact["bindings"], ASPECTS, "binding_aspects")
            for aspect, binding in fact["bindings"].items():
                if binding is None:
                    continue
                _keys(binding, ("value", "witnesses"), "binding_shape")
                _aspect_value(aspect, binding["value"])
                _list(binding["witnesses"], "binding_witnesses")
                if (not binding["witnesses"] or any(not isinstance(h, str) for h in binding["witnesses"])
                        or len(set(binding["witnesses"])) != len(binding["witnesses"])):
                    _fail("empty_or_duplicate_witnesses")
                for handle in binding["witnesses"]:
                    _handle(handle)
                    if handle not in witnesses or witnesses[handle]["aspect"] != aspect:
                        _fail("unavailable_binding_witness")
        object.__setattr__(self, "_serialized", _dump(pack))

    def snapshot(self):
        return _load(self._serialized)

    def as_json(self):
        return self._serialized

    @property
    def sha256(self):
        return hashlib.sha256(self._serialized.encode("utf-8")).hexdigest()

    def prompt_view(self):
        """Lossless compact registries; exact native token cost remains external."""
        data = self.snapshot()
        registries = {name: {} for name in ("texts", "values", "bindings")}
        rows = {name: [] for name in registries}
        def intern(name, value):
            key = _dump(value)
            if key not in registries[name]:
                identifier = name[0] + str(len(rows[name]) + 1).zfill(3)
                registries[name][key] = identifier
                rows[name].append([identifier, value])
            return registries[name][key]
        facts = []
        for fact in data["facts"]:
            bindings = []
            for aspect in ASPECTS:
                binding = fact["bindings"][aspect]
                if binding is None:
                    bindings.append(None)
                else:
                    value_id = intern("values", [aspect, binding["value"]])
                    bindings.append(intern("bindings", [value_id, binding["witnesses"]]))
            facts.append([fact["handle"], fact["value"],
                          [intern("texts", label) for label in fact["labels"]], bindings])
        blocks = [[b["handle"], intern("texts", b["text"])] for b in data["blocks"]]
        witnesses = [[w["handle"], w["aspect"], intern("texts", w["text"])] for w in data["witnesses"]]
        return {"schema_version": "reader_evidence_prompt_v18", "pack_id": data["pack_id"],
                "aspect_order": list(ASPECTS), "blocks": blocks, "witnesses": witnesses,
                **rows, "facts": facts}

    @classmethod
    def from_prompt_view(cls, payload):
        """Lossless expansion checker for the prompt projection."""
        view = _load(payload)
        _keys(view, ("schema_version", "pack_id", "aspect_order", "blocks", "witnesses",
                     "texts", "values", "bindings", "facts"), "prompt_shape")
        if view["schema_version"] != "reader_evidence_prompt_v18" or view["aspect_order"] != list(ASPECTS):
            _fail("prompt_schema")
        registries = {}
        for name in ("texts", "values", "bindings"):
            _list(view[name], "prompt_registry")
            registry = {}
            for row in view[name]:
                if not isinstance(row, list) or len(row) != 2:
                    _fail("prompt_registry_row")
                _handle(row[0])
                if row[0] in registry:
                    _fail("duplicate_registry_id")
                registry[row[0]] = row[1]
            registries[name] = registry
        def get(name, identifier):
            if not isinstance(identifier, str) or identifier not in registries[name]:
                _fail("unknown_registry_id")
            return registries[name][identifier]
        facts = []
        _list(view["facts"], "facts_limit", maximum=MAX_FACTS)
        for row in view["facts"]:
            if not isinstance(row, list) or len(row) != 4:
                _fail("prompt_fact_row")
            _list(row[2], "prompt_label_ids")
            if not isinstance(row[3], list) or len(row[3]) != len(ASPECTS):
                _fail("prompt_binding_order")
            bindings = {}
            for aspect, identifier in zip(ASPECTS, row[3]):
                if identifier is None:
                    bindings[aspect] = None
                    continue
                binding = get("bindings", identifier)
                if not isinstance(binding, list) or len(binding) != 2:
                    _fail("prompt_binding_row")
                value = get("values", binding[0])
                if not isinstance(value, list) or len(value) != 2 or value[0] != aspect:
                    _fail("prompt_binding_aspect")
                bindings[aspect] = {"value": value[1], "witnesses": binding[1]}
            facts.append({"handle": row[0], "value": row[1],
                          "labels": [get("texts", i) for i in row[2]], "bindings": bindings})
        blocks, witnesses = [], []
        for name, width in (("blocks", 2), ("witnesses", 3)):
            _list(view[name], "prompt_rows")
            for row in view[name]:
                if not isinstance(row, list) or len(row) != width:
                    _fail("prompt_row_shape")
                if name == "blocks":
                    blocks.append({"handle": row[0], "text": get("texts", row[1])})
                else:
                    witnesses.append({"handle": row[0], "aspect": row[1], "text": get("texts", row[2])})
        restored = cls({"schema_version": "reader_evidence_pack_v18", "pack_id": view["pack_id"],
                        "blocks": blocks, "witnesses": witnesses, "facts": facts})
        # A digest of an expanded pack cannot attest to ignored model-visible
        # registry rows. Require the canonical projection exactly, including its
        # row order and registry assignment, so no exposed content disappears.
        if _dump(restored.prompt_view()) != _dump(view):
            _fail("noncanonical_or_unused_prompt_content")
        return restored


def _pack(pack):
    if not isinstance(pack, EvidencePack):
        _fail("EvidencePack_required")
    return pack.snapshot()


def _citations(fact, citations, witnesses):
    _keys(citations, ASPECTS, "citation_aspects")
    for aspect in ASPECTS:
        handles = citations[aspect]
        _list(handles, "citation_list")
        if any(not isinstance(h, str) for h in handles) or len(set(handles)) != len(handles):
            _fail("duplicate_or_invalid_citation")
        binding = fact["bindings"][aspect]
        allowed = set(binding["witnesses"]) if binding else set()
        for handle in handles:
            if handle not in witnesses or handle not in allowed:
                _fail("unavailable_or_wrong_binding_citation")
        # Missing citations are a retained insufficiency, not a fabricated
        # witness or a silently relaxed constraint.


def _clarification(value, decision):
    _list(value, "clarification_aspects")
    if any(not isinstance(a, str) or a not in ASPECTS for a in value) or len(set(value)) != len(value):
        _fail("clarification_aspects")
    if value and decision != "clarify":
        _fail("clarification_action_mismatch")
    if decision == "clarify" and not value:
        _fail("empty_clarification")


def validate_proposal(pack, proposal):
    data = _pack(pack)
    value = _load(proposal)
    _keys(value, ("decision", "unknown", "clarify_aspects", "hypotheses"), "proposal_shape")
    if value["decision"] not in ("answer", "clarify", "insufficient") or type(value["unknown"]) is not bool:
        _fail("proposal_action")
    _clarification(value["clarify_aspects"], value["decision"])
    _list(value["hypotheses"], "hypothesis_limit", maximum=MAX_HYPOTHESES)
    facts = {f["handle"]: f for f in data["facts"]}
    witnesses = {w["handle"]: w for w in data["witnesses"]}
    for hypothesis in value["hypotheses"]:
        _keys(hypothesis, ("claims",), "hypothesis_shape")
        _list(hypothesis["claims"], "claim_limit", maximum=MAX_CLAIMS)
        if not hypothesis["claims"]:
            _fail("empty_hypothesis")
        claimed = set()
        for claim in hypothesis["claims"]:
            _keys(claim, ("fact_handle", "binding_witnesses"), "claim_shape")
            handle = claim["fact_handle"]
            if not isinstance(handle, str) or handle not in facts:
                _fail("unobserved_fact_handle")
            if handle in claimed:
                _fail("duplicate_claim_handle")
            claimed.add(handle)
            _citations(facts[handle], claim["binding_witnesses"], witnesses)
    return value


def _scope(fact):
    return {aspect: (fact["bindings"][aspect]["value"] if fact["bindings"][aspect] else None)
            for aspect in ASPECTS}


def _compatible(left, right):
    # Unknown metadata cannot rule out an answer-changing duplicate.
    return all(left[a] is None or right[a] is None or left[a] == right[a] for a in ASPECTS)


def _claim_resolution(data, claim):
    seed = next(f for f in data["facts"] if f["handle"] == claim["fact_handle"])
    scope = _scope(seed)
    candidates = [f for f in data["facts"] if _compatible(scope, _scope(f))]
    reasons = []
    if any(scope[a] is None for a in ASPECTS):
        reasons.append("unavailable_binding")
    if any(not claim["binding_witnesses"][a] for a in ASPECTS):
        reasons.append("uncited_binding")
    if any(f["value"] is None or any(_scope(f)[a] is None for a in ASPECTS) for f in candidates):
        reasons.append("unresolved_possible_match")
    values = {f["value"] for f in candidates if f["value"] is not None}
    if len(values) > 1:
        reasons.append("conflicting_quantities")
    result = {"seed_handle": seed["handle"], "candidate_handles": [f["handle"] for f in candidates],
              "scope": scope, "status": "blocked" if reasons else "resolved", "reason_codes": reasons}
    if not reasons:
        result["quantity"] = {"value": seed["value"], "scope": scope,
                              "occurrence_handles": [f["handle"] for f in candidates],
                              "binding_witnesses": claim["binding_witnesses"]}
    return result


def _merge_quantities(quantities):
    """One quantity per exact scope/value, retaining all cited provenance."""
    combined = {}
    for quantity in quantities:
        key = _dump({"scope": quantity["scope"], "value": quantity["value"]})
        if key not in combined:
            combined[key] = _load(quantity)
            continue
        target = combined[key]
        target["occurrence_handles"] = list(dict.fromkeys(target["occurrence_handles"] + quantity["occurrence_handles"]))
        for aspect in ASPECTS:
            target["binding_witnesses"][aspect] = list(dict.fromkeys(
                target["binding_witnesses"][aspect] + quantity["binding_witnesses"][aspect]))
    return combined


def render_proposal(pack, proposal):
    """Expand acquired bindings only; never certify semantic question mapping."""
    try:
        validated = validate_proposal(pack, proposal)
    except BindingError as error:
        return {"action": "invalid_output", "quantities": [], "reason_codes": [str(error)],
                "hypotheses": [], "unknown": True, "clarify_aspects": []}
    data = _pack(pack)
    hypotheses = [{"claims": [_claim_resolution(data, c) for c in h["claims"]]}
                  for h in validated["hypotheses"]]
    result = {"action": "insufficient", "quantities": [], "reason_codes": [],
              "hypotheses": hypotheses, "unknown": validated["unknown"], "clarify_aspects": []}
    if validated["decision"] != "answer":
        result["action"] = validated["decision"]
        result["clarify_aspects"] = validated["clarify_aspects"]
        result["reason_codes"] = ["proposer_" + validated["decision"]]
        return result
    if validated["unknown"]:
        result["action"] = "clarify"
        result["clarify_aspects"] = list(ASPECTS)
        result["reason_codes"] = ["unresolved_interpretation"]
        return result
    if not hypotheses:
        result["reason_codes"] = ["no_hypotheses"]
        return result
    reasons = sorted({reason for h in hypotheses for c in h["claims"] for reason in c["reason_codes"]})
    if reasons:
        result["reason_codes"] = reasons
        if reasons == ["conflicting_quantities"]:
            result["action"] = "insufficient"
        return result
    assignments = {}
    for hypothesis in hypotheses:
        quantities = _merge_quantities([c["quantity"] for c in hypothesis["claims"]])
        # Claim order is not a different interpretation; scopes remain exact.
        key = tuple(sorted(quantities))
        if key not in assignments:
            assignments[key] = list(quantities.values())
        else:
            assignments[key] = list(_merge_quantities(assignments[key] + list(quantities.values())).values())
    if len(assignments) != 1:
        result["action"] = "clarify"
        scopes = [c["scope"] for h in hypotheses for c in h["claims"]]
        result["clarify_aspects"] = [a for a in ASPECTS if len({_dump(s[a]) for s in scopes}) > 1]
        result["reason_codes"] = ["different_scoped_assignments"]
        return result
    result["action"] = "answered"
    result["quantities"] = next(iter(assignments.values()))
    return result


# Deliberately small, frozen language profile. No financial synonym dictionary,
# per-question aliases, numeric answer matching, learned scoring, or gold map.
STOP_WORDS = frozenset("a an the what which was were is are did does do please give report reported amount value values quantity quantities of for in on at as to from and its their company annual filing document show state how much respectively each both two year years ended ending total".split())
MULTI_WORDS = frozenset(("both", "each", "two", "respectively"))
UNIT_WORDS = frozenset(("usd", "eur", "gbp", "jpy", "inr", "dollars", "euros", "pounds", "yen", "rupees", "shares", "percent", "percentage"))


def _tokens(text):
    text = unicodedata.normalize("NFKC", text).casefold()
    return set(re.findall(r"[^\W_]+", text, flags=re.UNICODE))


def _lexical_texts(fact, witnesses, aspect):
    binding = fact["bindings"][aspect]
    if binding is None:
        return set()
    # Exclude source hashes, taxonomy URIs and context IDs from lexical cues.
    return set().union(*(_tokens(witnesses[h]["text"]) for h in binding["witnesses"]))


def lexical_baseline(pack, question):
    """B0: exact token-overlap labels plus explicit witnessed constraints."""
    _text(question, "question_text")
    data = _pack(pack)
    words = _tokens(question)
    witnesses = {w["handle"]: w for w in data["witnesses"]}
    facts = data["facts"]
    question_content = words - STOP_WORDS
    years = {w for w in words if re.fullmatch(r"(?:19|20)[0-9]{2}", w)}
    requested_units = words & UNIT_WORDS
    multi = bool(words & MULTI_WORDS)
    lexical = {f["handle"]: {a: _lexical_texts(f, witnesses, a) for a in ASPECTS} for f in facts}
    label_words = {f["handle"]: set().union(*(_tokens(t) for t in f["labels"])) if f["labels"] else set()
                   for f in facts}
    scores = {h: len((tokens - STOP_WORDS) & question_content) for h, tokens in label_words.items()}
    best = max(scores.values(), default=0)
    candidates = [f for f in facts if best and scores[f["handle"]] == best]
    constraints = {}
    for aspect in ("entity", "source", "population"):
        observed = set().union(*(lexical[f["handle"]][aspect] for f in facts)) if facts else set()
        other = set().union(*(label_words[f["handle"]] for f in facts)) if facts else set()
        # Only aspect-specific informative witness tokens are exact constraints.
        constraints[aspect] = (question_content & observed) - other - UNIT_WORDS - years
    filtered = []
    for fact in candidates:
        lex = lexical[fact["handle"]]
        if any(constraints[a] and fact["bindings"][a] is not None and not constraints[a] <= lex[a]
               for a in constraints):
            continue
        # Single-request year must be present in period witnesses. For explicit
        # plural requests, any requested year is admissible; all requested years
        # must then be represented before an answer is attempted.
        if years and fact["bindings"]["period"] is not None:
            if not (years & lex["period"]) if multi else not years <= lex["period"]:
                continue
        if requested_units and fact["bindings"]["unit"] is not None and not requested_units <= lex["unit"]:
            continue
        filtered.append(fact)
    represented = set().union(*(lexical[f["handle"]]["period"] for f in filtered)) if filtered else set()
    known = set().union(*(label_words[f["handle"]] | set().union(*lexical[f["handle"]].values()) for f in facts)) if facts else set()
    unknown = bool(question_content - known) or not years or not filtered or bool(years - represented)
    # A plural request without at least two distinguishable witnessed scopes is
    # unresolved. Equal numerical values never collapse scope identity.
    scope_groups = {}
    for fact in filtered:
        scope_groups.setdefault(_dump(_scope(fact)), fact)
    selected = list(scope_groups.values())
    if multi and len(selected) < 2:
        unknown = True
    if len(selected) > MAX_HYPOTHESES:
        return {"decision": "clarify", "unknown": True, "clarify_aspects": list(ASPECTS), "hypotheses": []}
    def claim(fact):
        return {"fact_handle": fact["handle"], "binding_witnesses": {
            a: list(fact["bindings"][a]["witnesses"]) if fact["bindings"][a] else [] for a in ASPECTS}}
    hypotheses = ([{"claims": [claim(f) for f in selected]}] if multi and selected
                  else [{"claims": [claim(f)]} for f in selected])
    decision = "answer" if not unknown else ("clarify" if selected else "insufficient")
    unclear = [a for a in ASPECTS if any(f["bindings"][a] is None for f in selected)]
    if not years:
        unclear.append("period")
    if question_content - known:
        unclear.append("concept")
    return {"decision": decision, "unknown": unknown, "hypotheses": hypotheses,
            "clarify_aspects": [a for a in ASPECTS if a in set(unclear)] or ["concept"] if decision == "clarify" else []}


def validate_direct_output(pack, output):
    """B2 syntax/access validation only; a wrong stated value remains scoreable."""
    data = _pack(pack)
    value = _load(output)
    _keys(value, ("action", "unknown", "clarify_aspects", "quantities"), "direct_shape")
    if value["action"] not in ("answered", "clarify", "insufficient") or type(value["unknown"]) is not bool:
        _fail("direct_action")
    _clarification(value["clarify_aspects"], value["action"])
    _list(value["quantities"], "direct_quantity_limit", maximum=MAX_CLAIMS)
    if value["action"] == "answered" and (not value["quantities"] or value["unknown"]):
        _fail("inconsistent_answer_action")
    facts = {f["handle"]: f for f in data["facts"]}
    witnesses = {w["handle"]: w for w in data["witnesses"]}
    for quantity in value["quantities"]:
        _keys(quantity, ("value", "fact_handle", "binding_witnesses"), "direct_quantity_shape")
        if not _canonical(quantity["value"]):
            _fail("noncanonical_direct_value")
        if not isinstance(quantity["fact_handle"], str) or quantity["fact_handle"] not in facts:
            _fail("unobserved_fact_handle")
        _citations(facts[quantity["fact_handle"]], quantity["binding_witnesses"], witnesses)
    return value


def score_direct_quantities(pack, output):
    """Check exposed support, not question meaning or hidden reference accuracy."""
    try:
        validated = validate_direct_output(pack, output)
    except BindingError as error:
        return {"action": "invalid_output", "checks": [], "reason_codes": [str(error)]}
    data = _pack(pack)
    checks = []
    for q in validated["quantities"]:
        resolution = _claim_resolution(data, {k: v for k, v in q.items() if k != "value"})
        checks.append({"fact_handle": q["fact_handle"], "stated_value": q["value"],
                       "scope": resolution["scope"], "candidate_handles": resolution["candidate_handles"],
                       "support_complete": resolution["status"] == "resolved",
                       "value_matches": resolution["status"] == "resolved" and q["value"] == resolution["quantity"]["value"],
                       "reason_codes": resolution["reason_codes"]})
    return {"action": validated["action"], "checks": checks, "reason_codes": [],
            "unknown": validated["unknown"], "clarify_aspects": validated["clarify_aspects"],
            "semantic_question_correctness": "not_checked"}
