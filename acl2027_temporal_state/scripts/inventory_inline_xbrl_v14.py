#!/usr/bin/env python3
"""Source-only local Inline XBRL links; no value normalization, QA or network.

Detailed metadata and source anchors stay external. Public output contains counts,
hashes and a bounded set of anchors, without source text or numeric fact values.
This is a structural inventory, not a conformant or validating XBRL processor.
"""
import argparse
import collections
import hashlib
import json
import re
import xml.parsers.expat
from pathlib import Path
from lxml import etree

IX="http://www.xbrl.org/2013/inlineXBRL"
XB="http://www.xbrl.org/2003/instance"
XD="http://xbrl.org/2006/xbrldi"
XS="http://www.w3.org/2001/XMLSchema-instance"
TAGS={f"{{{IX}}}nonFraction",f"{{{XB}}}context",f"{{{XB}}}unit"}

def sha(b): return hashlib.sha256(b).hexdigest()
def text(n): return "".join(n.itertext()).strip()
def qn(n,v):
    if v is None: return None
    parts=v.split(":",1)
    prefix,local=parts if len(parts)==2 else (None,parts[0])
    uri=n.nsmap.get(prefix)
    return f"{{{uri}}}{local}" if uri else None

def tag_end(data,start):
    quote=None
    for i in range(start,len(data)):
        c=data[i]
        if quote:
            if c==quote: quote=None
        elif c in (34,39): quote=c
        elif c==62: return i+1
    raise ValueError("Unterminated source tag")

def source_spans(data):
    parser=xml.parsers.expat.ParserCreate(namespace_separator="}")
    parser.SetParamEntityParsing(xml.parsers.expat.XML_PARAM_ENTITY_PARSING_NEVER)
    parser.ExternalEntityRefHandler=lambda *args:0
    stack=[]; spans=collections.defaultdict(list)
    def start(name,attrs):
        tag="{"+name if "}" in name else name
        begin=parser.CurrentByteIndex; opening_end=tag_end(data,begin)
        self_close=data[begin:opening_end].rstrip().endswith(b"/>")
        record={"byte_start":begin,"byte_stop":None}
        if tag in TAGS: spans[tag].append(record)
        stack.append((tag,record,opening_end,self_close))
    def end(name):
        tag,record,opening_end,self_close=stack.pop()
        if tag in TAGS:
            stop=opening_end if self_close else tag_end(data,parser.CurrentByteIndex)
            record.update(byte_stop=stop,span_sha256=sha(data[record["byte_start"]:stop]))
    parser.StartElementHandler=start; parser.EndElementHandler=end
    parser.Parse(data,True)
    return spans

def visibility(n):
    nodes=[n,*n.iterancestors()]
    ix_hidden=any(x.tag==f"{{{IX}}}hidden" for x in nodes)
    ix_header=any(x.tag==f"{{{IX}}}header" for x in nodes)
    inline_hidden=any("hidden" in x.attrib or re.search(r"display\s*:\s*none|visibility\s*:\s*hidden",x.get("style",""),re.I) for x in nodes)
    return {"ix_hidden":ix_hidden,"ix_header":ix_header,"explicit_inline_hidden":inline_hidden,
            "category":"hidden_markup" if ix_hidden or ix_header or inline_hidden else "no_hidden_marker_rendering_unverified"}

