#!/usr/bin/env python3
"""Build a blinded, deterministic sentence ranking for the v26 diagnostic.

This command reads query_inputs, source_index, and sources. It never opens
references.jsonl. Full text stays in the external output directory. It does not
create final C0/C1 packs or run a model. Token budgets need native preflight.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import html
import json
import math
from pathlib import Path
import re
import time


ROOT = Path(__file__).resolve().parents[1]
RECIPE = {
    "recipe_id": "interpretation_context_candidates_v26",
    "purpose": "known_document_pool_interpretation_diagnostic",
    "natural_corpus_retrieval_claim": False,
    "pool": "union of supplied gold_ids and negative_ids; strip role labels before ranking",
    "query": "original raw query; strip HTML and decode entities for BM25 only",
    "source_units": "nonempty paragraph spans, split at conservative sentence boundaries",
    "paragraph_boundary": "two or more newline separators with optional horizontal whitespace",
    "sentence_boundary": "terminal punctuation, whitespace, then capital, digit, opening quote, or bracket",
    "abbreviation_guard": ["mr", "mrs", "ms", "dr", "prof", "sr", "jr", "vs", "etc",
                           "e.g", "i.e", "u.s", "u.k", "no", "vol", "fig", "st"],
    "unit_text": "exact source substring; no ellipses, text repair, or hidden truncation",
    "offset_unit": "UTF-8 bytes in released source content",
    "lexical_preprocessing": "HTML tag removal; HTML entity decoding; Unicode casefold",
    "token_pattern": r"[^\W_]+(?:['’-][^\W_]+)*",
    "bm25": {"k1": 1.2, "b": 0.75, "idf": "log(1 + (N-df+0.5)/(df+0.5))",
             "query_term_frequency": "binary", "query_term_iteration": "sorted",
             "score_rounding_decimal_places": 12},
    "ranking": "descending BM25 score; source_id; byte_start; byte_stop",
    "maximum_units_per_source": 3,
    "maximum_candidate_units": 30,
    "zero_score_fill": True,
    "query_outcomes_or_answers_used": False,
    "pack_construction": "pending native tokenizer counts and worst-case previous-output reserve",
    "model_completions": 0,
}
ABBREVIATIONS = set(RECIPE["abbreviation_guard"])
PARAGRAPH = re.compile(r"\n[ \t\r]*\n+")
BOUNDARY = re.compile(r"[.!?][\"'”’)]*[ \t\r\n]+(?=[A-Z0-9\"'“‘(\[])")
TOKEN = re.compile(RECIPE["token_pattern"], re.UNICODE)
TAG = re.compile(r"<[^>]*>")


def sha_bytes(value):
    return hashlib.sha256(value).hexdigest()


def sha_file(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(2**20), b""):
            value.update(block)
    return value.hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def write_json(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def read_rows(path):
    with Path(path).open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                raise ValueError(f"blank input line: {path.name}:{number}")
            yield json.loads(line)


def lexical(text):
    return TOKEN.findall(html.unescape(TAG.sub(" ", text)).casefold())


def spans(text):
    """Return exact character spans; sentence segmentation is heuristic."""
    paragraph_start = 0
    paragraphs = []
    for boundary in PARAGRAPH.finditer(text):
        paragraphs.append((paragraph_start, boundary.start()))
        paragraph_start = boundary.end()
    paragraphs.append((paragraph_start, len(text)))
    result = []
    for start, stop in paragraphs:
        cursor = start
        fragment = text[start:stop]
        for boundary in BOUNDARY.finditer(fragment):
            punctuation = start + boundary.start()
            word_start = punctuation
            while word_start > start and (text[word_start - 1] in ".abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"):
                word_start -= 1
            word = text[word_start:punctuation].casefold()
            if word in ABBREVIATIONS or (len(word) == 1 and word.isalpha()):
                continue
            endpoint = start + boundary.start() + 1
            while endpoint < stop and text[endpoint] in "\"'”’)":
                endpoint += 1
            result.append((cursor, endpoint))
            cursor = start + boundary.end()
        result.append((cursor, stop))
    cleaned = []
    for start, stop in result:
        while start < stop and text[start].isspace():
            start += 1
        while start < stop and text[stop - 1].isspace():
            stop -= 1
        if start < stop:
            cleaned.append((start, stop))
    if any(a >= b or text[a:b].strip() != text[a:b] for a, b in cleaned):
        raise ValueError("invalid segmentation")
    if any(cleaned[i][1] > cleaned[i + 1][0] for i in range(len(cleaned) - 1)):
        raise ValueError("overlapping segmentation")
    # Segmentation may remove whitespace only. No words may silently disappear.
    if re.sub(r"\s", "", "".join(text[a:b] for a, b in cleaned)) != re.sub(r"\s", "", text):
        raise ValueError("segmentation lost non-whitespace source content")
    return cleaned


def source_units(row):
    text = row["content"]
    raw = text.encode("utf-8")
    if sha_bytes(raw) != row["content_sha256"]:
        raise ValueError("source digest mismatch")
    pieces = spans(text)
    endpoints = sorted({p for piece in pieces for p in piece})
    byte_offsets = {}
    previous_char = previous_byte = 0
    for endpoint in endpoints:
        previous_byte += len(text[previous_char:endpoint].encode("utf-8"))
        byte_offsets[endpoint] = previous_byte
        previous_char = endpoint
    units = []
    for start, stop in pieces:
        a, b = byte_offsets[start], byte_offsets[stop]
        excerpt = text[start:stop]
        digest = sha_bytes(raw[a:b])
        if raw[a:b].decode("utf-8") != excerpt:
            raise ValueError("UTF-8 interval mismatch")
        uid = sha_bytes(f"{row['id']}|{a}|{b}|{digest}".encode())[:24]
        tokens = lexical(excerpt)
        units.append({"unit_id": uid, "source_id": row["id"],
                      "source_sha256": row["content_sha256"], "byte_start": a,
                      "byte_stop": b, "span_sha256": digest,
                      "text": excerpt, "terms": Counter(tokens), "length": len(tokens)})
    return units


def rank(query, units):
    """BM25 on units in this query's admitted pool, without role labels."""
    count = len(units)
    if not count:
        return []
    query_terms = sorted(set(lexical(query)))
    frequencies = Counter()
    for unit in units:
        frequencies.update(set(unit["terms"]) & set(query_terms))
    average = sum(unit["length"] for unit in units) / count
    k1, b = RECIPE["bm25"]["k1"], RECIPE["bm25"]["b"]
    ranked = []
    for unit in units:
        score = 0.0
        denominator_length = unit["length"] / average if average else 0.0
        for term in query_terms:
            frequency = unit["terms"].get(term, 0)
            if not frequency:
                continue
            df = frequencies[term]
            idf = math.log(1 + (count - df + 0.5) / (df + 0.5))
            score += idf * frequency * (k1 + 1) / (frequency + k1 * (1 - b + b * denominator_length))
        ranked.append((round(score, 12), unit))
    ranked.sort(key=lambda pair: (-pair[0], pair[1]["source_id"],
                                  pair[1]["byte_start"], pair[1]["byte_stop"]))
    selected = []
    per_source = Counter()
    for score, unit in ranked:
        if per_source[unit["source_id"]] >= RECIPE["maximum_units_per_source"]:
            continue
        per_source[unit["source_id"]] += 1
        selected.append((score, unit))
        if len(selected) == RECIPE["maximum_candidate_units"]:
            break
    return selected


