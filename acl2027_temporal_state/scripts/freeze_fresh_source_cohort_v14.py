#!/usr/bin/env python3
"""Freeze a source-filename-only development cohort; never reads source or QA bodies."""
import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

SEED = "WikiGraphRag-source-audit-v14-20261003"
REVISION = "06387eff2b66def7a391f0bec8820b461339dd46"
REPO = "Anonymous-Team-HC-RAG/Multi-doc-2025"
PREVIEW = "AAL AAPL ABT ALB AMT AMZN APD AVGO BA BAC C CCI CL COP CRM CVX DD DE DIS ECL ED GOOGL GS HD JPM K KO LOW MO MPC MSFT NEE ORCL PEP PM PSX RTX TGT TMO TSLA UNH UPS VZ WMT XOM".split()
COUNTERPARTIES = {"T": ["AT&T"], "TMUS": ["T-Mobile"], "CE": ["Celanese"], "DOW": ["TDCC", "Dow"], "DD": ["EID", "DowDuPont"], "NEE": ["FPL"], "UNH": ["Optum", "UnitedHealthcare"], "ECL": ["Purolite"]}
PRIOR = {
    "IBM": ["IBM", "Red Hat"], "AMZN": ["Amazon", "AWS"],
    "DIS": ["Disney"], "SBUX": ["Starbucks"], "CMG": ["Chipotle"],
    "INTC": ["Intel"], "COST": ["Costco"], "CVS": ["CVS", "Aetna", "Caremark"],
    "BA": ["Boeing", "Boeing Commercial Airplanes"], "HSBC": ["HSBC"],
    "ADDYY": ["adidas"], "PUMSY": ["Puma"], "GOOGL": ["Google", "Alphabet", "Chrome"],
    "SONY": ["Sony", "PlayStation"], "U": ["Unity"], "ADBE": ["Adobe", "Flash"],
    "MSFT": ["Microsoft", "Internet Explorer"], "AAPL": ["Apple"],
    "META": ["Facebook", "Meta"], "NKE": ["Nike"], "LYFT": ["Lyft"],
    "PLUG": ["Plug Power"], "TAP": ["Molson Coors"], "AXP": ["American Express"],
    "LEE": ["Lee Enterprises"]
}
INPUTS = [
    "data/source_register/seed_sources.json", "data/source_register/expansion_v04.json",
    "data/source_stream_v07/development_frame.json", "data/source_stream_v08/capture_manifest.json",
    "data/source_stream_v09/capture_manifest.json",
    *[f"data/correction_gate_v{v}/{f}.json" for v in (10,11) for f in
      ("discovery_corporate", "discovery_science", "discovery_technical", "screen_register")],
    "docs/related_work_v14.txt", "docs/related_work_hcrag_followup_v14.txt"
]

def digest(b):
    return hashlib.sha256(b).hexdigest()