def context_record(n,anchor):
    ids=n.findall(f"{{{XB}}}entity/{{{XB}}}identifier")
    periods=n.findall(f"{{{XB}}}period")
    p=periods[0] if len(periods)==1 else None
    period={etree.QName(c).localname:text(c) for c in p} if p is not None else {}
    shape=sorted(period)
    valid_period=shape in [["instant"],["endDate","startDate"],["forever"]]
    dims=[]
    for d in n.iter():
        if d.tag not in (f"{{{XD}}}explicitMember",f"{{{XD}}}typedMember"): continue
        dims.append({"kind":etree.QName(d).localname,"dimension":qn(d,d.get("dimension")),
                     "member":qn(d,text(d)) if d.tag.endswith("explicitMember") else None,
                     "typed_content_sha256":sha(etree.tostring(d,with_tail=False)) if d.tag.endswith("typedMember") else None,
                     "parent_container":etree.QName(d.getparent()).localname})
    additional=[]
    for container in n.iter():
        if container.tag in (f"{{{XB}}}segment",f"{{{XB}}}scenario"):
            additional += [etree.QName(c).text for c in container if c.tag not in (f"{{{XD}}}explicitMember",f"{{{XD}}}typedMember") and isinstance(c.tag,str)]
    return {"id":n.get("id"),"anchor":anchor,"entity_identifiers":[{"scheme":x.get("scheme"),"identifier":text(x)} for x in ids],
        "period":period,"period_structurally_resolvable":len(periods)==1 and valid_period,
        "entity_structurally_resolvable":len(ids)==1 and bool(text(ids[0])) and bool(ids[0].get("scheme")),
        "dimensions":dims,"dimension_qnames_resolvable":all(d["dimension"] and (d["member"] or d["kind"]=="typedMember") for d in dims),
        "non_dimensional_segment_scenario_children":additional,
        "DTS_semantics_validated":False,"default_dimensions_inferred":False}

def unit_record(n,anchor):
    simple=n.findall(f"{{{XB}}}measure")
    divs=n.findall(f"{{{XB}}}divide")
    num=[]; den=[]
    if len(divs)==1:
        num=divs[0].findall(f"{{{XB}}}unitNumerator/{{{XB}}}measure")
        den=divs[0].findall(f"{{{XB}}}unitDenominator/{{{XB}}}measure")
    shape="simple_product" if simple and not divs else "divide" if len(divs)==1 and num and den and not simple else "unresolved"
    measures=[qn(x,text(x)) for x in simple]
    nums=[qn(x,text(x)) for x in num]; dens=[qn(x,text(x)) for x in den]
    return {"id":n.get("id"),"anchor":anchor,"shape":shape,"measures":measures,"numerator_measures":nums,"denominator_measures":dens,
            "locally_resolvable":shape!="unresolved" and all(measures+nums+dens),"semantic_unit_validation":False}