def messages(config, instruction, query, evidence="", previous=None):
    sections = ["INSTRUCTION\n" + config["prompts"][instruction],
                "OUTPUT_SCHEMA\n" + canonical(config["output_schema"]),
                "QUESTION\n" + query, "EVIDENCE\n" + evidence]
    if previous is not None:
        sections.append("PREVIOUS\n" + previous)
    return [{"role": "system", "content": config["prompts"]["system"]},
            {"role": "user", "content": "\n\n".join(sections)}]


def build(args):
    source_dir = Path(args.source_dir).resolve()
    external = Path(args.output_dir).resolve()
    public = Path(args.public_dir).resolve()
    public.mkdir(parents=True, exist_ok=True)
    if external.exists():
        raise ValueError("external output already exists")
    if any((public / name).exists() for name in ["context_candidate_freeze_v26.json",
                                                "context_candidates_v26.jsonl",
                                                "context_candidate_receipt_v26.json"]):
        raise ValueError("public output already exists")
    inputs = {name: source_dir / (name + ".jsonl")
              for name in ["query_inputs", "source_index", "sources"]}
    queries = list(read_rows(inputs["query_inputs"]))
    indexes = list(read_rows(inputs["source_index"]))
    if len(queries) != 60 or len(indexes) != 60:
        raise ValueError("fixed roster must contain 60 entries")
    query_ids = [r["item_id"] for r in queries]
    if len(set(query_ids)) != len(query_ids):
        raise ValueError("duplicate question")
    index = {r["item_id"]: r for r in indexes}
    if set(index) != set(query_ids):
        raise ValueError("source index roster mismatch")
    needed = set()
    pools = {}
    for row in queries:
        entry = index[row["item_id"]]
        if sha_bytes(row["query"].encode()) != entry["query_sha256"]:
            raise ValueError("query digest mismatch")
        pools[row["item_id"]] = sorted(set(entry["gold_ids"]) | set(entry["negative_ids"]))
        needed.update(pools[row["item_id"]])
    config_path = Path(args.config).resolve()
    config = json.loads(config_path.read_text())
    external.mkdir(parents=True)
    freeze = {"created_unix": time.time(), "recipe": RECIPE,
              "recipe_sha256": sha_bytes(canonical(RECIPE).encode()),
              "script_sha256": sha_file(__file__), "interpretation_config_sha256": sha_file(config_path),
              "input_sha256": {name: sha_file(path) for name, path in inputs.items()},
              "question_ids": query_ids, "model_predictions_read": 0,
              "reference_answers_read": 0, "created_before_unit_ranking": True}
    write_json(public / "context_candidate_freeze_v26.json", freeze)
    by_source = {}
    for row in read_rows(inputs["sources"]):
        if row["id"] not in needed:
            continue
        if row["id"] in by_source:
            raise ValueError("duplicate source")
        by_source[row["id"]] = source_units(row)
    missing = needed - by_source.keys()
    total = zeros = 0
    statuses = Counter()
    with (external / "candidate_units.jsonl").open("x", encoding="utf-8") as full, \
         (public / "context_candidates_v26.jsonl").open("x", encoding="utf-8") as meta, \
         (external / "tokenization_requests.jsonl").open("x", encoding="utf-8") as requests:
        for row in queries:
            pool = pools[row["item_id"]]
            missing_here = sorted(set(pool) & missing)
            units = [unit for source in pool for unit in by_source.get(source, [])]
            selected = rank(row["query"], units)
            candidates = []
            for position, (score, unit) in enumerate(selected, 1):
                entry = {key: unit[key] for key in ["unit_id", "source_id", "source_sha256",
                                                    "byte_start", "byte_stop", "span_sha256", "text"]}
                entry.update({"rank": position, "evidence_id": f"E{position:02}",
                              "bm25_score": score, "lexical_tokens": unit["length"]})
                entry["rendered_text"] = f"[{entry['evidence_id']} | {entry['source_id']}]\n{entry['text']}"
                entry["rendered_sha256"] = sha_bytes(entry["rendered_text"].encode())
                candidates.append(entry)
                requests.write(canonical({"id": f"{row['item_id']}.unit.{position:02}",
                                          "text": entry["rendered_text"]}) + "\n")
                total += 1
                zeros += score == 0
            for arm, instruction, previous in [("I0", "initial", None), ("F1", "initial", None),
                                               ("B1", "reextract", ""), ("R1", "revision", "")]:
                requests.write(canonical({"id": f"{row['item_id']}.base.{arm}",
                                          "messages": messages(config, instruction, row["query"], previous=previous)}) + "\n")
            status = "missing_source" if missing_here else ("ranked" if candidates else "empty_pool")
            statuses[status] += 1
            record = {"item_id": row["item_id"], "domain": row["domain"], "id": row["id"],
                      "query_sha256": sha_bytes(row["query"].encode()), "status": status,
                      "pool_document_count": len(pool), "pool_ids_sha256": sha_bytes(canonical(pool).encode()),
                      "pool_unit_count": len(units), "missing_source_ids": missing_here,
                      "candidates": candidates}
            full.write(canonical({**record, "query": row["query"], "pool_document_ids": pool}) + "\n")
            public_record = {**record, "candidates": [
                {k: v for k, v in candidate.items() if k not in {"text", "rendered_text"}}
                for candidate in candidates]}
            meta.write(canonical(public_record) + "\n")
    receipt = {"schema_version": "context_candidate_receipt_v26", "questions": len(queries),
               "question_statuses": dict(statuses), "source_documents": len(by_source),
               "source_units": sum(map(len, by_source.values())), "ranked_candidates": total,
               "zero_score_candidates": zeros, "missing_source_count": len(missing),
               "full_text_path": str(external / "candidate_units.jsonl"),
               "candidate_units_sha256": sha_file(external / "candidate_units.jsonl"),
               "public_candidates_sha256": sha_file(public / "context_candidates_v26.jsonl"),
               "tokenization_requests_sha256": sha_file(external / "tokenization_requests.jsonl"),
               "freeze_sha256": sha_file(public / "context_candidate_freeze_v26.json"),
               "final_evidence_packs_built": False, "model_generations": 0,
               "claims": "Known-document diagnostic pool only. No model outcome or natural retrieval result."}
    write_json(public / "context_candidate_receipt_v26.json", receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--public-dir", default=str(ROOT / "data/tempo_v26"))
    parser.add_argument("--config", default=str(ROOT / "configs/interpretation_v26.json"))
    args = parser.parse_args()
    print(json.dumps(build(args), indent=2))


if __name__ == "__main__":
    main()
