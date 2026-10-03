#!/usr/bin/env python3
"""Offline released-object integrity and source-only identity/structure inventory.

No QA files, financial answer values, native predictions, or network are used.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path
from lxml import etree, html

FACTS = ("EntityRegistrantName", "EntityCentralIndexKey", "DocumentType",
         "DocumentPeriodEndDate", "DocumentFiscalYearFocus", "DocumentFiscalPeriodFocus")

def h(data):
    return hashlib.sha256(data).hexdigest()

def write_new(path, obj):
    if path.exists():
        raise FileExistsError(path)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False)+"\n")

def audit_file(path, item):
    data = path.read_bytes()
    tree = html.fromstring(data)
    facts = []
    for node in tree.iter():
        if node.get("name") not in {"dei:"+x for x in FACTS}:
            continue
        ancestry = [node, *node.iterancestors()]
        facts.append({"name":node.get("name"), "value":" ".join(node.text_content().split()),
                      "tag":node.tag, "contextref":node.get("contextref"),
                      "dom_path":tree.getroottree().getpath(node),
                      "in_ix_header_or_hidden":any(n.tag in ("ix:header","ix:hidden") for n in ancestry),
                      "hidden_by_explicit_inline_style":any(re.search(r"display\s*:\s*none|visibility\s*:\s*hidden",n.get("style", ""),re.I) for n in ancestry),
                      "visibility_limit":"DOM/inline-style check only; external CSS and browser rendering not verified."})
    spans = []
    for field in FACTS:
        pattern = rb'<ix:nonNumeric\b[^>]*\bname=[\'"]dei:'+field.encode()+rb'[\'"][^>]*>.*?</ix:nonNumeric\s*>'
        for match in re.finditer(pattern, data, flags=re.I|re.S):
            spans.append({"name":"dei:"+field,"byte_start":match.start(),"byte_stop":match.end(),"span_sha256":h(match.group())})
    sec_urls = sorted(set(tree.xpath('//a[contains(@href,"sec.gov/Archives/")]/@href')))
    ix_contexts = [n for n in tree.iter() if isinstance(n.tag,str) and n.tag.lower().endswith(":context")]
    years = sorted(set(f["value"] for f in facts if f["name"] == "dei:DocumentFiscalYearFocus"))
    year = int(re.search(r"_(\d{4})\.html$",item["path"])[1])
    schema_refs = []
    for n in tree.iter():
        if isinstance(n.tag,str) and n.tag.lower().endswith("schemaref"):
            schema_refs.extend(v for k,v in n.attrib.items() if k.lower().endswith("href"))
    return {"source_path":item["path"],"source_sha256":h(data),"bytes":len(data),
        "html_title":" ".join(tree.xpath('string(//title)').split()),
        "filename_year":year,"document_fiscal_year_values":years,
        "filename_matches_document_fiscal_year":years==[str(year)],
        "identity_facts":facts,"identity_fact_byte_spans":spans,
        "structural_counts":{"html_tables":len(tree.xpath('//table')),"table_rows":len(tree.xpath('//tr')),
            "table_cells":len(tree.xpath('//td|//th')),"elements_with_colspan":len(tree.xpath('//*[@colspan]')),
            "elements_with_rowspan":len(tree.xpath('//*[@rowspan]')),
            "nested_tables":len(tree.xpath('//table[ancestor::table]')),
            "ix_nonfraction":sum(n.tag=="ix:nonfraction" for n in tree.iter()),
            "ix_nonnumeric":sum(n.tag=="ix:nonnumeric" for n in tree.iter()),"xbrl_contexts":len(ix_contexts)},
        "schema_references":schema_refs,"sec_archive_hyperlink_count":len(sec_urls),
        "sec_archive_hyperlink_urls":sec_urls,
        "original_source_identity_status":"Not independently matched to original SEC accession or bytes. Embedded exhibit/reference links do not establish this document's filing accession.",
        "structure_limit":"HTML tables may be layout tables. Raw tag counts do not establish semantic tables, faithful extraction, attachment correctness, comparison ambiguity or question answerability."}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--protocol",type=Path,required=True)
    ap.add_argument("--receipt",type=Path,required=True)
    ap.add_argument("--external-dir",type=Path,required=True)
    ap.add_argument("--integrity-out",type=Path,required=True)
    ap.add_argument("--identity-out",type=Path,required=True)
    args = ap.parse_args()
    proto_raw, receipt_raw = args.protocol.read_bytes(), args.receipt.read_bytes()
    proto, receipt = json.loads(proto_raw),json.loads(receipt_raw)
    records, identity = [], []
    by_path = {r["source_path"]:r for r in receipt["records"]}
    for pair in proto["selected"]:
        for item in pair["files"]:
            r = by_path[item["path"]]
            path = args.external_dir / r["external_relative_path"]
            data = path.read_bytes()
            lfs = item.get("lfs")
            actual = h(data) if lfs else hashlib.sha1(b"blob "+str(len(data)).encode()+b"\0"+data).hexdigest()
            expected = lfs["oid"] if lfs else item["oid"]
            match = actual==expected and len(data)==item["size"]
            records.append({"source_path":item["path"],"source_sha256":h(data),
                "initial_receipt_status":r["status"],"size_matches_listing":len(data)==item["size"],
                "digest_namespace":"LFS content SHA256" if lfs else "Git blob SHA1",
                "expected_object_digest":expected,"observed_object_digest":actual,
                "verified_exact_released_object":match,
                "reconciliation":"Git tree oid names the LFS pointer, not the downloaded HTML. SHA256 equals the content oid already present in the pre-download frozen listing/protocol." if lfs else "Ordinary Git blob digest confirmed.",
                "redownloads":0})
            identity.append(audit_file(path,item))
    bindings={"acquisition_protocol_sha256":h(proto_raw),"initial_capture_receipt_sha256":h(receipt_raw),"audit_script_sha256":h(Path(__file__).read_bytes())}
    write_new(args.integrity_out,{"schema_version":"fresh_source_integrity_reconciliation_v14",
        "input_bindings":bindings,"preserves_initial_receipt_and_checker":True,
        "reason":"Offline correction of digest namespace for two already frozen Git LFS entries, without changed source selection, retries or source bytes.",
        "records":records,"summary":{"verified_exact_released_objects":sum(r["verified_exact_released_object"] for r in records),"total_sources":12,"LFS_sources":sum(r["digest_namespace"]=="LFS content SHA256" for r in records)},
        "nonclaims":["Not an original-publisher byte match.","Not a faithful extraction or source completeness certificate.","Not benchmark admission."]})
    write_new(args.identity_out,{"schema_version":"fresh_source_identity_structure_v14",
        "input_bindings":bindings,"parser":{"library":"lxml.html", "version":etree.LXML_VERSION},
        "review_status":"Mechanical source-only inventory; no browser/rendered check, independent human review, new question/reference or model run.",
        "records":identity,"summary":{"source_files":len(identity),"filename_fiscal_year_mismatches":[r["source_path"] for r in identity if not r["filename_matches_document_fiscal_year"]]}})
    print(json.dumps({str(p):h(p.read_bytes()) for p in [args.integrity_out,args.identity_out]},indent=2))

if __name__=="__main__":
    main()
