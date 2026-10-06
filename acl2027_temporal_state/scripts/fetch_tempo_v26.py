#!/usr/bin/env python3
"""Fetch pinned TEMPO files outside Git and record public access receipts.

Install pyarrow before use. The script prints metadata, never query text.
No benchmark labels affect file selection or corpus construction.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sys
import urllib.parse
import urllib.request

DATASET = "tempo26/Tempo"
REVISION = "1dba7027d0afa628993c6dd794e9b68c657a6fac"
EXPOSED = {"126111_0", "126019_1", "112346_808", "111149_809", "79544_0", "79533_1"}


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".writing")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    os.replace(temporary, path)


def fetch(entry: dict, external: Path) -> dict:
    relative = entry["path"]
    target = external / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    expected = entry.get("lfs", {}).get("oid")
    if not expected:
        raise ValueError(f"Missing LFS SHA256 for {relative}")
    url = f"https://huggingface.co/datasets/{DATASET}/resolve/{REVISION}/{relative}"
    record = {"path": relative, "url": url, "expected_bytes": entry["size"],
              "expected_sha256": expected, "attempts": []}
    if target.exists() and target.stat().st_size == entry["size"] and digest(target) == expected:
        record.update(status="verified_cache", bytes=target.stat().st_size, sha256=expected)
        return record
    if target.exists():
        raise ValueError(f"Unverified existing target: {target}")
    temporary = target.with_name(target.name + ".part")
    for attempt in range(1, 4):
        started = stamp()
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "WikiGraphRAG-v26-research"})
            with urllib.request.urlopen(request, timeout=45) as response, temporary.open("wb") as f:
                while block := response.read(1024 * 1024):
                    f.write(block)
            actual_size, actual_digest = temporary.stat().st_size, digest(temporary)
            if actual_size != entry["size"] or actual_digest != expected:
                raise ValueError("Downloaded bytes fail pinned size or SHA256")
            os.replace(temporary, target)
            record["attempts"].append({"attempt": attempt, "started_utc": started,
                                       "ended_utc": stamp(), "status": "verified"})
            record.update(status="downloaded", bytes=actual_size, sha256=actual_digest)
            return record
        except Exception as exc:
            record["attempts"].append({"attempt": attempt, "started_utc": started,
                                       "ended_utc": stamp(), "status": "failed",
                                       "error": f"{type(exc).__name__}: {exc}"})
    record["status"] = "failed"
    return record


def query_inventory(external: Path, entries: list[dict]) -> dict:
    import pyarrow.parquet as pq
    rows = []
    domains = {}
    for entry in sorted(entries, key=lambda x: x["path"]):
        domain = Path(entry["path"]).stem
        # Hashing is mechanical. No query strings enter the public inventory.
        table = pq.read_table(external / entry["path"], columns=["id", "query"])
        domains[domain] = table.num_rows
        for ordinal, row in enumerate(table.to_pylist()):
            rows.append({"id": row["id"], "domain": domain, "row_index": ordinal,
                         "query_sha256": hashlib.sha256(row["query"].encode()).hexdigest(),
                         "previously_exposed_v25": row["id"] in EXPOSED})
    ids = [r["id"] for r in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("Question IDs are not globally unique")
    if not EXPOSED.issubset(ids):
        raise ValueError("A prior exposed ID is missing from the pinned source")
    result = {"dataset": DATASET, "revision": REVISION, "domains": domains,
              "total_rows": len(rows), "eligible_rows": sum(not r["previously_exposed_v25"] for r in rows),
              "selection_performed": False, "records": rows}
    atomic_json(external / "query_inventory.json", result)
    return result


def extract_roster(external: Path, roster_path: Path) -> dict:
    """Extract exact references after the caller freezes its roster.

    Retrieval inputs contain no reference answers or source labels.
    Reference packs are for annotation only.
    """
    import pyarrow.parquet as pq
    roster = json.loads(roster_path.read_text())
    selected = roster["records"]
    destination = external / "roster"
    destination.mkdir(exist_ok=True)
    receipt_path = destination / "extraction_receipt.json"
    roster_sha = digest(roster_path)
    if receipt_path.exists():
        prior = json.loads(receipt_path.read_text())
        if prior["roster_sha256"] != roster_sha:
            raise ValueError("Existing extraction belongs to another roster")
        for artifact in prior["artifacts"]:
            if digest(destination / artifact["path"]) != artifact["sha256"]:
                raise ValueError("Existing extraction changed")
        return prior
    by_domain = {}
    for row in selected:
        by_domain.setdefault(row["domain"], []).append(row)
    references = []
    wanted = {}
    for domain, records in sorted(by_domain.items()):
        table = pq.read_table(external / "examples" / (domain + ".parquet"))
        for record in records:
            source = table.slice(record["row_index"], 1).to_pylist()[0]
            if source["id"] != record["id"]:
                raise ValueError("Frozen query ID differs from the pinned row")
            if hashlib.sha256(source["query"].encode()).hexdigest() != record["query_sha256"]:
                raise ValueError("Frozen question hash differs from the pinned row")
            reference = {"item_id": record["item_id"], "domain": domain, **source}
            references.append(reference)
            wanted.setdefault(domain, set()).update(source["gold_ids"] + source["negative_ids"])
    sources = {}
    corpus_metadata = []
    for domain, ids in sorted(wanted.items()):
        source_path = external / "documents" / (domain + ".parquet")
        reader = pq.ParquetFile(source_path)
        corpus_metadata.append({"domain": domain, "rows": reader.metadata.num_rows,
                                "fields": reader.schema_arrow.names})
        for batch in reader.iter_batches(batch_size=2048, columns=["id", "content"]):
            for row in batch.to_pylist():
                if row["id"] not in ids:
                    continue
                if row["id"] in sources:
                    raise ValueError("Duplicate requested source ID")
                sources[row["id"]] = row["content"]
    missing = sorted(set().union(*wanted.values()) - sources.keys())
    references.sort(key=lambda x: x["item_id"])
    artifacts = []

    def jsonl(relative: str, rows) -> None:
        path = destination / relative
        with path.open("x", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        artifacts.append({"path": relative, "bytes": path.stat().st_size, "sha256": digest(path)})

    def plain(relative: str, body: str) -> None:
        path = destination / relative
        with path.open("x", encoding="utf-8") as f:
            f.write(body)
        artifacts.append({"path": relative, "bytes": path.stat().st_size, "sha256": digest(path)})

    jsonl("query_inputs.jsonl", ({k: r[k] for k in ("item_id", "domain", "id", "query")} for r in references))
    jsonl("references.jsonl", references)
    jsonl("sources.jsonl", ({"id": ident, "content": content,
                              "content_sha256": hashlib.sha256(content.encode()).hexdigest()}
                             for ident, content in sorted(sources.items())))
    index = []
    for row in references:
        record = {"item_id": row["item_id"], "id": row["id"], "domain": row["domain"],
                  "query_sha256": hashlib.sha256(row["query"].encode()).hexdigest(),
                  "gold_ids": row["gold_ids"], "negative_ids": row["negative_ids"],
                  "missing_ids": [i for i in row["gold_ids"] + row["negative_ids"] if i not in sources]}
        index.append(record)
        header = f"ANNOTATION ONLY: {row['item_id']} / {row['domain']} / {row['id']}\n"
        body = header + "\nQUESTION\n" + row["query"] + "\nRELEASED REFERENCE ANSWERS\n"
        body += "\n\n".join(row["gold_answers"]) + "\n\nGOLD SOURCES\n"
        for ident in row["gold_ids"]:
            body += f"\nSOURCE {ident}\n{sources.get(ident, '[MISSING SOURCE]')}\n"
        plain(row["item_id"] + "_reference.txt", body)
        body = header + "\nRELEASED NEGATIVE SOURCES\n"
        for ident in row["negative_ids"]:
            body += f"\nSOURCE {ident}\n{sources.get(ident, '[MISSING SOURCE]')}\n"
        plain(row["item_id"] + "_negative_sources.txt", body)
    jsonl("source_index.jsonl", index)
    source_hashes = [{"id": ident, "content_sha256": hashlib.sha256(content.encode()).hexdigest()}
                     for ident, content in sorted(sources.items())]
    result = {"dataset": DATASET, "revision": REVISION, "roster_sha256": roster_sha,
              "created_utc": stamp(), "script_sha256": digest(Path(__file__)),
              "question_count": len(references), "unique_requested_source_count": sum(map(len, wanted.values())),
              "exact_source_count": len(sources), "missing_source_ids": missing,
              "corpora": corpus_metadata, "artifacts": artifacts,
              "source_hashes": source_hashes,
              "question_source_index": [{k: r[k] for k in ("item_id", "id", "domain", "gold_ids", "negative_ids", "missing_ids")} for r in index],
              "inference_input_fields": ["item_id", "domain", "id", "query"],
              "query_text_printed": False, "model_calls": 0,
              "references_are_benchmark_labels_not_new_adjudication": True}
    atomic_json(receipt_path, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--external-dir", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--documents", nargs="*", default=[],
                        help="Document domains to download; use all for every domain.")
    parser.add_argument("--extract-roster", type=Path,
                        help="Frozen roster with item_id/domain/id/row_index/query_sha256 records.")
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    external = args.external_dir.resolve()
    repository = Path(__file__).resolve().parents[2]
    if external == repository or repository in external.parents:
        raise ValueError("Raw data must stay outside this Git repository")
    external.mkdir(parents=True, exist_ok=True)
    metadata = external / "hub_tree.json"
    tree_url = f"https://huggingface.co/api/datasets/{DATASET}/tree/{REVISION}?recursive=true&expand=false"
    if not metadata.exists():
        metadata.write_bytes(urllib.request.urlopen(tree_url, timeout=45).read())
    tree = json.loads(metadata.read_text())
    query_entries = [e for e in tree if e["type"] == "file" and e["path"].startswith("examples/")]
    all_documents = [e for e in tree if e["type"] == "file" and e["path"].startswith("documents/")]
    available_domains = {Path(e["path"]).stem for e in all_documents}
    selected = available_domains if "all" in args.documents else set(args.documents)
    if not selected.issubset(available_domains):
        raise ValueError("Unknown document domain")
    if args.workers < 1 or (selected and args.workers > 2):
        raise ValueError("Document downloads require one or two workers")
    entries = query_entries + [e for e in all_documents if Path(e["path"]).stem in selected]
    old_receipt = json.loads(args.receipt.read_text()) if args.receipt.exists() else None
    if old_receipt and old_receipt["revision"] != REVISION:
        raise ValueError("Existing receipt belongs to another revision")
    receipt = old_receipt or {"dataset": DATASET, "revision": REVISION,
                             "created_utc": stamp(), "access_runs": []}
    run = {"started_utc": stamp(), "script_sha256": digest(Path(__file__)),
           "tree_url": tree_url, "tree_sha256": digest(metadata),
           "query_files": len(query_entries), "query_bytes": sum(e["size"] for e in query_entries),
           "document_files_available": len(all_documents),
           "document_bytes_available": sum(e["size"] for e in all_documents),
           "document_domains_requested": sorted(selected), "files": []}
    receipt["access_runs"].append(run)
    atomic_json(args.receipt, receipt)
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(fetch, e, external): e for e in entries}
        for future in concurrent.futures.as_completed(futures):
            record = future.result()
            run["files"].append(record)
            atomic_json(args.receipt, receipt)
            print(json.dumps({"path": record["path"], "status": record["status"]}), flush=True)
    run["files"].sort(key=lambda x: x["path"])
    failures = [r for r in run["files"] if r["status"] == "failed"]
    if not any(r["path"].startswith("examples/") for r in failures):
        inventory = query_inventory(external, query_entries)
        receipt["query_inventory"] = {k: v for k, v in inventory.items() if k != "records"}
        receipt["query_inventory"]["sha256"] = digest(external / "query_inventory.json")
        import pyarrow
        receipt["pyarrow_version"] = pyarrow.__version__
    receipt["raw_data_committed"] = False
    receipt["model_calls"] = 0
    receipt["roster_selected_by_this_script"] = False
    receipt["prior_v25_exclusions"] = sorted(EXPOSED)
    if args.extract_roster and not failures:
        receipt["roster_extraction"] = extract_roster(external, args.extract_roster)
    run.update(ended_utc=stamp(), completed=not failures)
    atomic_json(args.receipt, receipt)
    print(json.dumps({"completed": not failures, "query_inventory": receipt.get("query_inventory")}), flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