def dump(path, obj):
    if path.exists():
        raise FileExistsError(f"Frozen artifact already exists: {path}")
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--listing", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    raw = args.listing.read_bytes()
    listing = json.loads(raw)
    assert len(listing) == 179
    by = {}
    for entry in listing:
        m = re.fullmatch(r"original_doc/(.+)_(202[234])\.html", entry["path"])
        assert entry["type"] == "file" and m, entry
        ticker, year = m[1], int(m[2])
        assert year not in by.setdefault(ticker, {})
        by[ticker][year] = entry
    assert len(by) == 89
    args.out.mkdir(parents=True, exist_ok=True)
    listing_dest = args.out / "source_listing.json"
    if listing_dest.exists():
        raise FileExistsError(listing_dest)
    listing_dest.write_bytes(raw)
    records = []
    for ticker, years in sorted(by.items()):
        reasons = []
        if ticker in PRIOR:
            reasons.append("prior project source/development inventory: " + ", ".join(PRIOR[ticker]))
        if ticker in PREVIEW:
            reasons.append("identifiable public QA/reference preview exposure during dataset metadata review")
        if ticker in COUNTERPARTIES:
            reasons.append("conservative supporting-entity/alias exposure: " + ", ".join(COUNTERPARTIES[ticker]))
        pairs = []
        for year in sorted(years):
            if year + 1 in years:
                paths = [years[year]["path"], years[year+1]["path"]]
                pair_key = SEED + "|pair|" + ticker + "|" + "|".join(paths)
                pairs.append({"years": [year, year+1], "paths": paths,
                              "pair_order_sha256": digest(pair_key.encode())})
        if not pairs:
            reasons.append("no consecutive released source years within 2022-2024")
        records.append({"issuer_id": ticker, "observed_years": sorted(years),
                        "aliases_used": PRIOR.get(ticker, []) + COUNTERPARTIES.get(ticker, []),
                        "eligibility": "excluded" if reasons else "eligible",
                        "exclusion_reasons": reasons,
                        "issuer_order_sha256": digest((SEED + "|" + ticker).encode()),
                        "consecutive_pairs_in_hash_order": sorted(pairs, key=lambda x:x["pair_order_sha256"])})
    eligible = sorted([r for r in records if r["eligibility"] == "eligible"], key=lambda x:x["issuer_order_sha256"])
    selected = []
    for rank, r in enumerate(eligible[:6], 1):
        pair = r["consecutive_pairs_in_hash_order"][0]
        files = []
        for path in pair["paths"]:
            entry = next(x for x in listing if x["path"] == path)
            files.append({**entry, "source_url": f"https://huggingface.co/datasets/{REPO}/resolve/{REVISION}/{path}",
                          "download_order": len(selected)*2 + len(files)+1})
        selected.append({"selection_rank":rank, "issuer_id":r["issuer_id"],
                         "issuer_order_sha256":r["issuer_order_sha256"], **pair, "files":files})
    ledger = {
        "schema_version":"fresh_source_exclusions_v14", "date":"2026-10-03",
        "source_listing_sha256":digest(raw),
        "inventory_inputs": {p:digest(Path(p).read_bytes()) for p in INPUTS},
        "all_old_noncorporate_groups_also_development": ["Docker", "NASA Artemis", "ESA/Roscosmos ExoMars", "HS2", "Mozilla Firefox", "Python", "OPERA", "JAMA opioid marketing", "Resplandy ocean heat", "Chan/Albarracin meta-analysis", "AstroSat", "GERDA", "GRACE", "Libreswan", "OpenSSL", "Rust", "xz", "PBL/IPCC", "curl/NVD"],
        "identifiable_preview_tickers":PREVIEW,
        "counterparty_aliases":COUNTERPARTIES,
        "other_exposed_names_without_unique_matching_issuer_in_listing":["TJC", "Delrin", "Schweppes", "PricewaterhouseCoopers", "Kensho"],
        "unresolved_exposure":"DocFinQA previews exposed truncated programs/answers with unidentified issuers. Absolute absence of content overlap cannot be certified; no cross-conversation search was made. Public model-pretraining contamination is uncontrolled.",
        "manual_selected_alias_check":{
            "PFE":["Pfizer"], "NFLX":["Netflix"], "NVDA":["NVIDIA"],
            "LLY":["Eli Lilly", "Lilly"], "BRK-B":["Berkshire Hathaway", "Berkshire", "GEICO", "BNSF"],
            "SLB":["Schlumberger", "SLB"]},
        "manual_alias_check_result":"No selected company-name matches in prior source registers, v07 frame or v10/v11 discovery ledgers; root reports no additional exposure known from visible task context/recovery summary. This is an inventory-based screen, not exhaustive zero-exposure certification.",
        "eligible_count":len(eligible), "excluded_count":len(records)-len(eligible),
        "records":records, "eligible_order":[r["issuer_id"] for r in eligible]
    }
    ledger_path = args.out / "exclusion_ledger.json"
    dump(ledger_path, ledger)
    protocol = {
        "schema_version":"fresh_source_acquisition_protocol_v14",
        "frozen_at_utc":datetime.now(timezone.utc).isoformat(),
        "status":"frozen before any of these twelve source bodies are downloaded or inspected",
        "purpose":"Six-group source-only development feasibility; no QA/reference/program access, model evaluation or benchmark admission.",
        "proposal_relation":"Bounded authorized action supersedes only the eight-group/two-attempt immediate acquisition idea in fresh_history_protocol_proposal.json; the broader evaluation design and numerical gates remain unexecuted proposals.",
        "dataset_repo":REPO, "revision":REVISION,
        "observed_source_directory_upload_commit":"759a5adea79c9c82a7a52424bab290682c3cb521",
        "listing_url":f"https://huggingface.co/api/datasets/{REPO}/tree/{REVISION}/original_doc?recursive=false&expand=false&limit=1000",
        "listing_count":179, "distinct_filename_ticker_count":89, "dataset_card_reported_companies":87, "company_count_discrepancy":"179 source files have 89 distinct ticker prefixes (45 triples and 44 singletons), whereas the dataset card reports 87 companies. No silent reconciliation or source-identity inference.", "listing_pagination_link":None,
        "source_listing_sha256":digest(raw), "exclusion_ledger_sha256":digest(ledger_path.read_bytes()),
        "selection_seed":SEED,
        "selection_rule":"Restrict ticker_year HTML to consecutive years in 2022-2024, remove every identifiable prior/preview/supported-alias entity, sort issuer IDs by SHA256(UTF8(seed+'|'+issuer_id)), choose first six. Within issuer sort consecutive pairs by SHA256(UTF8(seed+'|pair|'+issuer_id+'|'+earlier_path+'|'+later_path)), choose first. Download issuer order then earlier/later year. Hash strings use literal vertical bars and case-sensitive released ticker spelling. No answer/scope/difficulty/size selection.",
        "canonicalization_limit":"Released ticker is the source-listing issuer ID; obvious prior corporate aliases are mapped in the ledger. SEC CIK and actual issuer identities remain unverified until source-only identity checks. No contents-based reselection.",
        "selected":selected,
        "download_policy":{"logical_attempts_per_file":1, "redirects_allowed":True,
            "timeout_seconds_per_network_operation":30,"max_bytes_per_file":20*1024*1024,
            "max_total_downloaded_body_bytes":100*1024*1024,
            "replacement_on_failure":False,
            "oversize_policy":"Use advertised Content-Length when available; otherwise stop at cap and retain partial receipt. Failures/oversize/total-budget skips remain in the 12-file denominator. No retries or replacement issuers.",
            "integrity":"Record HTTP status, type, observed bytes, SHA256 and expected listing size/git blob OID match. A byte match certifies this released object only, not publisher identity, document completeness or faithful extraction."},
        "source_storage":"External tmp/fresh_source_audit_v14/source_files only; no full source bytes in repository.",
        "public_release":"Pinned URLs, source filenames, inventory/selection/exclusion metadata, retrieval receipts/hashes and scripts only. Dataset CC BY 4.0 label does not resolve underlying filing redistribution rights.",
        "allowed_followup":"Structure-only inventory and issuer/form/report-period/source provenance inspection; no new question/reference creation or QA/program access in this increment.",
        "admission_guardrails":["Transport success is not benchmark admission.", "Successive annual filings are not automatically revisions of the same assertion.", "Source host is a dataset mirror; original SEC/publisher correspondence is a separate check.", "Historical first-public availability is unresolved.", "All acquired groups become exposed development data; never call this cohort held out.", "True pair characterization, extraction fidelity and comparison-answer sufficiency remain separate later decisions.", "Source-only model-assisted review is not independent human annotation."],
        "pre_freeze_status":{"source_bodies_inspected":0,"source_bodies_downloaded":0,"native_v14_outputs_inspected":0,"new_QA_created":0},
        "metadata_access_record":["Metadata-only default-sandbox HTTP denied by network policy; no bytes returned.","Approved public pinned-tree API retrieval succeeded; 179 items and no Link pagination header.","Initial offline assertion expected card company count 87 and stopped before writing artifacts; actual filename-prefix count 89 was explicitly recorded before freezing.","Earlier helper partial tree view and failed API attempts preserved in discussion; full pinned listing now replaces partial coverage for selection, without changing seed."]
    }
    dump(args.out / "acquisition_protocol.json", protocol)
    print(json.dumps({"selected":[(x["issuer_id"],x["years"]) for x in selected],
      "expected_selected_bytes":sum(f["size"] for x in selected for f in x["files"]),
      "eligible_count":len(eligible),"file_hashes":{str(p):digest(p.read_bytes()) for p in args.out.iterdir() if p.is_file()}},indent=2))

if __name__ == "__main__":
    main()