def inventory(path,expected_hash,outdir):
    data=path.read_bytes(); assert sha(data)==expected_hash
    root=etree.fromstring(data,etree.XMLParser(resolve_entities=False,no_network=True,load_dtd=False,huge_tree=True))
    assert etree.QName(root).namespace=="http://www.w3.org/1999/xhtml"
    tree=root.getroottree(); spans=source_spans(data); anchors={}
    for tag in TAGS:
        nodes=list(root.iter(tag)); assert len(nodes)==len(spans[tag]),(path.name,tag)
        for node,span in zip(nodes,spans[tag]):
            anchors[node]={"source_sha256":expected_hash,"dom_path":tree.getpath(node),**span}
    contexts=[context_record(n,anchors[n]) for n in root.iter(f"{{{XB}}}context")]
    units=[unit_record(n,anchors[n]) for n in root.iter(f"{{{XB}}}unit")]
    contexts_by_id=collections.defaultdict(list); units_by_id=collections.defaultdict(list)
    for c in contexts: contexts_by_id[c["id"]].append(c)
    for u in units: units_by_id[u["id"]].append(u)
    all_ids=collections.Counter(n.get("id") for n in root.iter() if isinstance(n.tag,str) and n.get("id") is not None)
    counts=collections.Counter(); formats=collections.Counter(); targets=collections.Counter(); issues=collections.Counter()
    details=[]; examples={}; aspect_keys=collections.Counter()
    for i,n in enumerate(root.iter(f"{{{IX}}}nonFraction")):
        cr,ur=n.get("contextRef"),n.get("unitRef")
        cs,us=contexts_by_id.get(cr,[]),units_by_id.get(ur,[])
        context_status="unique_local" if len(cs)==1 else "missing_local" if not cs else "ambiguous_local"
        unit_status="unique_local" if len(us)==1 else "missing_local" if not us else "ambiguous_local"
        fmt=qn(n,n.get("format")); concept=qn(n,n.get("name"))
        vis=visibility(n); nil=n.get(f"{{{XS}}}nil") in ("true","1")
        nested_children=list(n.iterchildren(f"{{{IX}}}nonFraction"))
        nested_ancestor=any(a.tag==f"{{{IX}}}nonFraction" for a in n.iterancestors())
        local_issues=[]
        if context_status!="unique_local": local_issues.append("context_"+context_status)
        if unit_status!="unique_local": local_issues.append("unit_"+unit_status)
        if not concept: local_issues.append("concept_qname_unresolved")
        if n.get("format") is not None and fmt is None: local_issues.append("format_qname_unresolved")
        if n.get("sign") not in (None,"-"): local_issues.append("sign_lexical_invalid")
        if n.get("scale") is not None and not re.fullmatch(r"[+-]?\d+",n.get("scale")): local_issues.append("scale_lexical_invalid")
        if len(cs)==1:
            if not cs[0]["entity_structurally_resolvable"]: local_issues.append("context_entity_unresolved")
            if not cs[0]["period_structurally_resolvable"]: local_issues.append("context_period_unresolved")
            if not cs[0]["dimension_qnames_resolvable"]: local_issues.append("context_dimension_qname_unresolved")
            if cs[0]["non_dimensional_segment_scenario_children"]: local_issues.append("context_additional_content_uninterpreted")
        if len(us)==1 and not us[0]["locally_resolvable"]: local_issues.append("unit_structure_unresolved")
        for ident in (n.get("id"),cr,ur):
            if ident and all_ids[ident]>1: local_issues.append("referenced_or_fact_id_not_globally_unique")
        attrs={k:n.get(k) for k in ("id","name","contextRef","unitRef","format","sign","scale","decimals","precision","target","tupleRef")}
        record={"fact_ordinal":i,"anchor":anchors[n],"attributes":attrs,"concept_qname":concept,"format_qname":fmt,
            "context_link_status":context_status,"unit_link_status":unit_status,
            "context_anchor":cs[0]["anchor"] if len(cs)==1 else None,"unit_anchor":us[0]["anchor"] if len(us)==1 else None,
            "visibility":vis,"xsi_nil":nil,"nested_nonfraction_children":len(nested_children),"nested_nonfraction_ancestor":nested_ancestor,
            "issues":sorted(set(local_issues)),"local_link_metadata_resolvable":not local_issues,
            "value_status":"nil_declared_no_quantity" if nil else "format_transform_pending" if n.get("format") else "untransformed_lexical_value_not_validated",
            "numeric_value_read_or_normalized_for_output":False,"taxonomy_and_registry_validated":False}
        details.append(record); counts["nonfraction_facts"]+=1; counts["local_links_and_basic_shapes_resolvable"]+=not local_issues
        counts["context_unique_local"]+=len(cs)==1; counts["unit_unique_local"]+=len(us)==1
        counts[vis["category"]]+=1; counts["ix_hidden"]+=vis["ix_hidden"]; counts["xsi_nil"]+=nil
        counts["format_attribute_present"]+=n.get("format") is not None; counts["scale_attribute_present"]+=n.get("scale") is not None
        counts["sign_attribute_present"]+=n.get("sign") is not None; counts["nested_nonfraction_children"]+=bool(nested_children)
        counts["nested_nonfraction_ancestor"]+=nested_ancestor
        counts["context_with_explicit_dimensions"]+=len(cs)==1 and any(d["kind"]=="explicitMember" for d in cs[0]["dimensions"])
        counts["context_with_typed_dimensions"]+=len(cs)==1 and any(d["kind"]=="typedMember" for d in cs[0]["dimensions"])
        counts["unit_divide"]+=len(us)==1 and us[0]["shape"]=="divide"
        formats[fmt or ("unresolved_qname" if n.get("format") else "absent")]+=1
        targets[n.get("target","default")]+=1; issues.update(set(local_issues))
        aspect_keys[(n.get("target","default"),concept,cr,ur)]+=1
        for category,flag in [("first",i==0),("format",bool(n.get("format"))),("hidden",vis["category"]=="hidden_markup"),("nil",nil),("dimensions",len(cs)==1 and bool(cs[0]["dimensions"])),("issue",bool(local_issues)),("nested",bool(nested_children))]:
            if flag and category not in examples:
                examples[category]={"fact_ordinal":i,"anchor":anchors[n],"context_anchor":record["context_anchor"],"unit_anchor":record["unit_anchor"],"local_link_metadata_resolvable":not local_issues,"category":category,"source_text_and_value_omitted":True}
    bundle={"source_path":path.name,"source_sha256":expected_hash,"contexts":contexts,"units":units,"facts":details,
        "scope":"Metadata/anchors only. No raw fact text, numeric normalization, QA or inferred equivalence. Context dates/identifiers and dimension/unit QNames are source metadata kept external."}
    dst=outdir/(path.stem+".bindings.json"); assert not dst.exists(); dst.write_text(json.dumps(bundle,indent=2)+"\n")
    return {"source_path":path.name,"source_sha256":expected_hash,"strict_XML_parse":True,"counts":dict(counts),
        "context_count":len(contexts),"unit_count":len(units),"duplicate_global_id_count":sum(v>1 for v in all_ids.values()),
        "same_concept_contextref_unitref_target_groups_with_multiple_facts":sum(v>1 for v in aspect_keys.values()),
        "same_key_limit":"Syntactic repeated IDs/aspects only, not semantic duplicate detection or value-consistency reconciliation.",
        "format_qname_counts":dict(formats),"target_counts":dict(targets),"issue_counts":dict(issues),
        "anchors":examples,"external_metadata_path":str(dst),"external_metadata_sha256":sha(dst.read_bytes()),"external_metadata_bytes":dst.stat().st_size}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--identity",type=Path,required=True); ap.add_argument("--sources",type=Path,required=True)
    ap.add_argument("--external-output",type=Path,required=True); ap.add_argument("--public-output",type=Path,required=True); args=ap.parse_args()
    assert not args.public_output.exists(); args.external_output.mkdir(parents=True,exist_ok=True)
    identity=json.loads(args.identity.read_text()); records=[]
    for source in identity["records"]:
        p=args.sources/Path(source["source_path"]).name
        records.append(inventory(p,source["source_sha256"],args.external_output))
        print(json.dumps({"file":p.name,"counts":records[-1]["counts"],"issues":records[-1]["issue_counts"]}),flush=True)
    totals=collections.Counter()
    for r in records: totals.update(r["counts"])
    result={"schema_version":"inline_xbrl_link_inventory_v14","status":"source-only structural feasibility; no normalized quantities, QA references or predictions",
        "input_identity_sha256":sha(args.identity.read_bytes()),"script_sha256":sha(Path(__file__).read_bytes()),
        "parser":{"XML":"lxml.etree strict namespace-aware, no external entities/DTD/network","exact_spans":"Expat byte indices with quoted-attribute-aware tag bounds","lxml_version":etree.LXML_VERSION},
        "records":records,"totals":dict(totals),
        "interpretation_limits":["Local reference resolution is not XBRL conformance or valid financial semantics.","Transformation QName resolution is not registry support or successful value conversion.","Numeric fact values are not published, normalized, reconciled or used to create QA.","No hidden marker is not verified browser visibility; CSS/rendering remain untested.","DTS/taxonomy types, period-type compatibility, dimensional defaults/domains, typed dimensions, schema-defined concepts, duplicates and calculations are unvalidated.","Only each captured document is inspected; absent references would remain unresolved within this single-document inventory, not prove invalidity of an entire Inline XBRL Document Set.","A structured-reader baseline must receive the same accessible metadata and charge its representation tokens; source tags do not automatically answer comparative scope questions.","This uses existing standards and is not a novelty claim or an independent annotation source."]}
    args.public_output.write_text(json.dumps(result,indent=2)+"\n")
    print(json.dumps({"public_output":str(args.public_output),"sha256":sha(args.public_output.read_bytes()),"totals":dict(totals)}),flush=True)

if __name__=="__main__": main()
